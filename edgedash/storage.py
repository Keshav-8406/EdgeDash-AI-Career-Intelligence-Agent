"""
Single storage module — the ONLY place sqlite3 is imported.

Swapping SQLite for Postgres in week 4 means editing this file only:
replace the sqlite3 calls with psycopg2 (or similar) and adjust the
placeholder style from '?' to '%s'.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator, Sequence


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# A listing row as accepted by upsert_listings.
ListingRow = dict[str, Any]


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@contextmanager
def _connect(path: str) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS listings (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    location    TEXT,
    url         TEXT NOT NULL,
    description TEXT,
    source      TEXT NOT NULL,
    posted_at   TEXT,
    fetched_at  TEXT NOT NULL,
    fit_score   INTEGER,
    fit_reason  TEXT
);

CREATE TABLE IF NOT EXISTS skill_gaps (
    skill       TEXT PRIMARY KEY,
    frequency   INTEGER NOT NULL DEFAULT 1,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cycle_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent           TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    records_touched INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    notes           TEXT
);
"""


def init_db(path: str) -> None:
    """Create tables if they do not already exist."""
    with _connect(path) as conn:
        conn.executescript(_DDL)


# ---------------------------------------------------------------------------
# Stable listing ID
# ---------------------------------------------------------------------------

def make_listing_id(source: str, url: str) -> str:
    """Return a stable SHA-256 hex digest (first 40 chars) of source + url."""
    raw = f"{source}|{url}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:40]


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

def upsert_listings(path: str, rows: Sequence[ListingRow]) -> int:
    """
    Insert new listings, silently skip duplicates (INSERT OR IGNORE on id).

    Each row dict must contain: title, company, url, source.
    Optional keys: location, description, posted_at, fit_score, fit_reason.

    Returns the count of genuinely NEW rows inserted.
    """
    if not rows:
        return 0

    now = _utcnow()
    inserted = 0

    with _connect(path) as conn:
        for row in rows:
            listing_id = make_listing_id(row["source"], row["url"])
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO listings
                    (id, title, company, location, url, description,
                     source, posted_at, fetched_at, fit_score, fit_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    listing_id,
                    row["title"],
                    row["company"],
                    row.get("location"),
                    row["url"],
                    row.get("description"),
                    row["source"],
                    row.get("posted_at"),
                    now,
                    row.get("fit_score"),
                    row.get("fit_reason"),
                ),
            )
            inserted += cursor.rowcount

    return inserted


def count_unscored(path: str) -> int:
    """Return the number of listings that have not yet been scored."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE fit_score IS NULL"
        ).fetchone()
    return int(row[0])


def last_fetch_time(path: str) -> datetime | None:
    """Return the most recent fetched_at timestamp, or None if no listings exist."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT MAX(fetched_at) FROM listings"
        ).fetchone()
    raw: str | None = row[0]
    if raw is None:
        return None
    return datetime.fromisoformat(raw)


def get_listings(
    path: str,
    limit: int = 50,
    min_score: int | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch listings ordered by fit_score descending.

    Args:
        limit: maximum rows to return.
        min_score: if given, exclude rows with fit_score below this value.
    """
    query = "SELECT * FROM listings"
    params: list[Any] = []

    if min_score is not None:
        query += " WHERE fit_score >= ?"
        params.append(min_score)

    query += " ORDER BY fit_score DESC NULLS LAST LIMIT ?"
    params.append(limit)

    with _connect(path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Cycle log
# ---------------------------------------------------------------------------

def log_cycle(
    path: str,
    agent: str,
    started_at: datetime,
    finished_at: datetime,
    records_touched: int,
    status: str,
    notes: str = "",
) -> None:
    """Write one row to cycle_log for an agent run."""
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO cycle_log
                (agent, started_at, finished_at, records_touched, status, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                agent,
                started_at.isoformat(),
                finished_at.isoformat(),
                records_touched,
                status,
                notes,
            ),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
