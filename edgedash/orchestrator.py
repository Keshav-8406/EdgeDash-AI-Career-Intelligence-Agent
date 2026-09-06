"""
Orchestrator — reads state, decides what to run, delegates to agents,
logs every run to cycle_log, and prints a readable cycle summary.

The Orchestrator never fetches or scores directly. It only reads state
and calls agent.run().
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Type

import edgedash.storage as storage
from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.fetcher import Fetcher
from edgedash.agents.gap_analyzer import GapAnalyzer
from edgedash.agents.mock_fetcher import MockFetcher
from edgedash.agents.scorer import Scorer
from edgedash.config import Config


# ---------------------------------------------------------------------------
# Placeholder agent factory
# ---------------------------------------------------------------------------

def _make_placeholder(agent_name: str) -> Agent:
    """Return a no-op agent that logs 'not implemented yet' and skips."""

    class _Placeholder:
        name: str = agent_name

        def run(self, config: Config, db_path: str) -> AgentResult:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="NOT IMPLEMENTED YET — skipped.",
            )

    _Placeholder.name = agent_name
    return _Placeholder()


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------
# To swap in the real Fetcher on Thursday: replace MockFetcher() with
# RealFetcher() in the list below — nothing else changes.

def _build_registry(config: Config) -> list[Agent]:
    fetcher: Agent = MockFetcher() if config.use_mock_fetcher else Fetcher()
    return [
        fetcher,
        Scorer(),
        GapAnalyzer(),
    ]


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_DIVIDER = "─" * 60
_THIN    = "·" * 60


def _header(text: str) -> None:
    print(f"\n{_DIVIDER}")
    print(f"  {text}")
    print(_DIVIDER)


def _print_state(last_fetch: datetime | None, unscored: int) -> None:
    print("\n📋  STATE READ")
    print(_THIN)
    if last_fetch is None:
        print("  Last fetch    : never (fresh database)")
    else:
        age_mins = (datetime.now(timezone.utc) - last_fetch).seconds // 60
        print(f"  Last fetch    : {last_fetch.strftime('%Y-%m-%d %H:%M UTC')}  ({age_mins} min ago)")
    print(f"  Unscored rows : {unscored}")


def _print_plan(agents: list[Agent]) -> None:
    print("\n🗺️  PLAN")
    print(_THIN)

    for agent in agents:
        if "Placeholder" in type(agent).__name__:
            tag = "  ⏭  SKIP (placeholder)"
        else:
            tag = "  ▶  RUN"

        print(f"{tag}  {agent.name}")

    print()
    print("  Fetcher runs every cycle to pick up new listings.")
    print("  Scorer processes unscored listings in configurable batches.")
    print("  GapAnalyzer computes skill gaps from scored listings.")


def _print_agent_result(result: AgentResult, elapsed_ms: int) -> None:
    icon = "✅" if result.status == "ok" else "❌"
    print(f"\n  {icon}  {result.agent}")
    print(f"      status          : {result.status}")
    print(f"      records touched : {result.records_touched}")
    print(f"      notes           : {result.notes}")
    print(f"      elapsed         : {elapsed_ms} ms")


def _print_summary(results: list[AgentResult], cycle_start: datetime) -> None:
    elapsed = (datetime.now(timezone.utc) - cycle_start).seconds
    _header("CYCLE SUMMARY")
    print(f"  {'Agent':<20} {'Status':<8} {'Records':>8}  Notes")
    print(_THIN)
    for r in results:
        print(f"  {r.agent:<20} {r.status:<8} {r.records_touched:>8}  {r.notes}")
    print(_THIN)
    total = sum(r.records_touched for r in results)
    failed = sum(1 for r in results if r.status == "failed")
    print(f"  {'TOTAL':<20} {'ok' if failed == 0 else 'FAILED':<8} {total:>8}  "
          f"{failed} agent(s) failed  |  cycle took ~{elapsed}s")
    print()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_cycle(config: Config) -> None:
    cycle_start = datetime.now(timezone.utc)

    _header(f"EdgeDash  —  cycle started {cycle_start.strftime('%Y-%m-%d %H:%M UTC')}")

    # (a) Init DB
    storage.init_db(config.db_path)

    # (b) Read state
    last_fetch = storage.last_fetch_time(config.db_path)
    unscored   = storage.count_unscored(config.db_path)
    _print_state(last_fetch, unscored)

    # (c) Print plan
    agents = _build_registry(config)
    _print_plan(agents)

    # (d+e) Run agents and log each one
    results: list[AgentResult] = []
    print("\n⚙️   RUNNING AGENTS")
    print(_THIN)

    for agent in agents:
        agent_start = datetime.now(timezone.utc)
        try:
            result = agent.run(config, config.db_path)
        except Exception as exc:
            result = AgentResult(
                agent=agent.name,
                status="failed",
                records_touched=0,
                notes=str(exc),
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
        results.append(result)

    # (f) Summary
    _print_summary(results, cycle_start)
