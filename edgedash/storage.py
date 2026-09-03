"""
Single storage module — the ONLY place sqlite3 is imported.

Swapping SQLite for Postgres later means editing this file only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator, Sequence


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

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
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    location        TEXT,
    url             TEXT NOT NULL,
    description     TEXT,
    source          TEXT NOT NULL,
    posted_at       TEXT,
    fetched_at      TEXT NOT NULL,
    fit_score       INTEGER,
    fit_reason      TEXT,
    components_json TEXT,
    scored_at       TEXT
);

CREATE TABLE IF NOT EXISTS skill_gaps (
    skill       TEXT PRIMARY KEY,
    frequency   INTEGER NOT NULL DEFAULT 1,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cycle_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agent            TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    records_touched INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL,
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS extraction_cache (
    description_hash TEXT PRIMARY KEY,
    facts_json       TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
"""


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    """Safely add a column if an older database does not have it."""
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }

    if column not in columns:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db(path: str) -> None:
    """Create tables and safely migrate older databases."""
    with _connect(path) as conn:
        conn.executescript(_DDL)

        _ensure_column(
            conn,
            "listings",
            "components_json",
            "TEXT",
        )

        _ensure_column(
            conn,
            "listings",
            "scored_at",
            "TEXT",
        )


# ---------------------------------------------------------------------------
# Stable listing ID
# ---------------------------------------------------------------------------

def make_listing_id(source: str, url: str) -> str:
    """Return a stable SHA-256 hex digest of source + url."""
    raw = f"{source}|{url}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:40]


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

def upsert_listings(
    path: str,
    rows: Sequence[ListingRow],
) -> int:
    """
    Insert new listings and silently skip duplicates.

    Returns the number of genuinely NEW rows inserted.
    """
    if not rows:
        return 0

    now = _utcnow()
    inserted = 0

    with _connect(path) as conn:
        for row in rows:
            listing_id = make_listing_id(
                row["source"],
                row["url"],
            )

            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO listings
                (
                    id,
                    title,
                    company,
                    location,
                    url,
                    description,
                    source,
                    posted_at,
                    fetched_at,
                    fit_score,
                    fit_reason,
                    components_json,
                    scored_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    row.get("components_json"),
                    row.get("scored_at"),
                ),
            )

            inserted += cursor.rowcount

    return inserted


def count_unscored(path: str) -> int:
    """Return the number of listings that have not yet been scored."""
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM listings
            WHERE fit_score IS NULL
            """
        ).fetchone()

    return int(row[0])


def get_unscored_listings(
    path: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """
    Return listings that have no score yet.

    Only rows where fit_score IS NULL are returned.
    """
    with _connect(path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM listings
            WHERE fit_score IS NULL
            ORDER BY fetched_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]


def update_listing_score(
    path: str,
    listing_id: str,
    fit_score: int,
    fit_reason: str,
    components: dict[str, Any],
) -> bool:
    """
    Save a deterministic score for one listing.

    The WHERE fit_score IS NULL condition makes scoring idempotent:
    an already-scored listing will not be overwritten.
    """
    with _connect(path) as conn:
        cursor = conn.execute(
            """
            UPDATE listings
            SET
                fit_score = ?,
                fit_reason = ?,
                components_json = ?,
                scored_at = ?
            WHERE id = ?
              AND fit_score IS NULL
            """,
            (
                int(fit_score),
                fit_reason,
                json.dumps(
                    components,
                    ensure_ascii=False,
                ),
                _utcnow(),
                listing_id,
            ),
        )

    return cursor.rowcount == 1


def last_fetch_time(path: str) -> datetime | None:
    """Return the most recent fetched_at timestamp."""
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT MAX(fetched_at)
            FROM listings
            """
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
        min_score: if given, exclude rows below this score.
    """
    query = "SELECT * FROM listings"
    params: list[Any] = []

    if min_score is not None:
        query += " WHERE fit_score >= ?"
        params.append(min_score)

    query += " ORDER BY fit_score DESC NULLS LAST LIMIT ?"
    params.append(limit)

    with _connect(path) as conn:
        rows = conn.execute(
            query,
            params,
        ).fetchall()

    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Extraction cache
# ---------------------------------------------------------------------------

def get_extraction_cache(
    path: str,
    description_hash: str,
) -> dict[str, Any] | None:
    """Return cached extraction facts, or None if not cached."""
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT facts_json
            FROM extraction_cache
            WHERE description_hash = ?
            """,
            (description_hash,),
        ).fetchone()

    if row is None:
        return None

    return json.loads(row["facts_json"])


def save_extraction_cache(
    path: str,
    description_hash: str,
    facts: dict[str, Any],
) -> None:
    """Store extraction facts keyed by description hash."""
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO extraction_cache
            (
                description_hash,
                facts_json,
                created_at
            )
            VALUES (?, ?, ?)
            """,
            (
                description_hash,
                json.dumps(
                    facts,
                    ensure_ascii=False,
                ),
                _utcnow(),
            ),
        )


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
    """Write one row to cycle_log."""
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO cycle_log
            (
                agent,
                started_at,
                finished_at,
                records_touched,
                status,
                notes
            )
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