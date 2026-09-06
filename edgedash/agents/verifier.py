"""
Verifier — plausibility judge for cycle output.

Rules enforced here (steering rules 34-39):

  34  Judges only.  Writes NO data beyond the verdict itself.
      Never repairs, rewrites, or adjusts any row in the database.
  35  Checks assert properties of the output distribution and shape,
      not the accuracy of any single value.
  37  Every failure names the check, the observed value, and the
      threshold — never just "failed".

No LLM anywhere in this file.  A model cannot be the judge of a model's
output.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import edgedash.storage as storage
from edgedash.agents.base import AgentResult
from edgedash.config import Config
from edgedash.verification import Verdict, run_all_checks


class Verifier:
    """
    Reads current cycle data from storage, runs all plausibility checks,
    and returns a verdict in the AgentResult notes.

    Writes nothing to the database.
    """

    name = "Verifier"

    def run(
        self,
        config: Config,
        db_path: str,
        stop_conditions: dict | None = None,
    ) -> AgentResult:
        now = datetime.now(timezone.utc)

        # ── Gather inputs — read-only storage calls ────────────────────────

        # Scores: fit_score from every scored listing.
        scored_listings = storage.get_listings(db_path, limit=10_000)
        scores: list[int] = [
            int(r["fit_score"])
            for r in scored_listings
            if r.get("fit_score") is not None
        ]

        # Extraction facts: required_skills lists from cache.
        facts_rows = storage.get_scored_listings_with_facts(db_path)
        facts_list: list[dict[str, Any]] = [
            {"required_skills": r.get("required_skills") or []}
            for r in facts_rows
        ]

        # Gap snapshot: top gap rows ordered by opportunity_cost desc.
        gaps = storage.get_latest_gap_snapshot(db_path)

        # Latest fetch timestamp.
        latest_fetch_at = storage.last_fetch_time(db_path)

        # ── Run all checks ─────────────────────────────────────────────────
        verdict: Verdict = run_all_checks(
            scores=scores,
            facts_list=facts_list,
            gaps=gaps,
            latest_fetch_at=latest_fetch_at,
            config=config,
            now=now,
        )

        # ── Build notes (rule 37 — always specific) ────────────────────────
        if verdict.passed:
            notes = f"VERDICT: pass — {verdict.summary}"
        else:
            failure_details = " | ".join(
                f"{r.name}: observed {r.observed} (threshold {r.threshold})"
                for r in verdict.failed_checks
            )
            notes = f"VERDICT: fail — {failure_details}"

        return AgentResult(
            agent=self.name,
            status="ok" if verdict.passed else "failed",
            records_touched=0,        # Verifier writes nothing
            notes=notes,
        )
