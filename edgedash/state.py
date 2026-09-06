"""
System-state inspection for the Orchestrator.

    state = read_state(config, now=datetime.now(timezone.utc))

`now` is a required parameter — never called inside this module — so
every caller can be unit-tested with a fixed clock.

All reads go through the storage module only (rule 2).
Every query is a cheap scalar: COUNT(*) or MAX(timestamp), no full
table loads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import edgedash.storage as storage
from edgedash.config import Config


# ---------------------------------------------------------------------------
# SystemState
# ---------------------------------------------------------------------------

@dataclass
class SystemState:
    """
    A point-in-time snapshot of the values the Orchestrator needs to
    decide which agents to run.

    All timestamp fields are UTC-aware datetimes or None.
    All numeric fields are plain ints or floats.
    No I/O after construction.
    """

    # --- Fetch ---
    last_fetch_at: datetime | None
    """Most recent fetched_at timestamp across all listings, or None."""

    hours_since_fetch: float
    """
    (now - last_fetch_at).total_seconds() / 3600.
    Float('inf') when last_fetch_at is None (never fetched).
    """

    # --- Scoring ---
    unscored_count: int
    """Number of listings with fit_score IS NULL."""

    # --- Gap analysis ---
    gaps_computed_at: datetime | None
    """
    Timestamp of the most recent gap snapshot row, or None if no
    snapshot exists yet.
    """

    gaps_stale: bool
    """
    True when any listing was scored more recently than the latest gap
    snapshot, meaning the snapshot no longer reflects current scores.
    Also True when gaps_computed_at is None (never run).
    """

    # --- Last cycle ---
    last_cycle_verdict: str | None
    """status field from the most recent cycle_log row, or None."""

    last_cycle_at: datetime | None
    """started_at from the most recent cycle_log row, or None."""


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def read_state(config: Config, now: datetime) -> SystemState:
    """
    Read the current system state from the database and return a
    SystemState.

    Args:
        config: project config (provides db_path).
        now:    the caller's clock — never datetime.now() inside here,
                so callers can pass a fixed value for testing.

    All queries are cheap scalars — no full table loads.
    """
    db = config.db_path

    # --- last fetch ---
    last_fetch_at = storage.last_fetch_time(db)
    if last_fetch_at is None:
        hours_since_fetch = float("inf")
    else:
        # Ensure both are UTC-aware before subtracting.
        lf = _ensure_utc(last_fetch_at)
        n  = _ensure_utc(now)
        hours_since_fetch = (n - lf).total_seconds() / 3600.0

    # --- unscored ---
    unscored_count = storage.count_unscored(db)

    # --- gap freshness ---
    # latest gap snapshot timestamp
    gap_rows = storage.get_latest_gap_snapshot(db)
    if gap_rows:
        gaps_computed_at: datetime | None = datetime.fromisoformat(
            gap_rows[0]["computed_at"]
        )
    else:
        gaps_computed_at = None

    # latest scored_at across all listings
    latest_score_at = storage.latest_score_time(db)

    if gaps_computed_at is None:
        gaps_stale = True
    elif latest_score_at is None:
        # Nothing has ever been scored — gaps cannot be stale
        gaps_stale = False
    else:
        gaps_stale = _ensure_utc(latest_score_at) > _ensure_utc(
            gaps_computed_at
        )

    # --- last cycle ---
    last_row: dict[str, Any] | None = storage.last_cycle(db)
    if last_row is None:
        last_cycle_verdict = None
        last_cycle_at      = None
    else:
        last_cycle_verdict = last_row["status"]
        raw_ts = last_row.get("started_at")
        last_cycle_at = (
            datetime.fromisoformat(raw_ts) if raw_ts else None
        )

    return SystemState(
        last_fetch_at=last_fetch_at,
        hours_since_fetch=round(hours_since_fetch, 3),
        unscored_count=unscored_count,
        gaps_computed_at=gaps_computed_at,
        gaps_stale=gaps_stale,
        last_cycle_verdict=last_cycle_verdict,
        last_cycle_at=last_cycle_at,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_utc(dt: datetime) -> datetime:
    """Attach UTC tzinfo if naive, return unchanged if already aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
