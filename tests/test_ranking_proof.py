"""
Tests for edgedash.ranking_proof.compute_ranking_proof().

All tests are pure — no database, no filesystem, no network, no LLM.
Synthetic listing dicts are constructed directly so every assertion
is traceable to exact numbers.

The core invariant under test: the proof demonstrates that raw-frequency
and opportunity-cost rankings can diverge, and the divergence is
explained by the fit scores of the listings containing each skill.
"""

from __future__ import annotations

import pytest

from edgedash.ranking_proof import (
    RankingProof,
    SkillMetrics,
    compute_ranking_proof,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _listing(
    listing_id: str,
    fit_score: int,
    required_skills: list[str],
) -> dict:
    """Build a minimal listing dict that compute_ranking_proof() accepts."""
    return {
        "id": listing_id,
        "fit_score": fit_score,
        "required_skills": required_skills,
        "nice_to_have": [],
    }


NO_ALIASES: dict[str, str] = {}


# ---------------------------------------------------------------------------
# The key divergence scenario
#
# skill_a  appears in 5 listings, all scored 20  → freq=5, cost=1.00, mean=20
# skill_b  appears in 2 listings, both scored 90 → freq=2, cost=1.80, mean=90
#
# By raw_frequency : skill_a ranks #1, skill_b ranks #2
# By opportunity_cost: skill_b ranks #1, skill_a ranks #2
# Rank delta for skill_a: freq_rank(1) - cost_rank(2) = -1  (demoted by cost)
# Rank delta for skill_b: freq_rank(2) - cost_rank(1) = +1  (promoted by cost)
# ---------------------------------------------------------------------------

def _divergence_listings() -> list[dict]:
    listings = []
    for i in range(5):
        listings.append(_listing(f"low-{i:02d}", 20, ["skill_a"]))
    for i in range(2):
        listings.append(_listing(f"high-{i:02d}", 90, ["skill_b"]))
    return listings


class TestDivergenceScenario:
    """The fundamental proof: freq ranking ≠ cost ranking when scores differ."""

    def test_has_proof_is_true(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        assert proof.has_proof is True

    def test_divergent_contains_both_skills(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        divergent_skills = {m.skill for m in proof.divergent}
        assert "skill_a" in divergent_skills
        assert "skill_b" in divergent_skills

    def test_skill_a_demoted_by_cost(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        skill_a = next(m for m in proof.divergent if m.skill == "skill_a")
        # freq_rank=1, cost_rank=2 → rank_delta = -1 (demoted)
        assert skill_a.freq_rank < skill_a.cost_rank
        assert skill_a.rank_delta < 0

    def test_skill_b_promoted_by_cost(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        skill_b = next(m for m in proof.divergent if m.skill == "skill_b")
        # freq_rank=2, cost_rank=1 → rank_delta = +1 (promoted)
        assert skill_b.freq_rank > skill_b.cost_rank
        assert skill_b.rank_delta > 0

    def test_skill_a_leads_frequency_table(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        assert proof.by_frequency[0].skill == "skill_a"

    def test_skill_b_leads_cost_table(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        assert proof.by_cost[0].skill == "skill_b"


# ---------------------------------------------------------------------------
# Arithmetic correctness
# ---------------------------------------------------------------------------

class TestArithmetic:
    def test_raw_frequency_equals_listing_count(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        skill_a = next(m for m in proof.by_frequency if m.skill == "skill_a")
        assert skill_a.raw_frequency == 5

    def test_opportunity_cost_is_sum_of_score_over_100(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        skill_a = next(m for m in proof.by_frequency if m.skill == "skill_a")
        # 5 listings × (20/100) = 1.00
        assert abs(skill_a.opportunity_cost - 1.00) < 1e-6

    def test_opportunity_cost_skill_b(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        skill_b = next(m for m in proof.by_cost if m.skill == "skill_b")
        # 2 listings × (90/100) = 1.80
        assert abs(skill_b.opportunity_cost - 1.80) < 1e-6

    def test_mean_score_is_arithmetic_mean(self):
        listings = [
            _listing("x1", 60, ["alpha"]),
            _listing("x2", 80, ["alpha"]),
        ]
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        alpha = next(m for m in proof.by_frequency if m.skill == "alpha")
        assert abs(alpha.mean_score - 70.0) < 1e-6

    def test_listing_evidence_sorted_score_desc(self):
        listings = [
            _listing("a", 30, ["zeta"]),
            _listing("b", 90, ["zeta"]),
            _listing("c", 60, ["zeta"]),
        ]
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        zeta = next(m for m in proof.by_frequency if m.skill == "zeta")
        scores = [score for _, score in zeta.listing_evidence]
        assert scores == sorted(scores, reverse=True)

    def test_listing_evidence_contains_all_ids(self):
        listings = [
            _listing("id-aaa", 70, ["beta"]),
            _listing("id-bbb", 80, ["beta"]),
        ]
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        beta = next(m for m in proof.by_frequency if m.skill == "beta")
        ids = {lid for lid, _ in beta.listing_evidence}
        assert ids == {"id-aaa", "id-bbb"}

    def test_total_scored_listings_count(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        assert proof.total_scored_listings == 7   # 5 low + 2 high

    def test_total_skills_observed(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        assert proof.total_skills_observed == 2


# ---------------------------------------------------------------------------
# rank_delta property
# ---------------------------------------------------------------------------

class TestRankDelta:
    def test_rank_delta_positive_when_cost_rank_better(self):
        # skill_b: freq_rank=2, cost_rank=1 → delta = 2-1 = +1
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        skill_b = next(m for m in proof.divergent if m.skill == "skill_b")
        assert skill_b.rank_delta == skill_b.freq_rank - skill_b.cost_rank
        assert skill_b.rank_delta > 0

    def test_rank_delta_negative_when_freq_rank_better(self):
        # skill_a: freq_rank=1, cost_rank=2 → delta = 1-2 = -1
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        skill_a = next(m for m in proof.divergent if m.skill == "skill_a")
        assert skill_a.rank_delta == skill_a.freq_rank - skill_a.cost_rank
        assert skill_a.rank_delta < 0

    def test_rank_delta_zero_when_ranks_equal(self):
        # Single skill — only one skill exists, must rank #1 in both.
        listings = [_listing("x", 50, ["solo"])]
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        solo = proof.by_frequency[0]
        assert solo.rank_delta == 0

    def test_divergent_sorted_by_abs_rank_delta_desc(self):
        # Build 3 skills with known deltas to check sort order.
        listings = (
            [_listing(f"low-{i}", 10, ["freq_king"]) for i in range(10)]
            + [_listing(f"mid-{i}", 50, ["middling"]) for i in range(5)]
            + [_listing(f"high-{i}", 95, ["cost_king"]) for i in range(2)]
        )
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=3)
        deltas = [abs(m.rank_delta) for m in proof.divergent]
        assert deltas == sorted(deltas, reverse=True)


# ---------------------------------------------------------------------------
# Alias map integration
# ---------------------------------------------------------------------------

class TestAliasIntegration:
    def test_alias_merges_variants_before_counting(self):
        # "k8s" and "kubernetes" should merge into "kubernetes" via alias map.
        aliases = {"k8s": "kubernetes"}
        listings = [
            _listing("a", 80, ["kubernetes"]),
            _listing("b", 60, ["k8s"]),
        ]
        proof = compute_ranking_proof(listings, aliases, top_n=5)
        skills = {m.skill for m in proof.by_frequency}
        assert "kubernetes" in skills
        assert "k8s" not in skills

    def test_alias_merges_count_correctly(self):
        aliases = {"k8s": "kubernetes"}
        listings = [
            _listing("a", 80, ["kubernetes"]),
            _listing("b", 60, ["k8s"]),
        ]
        proof = compute_ranking_proof(listings, aliases, top_n=5)
        k8s = next(m for m in proof.by_frequency if m.skill == "kubernetes")
        assert k8s.raw_frequency == 2

    def test_alias_merges_cost_correctly(self):
        aliases = {"k8s": "kubernetes"}
        listings = [
            _listing("a", 80, ["kubernetes"]),
            _listing("b", 60, ["k8s"]),
        ]
        proof = compute_ranking_proof(listings, aliases, top_n=5)
        k8s = next(m for m in proof.by_frequency if m.skill == "kubernetes")
        expected_cost = (80 + 60) / 100.0
        assert abs(k8s.opportunity_cost - expected_cost) < 1e-6


# ---------------------------------------------------------------------------
# Edge / boundary cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_listings_returns_empty_proof(self):
        proof = compute_ranking_proof([], NO_ALIASES, top_n=5)
        assert proof.total_scored_listings == 0
        assert proof.total_skills_observed == 0
        assert proof.by_frequency == []
        assert proof.by_cost == []
        assert proof.divergent == []
        assert proof.has_proof is False

    def test_unscored_listings_skipped(self):
        listings = [
            {"id": "x", "fit_score": None, "required_skills": ["python"]},
        ]
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        assert proof.total_scored_listings == 0
        assert proof.total_skills_observed == 0

    def test_no_divergence_when_single_skill(self):
        listings = [_listing("a", 70, ["python"]), _listing("b", 80, ["python"])]
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        assert proof.has_proof is False
        assert proof.divergent == []

    def test_top_n_caps_output_tables(self):
        listings = [_listing(f"id-{i}", 50, [f"skill_{i}"]) for i in range(20)]
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        assert len(proof.by_frequency) == 5
        assert len(proof.by_cost) == 5

    def test_identical_scores_no_divergence(self):
        # When all listings have identical scores, freq and cost are
        # proportional — no divergence is possible.
        listings = [
            _listing("a", 50, ["alpha"]),
            _listing("b", 50, ["alpha"]),
            _listing("c", 50, ["beta"]),
        ]
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        assert proof.has_proof is False

    def test_deterministic_same_output_twice(self):
        listings = _divergence_listings()
        p1 = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        p2 = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        assert [m.skill for m in p1.by_frequency] == [m.skill for m in p2.by_frequency]
        assert [m.skill for m in p1.by_cost] == [m.skill for m in p2.by_cost]
        assert [m.skill for m in p1.divergent] == [m.skill for m in p2.divergent]

    def test_listing_with_no_required_skills_ignored(self):
        listings = [
            _listing("a", 80, []),
            _listing("b", 70, ["docker"]),
        ]
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        assert proof.total_skills_observed == 1
        assert proof.by_frequency[0].skill == "docker"

    def test_skill_appearing_in_all_listings_still_ranked(self):
        listings = [_listing(f"x{i}", 50 + i * 5, ["common"]) for i in range(4)]
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        assert proof.by_frequency[0].skill == "common"
        assert proof.by_frequency[0].raw_frequency == 4

    def test_return_type_is_ranking_proof(self):
        proof = compute_ranking_proof([], NO_ALIASES)
        assert isinstance(proof, RankingProof)

    def test_by_frequency_elements_are_skill_metrics(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        assert all(isinstance(m, SkillMetrics) for m in proof.by_frequency)

    def test_by_cost_elements_are_skill_metrics(self):
        proof = compute_ranking_proof(_divergence_listings(), NO_ALIASES, top_n=5)
        assert all(isinstance(m, SkillMetrics) for m in proof.by_cost)


# ---------------------------------------------------------------------------
# Stability / sorting tie-breaking
# ---------------------------------------------------------------------------

class TestStability:
    def test_freq_ties_broken_alphabetically(self):
        # Two skills with the same frequency — alphabetical order must win.
        listings = [
            _listing("a", 50, ["zebra"]),
            _listing("b", 50, ["alpha"]),
        ]
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        skills = [m.skill for m in proof.by_frequency]
        assert skills == sorted(skills)   # alpha < zebra

    def test_cost_ties_broken_alphabetically(self):
        # Same cost → alphabetical.
        listings = [
            _listing("a", 50, ["zebra"]),
            _listing("b", 50, ["alpha"]),
        ]
        proof = compute_ranking_proof(listings, NO_ALIASES, top_n=5)
        skills = [m.skill for m in proof.by_cost]
        assert skills == sorted(skills)
