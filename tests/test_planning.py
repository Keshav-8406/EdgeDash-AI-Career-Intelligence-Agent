"""
Tests for edgedash.planning.build_plan().

All tests are pure — no database, no filesystem, no network, no clock.
SystemState and config are constructed directly in each test.

Four scenarios (as specified):
    A. everything stale    — all three agents run
    B. nothing to do       — all three agents skipped
    C. only unscored       — Scorer runs, Fetcher and GapAnalyzer skipped
    D. gaps stale, no unscored — GapAnalyzer runs, Scorer skipped

Additional coverage: stop-conditions, reason strings, render(), properties.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from edgedash.planning import Plan, Task, build_plan
from edgedash.state import SystemState


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _config(
    fetch_interval_hours: int = 6,
    fetch_max_pages: int = 10,
    fetch_max_listings: int = 200,
    score_batch_size: int = 25,
    score_max_seconds: int = 300,
    analyse_max_seconds: int = 120,
) -> SimpleNamespace:
    return SimpleNamespace(
        fetch_interval_hours=fetch_interval_hours,
        fetch_max_pages=fetch_max_pages,
        fetch_max_listings=fetch_max_listings,
        score_batch_size=score_batch_size,
        score_max_seconds=score_max_seconds,
        analyse_max_seconds=analyse_max_seconds,
    )


_NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
_OLD = datetime(2026, 9, 6,  0, 0, 0, tzinfo=timezone.utc)   # 12 h ago
_GAP = datetime(2026, 9, 6,  8, 0, 0, tzinfo=timezone.utc)   # 4 h ago


def _state(
    hours_since_fetch: float = 0.0,
    unscored_count: int = 0,
    gaps_stale: bool = False,
    gaps_computed_at: datetime | None = _GAP,
    last_fetch_at: datetime | None = _NOW,
    last_cycle_verdict: str | None = "ok",
    last_cycle_at: datetime | None = _NOW,
) -> SystemState:
    return SystemState(
        last_fetch_at=last_fetch_at,
        hours_since_fetch=hours_since_fetch,
        unscored_count=unscored_count,
        gaps_computed_at=gaps_computed_at,
        gaps_stale=gaps_stale,
        last_cycle_verdict=last_cycle_verdict,
        last_cycle_at=last_cycle_at,
    )


# ---------------------------------------------------------------------------
# Scenario A — everything stale: all three agents run
# ---------------------------------------------------------------------------

class TestEverythingStale:
    """
    hours_since_fetch=12 >= 6, unscored_count=41, gaps_stale=True
    → Fetcher, Scorer, GapAnalyzer all run.
    """

    def setup_method(self):
        self.plan = build_plan(
            _state(hours_since_fetch=12.0, unscored_count=41, gaps_stale=True),
            _config(),
        )

    def test_all_three_agents_run(self):
        assert [t.agent_name for t in self.plan.agents_to_run] == [
            "Fetcher", "Scorer", "GapAnalyzer"
        ]

    def test_no_agents_skipped(self):
        assert self.plan.agents_skipped == []

    def test_is_idle_false(self):
        assert self.plan.is_idle is False

    def test_fetcher_not_skipped(self):
        t = self._task("Fetcher")
        assert t.skipped is False

    def test_scorer_not_skipped(self):
        t = self._task("Scorer")
        assert t.skipped is False

    def test_gap_analyzer_not_skipped(self):
        t = self._task("GapAnalyzer")
        assert t.skipped is False

    def test_fetcher_reason_names_hours(self):
        t = self._task("Fetcher")
        assert "hours_since_fetch=12.0" in t.reason
        assert "fetch_interval_hours=6" in t.reason

    def test_scorer_reason_names_count(self):
        t = self._task("Scorer")
        assert "unscored_count=41" in t.reason

    def test_gap_analyzer_reason_mentions_stale(self):
        t = self._task("GapAnalyzer")
        assert "stale" in t.reason.lower() or "null" in t.reason.lower()

    def _task(self, name: str) -> Task:
        return next(t for t in self.plan.tasks if t.agent_name == name)


# ---------------------------------------------------------------------------
# Scenario B — nothing to do: all three skipped
# ---------------------------------------------------------------------------

class TestNothingToDo:
    """
    hours_since_fetch=1.5 < 6, unscored_count=0, gaps_stale=False
    → all three agents skipped.
    """

    def setup_method(self):
        self.plan = build_plan(
            _state(hours_since_fetch=1.5, unscored_count=0, gaps_stale=False),
            _config(),
        )

    def test_is_idle_true(self):
        assert self.plan.is_idle is True

    def test_no_agents_run(self):
        assert self.plan.agents_to_run == []

    def test_all_three_present_as_skipped(self):
        names = [t.agent_name for t in self.plan.tasks]
        assert names == ["Fetcher", "Scorer", "GapAnalyzer"]
        assert all(t.skipped for t in self.plan.tasks)

    def test_fetcher_skip_reason_names_hours(self):
        t = next(t for t in self.plan.tasks if t.agent_name == "Fetcher")
        assert "skipped" in t.reason
        assert "hours_since_fetch=1.5" in t.reason

    def test_scorer_skip_reason_names_count(self):
        t = next(t for t in self.plan.tasks if t.agent_name == "Scorer")
        assert "skipped" in t.reason
        assert "unscored_count=0" in t.reason

    def test_gap_analyzer_skip_reason_names_stale(self):
        t = next(t for t in self.plan.tasks if t.agent_name == "GapAnalyzer")
        assert "skipped" in t.reason
        assert "gaps_stale=false" in t.reason.lower()

    def test_render_contains_skip_for_all(self):
        rendered = self.plan.render()
        assert rendered.count("[SKIP]") == 3
        assert "[_RUN]" not in rendered

    def test_render_contains_nothing_to_do(self):
        rendered = self.plan.render()
        assert "Nothing to do" in rendered


# ---------------------------------------------------------------------------
# Scenario C — only unscored listings: Scorer runs, others skipped
# ---------------------------------------------------------------------------

class TestOnlyUnscored:
    """
    hours_since_fetch=2 < 6, unscored_count=15, gaps_stale=False
    → only Scorer runs.
    """

    def setup_method(self):
        self.plan = build_plan(
            _state(hours_since_fetch=2.0, unscored_count=15, gaps_stale=False),
            _config(),
        )

    def test_only_scorer_runs(self):
        assert [t.agent_name for t in self.plan.agents_to_run] == ["Scorer"]

    def test_fetcher_skipped(self):
        t = next(t for t in self.plan.tasks if t.agent_name == "Fetcher")
        assert t.skipped is True

    def test_gap_analyzer_skipped(self):
        t = next(t for t in self.plan.tasks if t.agent_name == "GapAnalyzer")
        assert t.skipped is True

    def test_scorer_not_skipped(self):
        t = next(t for t in self.plan.tasks if t.agent_name == "Scorer")
        assert t.skipped is False

    def test_scorer_reason_names_count(self):
        t = next(t for t in self.plan.tasks if t.agent_name == "Scorer")
        assert "unscored_count=15" in t.reason

    def test_is_idle_false(self):
        assert self.plan.is_idle is False

    def test_all_three_tasks_present(self):
        assert len(self.plan.tasks) == 3


# ---------------------------------------------------------------------------
# Scenario D — gaps stale, nothing unscored: GapAnalyzer runs, Scorer skipped
# ---------------------------------------------------------------------------

class TestGapsStaleNoUnscored:
    """
    hours_since_fetch=3 < 6, unscored_count=0, gaps_stale=True
    → only GapAnalyzer runs.
    """

    def setup_method(self):
        self.plan = build_plan(
            _state(hours_since_fetch=3.0, unscored_count=0, gaps_stale=True),
            _config(),
        )

    def test_only_gap_analyzer_runs(self):
        assert [t.agent_name for t in self.plan.agents_to_run] == ["GapAnalyzer"]

    def test_fetcher_skipped(self):
        t = next(t for t in self.plan.tasks if t.agent_name == "Fetcher")
        assert t.skipped is True

    def test_scorer_skipped(self):
        t = next(t for t in self.plan.tasks if t.agent_name == "Scorer")
        assert t.skipped is True

    def test_gap_analyzer_not_skipped(self):
        t = next(t for t in self.plan.tasks if t.agent_name == "GapAnalyzer")
        assert t.skipped is False

    def test_gap_analyzer_reason_mentions_stale(self):
        t = next(t for t in self.plan.tasks if t.agent_name == "GapAnalyzer")
        assert "stale" in t.reason.lower()

    def test_scorer_reason_names_zero_count(self):
        t = next(t for t in self.plan.tasks if t.agent_name == "Scorer")
        assert "unscored_count=0" in t.reason


# ---------------------------------------------------------------------------
# Stop-conditions
# ---------------------------------------------------------------------------

class TestStopConditions:
    """
    Stop-conditions must come from config, not be hardcoded or
    invented by the agent.
    """

    def _plan(self, **kw) -> Plan:
        return build_plan(
            _state(hours_since_fetch=12.0, unscored_count=10, gaps_stale=True),
            _config(**kw),
        )

    def test_fetcher_stop_conditions_from_config(self):
        plan = self._plan(fetch_max_pages=5, fetch_max_listings=100)
        t = next(t for t in plan.tasks if t.agent_name == "Fetcher")
        assert t.stop_conditions["max_pages"] == 5
        assert t.stop_conditions["max_listings"] == 100

    def test_scorer_stop_conditions_from_config(self):
        plan = self._plan(score_batch_size=50, score_max_seconds=600)
        t = next(t for t in plan.tasks if t.agent_name == "Scorer")
        assert t.stop_conditions["max_items"] == 50
        assert t.stop_conditions["max_seconds"] == 600

    def test_gap_analyzer_stop_conditions_from_config(self):
        plan = self._plan(analyse_max_seconds=60)
        t = next(t for t in plan.tasks if t.agent_name == "GapAnalyzer")
        assert t.stop_conditions["max_seconds"] == 60

    def test_skipped_tasks_still_carry_stop_conditions(self):
        # Stop-conditions must be present even for skipped agents so
        # the plan is fully auditable.
        plan = build_plan(
            _state(hours_since_fetch=1.0, unscored_count=0, gaps_stale=False),
            _config(),
        )
        for task in plan.tasks:
            assert task.stop_conditions, f"{task.agent_name} has no stop_conditions"


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------

class TestBoundary:
    def test_fetch_triggers_exactly_at_threshold(self):
        # hours_since_fetch == fetch_interval_hours → should run
        plan = build_plan(
            _state(hours_since_fetch=6.0, unscored_count=0, gaps_stale=False),
            _config(fetch_interval_hours=6),
        )
        t = next(t for t in plan.tasks if t.agent_name == "Fetcher")
        assert t.skipped is False

    def test_fetch_does_not_trigger_just_below_threshold(self):
        plan = build_plan(
            _state(hours_since_fetch=5.9, unscored_count=0, gaps_stale=False),
            _config(fetch_interval_hours=6),
        )
        t = next(t for t in plan.tasks if t.agent_name == "Fetcher")
        assert t.skipped is True

    def test_never_fetched_triggers_fetch(self):
        # hours_since_fetch=inf when last_fetch_at is None
        plan = build_plan(
            _state(
                hours_since_fetch=float("inf"),
                last_fetch_at=None,
                unscored_count=0,
                gaps_stale=False,
            ),
            _config(),
        )
        t = next(t for t in plan.tasks if t.agent_name == "Fetcher")
        assert t.skipped is False
        assert "never" in t.reason.lower() or "inf" in t.reason.lower()

    def test_gaps_null_triggers_analyse(self):
        # gaps_computed_at=None → gaps_stale=True → GapAnalyzer runs
        plan = build_plan(
            _state(
                hours_since_fetch=1.0,
                unscored_count=0,
                gaps_stale=True,
                gaps_computed_at=None,
            ),
            _config(),
        )
        t = next(t for t in plan.tasks if t.agent_name == "GapAnalyzer")
        assert t.skipped is False
        assert "null" in t.reason.lower() or "never" in t.reason.lower()


# ---------------------------------------------------------------------------
# Plan structure invariants
# ---------------------------------------------------------------------------

class TestPlanInvariants:
    def test_always_three_tasks(self):
        for state in [
            _state(hours_since_fetch=12.0, unscored_count=41, gaps_stale=True),
            _state(hours_since_fetch=1.0, unscored_count=0, gaps_stale=False),
        ]:
            plan = build_plan(state, _config())
            assert len(plan.tasks) == 3

    def test_task_order_is_fetcher_scorer_gap(self):
        plan = build_plan(
            _state(hours_since_fetch=12.0, unscored_count=5, gaps_stale=True),
            _config(),
        )
        names = [t.agent_name for t in plan.tasks]
        assert names == ["Fetcher", "Scorer", "GapAnalyzer"]

    def test_return_type_is_plan(self):
        plan = build_plan(_state(), _config())
        assert isinstance(plan, Plan)

    def test_each_task_has_non_empty_reason(self):
        plan = build_plan(
            _state(hours_since_fetch=12.0, unscored_count=5, gaps_stale=True),
            _config(),
        )
        for task in plan.tasks:
            assert task.reason.strip(), f"{task.agent_name} has empty reason"

    def test_deterministic_same_output_twice(self):
        state = _state(hours_since_fetch=8.0, unscored_count=3, gaps_stale=True)
        cfg   = _config()
        p1 = build_plan(state, cfg)
        p2 = build_plan(state, cfg)
        for t1, t2 in zip(p1.tasks, p2.tasks):
            assert t1.agent_name == t2.agent_name
            assert t1.skipped == t2.skipped
            assert t1.reason == t2.reason


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

class TestRender:
    def test_render_contains_all_agent_names(self):
        plan = build_plan(
            _state(hours_since_fetch=12.0, unscored_count=5, gaps_stale=True),
            _config(),
        )
        rendered = plan.render()
        for name in ("Fetcher", "Scorer", "GapAnalyzer"):
            assert name in rendered

    def test_render_shows_run_for_active_agents(self):
        plan = build_plan(
            _state(hours_since_fetch=12.0, unscored_count=5, gaps_stale=True),
            _config(),
        )
        rendered = plan.render()
        assert "[ RUN]" in rendered

    def test_render_shows_skip_for_idle_agents(self):
        plan = build_plan(
            _state(hours_since_fetch=1.0, unscored_count=0, gaps_stale=False),
            _config(),
        )
        rendered = plan.render()
        assert "[SKIP]" in rendered

    def test_render_contains_stop_conditions(self):
        plan = build_plan(
            _state(hours_since_fetch=12.0, unscored_count=5, gaps_stale=True),
            _config(fetch_max_pages=7),
        )
        rendered = plan.render()
        assert "max_pages=7" in rendered

    def test_task_render_includes_reason(self):
        task = Task(
            agent_name="Scorer",
            goal="score listings",
            stop_conditions={"max_items": 25},
            reason="unscored_count=15",
            skipped=False,
        )
        line = task.render()
        assert "unscored_count=15" in line
        assert "max_items=25" in line
        assert "[ RUN]" in line

    def test_skipped_task_render_says_skip(self):
        task = Task(
            agent_name="Fetcher",
            goal="fetch listings",
            stop_conditions={"max_pages": 10},
            reason="skipped: hours_since_fetch=2.0 < fetch_interval_hours=6",
            skipped=True,
        )
        line = task.render()
        assert "[SKIP]" in line
