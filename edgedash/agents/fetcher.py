"""
Fetcher — real network agent that delegates to registered Source classes.

Steering rules enforced here:
  9  Each source is instantiated from the SOURCES registry; the Fetcher
     contains no source-specific parsing.
 11  All HTTP is handled inside each Source via edgedash.sources.http.
 12  A failing source never kills the cycle: it is caught, logged to
     cycle_log, and execution continues with the next source.
  5  Every source attempt is logged to cycle_log (ok or failed).
"""

from __future__ import annotations

from datetime import datetime, timezone

import edgedash.storage as storage
from edgedash.agents.base import AgentResult
from edgedash.config import Config
from edgedash.sources.base import SOURCES

# Import all source modules so their @register decorators fire.
import edgedash.sources.arbeitnow  # noqa: F401


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Fetcher:
    name: str = "Fetcher"

    def run(
        self,
        config: Config,
        db_path: str,
        stop_conditions: dict | None = None,
    ) -> AgentResult:
        sc = stop_conditions or {}
        max_listings: int = sc.get("max_listings", 10_000)  # generous default
        parts: list[str] = []   # per-source summary fragments for notes
        total_new = 0

        for source_name in config.sources:
            # ── Look up the source ─────────────────────────────────────────
            source_cls = SOURCES.get(source_name)
            if source_cls is None:
                msg = f"Source '{source_name}' not found in registry."
                print(f"  ⚠  Fetcher: {msg}")
                parts.append(f"{source_name}: FAILED (not registered)")
                _log_source(db_path, source_name, 0, "failed", msg)
                continue

            source = source_cls()
            started = datetime.now(timezone.utc)

            # ── Fetch + write (rule 12: entire source block is isolated) ───
            # The try covers both fetch() and upsert_listings() so that a
            # failure at either step is caught, logged, and skipped — the
            # loop continues to the next source either way.
            try:
                rows = source.fetch(config)
                # Respect max_listings stop-condition from the Orchestrator.
                if len(rows) > max_listings:
                    rows = rows[:max_listings]
                    parts_prefix = f"[capped at {max_listings}] "
                else:
                    parts_prefix = ""
                new_count = storage.upsert_listings(db_path, rows)
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                print(f"  ⚠  Fetcher: source '{source_name}' failed — {msg}")
                parts.append(f"{source_name}: FAILED ({type(exc).__name__})")
                _log_source(db_path, source_name, 0, "failed", msg, started)
                continue

            total_new += new_count
            parts.append(f"{parts_prefix}{source_name}: {len(rows)} rows ({new_count} new)")
            _log_source(db_path, source_name, new_count, "ok", "", started)

        notes = " | ".join(parts) if parts else "no sources configured"

        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=total_new,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _log_source(
    db_path: str,
    source_name: str,
    new_count: int,
    status: str,
    notes: str,
    started: datetime | None = None,
) -> None:
    """Write a cycle_log row for one source run."""
    now = datetime.now(timezone.utc)
    storage.log_cycle(
        path=db_path,
        agent=f"Fetcher/{source_name}",
        started_at=started or now,
        finished_at=now,
        records_touched=new_count,
        status=status,
        notes=notes,
    )
