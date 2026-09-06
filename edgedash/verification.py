"""
Verification checks for EdgeDash cycle output.

Rules enforced here (steering rules 34-39):
  34  Verifier judges, never repairs.  Every function here returns a
      CheckResult — it never modifies the data it receives.
  35  Checks assert plausibility properties of distributions and shapes,
      not the accuracy of any single value.
  37  Every CheckResult carries the check name, the observed value that
      triggered a failure, and the threshold it was tested against.
  39  All thresholds come from config — none are hardcoded here.

No LLM, no network, no database reads, no clock calls (except where
`now` is explicitly passed in as a parameter).
Same inputs always produce the same output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import mean, stdev
from typing import Any


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """
    The verdict of one plausibility check.

    Fields
    ------
    name:      canonical check identifier, e.g. "score_spread"
    passed:    True if the check passed
    observed:  the computed value that was tested (str for readability)
    threshold: the limit from config that was applied
    message:   human-readable explanation — always specific (rule 37)
    """
    name:      str
    passed:    bool
    observed:  str
    threshold: str
    message:   str


@dataclass
class Verdict:
    """
    Aggregate result of run_all_checks().

    Fields
    ------
    passed:        True only when every individual check passed
    failed_checks: list of CheckResult where passed=False
    all_checks:    every CheckResult in run order
    summary:       one-line human-readable outcome
    """
    passed:        bool
    failed_checks: list[CheckResult]
    all_checks:    list[CheckResult]
    summary:       str


# ---------------------------------------------------------------------------
# Minimum sample size before spread/stdev checks fire
# ---------------------------------------------------------------------------

_MIN_SCORES_FOR_SPREAD = 5


# ---------------------------------------------------------------------------
# 1. check_score_spread
# ---------------------------------------------------------------------------

def check_score_spread(scores: list[int], config: Any) -> CheckResult:
    """
    Verify that scored listings show meaningful spread.

    Two sub-conditions (both must pass):
        spread = max(scores) - min(scores) >= config.min_score_spread
        stdev(scores)                       >= config.min_score_stdev

    Catches the score-inflation failure mode: a model that returns the
    same score for every listing regardless of content.

    Passes trivially (with an explanatory message) when fewer than
    _MIN_SCORES_FOR_SPREAD scores are present — not enough data to judge.
    """
    name = "score_spread"

    if len(scores) < _MIN_SCORES_FOR_SPREAD:
        return CheckResult(
            name=name,
            passed=True,
            observed=f"n={len(scores)}",
            threshold=f"min_n={_MIN_SCORES_FOR_SPREAD}",
            message=(
                f"Trivial pass: only {len(scores)} score(s) present; "
                f"need at least {_MIN_SCORES_FOR_SPREAD} to evaluate spread."
            ),
        )

    spread = max(scores) - min(scores)
    sd     = stdev(scores)   # sample stdev; requires >= 2 values (guaranteed above)

    min_spread = config.min_score_spread
    min_stdev  = config.min_score_stdev

    if spread < min_spread:
        return CheckResult(
            name=name,
            passed=False,
            observed=f"spread={spread}",
            threshold=f"min_score_spread={min_spread}",
            message=(
                f"FAIL score_spread: spread={spread} < "
                f"min_score_spread={min_spread}. "
                f"Scores: min={min(scores)}, max={max(scores)}, "
                f"mean={mean(scores):.1f}, stdev={sd:.2f}. "
                "Possible score inflation — all listings scored similarly."
            ),
        )

    if sd < min_stdev:
        return CheckResult(
            name=name,
            passed=False,
            observed=f"stdev={sd:.4f}",
            threshold=f"min_score_stdev={min_stdev}",
            message=(
                f"FAIL score_spread: stdev={sd:.4f} < "
                f"min_score_stdev={min_stdev}. "
                f"Scores: min={min(scores)}, max={max(scores)}, "
                f"spread={spread}, mean={mean(scores):.1f}. "
                "Scores are clustered too tightly around a central value."
            ),
        )

    return CheckResult(
        name=name,
        passed=True,
        observed=f"spread={spread}, stdev={sd:.4f}",
        threshold=f"min_score_spread={min_spread}, min_score_stdev={min_stdev}",
        message=(
            f"OK score_spread: spread={spread} >= {min_spread}, "
            f"stdev={sd:.4f} >= {min_stdev}."
        ),
    )


# ---------------------------------------------------------------------------
# 2. check_extraction_sanity
# ---------------------------------------------------------------------------

def check_extraction_sanity(
    facts_list: list[dict[str, Any]],
    config: Any,
) -> CheckResult:
    """
    Verify that extracted facts look structurally plausible.

    Two sub-conditions (first failure wins):
        A. empty_pct  = (listings with empty required_skills) / total
                        must be <= config.max_empty_extraction_pct (%)
        B. No single listing may have more than config.max_skills_per_listing
           skills in required_skills.

    Catches:
        A — a broken extractor silently returning no skills for most listings
        B — a model that returned a full sentence as one "skill" entry, or
            dumped an entire job description into the skills list
    """
    name = "extraction_sanity"

    if not facts_list:
        return CheckResult(
            name=name,
            passed=True,
            observed="n=0",
            threshold="",
            message="Trivial pass: no extraction facts to check.",
        )

    total = len(facts_list)
    max_empty_pct    = config.max_empty_extraction_pct
    max_skills       = config.max_skills_per_listing

    # Sub-condition A — empty required_skills rate
    empty_count = sum(
        1 for f in facts_list
        if not f.get("required_skills")
    )
    empty_pct = (empty_count / total) * 100.0

    if empty_pct > max_empty_pct:
        return CheckResult(
            name=name,
            passed=False,
            observed=f"empty_pct={empty_pct:.1f}%",
            threshold=f"max_empty_extraction_pct={max_empty_pct}%",
            message=(
                f"FAIL extraction_sanity: {empty_count}/{total} listings "
                f"({empty_pct:.1f}%) have empty required_skills — "
                f"threshold is {max_empty_pct}%. "
                "Possible broken extractor or parsing failure."
            ),
        )

    # Sub-condition B — oversized skill list
    for idx, facts in enumerate(facts_list):
        skills = facts.get("required_skills") or []
        n = len(skills)
        if n > max_skills:
            return CheckResult(
                name=name,
                passed=False,
                observed=f"skills_per_listing={n} (listing index {idx})",
                threshold=f"max_skills_per_listing={max_skills}",
                message=(
                    f"FAIL extraction_sanity: listing at index {idx} has "
                    f"{n} required_skills — threshold is {max_skills}. "
                    "Possible model returning a sentence or paragraph as a skill."
                ),
            )

    return CheckResult(
        name=name,
        passed=True,
        observed=f"empty_pct={empty_pct:.1f}%, max_skills_seen<={max_skills}",
        threshold=(
            f"max_empty_extraction_pct={max_empty_pct}%, "
            f"max_skills_per_listing={max_skills}"
        ),
        message=(
            f"OK extraction_sanity: {empty_count}/{total} empty "
            f"({empty_pct:.1f}%), all skill counts within limit."
        ),
    )


# ---------------------------------------------------------------------------
# 3. check_gap_sample_size
# ---------------------------------------------------------------------------

def check_gap_sample_size(
    gaps: list[dict[str, Any]],
    config: Any,
) -> CheckResult:
    """
    Verify the top-ranked gap has enough supporting listings.

    FAILS if gaps[0]["listings_blocked"] < config.min_gap_sample.
    Catches ranking a gap that is based on a single listing (a rumour).
    """
    name = "gap_sample_size"

    if not gaps:
        return CheckResult(
            name=name,
            passed=True,
            observed="n=0",
            threshold="",
            message="Trivial pass: no gaps to check.",
        )

    top_gap      = gaps[0]
    top_skill    = top_gap.get("skill", "unknown")
    top_n        = int(top_gap.get("listings_blocked", 0))
    min_sample   = config.min_gap_sample

    if top_n < min_sample:
        return CheckResult(
            name=name,
            passed=False,
            observed=f"listings_blocked={top_n} for '{top_skill}'",
            threshold=f"min_gap_sample={min_sample}",
            message=(
                f"FAIL gap_sample_size: top gap '{top_skill}' is based on "
                f"{top_n} listing(s) — minimum is {min_sample}. "
                "Top-ranked gap is low-confidence; may be a one-listing artefact."
            ),
        )

    return CheckResult(
        name=name,
        passed=True,
        observed=f"listings_blocked={top_n} for '{top_skill}'",
        threshold=f"min_gap_sample={min_sample}",
        message=(
            f"OK gap_sample_size: top gap '{top_skill}' has "
            f"{top_n} listing(s) >= {min_sample}."
        ),
    )


# ---------------------------------------------------------------------------
# 4. check_freshness
# ---------------------------------------------------------------------------

def check_freshness(
    latest_fetch_at: datetime | None,
    config: Any,
    now: datetime,
) -> CheckResult:
    """
    Verify that the newest data is not too old.

    FAILS if (now - latest_fetch_at) > config.max_data_age_days days.
    Also fails if latest_fetch_at is None (data has never been fetched).

    `now` is a parameter — never called inside this function — so the
    check is fully testable with a fixed clock.
    """
    name = "freshness"
    max_days = config.max_data_age_days

    if latest_fetch_at is None:
        return CheckResult(
            name=name,
            passed=False,
            observed="latest_fetch_at=None",
            threshold=f"max_data_age_days={max_days}",
            message=(
                "FAIL freshness: no listings have ever been fetched. "
                "Run a fetch cycle before verifying."
            ),
        )

    # Ensure both datetimes are UTC-aware before subtracting.
    lf  = _ensure_utc(latest_fetch_at)
    now = _ensure_utc(now)
    age_days = (now - lf).total_seconds() / 86400.0

    if age_days > max_days:
        return CheckResult(
            name=name,
            passed=False,
            observed=f"age_days={age_days:.2f}",
            threshold=f"max_data_age_days={max_days}",
            message=(
                f"FAIL freshness: newest listing is {age_days:.2f} days old — "
                f"threshold is {max_days} days. "
                "Fetch cycle may be broken or stalled."
            ),
        )

    return CheckResult(
        name=name,
        passed=True,
        observed=f"age_days={age_days:.2f}",
        threshold=f"max_data_age_days={max_days}",
        message=(
            f"OK freshness: data is {age_days:.2f} days old — "
            f"within {max_days}-day limit."
        ),
    )


# ---------------------------------------------------------------------------
# 5. run_all_checks
# ---------------------------------------------------------------------------

def run_all_checks(
    scores: list[int],
    facts_list: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    latest_fetch_at: datetime | None,
    config: Any,
    now: datetime,
) -> Verdict:
    """
    Run every verification check and return a Verdict.

    All checks run regardless of earlier failures so the full picture
    is always logged (rule 37).

    Args:
        scores:          fit_score values from the current scoring run
        facts_list:      extraction facts dicts (required_skills, …)
        gaps:            gap snapshot rows, ordered by opportunity_cost desc
        latest_fetch_at: most recent fetched_at timestamp, or None
        config:          Config instance carrying all thresholds
        now:             caller's clock — never datetime.now() inside here

    Returns:
        Verdict with passed=True only when every check passed.
    """
    results: list[CheckResult] = [
        check_score_spread(scores, config),
        check_extraction_sanity(facts_list, config),
        check_gap_sample_size(gaps, config),
        check_freshness(latest_fetch_at, config, now),
    ]

    failed = [r for r in results if not r.passed]
    passed = len(failed) == 0

    if passed:
        summary = f"PASS — all {len(results)} checks passed."
    else:
        names = ", ".join(r.name for r in failed)
        summary = (
            f"FAIL — {len(failed)}/{len(results)} check(s) failed: {names}."
        )

    return Verdict(
        passed=passed,
        failed_checks=failed,
        all_checks=results,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_utc(dt: datetime) -> datetime:
    """Attach UTC tzinfo if naive, return unchanged if already aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
