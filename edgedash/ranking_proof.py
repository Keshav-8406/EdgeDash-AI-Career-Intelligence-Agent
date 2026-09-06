"""
Deterministic proof that opportunity-cost ranking differs from raw-frequency
ranking, and that the difference matters.

    python -m edgedash.gaps --ranking-proof

Everything here is read-only and deterministic.  No LLM, no network,
no writes.

Design
------
For every canonical skill that appears as *required* in at least one
scored listing (regardless of whether it is a gap for this user), collect:

    raw_frequency   = number of scored listings that list the skill as required
    opportunity_cost = sum(listing.fit_score / 100)  for those listings

This is the identical arithmetic used by GapAnalyzer — the proof reuses
the same formula so the output is directly auditable against the main
gap report.

The output then:
  1. Shows top-N by raw_frequency.
  2. Shows top-N by opportunity_cost.
  3. Finds skills whose rank differs between the two orderings — at least
     one such divergence is the proof that the two metrics are not equivalent.
  4. For every divergent pair (high-freq/low-cost vs low-freq/high-cost),
     shows the actual listing IDs and scores so the reader can verify the
     arithmetic themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from edgedash.skills import canonical


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SkillMetrics:
    """
    Both metrics for one canonical skill, plus the evidence rows so
    every number is traceable to listing IDs (rule 26).
    """
    skill: str
    raw_frequency: int                          # count of listings containing skill
    opportunity_cost: float                     # Σ(score/100)
    mean_score: float                           # mean fit score
    listing_evidence: list[tuple[str, int]]     # [(listing_id, fit_score), …] sorted score desc

    # Ranks assigned after full sort — set by compute_ranking_proof()
    freq_rank: int = 0
    cost_rank: int = 0

    @property
    def rank_delta(self) -> int:
        """
        How many positions the skill moves when switching from
        frequency ranking to cost ranking.

        Positive  = ranked higher by cost than by frequency.
        Negative  = ranked lower by cost than by frequency.
        Zero      = same rank under both orderings.
        """
        return self.freq_rank - self.cost_rank


@dataclass
class RankingProof:
    """
    Full output of compute_ranking_proof().  All fields are plain Python
    values — no I/O, no model output.
    """
    total_scored_listings: int
    total_skills_observed: int
    top_n: int

    by_frequency: list[SkillMetrics]   # top_n skills by raw_frequency desc
    by_cost: list[SkillMetrics]        # top_n skills by opportunity_cost desc

    # Skills whose freq_rank != cost_rank within the union of both top-N lists
    divergent: list[SkillMetrics]      # sorted by abs(rank_delta) desc

    has_proof: bool   # True when at least one divergence exists


# ---------------------------------------------------------------------------
# Core computation  (pure — no I/O)
# ---------------------------------------------------------------------------

TOP_N = 10


def compute_ranking_proof(
    listings: list[dict[str, Any]],
    aliases: dict[str, str],
    top_n: int = TOP_N,
) -> RankingProof:
    """
    Compute both raw-frequency and opportunity-cost rankings for every
    canonical skill observed in scored listings.

    Args:
        listings: output of storage.get_scored_listings_with_facts().
                  Each dict must have keys: id, fit_score, required_skills.
                  Listings with fit_score IS NULL are silently skipped.
        aliases:  canonical alias map (same as used by GapAnalyzer).
        top_n:    how many skills to show in each ranking.

    Returns:
        RankingProof — fully computed, no further I/O needed.
    """
    # Accumulate evidence per canonical skill.
    # evidence[skill] = [(listing_id, fit_score), …]
    evidence: dict[str, list[tuple[str, int]]] = {}

    scored_count = 0
    for listing in listings:
        fit_score = listing.get("fit_score")
        if fit_score is None:
            continue
        scored_count += 1
        fit_score = int(fit_score)
        listing_id: str = listing["id"]

        required: list[str] = listing.get("required_skills") or []
        for raw in required:
            canon = canonical(raw, aliases)
            if not canon:
                continue
            if canon not in evidence:
                evidence[canon] = []
            evidence[canon].append((listing_id, fit_score))

    # Build SkillMetrics for every observed skill.
    all_metrics: list[SkillMetrics] = []
    for skill, pairs in evidence.items():
        pairs_sorted = sorted(pairs, key=lambda p: p[1], reverse=True)
        raw_freq = len(pairs_sorted)
        opp_cost = round(sum(s / 100.0 for _, s in pairs_sorted), 4)
        m_score  = round(mean(s for _, s in pairs_sorted), 2)
        all_metrics.append(SkillMetrics(
            skill=skill,
            raw_frequency=raw_freq,
            opportunity_cost=opp_cost,
            mean_score=m_score,
            listing_evidence=pairs_sorted,
        ))

    # Sort by frequency (desc), then skill name for stability.
    by_freq = sorted(
        all_metrics,
        key=lambda m: (-m.raw_frequency, m.skill),
    )
    # Sort by opportunity cost (desc), then skill name for stability.
    by_cost = sorted(
        all_metrics,
        key=lambda m: (-m.opportunity_cost, m.skill),
    )

    # Assign ranks (1-based) across the full list.
    for rank, m in enumerate(by_freq, start=1):
        m.freq_rank = rank
    for rank, m in enumerate(by_cost, start=1):
        m.cost_rank = rank

    top_by_freq = by_freq[:top_n]
    top_by_cost = by_cost[:top_n]

    # Divergent = any skill in the union of both top-N lists
    # whose freq_rank != cost_rank.
    union_skills: set[str] = (
        {m.skill for m in top_by_freq} | {m.skill for m in top_by_cost}
    )
    skill_lookup: dict[str, SkillMetrics] = {m.skill: m for m in all_metrics}

    divergent: list[SkillMetrics] = sorted(
        (
            skill_lookup[s]
            for s in union_skills
            if skill_lookup[s].freq_rank != skill_lookup[s].cost_rank
        ),
        key=lambda m: (-abs(m.rank_delta), m.skill),
    )

    return RankingProof(
        total_scored_listings=scored_count,
        total_skills_observed=len(all_metrics),
        top_n=top_n,
        by_frequency=top_by_freq,
        by_cost=top_by_cost,
        divergent=divergent,
        has_proof=len(divergent) > 0,
    )


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

_DIVIDER = "─" * 88
_THIN    = "·" * 88
_W_SKILL = 30
_TOP_EV  = 3      # max listing evidence rows to print per divergent skill


def _freq_table(metrics: list[SkillMetrics], title: str) -> None:
    print(f"\n  {title}")
    print("  " + "-" * 74)
    print(
        f"  {'RANK':>4}  {'SKILL':<{_W_SKILL}}  {'FREQ':>5}  "
        f"{'COST':>6}  {'MEAN':>5}"
    )
    print("  " + "-" * 74)
    for m in metrics:
        print(
            f"  {m.freq_rank:>4}  {m.skill:<{_W_SKILL}}  "
            f"{m.raw_frequency:>5}  {m.opportunity_cost:>6.2f}  "
            f"{m.mean_score:>5.1f}"
        )


def _cost_table(metrics: list[SkillMetrics], title: str) -> None:
    print(f"\n  {title}")
    print("  " + "-" * 74)
    print(
        f"  {'RANK':>4}  {'SKILL':<{_W_SKILL}}  {'COST':>6}  "
        f"{'FREQ':>5}  {'MEAN':>5}"
    )
    print("  " + "-" * 74)
    for m in metrics:
        print(
            f"  {m.cost_rank:>4}  {m.skill:<{_W_SKILL}}  "
            f"{m.opportunity_cost:>6.2f}  {m.raw_frequency:>5}  "
            f"{m.mean_score:>5.1f}"
        )


def print_ranking_proof(proof: RankingProof) -> None:
    """Render a RankingProof to stdout.  No I/O beyond printing."""
    print()
    print("  EDGEDASH — RANKING PROOF")
    print("  Why opportunity cost, not raw frequency?")
    print(_DIVIDER)
    print(f"  Scored listings analysed : {proof.total_scored_listings}")
    print(f"  Distinct canonical skills: {proof.total_skills_observed}")
    print(f"  Top-N shown              : {proof.top_n}")
    print(_THIN)
    print(
        "  opportunity_cost = Σ(fit_score / 100) over listings"
        " containing the skill"
    )
    print(
        "  A skill in a 90-point listing contributes 0.90;"
        " in a 20-point listing, 0.20."
    )
    print(_DIVIDER)

    # ----------------------------------------------------------------
    # Table A — by raw frequency
    # ----------------------------------------------------------------
    _freq_table(proof.by_frequency, f"TABLE A — TOP {proof.top_n} BY RAW FREQUENCY")

    # ----------------------------------------------------------------
    # Table B — by opportunity cost
    # ----------------------------------------------------------------
    _cost_table(proof.by_cost, f"TABLE B — TOP {proof.top_n} BY OPPORTUNITY COST")

    # ----------------------------------------------------------------
    # Divergence analysis
    # ----------------------------------------------------------------
    print(f"\n  DIVERGENCE ANALYSIS")
    print("  " + "-" * 74)

    if not proof.has_proof:
        print(
            "  No rank divergence detected in current data.\n"
            "  Both orderings produce the same top-N.\n"
            "  This can happen when all listings have similar fit scores.\n"
            "  Run more cycles to accumulate listings with varied scores."
        )
        print()
        return

    print(
        f"  {len(proof.divergent)} skill(s) rank differently under the two "
        f"orderings."
    )
    print(
        "  Δrank = freq_rank − cost_rank."
        "  Positive = ranked higher by cost."
    )
    print("  " + "-" * 74)
    print(
        f"  {'SKILL':<{_W_SKILL}}  "
        f"{'FREQ_RK':>7}  {'COST_RK':>7}  {'Δrank':>6}  "
        f"{'FREQ':>5}  {'COST':>6}  {'MEAN':>5}"
    )
    print("  " + "-" * 74)

    for m in proof.divergent:
        delta = m.rank_delta
        sign  = "+" if delta > 0 else ""
        print(
            f"  {m.skill:<{_W_SKILL}}  "
            f"{m.freq_rank:>7}  {m.cost_rank:>7}  "
            f"{sign}{delta:>5}  "
            f"{m.raw_frequency:>5}  {m.opportunity_cost:>6.2f}  "
            f"{m.mean_score:>5.1f}"
        )

    # ----------------------------------------------------------------
    # Detailed evidence for the two most-divergent skills
    # ----------------------------------------------------------------
    print(f"\n  EVIDENCE  (up to {_TOP_EV} listings per skill, score desc)")
    print("  " + "-" * 74)
    print("  Listing IDs and fit scores are shown so you can verify the")
    print("  arithmetic: cost contribution = score / 100 per listing.")
    print("  " + "-" * 74)

    for m in proof.divergent[:2]:
        print(
            f"\n  {m.skill}  "
            f"(freq_rank={m.freq_rank}, cost_rank={m.cost_rank}, "
            f"Δ={'+' if m.rank_delta > 0 else ''}{m.rank_delta})"
        )
        shown = m.listing_evidence[:_TOP_EV]
        running = 0.0
        for lid, score in shown:
            contrib = score / 100.0
            running += contrib
            print(
                f"      listing {lid[:16]}…  "
                f"score={score:>3}  contrib={contrib:.2f}  "
                f"running_cost={running:.2f}"
            )
        if len(m.listing_evidence) > _TOP_EV:
            remainder = m.listing_evidence[_TOP_EV:]
            rest_cost = sum(s / 100.0 for _, s in remainder)
            print(
                f"      … {len(remainder)} more listing(s), "
                f"contributing {rest_cost:.2f} to cost"
            )
        print(
            f"      TOTAL  freq={m.raw_frequency}  "
            f"cost={m.opportunity_cost:.2f}  mean={m.mean_score:.1f}"
        )

    # ----------------------------------------------------------------
    # Plain-English conclusion
    # ----------------------------------------------------------------
    # Find the most striking case: highest freq that cost demotes.
    demoted = [m for m in proof.divergent if m.rank_delta < 0]
    promoted = [m for m in proof.divergent if m.rank_delta > 0]

    print(f"\n  CONCLUSION")
    print("  " + "-" * 74)

    if demoted and promoted:
        d = demoted[0]   # highest freq_rank skill that cost demotes
        p = promoted[0]  # highest cost_rank skill that freq misses

        print(
            f"  '{d.skill}' appears in {d.raw_frequency} listings (freq rank #{d.freq_rank})\n"
            f"  but those listings have a mean fit score of {d.mean_score:.1f},\n"
            f"  giving it an opportunity cost of only {d.opportunity_cost:.2f}.\n"
        )
        print(
            f"  '{p.skill}' appears in only {p.raw_frequency} listing(s) (freq rank #{p.freq_rank})\n"
            f"  but those listings have a mean fit score of {p.mean_score:.1f},\n"
            f"  giving it an opportunity cost of {p.opportunity_cost:.2f}.\n"
        )
        print(
            f"  Ranking by raw frequency would prioritise '{d.skill}' over\n"
            f"  '{p.skill}'.  Ranking by opportunity cost reverses this because\n"
            f"  the listings '{p.skill}' blocks are better fits for this user.\n"
            f"  Fixing '{p.skill}' unlocks more actual opportunity."
        )
    elif demoted:
        d = demoted[0]
        print(
            f"  '{d.skill}' ranks #{d.freq_rank} by frequency but only "
            f"#{d.cost_rank} by cost\n"
            f"  because its {d.raw_frequency} listings have a low mean score "
            f"({d.mean_score:.1f}).\n"
            f"  Frequency overstates its importance."
        )
    else:
        p = promoted[0]
        print(
            f"  '{p.skill}' ranks #{p.cost_rank} by cost but only "
            f"#{p.freq_rank} by frequency\n"
            f"  because its {p.raw_frequency} listing(s) have a high mean score "
            f"({p.mean_score:.1f}).\n"
            f"  Frequency understates its importance."
        )

    print()
