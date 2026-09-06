"""
Deterministic cycle planner.

    plan = build_plan(state, config)
    print(plan.render())

Pure function of (SystemState, Config).  No I/O at all — no database
reads, no clock calls, no file access.  Same inputs always produce the
same Plan.

Decision rules (all thresholds come from config — sub-agents never set
their own limits, rule 29):

    FETCH   if state.hours_since_fetch >= config.fetch_interval_hours
    SCORE   if state.unscored_count > 0
    ANALYSE if state.gaps_stale
              (also True when gaps_computed_at is None — never analysed)

Skipped agents appear in the Plan with skipped=True and a reason that
names the state value that caused the decision (rule 31).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edgedash.config import Config
from edgedash.state import SystemState


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """
    One agent's instruction inside a Plan.

    Fields
    ------
    agent_name:       canonical name matching the agent's .name attribute
    goal:             one-sentence statement of what the agent should do
    stop_conditions:  dict of limit names → values set by the Orchestrator
    reason:           human-readable string naming the state value that
                      caused this decision, e.g. "unscored_count=41" or
                      "skipped: hours_since_fetch=2.1 < 6"
    skipped:          True when the agent is intentionally not run this cycle
    """
    agent_name:      str
    goal:            str
    stop_conditions: dict[str, Any]
    reason:          str
    skipped:         bool = False

    def render(self) -> str:
        """One-line representation suitable for console output."""
        tag = "SKIP" if self.skipped else " RUN"
        stops = "  ".join(f"{k}={v}" for k, v in self.stop_conditions.items())
        return (
            f"  [{tag}]  {self.agent_name:<14}  "
            f"goal: {self.goal:<42}  "
            f"stop: {stops:<32}  "
            f"reason: {self.reason}"
        )


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    """
    Ordered list of Tasks for one cycle.

    All three agents are always present — skipped agents carry
    skipped=True so the plan is fully auditable (rule 31).
    """
    tasks: list[Task] = field(default_factory=list)

    # ----------------------------------------------------------------
    # Convenience accessors
    # ----------------------------------------------------------------

    @property
    def agents_to_run(self) -> list[Task]:
        """Tasks that will actually execute this cycle."""
        return [t for t in self.tasks if not t.skipped]

    @property
    def agents_skipped(self) -> list[Task]:
        """Tasks intentionally skipped this cycle."""
        return [t for t in self.tasks if t.skipped]

    @property
    def is_idle(self) -> bool:
        """True when every agent is skipped — nothing to do this cycle."""
        return all(t.skipped for t in self.tasks)

    # ----------------------------------------------------------------
    # Render
    # ----------------------------------------------------------------

    def render(self) -> str:
        """
        Compact human-readable plan, one line per agent.

        Format per line:
            [_RUN] / [SKIP]  <agent>  goal: …  stop: …  reason: …

        Followed by a one-line summary.
        """
        lines: list[str] = ["CYCLE PLAN"]
        lines.append("  " + "─" * 84)
        for task in self.tasks:
            lines.append(task.render())
        lines.append("  " + "─" * 84)
        run_names  = [t.agent_name for t in self.agents_to_run]
        skip_names = [t.agent_name for t in self.agents_skipped]
        if run_names:
            lines.append(
                f"  Running : {', '.join(run_names)}"
            )
        if skip_names:
            lines.append(
                f"  Skipping: {', '.join(skip_names)}"
            )
        if self.is_idle:
            lines.append("  Nothing to do this cycle.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Builder  (pure function)
# ---------------------------------------------------------------------------

def build_plan(state: SystemState, config: Config) -> Plan:
    """
    Derive a Plan from the current SystemState and Config.

    Pure function — no I/O, no side effects, no randomness.
    Every agent appears in the returned Plan exactly once.

    Args:
        state:  output of read_state()
        config: project configuration (provides all thresholds)

    Returns:
        Plan with tasks in execution order: Fetcher, Scorer, GapAnalyzer.
    """
    tasks: list[Task] = []

    # ----------------------------------------------------------------
    # FETCH
    # ----------------------------------------------------------------
    if state.hours_since_fetch >= config.fetch_interval_hours:
        if state.hours_since_fetch == float("inf"):
            fetch_reason = "hours_since_fetch=never (first run)"
        else:
            fetch_reason = (
                f"hours_since_fetch={state.hours_since_fetch:.1f} "
                f">= fetch_interval_hours={config.fetch_interval_hours}"
            )
        tasks.append(Task(
            agent_name="Fetcher",
            goal="Fetch new job listings from all configured sources",
            stop_conditions={
                "max_pages":    config.fetch_max_pages,
                "max_listings": config.fetch_max_listings,
            },
            reason=fetch_reason,
            skipped=False,
        ))
    else:
        tasks.append(Task(
            agent_name="Fetcher",
            goal="Fetch new job listings from all configured sources",
            stop_conditions={
                "max_pages":    config.fetch_max_pages,
                "max_listings": config.fetch_max_listings,
            },
            reason=(
                f"skipped: hours_since_fetch={state.hours_since_fetch:.1f} "
                f"< fetch_interval_hours={config.fetch_interval_hours}"
            ),
            skipped=True,
        ))

    # ----------------------------------------------------------------
    # SCORE
    # ----------------------------------------------------------------
    if state.unscored_count > 0:
        tasks.append(Task(
            agent_name="Scorer",
            goal=f"Score unscored listings (batch cap {config.score_batch_size})",
            stop_conditions={
                "max_items":   config.score_batch_size,
                "max_seconds": config.score_max_seconds,
            },
            reason=f"unscored_count={state.unscored_count}",
            skipped=False,
        ))
    else:
        tasks.append(Task(
            agent_name="Scorer",
            goal=f"Score unscored listings (batch cap {config.score_batch_size})",
            stop_conditions={
                "max_items":   config.score_batch_size,
                "max_seconds": config.score_max_seconds,
            },
            reason="skipped: unscored_count=0",
            skipped=True,
        ))

    # ----------------------------------------------------------------
    # ANALYSE
    # ----------------------------------------------------------------
    if state.gaps_stale:
        if state.gaps_computed_at is None:
            analyse_reason = "gaps_computed_at=null (never analysed)"
        else:
            analyse_reason = (
                "gaps_stale=true (score newer than latest gap snapshot)"
            )
        tasks.append(Task(
            agent_name="GapAnalyzer",
            goal="Compute skill-gap snapshot from all scored listings",
            stop_conditions={
                "max_seconds": config.analyse_max_seconds,
            },
            reason=analyse_reason,
            skipped=False,
        ))
    else:
        tasks.append(Task(
            agent_name="GapAnalyzer",
            goal="Compute skill-gap snapshot from all scored listings",
            stop_conditions={
                "max_seconds": config.analyse_max_seconds,
            },
            reason="skipped: gaps_stale=false",
            skipped=True,
        ))

    return Plan(tasks=tasks)
