"""
Orchestrator — state-driven cycle runner.

Rules enforced here (steering rules 28-33, 36):

  28  Read state, decide which agents to run.  Never a fixed sequence.
      A cycle where all agents are skipped is a SUCCESS.
  29  Every delegation carries goal + stop_conditions set here.
      Agents never set their own limits.
  30  The Orchestrator never does an agent's work.
      No fetch, score, or analysis logic lives here.
  31  Print and log the PLAN before executing it — which agents run,
      which are skipped, and the state value that caused each decision.
  32  One agent failing does not stop the cycle.  Log failure, continue,
      mark cycle "partial".
  33  Write exactly one summary row per cycle: what ran, what was skipped,
      why, duration per agent, outcome (complete | partial | nothing_to_do
      | degraded).
  36  On verification failure: re-run ONLY the failing agent once with
      adjusted context, then verify once more.  If still failing, mark
      cycle "degraded" and stop.  No unbounded retry.

Adding a fourth agent requires exactly:
  1. One entry in _build_registry().
  2. One decision rule in planning.build_plan().
  Nothing else.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import edgedash.storage as storage
from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.fetcher import Fetcher
from edgedash.agents.gap_analyzer import GapAnalyzer
from edgedash.agents.mock_fetcher import MockFetcher
from edgedash.agents.scorer import Scorer
from edgedash.agents.verifier import Verifier
from edgedash.config import Config
from edgedash.planning import Plan, Task, build_plan
from edgedash.state import read_state


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------

def _build_registry(config: Config) -> dict[str, Agent]:
    fetcher: Agent = MockFetcher() if config.use_mock_fetcher else Fetcher()
    return {
        fetcher.name:      fetcher,
        Scorer.name:       Scorer(),
        GapAnalyzer.name:  GapAnalyzer(),
        Verifier.name:     Verifier(),        # fourth agent — one line
    }


# ---------------------------------------------------------------------------
# Verifier task (always appended after productive agents run)
# ---------------------------------------------------------------------------

def _verifier_task(config: Config) -> Task:
    return Task(
        agent_name="Verifier",
        goal="Check plausibility of this cycle's scores, extraction, and gaps",
        stop_conditions={},
        reason="run after every productive cycle",
        skipped=False,
    )


# ---------------------------------------------------------------------------
# Adjusted stop_conditions for score_spread retry
# ---------------------------------------------------------------------------

def _widen_scorer_conditions(config: Config) -> dict[str, Any]:
    """
    Return stop_conditions for a Scorer retry after a score_spread failure.

    Strategy: double the batch size (capped at fetch_max_listings).

    Why this should produce more spread
    ------------------------------------
    The Scorer is deterministic Python — it cannot inflate or compress
    scores on its own.  A narrow spread means the listings in the batch
    are genuinely similar (same seniority band, same city, all remote,
    etc.).  A larger sample drawn from a more varied pool is the only
    lever available without touching scoring weights.

    Doubling max_items makes the Scorer pull from a wider slice of the
    unscored queue, increasing the chance of hitting listings with
    different seniority, location, or skill overlap — all of which drive
    the spread.  If spread is still low after the wider sample, the data
    genuinely clusters and that's a legitimate finding, not a bug.
    """
    widened = min(
        config.score_batch_size * 2,
        config.fetch_max_listings,
    )
    return {
        "max_items":    widened,
        "max_seconds":  config.score_max_seconds,
        "widen_sample": True,   # self-documenting flag in cycle_log
    }


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_DIVIDER = "─" * 72
_THIN    = "·" * 72


def _header(text: str) -> None:
    print(f"\n{_DIVIDER}")
    print(f"  {text}")
    print(_DIVIDER)


def _print_state(state) -> None:
    print("\n📋  STATE")
    print(_THIN)
    if state.last_fetch_at is None:
        print("  last_fetch_at      : never")
    else:
        print(f"  last_fetch_at      : "
              f"{state.last_fetch_at.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  hours_since_fetch  : {state.hours_since_fetch:.1f}")
    print(f"  unscored_count     : {state.unscored_count}")
    print(f"  gaps_stale         : {state.gaps_stale}")
    if state.last_cycle_verdict:
        print(f"  last_cycle_verdict : {state.last_cycle_verdict}")


def _print_agent_result(result: AgentResult, elapsed_ms: int) -> None:
    icon = "✅" if result.status not in ("failed",) else "❌"
    print(f"\n  {icon}  {result.agent}")
    print(f"      status          : {result.status}")
    print(f"      records touched : {result.records_touched}")
    print(f"      notes           : {result.notes}")
    print(f"      elapsed         : {elapsed_ms} ms")


# ---------------------------------------------------------------------------
# Run one agent and log its per-agent row
# ---------------------------------------------------------------------------

def _run_agent(
    agent: Agent,
    config: Config,
    stop_conditions: dict[str, Any],
    run_results: list[dict[str, Any]],
) -> AgentResult:
    """
    Execute one agent, log its per-agent cycle_log row, record in
    run_results, and return the AgentResult.

    Exceptions are caught here so the cycle continues (rule 32).
    """
    agent_start = datetime.now(timezone.utc)
    try:
        result = agent.run(config, config.db_path,
                           stop_conditions=stop_conditions)
    except Exception as exc:
        result = AgentResult(
            agent=agent.name,
            status="failed",
            records_touched=0,
            notes=f"{type(exc).__name__}: {exc}",
        )

    agent_end = datetime.now(timezone.utc)
    elapsed_ms = int((agent_end - agent_start).total_seconds() * 1000)

    _print_agent_result(result, elapsed_ms)

    storage.log_cycle(
        path=config.db_path,
        agent=result.agent,
        started_at=agent_start,
        finished_at=agent_end,
        records_touched=result.records_touched,
        status=result.status,
        notes=result.notes,
    )

    run_results.append({
        "agent":           result.agent,
        "status":          result.status,
        "records_touched": result.records_touched,
        "elapsed_ms":      elapsed_ms,
        "notes":           result.notes,
    })

    return result


# ---------------------------------------------------------------------------
# Summary row builder
# ---------------------------------------------------------------------------

def _build_summary_notes(
    plan: Plan,
    run_results: list[dict[str, Any]],
    outcome: str,
    verdict_str: str,
    retry_count: int,
    cycle_elapsed_s: float,
) -> str:
    parts: list[str] = [f"outcome={outcome}", f"verdict={verdict_str}"]

    if retry_count:
        parts.append(f"retries={retry_count}")

    for r in run_results:
        parts.append(
            f"{r['agent']}: {r['status']} "
            f"({r['elapsed_ms']}ms, {r['records_touched']} records)"
        )

    for task in plan.agents_skipped:
        parts.append(f"{task.agent_name}: skipped ({task.reason})")

    parts.append(f"cycle={cycle_elapsed_s:.1f}s")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_cycle(config: Config) -> None:
    cycle_start = datetime.now(timezone.utc)

    _header(
        f"EdgeDash  —  cycle started "
        f"{cycle_start.strftime('%Y-%m-%d %H:%M UTC')}"
    )

    # (a) Init DB.
    storage.init_db(config.db_path)

    # (b) Read state.
    state = read_state(config, now=cycle_start)
    _print_state(state)

    # (c) Build plan.
    registry = _build_registry(config)
    plan = build_plan(state, config)

    # (d) Print plan BEFORE executing (rule 31).
    print(f"\n🗺️  {plan.render()}")

    # (e) nothing_to_do — clean exit, no warnings (rule 28).
    if plan.is_idle:
        _write_cycle_summary(
            config=config,
            plan=plan,
            run_results=[],
            outcome="nothing_to_do",
            verdict_str="n/a",
            retry_count=0,
            cycle_start=cycle_start,
        )
        print(
            f"\n  ✓  Nothing to do this cycle  "
            f"(next fetch in "
            f"{config.fetch_interval_hours - state.hours_since_fetch:.1f}h)\n"
        )
        return

    # (f) Execute productive tasks (rule 32 — one failure never stops cycle).
    run_results: list[dict[str, Any]] = []
    any_failed  = False

    print("\n⚙️   RUNNING")
    print(_THIN)

    for task in plan.agents_to_run:
        agent = registry[task.agent_name]
        result = _run_agent(agent, config, task.stop_conditions, run_results)
        if result.status == "failed":
            any_failed = True

    # (g) Verify (rule 36).
    verifier      = registry[Verifier.name]
    retry_count   = 0
    verdict_str   = "n/a"

    print("\n🔍  VERIFYING")
    print(_THIN)

    v_result = _run_agent(verifier, config, {}, run_results)
    verdict_str = "pass" if v_result.status == "ok" else "fail"

    if v_result.status != "ok":
        # ── One retry for the failing agent (rule 36) ─────────────────────
        failed_check = _extract_failed_check(v_result.notes)
        retry_agent_name, retry_stop = _retry_plan(
            failed_check, config, registry
        )

        if retry_agent_name:
            retry_count = 1   # only counted when a retry actually executes
            print(f"\n↩️   RETRY  ({retry_agent_name}, reason: {failed_check})")
            print(_THIN)
            retry_agent = registry[retry_agent_name]
            _run_agent(retry_agent, config, retry_stop, run_results)

            # Verify once more — no further retry allowed (rule 36).
            print("\n🔍  RE-VERIFYING  (final — no further retry)")
            print(_THIN)
            v2_result = _run_agent(verifier, config, {}, run_results)
            verdict_str = "pass" if v2_result.status == "ok" else "fail"

            if v2_result.status != "ok":
                # Degraded — stop, log, do NOT raise (rule 36).
                print(
                    f"\n  ⚠️  Verification failed after retry. "
                    f"Cycle marked DEGRADED.\n"
                    f"     {v2_result.notes}"
                )
                outcome = "degraded"
                _write_cycle_summary(
                    config=config,
                    plan=plan,
                    run_results=run_results,
                    outcome=outcome,
                    verdict_str=verdict_str,
                    retry_count=retry_count,
                    cycle_start=cycle_start,
                )
                _print_console_summary(
                    plan, run_results, outcome, cycle_start,
                    verdict_str, retry_count
                )
                return
        else:
            # No retry strategy for this check — degrade immediately.
            print(
                f"\n  ⚠️  No retry strategy for check '{failed_check}'. "
                f"Cycle marked DEGRADED."
            )
            outcome = "degraded"
            _write_cycle_summary(
                config=config,
                plan=plan,
                run_results=run_results,
                outcome=outcome,
                verdict_str=verdict_str,
                retry_count=retry_count,
                cycle_start=cycle_start,
            )
            _print_console_summary(
                plan, run_results, outcome, cycle_start,
                verdict_str, retry_count
            )
            return

    # (h) Normal outcome.
    outcome = "partial" if any_failed else "complete"
    _write_cycle_summary(
        config=config,
        plan=plan,
        run_results=run_results,
        outcome=outcome,
        verdict_str=verdict_str,
        retry_count=retry_count,
        cycle_start=cycle_start,
    )
    _print_console_summary(
        plan, run_results, outcome, cycle_start,
        verdict_str, retry_count
    )


# ---------------------------------------------------------------------------
# Retry routing
# ---------------------------------------------------------------------------

def _extract_failed_check(notes: str) -> str:
    """
    Parse the first failing check name from Verifier notes.

    Notes format: "VERDICT: fail — <check>: observed … | <check>: …"
    Returns the first check name, or "" if unparseable.
    """
    # Strip the prefix
    marker = "VERDICT: fail — "
    if marker not in notes:
        return ""
    tail = notes.split(marker, 1)[1]
    # First segment before "|" or ":"
    first = tail.split("|")[0].strip()
    return first.split(":")[0].strip()


def _retry_plan(
    failed_check: str,
    config: Config,
    registry: dict[str, Agent],
) -> tuple[str, dict[str, Any]]:
    """
    Return (agent_name, stop_conditions) for a single retry, or
    ("", {}) when no retry strategy exists for this check.
    """
    if failed_check == "score_spread":
        return "Scorer", _widen_scorer_conditions(config)

    if failed_check == "gap_sample_size":
        # Re-run GapAnalyzer — maybe scoring batch added more data.
        return "GapAnalyzer", {"max_seconds": config.analyse_max_seconds}

    # freshness and extraction_sanity have no meaningful one-cycle remedy.
    return "", {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_cycle_summary(
    config: Config,
    plan: Plan,
    run_results: list[dict[str, Any]],
    outcome: str,
    verdict_str: str,
    retry_count: int,
    cycle_start: datetime,
) -> None:
    """Write exactly one cycle_log summary row (rule 33)."""
    now = datetime.now(timezone.utc)
    elapsed_s = (now - cycle_start).total_seconds()
    notes = _build_summary_notes(
        plan, run_results, outcome, verdict_str, retry_count, elapsed_s
    )
    storage.log_cycle(
        path=config.db_path,
        agent="Orchestrator",
        started_at=cycle_start,
        finished_at=now,
        records_touched=sum(r["records_touched"] for r in run_results),
        status="ok" if outcome not in ("partial", "degraded") else "failed",
        notes=notes,
    )


def _print_console_summary(
    plan: Plan,
    run_results: list[dict[str, Any]],
    outcome: str,
    cycle_start: datetime,
    verdict_str: str = "n/a",
    retry_count: int = 0,
) -> None:
    elapsed_s = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    _header("CYCLE SUMMARY")
    print(f"  Outcome : {outcome.upper()}")
    print(f"  Verdict : {verdict_str.upper()}")
    if retry_count:
        print(f"  Retries : {retry_count}")
    print(f"  Elapsed : {elapsed_s:.1f}s")
    print(_THIN)
    print(f"  {'Agent':<20} {'Status':<10} {'Records':>8}  {'ms':>6}  Notes")
    print(_THIN)
    for r in run_results:
        print(
            f"  {r['agent']:<20} {r['status']:<10} "
            f"{r['records_touched']:>8}  {r['elapsed_ms']:>6}  "
            f"{r['notes'][:60]}"
        )
    for task in plan.agents_skipped:
        print(
            f"  {task.agent_name:<20} {'skipped':<10} "
            f"{'':>8}  {'':>6}  {task.reason[:60]}"
        )
    print(_THIN)
    total = sum(r["records_touched"] for r in run_results)
    print(f"  {'TOTAL':<20} {'':<10} {total:>8}")
    print()
