"""
Tests for edgedash.verification.

All tests are pure — no database, no filesystem, no network, no LLM.
Config thresholds are passed via SimpleNamespace so each test owns
its exact limits.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from edgedash.verification import (
    CheckResult,
    Verdict,
    check_extraction_sanity,
    check_freshness,
    check_gap_sample_size,
    check_score_spread,
    run_all_checks,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)


def _cfg(
    min_score_spread: int = 10,
    min_score_stdev: float = 5.0,
    max_empty_extraction_pct: float = 20.0,
    max_skills_per_listing: int = 20,
    min_gap_sample: int = 3,
    max_data_age_days: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        min_score_spread=min_score_spread,
        min_score_stdev=min_score_stdev,
        max_empty_extraction_pct=max_empty_extraction_pct,
        max_skills_per_listing=max_skills_per_listing,
        min_gap_sample=min_gap_sample,
        max_data_age_days=max_data_age_days,
    )


def _gap(skill: str, n: int) -> dict:
    return {"skill": skill, "listings_blocked": n, "opportunity_cost": n * 0.5}


def _facts(skills: list[str]) -> dict:
    return {"required_skills": skills}


# ===========================================================================
# check_score_spread
# ===========================================================================

class TestCheckScoreSpread:

    # --- fewer-than-5-scores trivial pass ---

    def test_trivial_pass_zero_scores(self):
        r = check_score_spread([], _cfg())
        assert r.passed is True
        assert r.name == "score_spread"
        assert "Trivial pass" in r.message

    def test_trivial_pass_one_score(self):
        r = check_score_spread([50], _cfg())
        assert r.passed is True
        assert "Trivial pass" in r.message

    def test_trivial_pass_four_scores(self):
        r = check_score_spread([20, 40, 60, 80], _cfg())
        assert r.passed is True
        assert "Trivial pass" in r.message

    def test_trivial_pass_says_how_many(self):
        r = check_score_spread([10, 20, 30], _cfg())
        assert "3" in r.message

    # --- passing case ---

    def test_passes_with_sufficient_spread_and_stdev(self):
        # spread=60, stdev large — well above both thresholds
        scores = [20, 30, 50, 60, 80]
        r = check_score_spread(scores, _cfg(min_score_spread=10, min_score_stdev=5.0))
        assert r.passed is True
        assert r.name == "score_spread"
        assert "OK" in r.message

    def test_pass_result_has_observed_and_threshold(self):
        scores = [10, 30, 50, 70, 90]
        r = check_score_spread(scores, _cfg())
        assert "spread=" in r.observed
        assert "stdev=" in r.observed
        assert "min_score_spread=" in r.threshold

    # --- failing: spread too low ---

    def test_fails_when_spread_below_threshold(self):
        # All scores within a 5-point band; spread=5 < threshold=10
        scores = [50, 51, 52, 53, 55]
        r = check_score_spread(scores, _cfg(min_score_spread=10))
        assert r.passed is False
        assert "spread" in r.observed
        assert "min_score_spread" in r.threshold

    def test_fail_spread_message_is_specific(self):
        scores = [50, 51, 52, 53, 55]
        r = check_score_spread(scores, _cfg(min_score_spread=10))
        assert "spread=5" in r.message
        assert "min_score_spread=10" in r.message

    def test_fails_spread_exactly_below(self):
        # spread = threshold - 1 → fail
        scores = [40, 41, 42, 43, 49]   # spread=9, threshold=10
        r = check_score_spread(scores, _cfg(min_score_spread=10))
        assert r.passed is False

    def test_passes_spread_exactly_at_threshold(self):
        # spread == threshold → pass
        scores = [40, 45, 48, 49, 50]   # spread=10
        r = check_score_spread(scores, _cfg(min_score_spread=10, min_score_stdev=1.0))
        assert r.passed is True

    # --- failing: stdev too low ---

    def test_fails_when_stdev_below_threshold(self):
        # spread=20 clears spread check, but stdev is tiny
        # scores: 39, 40, 40, 40, 59 → spread=20, stdev≈8.5 — need a tighter case
        # Use scores very close together but spread just passes
        scores = [45, 50, 50, 50, 55]   # spread=10, stdev≈3.5
        r = check_score_spread(scores, _cfg(min_score_spread=10, min_score_stdev=5.0))
        assert r.passed is False
        assert "stdev=" in r.observed
        assert "min_score_stdev" in r.threshold

    def test_fail_stdev_message_is_specific(self):
        scores = [45, 50, 50, 50, 55]
        r = check_score_spread(scores, _cfg(min_score_spread=10, min_score_stdev=5.0))
        assert "stdev=" in r.message
        assert "min_score_stdev=5.0" in r.message

    # --- return type ---

    def test_returns_check_result(self):
        r = check_score_spread([10, 30, 50, 70, 90], _cfg())
        assert isinstance(r, CheckResult)

    def test_name_is_score_spread(self):
        r = check_score_spread([], _cfg())
        assert r.name == "score_spread"


# ===========================================================================
# check_extraction_sanity
# ===========================================================================

class TestCheckExtractionSanity:

    # --- trivial pass: empty list ---

    def test_trivial_pass_empty_facts_list(self):
        r = check_extraction_sanity([], _cfg())
        assert r.passed is True
        assert "Trivial pass" in r.message

    # --- passing case ---

    def test_passes_all_have_skills_within_limit(self):
        facts = [
            _facts(["python", "sql"]),
            _facts(["docker", "kubernetes"]),
            _facts(["spark", "scala"]),
        ]
        r = check_extraction_sanity(facts, _cfg(max_empty_extraction_pct=20.0,
                                                 max_skills_per_listing=20))
        assert r.passed is True
        assert "OK" in r.message

    def test_pass_result_has_observed_and_threshold(self):
        facts = [_facts(["python"])]
        r = check_extraction_sanity(facts, _cfg())
        assert "empty_pct=" in r.observed
        assert "max_empty_extraction_pct" in r.threshold

    # --- failing: empty skills rate ---

    def test_fails_when_too_many_empty(self):
        # 3 of 4 empty = 75% > 20% threshold
        facts = [
            _facts([]),
            _facts([]),
            _facts([]),
            _facts(["python"]),
        ]
        r = check_extraction_sanity(facts, _cfg(max_empty_extraction_pct=20.0))
        assert r.passed is False
        assert "empty_pct" in r.observed

    def test_fail_empty_message_is_specific(self):
        facts = [_facts([]), _facts([]), _facts([]), _facts(["python"])]
        r = check_extraction_sanity(facts, _cfg(max_empty_extraction_pct=20.0))
        assert "75.0%" in r.message
        assert "max_empty_extraction_pct=20.0" in r.threshold

    def test_fails_exactly_above_threshold(self):
        # 21% empty > 20% threshold
        facts = [_facts([])] * 21 + [_facts(["python"])] * 79
        r = check_extraction_sanity(facts, _cfg(max_empty_extraction_pct=20.0))
        assert r.passed is False

    def test_passes_exactly_at_threshold(self):
        # 20% empty == 20% threshold → pass (not strictly greater)
        facts = [_facts([])] * 20 + [_facts(["python"])] * 80
        r = check_extraction_sanity(facts, _cfg(max_empty_extraction_pct=20.0))
        assert r.passed is True

    # --- failing: oversized skill list ---

    def test_fails_when_skills_exceed_max(self):
        many = [f"skill_{i}" for i in range(25)]  # 25 > 20
        facts = [_facts(["python"]), _facts(many)]
        r = check_extraction_sanity(facts, _cfg(max_skills_per_listing=20))
        assert r.passed is False
        assert "skills_per_listing=25" in r.observed

    def test_fail_oversize_message_names_index(self):
        many = [f"skill_{i}" for i in range(25)]
        facts = [_facts(["python"]), _facts(many)]
        r = check_extraction_sanity(facts, _cfg(max_skills_per_listing=20))
        assert "index 1" in r.message
        assert "max_skills_per_listing=20" in r.threshold

    def test_fails_first_offending_listing(self):
        # Two oversized listings — index 0 should be reported
        many = [f"s{i}" for i in range(21)]
        facts = [_facts(many), _facts(many)]
        r = check_extraction_sanity(facts, _cfg(max_skills_per_listing=20))
        assert "index 0" in r.message

    def test_passes_exactly_at_max_skills(self):
        exactly = [f"skill_{i}" for i in range(20)]  # 20 == 20 → pass
        facts = [_facts(exactly)]
        r = check_extraction_sanity(facts, _cfg(max_skills_per_listing=20))
        assert r.passed is True

    # --- return type ---

    def test_returns_check_result(self):
        r = check_extraction_sanity([], _cfg())
        assert isinstance(r, CheckResult)

    def test_name_is_extraction_sanity(self):
        r = check_extraction_sanity([], _cfg())
        assert r.name == "extraction_sanity"


# ===========================================================================
# check_gap_sample_size
# ===========================================================================

class TestCheckGapSampleSize:

    def test_trivial_pass_no_gaps(self):
        r = check_gap_sample_size([], _cfg())
        assert r.passed is True
        assert "Trivial pass" in r.message

    def test_passes_when_top_gap_meets_minimum(self):
        gaps = [_gap("kubernetes", 5)]
        r = check_gap_sample_size(gaps, _cfg(min_gap_sample=3))
        assert r.passed is True
        assert "kubernetes" in r.message

    def test_fails_when_top_gap_below_minimum(self):
        gaps = [_gap("kubernetes", 2)]
        r = check_gap_sample_size(gaps, _cfg(min_gap_sample=3))
        assert r.passed is False
        assert "listings_blocked=2" in r.observed

    def test_fail_message_names_skill_and_count(self):
        gaps = [_gap("terraform", 1)]
        r = check_gap_sample_size(gaps, _cfg(min_gap_sample=3))
        assert "terraform" in r.message
        assert "1" in r.message
        assert "min_gap_sample=3" in r.threshold

    def test_passes_exactly_at_minimum(self):
        gaps = [_gap("docker", 3)]
        r = check_gap_sample_size(gaps, _cfg(min_gap_sample=3))
        assert r.passed is True

    def test_only_top_gap_checked(self):
        # Second gap is below minimum — should not affect result
        gaps = [_gap("kubernetes", 5), _gap("terraform", 1)]
        r = check_gap_sample_size(gaps, _cfg(min_gap_sample=3))
        assert r.passed is True

    def test_returns_check_result(self):
        r = check_gap_sample_size([], _cfg())
        assert isinstance(r, CheckResult)

    def test_name_is_gap_sample_size(self):
        r = check_gap_sample_size([], _cfg())
        assert r.name == "gap_sample_size"


# ===========================================================================
# check_freshness
# ===========================================================================

class TestCheckFreshness:

    def test_fails_when_latest_fetch_is_none(self):
        r = check_freshness(None, _cfg(max_data_age_days=3), _NOW)
        assert r.passed is False
        assert "None" in r.observed

    def test_fail_none_message_is_specific(self):
        r = check_freshness(None, _cfg(max_data_age_days=3), _NOW)
        assert "have ever been fetched" in r.message

    def test_passes_when_data_is_fresh(self):
        fetch_at = _NOW - timedelta(hours=12)   # 0.5 days < 3 days
        r = check_freshness(fetch_at, _cfg(max_data_age_days=3), _NOW)
        assert r.passed is True
        assert "OK" in r.message

    def test_fails_when_data_too_old(self):
        fetch_at = _NOW - timedelta(days=4)     # 4 days > 3 days
        r = check_freshness(fetch_at, _cfg(max_data_age_days=3), _NOW)
        assert r.passed is False
        assert "age_days=" in r.observed

    def test_fail_message_names_age_and_threshold(self):
        fetch_at = _NOW - timedelta(days=5)
        r = check_freshness(fetch_at, _cfg(max_data_age_days=3), _NOW)
        assert "5.00" in r.message or "5." in r.message
        assert "3" in r.message

    def test_passes_exactly_at_threshold(self):
        # exactly 3 days old — not strictly greater, should pass
        fetch_at = _NOW - timedelta(days=3)
        r = check_freshness(fetch_at, _cfg(max_data_age_days=3), _NOW)
        assert r.passed is True

    def test_fails_just_past_threshold(self):
        # 3 days + 1 second
        fetch_at = _NOW - timedelta(days=3, seconds=1)
        r = check_freshness(fetch_at, _cfg(max_data_age_days=3), _NOW)
        assert r.passed is False

    def test_now_parameter_controls_result(self):
        fetch_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
        # With now=Sep 3 (2 days later) → fresh
        now_fresh = datetime(2026, 9, 3, tzinfo=timezone.utc)
        assert check_freshness(fetch_at, _cfg(max_data_age_days=3), now_fresh).passed is True
        # With now=Sep 10 (9 days later) → stale
        now_stale = datetime(2026, 9, 10, tzinfo=timezone.utc)
        assert check_freshness(fetch_at, _cfg(max_data_age_days=3), now_stale).passed is False

    def test_naive_datetime_handled(self):
        # Naive datetimes should be treated as UTC without raising
        fetch_at = datetime(2026, 9, 6, 10, 0, 0)   # no tzinfo
        now      = datetime(2026, 9, 6, 12, 0, 0)   # no tzinfo
        r = check_freshness(fetch_at, _cfg(max_data_age_days=3), now)
        assert r.passed is True

    def test_returns_check_result(self):
        r = check_freshness(None, _cfg(), _NOW)
        assert isinstance(r, CheckResult)

    def test_name_is_freshness(self):
        r = check_freshness(None, _cfg(), _NOW)
        assert r.name == "freshness"


# ===========================================================================
# run_all_checks
# ===========================================================================

class TestRunAllChecks:

    def _all_pass_inputs(self):
        scores      = [20, 35, 55, 70, 90]
        facts_list  = [_facts(["python", "sql"]), _facts(["docker"])]
        gaps        = [_gap("kubernetes", 5)]
        fetch_at    = _NOW - timedelta(hours=6)
        return scores, facts_list, gaps, fetch_at

    def test_passes_when_all_checks_pass(self):
        scores, facts_list, gaps, fetch_at = self._all_pass_inputs()
        v = run_all_checks(scores, facts_list, gaps, fetch_at, _cfg(), _NOW)
        assert v.passed is True

    def test_failed_checks_empty_when_all_pass(self):
        scores, facts_list, gaps, fetch_at = self._all_pass_inputs()
        v = run_all_checks(scores, facts_list, gaps, fetch_at, _cfg(), _NOW)
        assert v.failed_checks == []

    def test_all_checks_contains_four_results(self):
        scores, facts_list, gaps, fetch_at = self._all_pass_inputs()
        v = run_all_checks(scores, facts_list, gaps, fetch_at, _cfg(), _NOW)
        assert len(v.all_checks) == 4

    def test_summary_says_pass_when_all_pass(self):
        scores, facts_list, gaps, fetch_at = self._all_pass_inputs()
        v = run_all_checks(scores, facts_list, gaps, fetch_at, _cfg(), _NOW)
        assert "PASS" in v.summary

    def test_fails_when_one_check_fails(self):
        # Make freshness fail — data is 5 days old
        scores, facts_list, gaps, _ = self._all_pass_inputs()
        stale_fetch = _NOW - timedelta(days=5)
        v = run_all_checks(scores, facts_list, gaps, stale_fetch, _cfg(), _NOW)
        assert v.passed is False

    def test_failed_checks_names_the_failing_check(self):
        scores, facts_list, gaps, _ = self._all_pass_inputs()
        stale_fetch = _NOW - timedelta(days=5)
        v = run_all_checks(scores, facts_list, gaps, stale_fetch, _cfg(), _NOW)
        assert any(r.name == "freshness" for r in v.failed_checks)

    def test_all_checks_run_even_when_one_fails(self):
        # Both score_spread and freshness will fail
        bad_scores  = [50, 50, 50, 50, 50]   # spread=0 → fail
        _, facts_list, gaps, _ = self._all_pass_inputs()
        stale_fetch = _NOW - timedelta(days=5)
        v = run_all_checks(bad_scores, facts_list, gaps, stale_fetch, _cfg(), _NOW)
        assert len(v.all_checks) == 4   # all four still run
        assert len(v.failed_checks) >= 2

    def test_summary_names_failing_checks(self):
        bad_scores = [50, 50, 50, 50, 50]
        _, facts_list, gaps, fetch_at = self._all_pass_inputs()
        v = run_all_checks(bad_scores, facts_list, gaps, fetch_at, _cfg(), _NOW)
        assert "score_spread" in v.summary

    def test_returns_verdict(self):
        scores, facts_list, gaps, fetch_at = self._all_pass_inputs()
        v = run_all_checks(scores, facts_list, gaps, fetch_at, _cfg(), _NOW)
        assert isinstance(v, Verdict)

    def test_check_order_is_spread_extraction_gap_freshness(self):
        scores, facts_list, gaps, fetch_at = self._all_pass_inputs()
        v = run_all_checks(scores, facts_list, gaps, fetch_at, _cfg(), _NOW)
        names = [r.name for r in v.all_checks]
        assert names == [
            "score_spread",
            "extraction_sanity",
            "gap_sample_size",
            "freshness",
        ]
