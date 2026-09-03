from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean

from edgedash import storage
from edgedash.agents.base import AgentResult
from edgedash.agents.extractor import extract
from edgedash.scoring import score_listing


class Scorer:
    """
    Deterministic scoring agent.

    Only listings with fit_score IS NULL are processed.
    One listing failure never stops the remaining listings.
    """

    name = "Scorer"

    def run(self, config, db_path: str) -> AgentResult:
        started_at = datetime.now(timezone.utc)

        listings = storage.get_unscored_listings(
            db_path,
            limit=config.score_batch_size,
        )

        if not listings:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="No unscored listings.",
            )

        scores: list[int] = []
        failures: list[str] = []
        scored_count = 0

        for listing in listings:
            listing_id = listing["id"]

            try:
                facts = extract(
                    listing,
                    config=config,
                )

                result = score_listing(
                    listing,
                    facts,
                    config,
                )

                saved = storage.update_listing_score(
                    db_path,
                    listing_id,
                    result["score"],
                    result["reason"],
                    result["components"],
                )

                if saved:
                    scored_count += 1
                    scores.append(result["score"])

            except Exception as exc:
                failures.append(
                    f"{listing_id}: {type(exc).__name__}: {exc}"
                )

        if scores:
            minimum = min(scores)
            maximum = max(scores)
            average = mean(scores)
            spread = maximum - minimum

            distribution = (
                f"count={len(scores)}, "
                f"min={minimum}, "
                f"max={maximum}, "
                f"mean={average:.2f}, "
                f"spread={spread}"
            )

            status = "suspect" if spread < 10 else "ok"

        else:
            distribution = (
                "count=0, min=0, max=0, "
                "mean=0.00, spread=0"
            )

            status = "failed" if failures else "ok"

        notes_parts = [
            f"Scored {scored_count}/{len(listings)} listings.",
            f"Distribution: {distribution}.",
        ]

        if failures:
            notes_parts.append(
                f"Listing failures: {len(failures)}."
            )

            # Keep cycle_log notes readable.
            notes_parts.extend(failures[:10])

        elapsed_ms = int(
            (
                datetime.now(timezone.utc) - started_at
            ).total_seconds() * 1000
        )

        notes_parts.append(
            f"Elapsed: {elapsed_ms} ms."
        )

        return AgentResult(
            agent=self.name,
            status=status,
            records_touched=scored_count,
            notes=" ".join(notes_parts),
        )


def run(config, db_path: str) -> AgentResult:
    """Convenience entry point."""
    return Scorer().run(config, db_path)