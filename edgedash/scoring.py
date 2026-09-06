"""
Deterministic job-fit scoring for EdgeDash.

The LLM is never used to calculate scores.

Scoring weights:
    Skill Match  : 45%
    Seniority    : 25%
    Location     : 15%
    Recency      : 15%

Final score is calculated entirely by Python.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

SKILL_WEIGHT     = 0.45
SENIORITY_WEIGHT = 0.25
LOCATION_WEIGHT  = 0.15
RECENCY_WEIGHT   = 0.15


# ---------------------------------------------------------------------------
# Seniority ordering
# ---------------------------------------------------------------------------

SENIORITY_ORDER = {
    "junior": 0,
    "mid": 1,
    "senior": 2,
    "lead": 3,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(value: Any) -> str:
    """Normalize a string for case-insensitive comparisons."""
    return str(value or "").strip().lower()


def _normalize_skills(values: Any) -> set[str]:
    """Return normalized non-empty skills."""
    if not isinstance(values, list):
        return set()

    return {
        _normalize(skill)
        for skill in values
        if _normalize(skill)
    }


# ---------------------------------------------------------------------------
# Skill Match
# ---------------------------------------------------------------------------

def _skill_match(
    facts: dict[str, Any],
    config: Any,
) -> tuple[float, list[str]]:
    """
    Calculate skill match.

    Required skills:
        Full weight.

    Nice-to-have skills:
        One-third of required-skill contribution.

    Returns:
        (score between 0 and 1, missing required skills)
    """
    required = _normalize_skills(
        facts.get("required_skills", [])
    )

    nice_to_have = _normalize_skills(
        facts.get("nice_to_have", [])
    )

    candidate_skills = _normalize_skills(
        getattr(config, "my_skills", [])
    )

    # No required or nice-to-have skills stated.
    if not required and not nice_to_have:
        return 1.0, []

    required_matches = required & candidate_skills
    nice_matches = nice_to_have & candidate_skills

    missing_required = sorted(
        required - candidate_skills
    )

    # Required skills receive full importance.
    required_component = (
        len(required_matches) / len(required)
        if required
        else 0.0
    )

    # Nice-to-have contributes at one-third weight.
    nice_component = (
        (len(nice_matches) / len(nice_to_have)) / 3.0
        if nice_to_have
        else 0.0
    )

    if required:
        # Required skills dominate the calculation.
        total = required_component * 0.75 + nice_component * 0.25
    else:
        total = nice_component

    return min(1.0, max(0.0, total)), missing_required


# ---------------------------------------------------------------------------
# Seniority Fit
# ---------------------------------------------------------------------------

def _seniority_fit(
    facts: dict[str, Any],
    config: Any,
) -> float:
    """
    Calculate seniority compatibility.

    Exact match: 1.0
    One band away: 0.6
    Two bands away: 0.25
    Three or more: 0.0

    Unknown seniority receives 0.5.
    """
    listing_level = _normalize(
        facts.get("seniority")
    )

    target_level = _normalize(
        getattr(config, "target_seniority", "mid")
    )

    if listing_level not in SENIORITY_ORDER:
        return 0.5

    if target_level not in SENIORITY_ORDER:
        target_level = "mid"

    distance = abs(
        SENIORITY_ORDER[listing_level]
        - SENIORITY_ORDER[target_level]
    )

    if distance == 0:
        return 1.0

    if distance == 1:
        return 0.6

    if distance == 2:
        return 0.25

    return 0.0


# ---------------------------------------------------------------------------
# Location Fit
# ---------------------------------------------------------------------------

def _location_fit(
    listing: dict[str, Any],
    facts: dict[str, Any],
    config: Any,
) -> float:
    """
    Calculate location compatibility.

    Remote explicitly allowed: 1.0
    Listing city matches target city: 1.0
    Unknown location: 0.5
    Clearly elsewhere and not remote: 0.1
    """
    remote_ok = facts.get("remote_ok")

    if remote_ok is True:
        return 1.0

    location = _normalize(
        listing.get("location")
    )

    target_city = _normalize(
        getattr(config, "target_city", "")
    )

    if not location:
        return 0.5

    if target_city and target_city in location:
        return 1.0

    return 0.1


# ---------------------------------------------------------------------------
# Recency
# ---------------------------------------------------------------------------

def _parse_posted_at(value: Any) -> datetime | None:
    """Parse a listing posted_at timestamp."""
    if not value:
        return None

    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def _recency_fit(
    listing: dict[str, Any],
) -> float:
    """
    Calculate recency.

    Today: 1.0
    30 days old: 0.0
    Linear decay between them.

    Missing/invalid posted_at: 0.5
    """
    posted_at = _parse_posted_at(
        listing.get("posted_at")
    )

    if posted_at is None:
        return 0.5

    now = datetime.now(timezone.utc)

    age_days = max(
        0.0,
        (now - posted_at).total_seconds() / 86400,
    )

    if age_days >= 30:
        return 0.0

    return max(
        0.0,
        1.0 - (age_days / 30.0),
    )


# ---------------------------------------------------------------------------
# Reason
# ---------------------------------------------------------------------------

def build_reason(
    components: dict[str, float],
    missing_skills: list[str],
) -> str:
    """
    Build a compact deterministic explanation.

    No LLM-generated free text is used.
    """
    skill = components["skill_match"] * 100
    seniority = components["seniority_fit"] * 100
    location = components["location_fit"] * 100
    recency = components["recency"] * 100

    reason = (
        f"Skills {skill:.0f}%, "
        f"seniority {seniority:.0f}%, "
        f"location {location:.0f}%, "
        f"recency {recency:.0f}%."
    )

    if missing_skills:
        reason += (
            " Missing required skills: "
            + ", ".join(missing_skills)
            + "."
        )
    else:
        reason += " No required skill gaps."

    return reason


# ---------------------------------------------------------------------------
# Public scorer
# ---------------------------------------------------------------------------

def score_listing(
    listing: dict[str, Any],
    facts: dict[str, Any],
    config: Any,
) -> dict[str, Any]:
    """
    Calculate a deterministic 0–100 job-fit score.

    The model is not involved in this calculation.

    Returns:
        {
            "score": int,
            "reason": str,
            "components": {
                "skill_match": float,
                "seniority_fit": float,
                "location_fit": float,
                "recency": float
            }
        }
    """
    skill_score, missing_skills = _skill_match(
        facts,
        config,
    )

    seniority_score = _seniority_fit(
        facts,
        config,
    )

    location_score = _location_fit(
        listing,
        facts,
        config,
    )

    recency_score = _recency_fit(
        listing,
    )

    weighted_score = (
        skill_score * SKILL_WEIGHT
        + seniority_score * SENIORITY_WEIGHT
        + location_score * LOCATION_WEIGHT
        + recency_score * RECENCY_WEIGHT
    )

    final_score = int(
        round(
            max(
                0.0,
                min(100.0, weighted_score * 100),
            )
        )
    )

    components = {
        "skill_match": round(skill_score, 4),
        "seniority_fit": round(seniority_score, 4),
        "location_fit": round(location_score, 4),
        "recency": round(recency_score, 4),
    }

    reason = build_reason(
        components,
        missing_skills,
    )

    return {
        "score": final_score,
        "reason": reason,
        "components": components,
    }