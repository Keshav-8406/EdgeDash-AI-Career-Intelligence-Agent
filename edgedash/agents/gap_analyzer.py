"""
GapAnalyzer — deterministic skill-gap analysis agent.

No LLM call is made anywhere in this file.
All arithmetic is plain Python.  Same inputs always produce the same output.

Opportunity-cost arithmetic (rule 24)
--------------------------------------
For each canonical skill that I am missing, collect every scored listing
that lists it as required.  Then:

    opportunity_cost = sum(listing.fit_score / 100
                           for each blocking listing)

A listing scored 85 contributes 0.85; a listing scored 20 contributes 0.20.
Gaps are ranked by this sum descending, so a skill blocking a few high-fit
listings outranks a skill that appears in many low-fit listings.
Raw frequency is never the ranking key (rule 24).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean
from typing import Any

import edgedash.storage as storage
from edgedash.agents.base import AgentResult
from edgedash.config import Config
from edgedash.skills import canonical


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOP_N = 10
LOW_CONFIDENCE_THRESHOLD = 3   # listings_blocked < this → flag as low confidence
MAX_EXAMPLE_IDS = 5


# ---------------------------------------------------------------------------
# Internal data container
# ---------------------------------------------------------------------------

class _GapAccumulator:
    """
    Accumulates per-skill evidence across all scored listings.

    Attributes are all built from deterministic set/list operations —
    no model judgment involved.
    """

    __slots__ = (
        "skill",
        "_blocking",        # list of (fit_score, listing_id)
        "_nice_to_have",    # count of listings where it was nice-to-have
    )

    def __init__(self, skill: str) -> None:
        self.skill = skill
        self._blocking: list[tuple[int, str]] = []
        self._nice_to_have: int = 0

    def add_blocking(self, fit_score: int, listing_id: str) -> None:
        self._blocking.append((fit_score, listing_id))

    def add_nice_to_have(self) -> None:
        self._nice_to_have += 1

    # ------------------------------------------------------------------
    # Derived metrics — all deterministic
    # ------------------------------------------------------------------

    @property
    def listings_blocked(self) -> int:
        return len(self._blocking)

    @property
    def opportunity_cost(self) -> float:
        """
        Sum of (fit_score / 100) over every listing blocked by this gap.

        This is the primary ranking key (rule 24).  A gap in a listing
        scored 85 is worth 0.85; a gap in a listing scored 20 is worth
        0.20.  Never ranked by raw frequency alone.
        """
        return sum(score / 100.0 for score, _ in self._blocking)

    @property
    def mean_score(self) -> float:
        if not self._blocking:
            return 0.0
        return mean(score for score, _ in self._blocking)

    @property
    def top_score(self) -> int:
        if not self._blocking:
            return 0
        return max(score for score, _ in self._blocking)

    @property
    def example_ids(self) -> list[str]:
        """Up to MAX_EXAMPLE_IDS listing IDs, highest score first (rule 26)."""
        sorted_pairs = sorted(self._blocking, key=lambda p: p[0], reverse=True)
        return [lid for _, lid in sorted_pairs[:MAX_EXAMPLE_IDS]]

    @property
    def also_nice_to_have(self) -> int:
        return self._nice_to_have

    @property
    def low_confidence(self) -> bool:
        """True when sample size is below threshold (rule 27)."""
        return self.listings_blocked < LOW_CONFIDENCE_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill":             self.skill,
            "listings_blocked":  self.listings_blocked,
            "opportunity_cost":  round(self.opportunity_cost, 4),
            "mean_score":        round(self.mean_score, 2),
            "top_score":         self.top_score,
            "example_ids":       self.example_ids,
            "also_nice_to_have": self.also_nice_to_have,
            "low_confidence":    self.low_confidence,
        }


# ---------------------------------------------------------------------------
# Core analysis function (pure — no I/O, testable in isolation)
# ---------------------------------------------------------------------------

def _analyse(
    listings: list[dict[str, Any]],
    my_skills: list[str],
    aliases: dict[str, str],
) -> list[dict[str, Any]]:
    """
    Compute gap metrics from a list of scored+fact-enriched listings.

    Args:
        listings:  Output of storage.get_scored_listings_with_facts().
                   Each dict must have fit_score, id, required_skills,
                   and nice_to_have.
        my_skills: The user's skill list from config, passed in directly
                   so this function has no config dependency.
        aliases:   Alias map from config.skill_aliases.

    Returns:
        List of gap dicts, ranked by opportunity_cost descending,
        capped at TOP_N.
    """
    # Canonicalise my skills once up front.
    my_canon: set[str] = {
        canonical(s, aliases)
        for s in my_skills
        if s.strip()
    }

    accumulators: dict[str, _GapAccumulator] = defaultdict(
        lambda: _GapAccumulator("")  # placeholder; filled on first access
    )

    for listing in listings:
        fit_score = listing.get("fit_score")

        # Rule: skip unscored listings — they haven't been analysed yet.
        if fit_score is None:
            continue

        fit_score = int(fit_score)
        listing_id: str = listing["id"]

        required: list[str] = listing.get("required_skills") or []
        nice_to_have: list[str] = listing.get("nice_to_have") or []

        canon_required: set[str] = {
            c for s in required
            if (c := canonical(s, aliases))
        }
        canon_nice: set[str] = {
            c for s in nice_to_have
            if (c := canonical(s, aliases))
        }

        # Gaps: required skills I don't have.
        for skill in canon_required - my_canon:
            if skill not in accumulators:
                accumulators[skill] = _GapAccumulator(skill)
            accumulators[skill].add_blocking(fit_score, listing_id)

        # Nice-to-have tracking: skills I'm missing that were nice-to-have
        # in this listing — tracked separately, never mixed into required count.
        for skill in (canon_nice - my_canon) - canon_required:
            if skill not in accumulators:
                accumulators[skill] = _GapAccumulator(skill)
            accumulators[skill].add_nice_to_have()

    # Rank by opportunity_cost descending (rule 24), cap at TOP_N.
    ranked = sorted(
        (acc for acc in accumulators.values() if acc.listings_blocked > 0),
        key=lambda a: a.opportunity_cost,
        reverse=True,
    )

    return [acc.to_dict() for acc in ranked[:TOP_N]]


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class GapAnalyzer:
    """
    Deterministic skill-gap analysis agent.

    Reads scored listings + extracted facts, computes gap metrics in pure
    Python, writes a timestamped snapshot to skill_gaps_v2, and returns
    an AgentResult summary.

    No LLM call is made anywhere in this class.
    """

    name = "GapAnalyzer"

    def run(self, config: Config, db_path: str) -> AgentResult:
        listings = storage.get_scored_listings_with_facts(db_path)

        if not listings:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="No scored listings with extracted facts found.",
            )

        aliases: dict[str, str] = {
            str(k).lower().strip(): str(v).lower().strip()
            for k, v in (getattr(config, "skill_aliases", None) or {}).items()
        }

        gaps = _analyse(listings, config.my_skills, aliases)

        computed_at = datetime.now(timezone.utc).isoformat()
        run_id = computed_at[:19].replace(":", "-") + "_" + uuid.uuid4().hex[:8]

        saved = storage.save_gap_snapshot(
            db_path,
            run_id=run_id,
            computed_at=computed_at,
            gaps=gaps,
        )

        notes = _build_notes(gaps, len(listings), saved)

        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=saved,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Notes builder
# ---------------------------------------------------------------------------

def _build_notes(
    gaps: list[dict[str, Any]],
    listings_analysed: int,
    saved: int,
) -> str:
    if not gaps:
        return (
            f"No gaps found · {listings_analysed} listings analysed · "
            f"{saved} snapshot rows written."
        )

    top = gaps[0]
    top_summary = (
        f"{top['skill']} "
        f"({top['listings_blocked']} listings, "
        f"cost {top['opportunity_cost']:.1f})"
    )

    low_conf = sum(1 for g in gaps if g["low_confidence"])
    low_conf_note = f" · {low_conf} low-confidence" if low_conf else ""

    return (
        f"{len(gaps)} gaps · top: {top_summary} · "
        f"{listings_analysed} listings analysed · "
        f"{saved} snapshot rows written"
        f"{low_conf_note}."
    )
