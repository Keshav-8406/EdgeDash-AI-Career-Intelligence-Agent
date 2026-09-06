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

CREATE TABLE IF NOT EXISTS skill_gaps_v2 (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT    NOT NULL,
    computed_at       TEXT    NOT NULL,
    skill             TEXT    NOT NULL,
    listings_blocked  INTEGER NOT NULL,
    opportunity_cost  REAL    NOT NULL,
    mean_score        REAL    NOT NULL,
    top_score         INTEGER NOT NULL,
    example_ids       TEXT    NOT NULL,  -- JSON array, up to 5 listing IDs
    also_nice_to_have INTEGER NOT NULL DEFAULT 0,
    low_confidence    INTEGER NOT NULL DEFAULT 0   -- 1 when listings_blocked < 3
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

        # skill_gaps_v2 is created by _DDL above; ensure extra columns
        # exist for databases created before this migration.
        _ensure_column(
            conn,
            "skill_gaps_v2",
            "also_nice_to_have",
            "INTEGER NOT NULL DEFAULT 0",
        )

        _ensure_column(
            conn,
            "skill_gaps_v2",
            "low_confidence",
            "INTEGER NOT NULL DEFAULT 0",
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
# Gap snapshots  (skill_gaps_v2)
# ---------------------------------------------------------------------------

def save_gap_snapshot(
    path: str,
    run_id: str,
    computed_at: str,
    gaps: list[dict[str, Any]],
) -> int:
    """
    Write one timestamped snapshot of gap rows.  Never overwrites a
    previous run — each run_id produces a distinct set of rows.

    Each dict in `gaps` must have the keys:
        skill, listings_blocked, opportunity_cost, mean_score,
        top_score, example_ids (list[str]), also_nice_to_have,
        low_confidence (bool)

    Returns the number of rows inserted.
    """
    if not gaps:
        return 0

    with _connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO skill_gaps_v2
            (
                run_id, computed_at, skill, listings_blocked,
                opportunity_cost, mean_score, top_score,
                example_ids, also_nice_to_have, low_confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    computed_at,
                    g["skill"],
                    int(g["listings_blocked"]),
                    float(g["opportunity_cost"]),
                    float(g["mean_score"]),
                    int(g["top_score"]),
                    json.dumps(g["example_ids"], ensure_ascii=False),
                    int(g["also_nice_to_have"]),
                    int(g["low_confidence"]),
                )
                for g in gaps
            ],
        )

    return len(gaps)


def get_latest_gap_snapshot(
    path: str,
) -> list[dict[str, Any]]:
    """
    Return all rows from the most recent gap snapshot, ordered by
    opportunity_cost descending.

    Returns an empty list if no snapshots exist yet.
    """
    with _connect(path) as conn:
        # Find the run_id with the latest computed_at
        row = conn.execute(
            """
            SELECT run_id
            FROM skill_gaps_v2
            ORDER BY computed_at DESC
            LIMIT 1
            """
        ).fetchone()

        if row is None:
            return []

        latest_run_id = row["run_id"]

        rows = conn.execute(
            """
            SELECT *
            FROM skill_gaps_v2
            WHERE run_id = ?
            ORDER BY opportunity_cost DESC
            """,
            (latest_run_id,),
        ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["example_ids"] = json.loads(d["example_ids"])
        result.append(d)

    return result


def get_earliest_gap_snapshot(
    path: str,
) -> list[dict[str, Any]]:
    """
    Return all rows from the oldest gap snapshot, ordered by
    opportunity_cost descending.

    Returns an empty list if no snapshots exist yet.
    """
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT run_id
            FROM skill_gaps_v2
            ORDER BY computed_at ASC
            LIMIT 1
            """
        ).fetchone()

        if row is None:
            return []

        earliest_run_id = row["run_id"]

        rows = conn.execute(
            """
            SELECT *
            FROM skill_gaps_v2
            WHERE run_id = ?
            ORDER BY opportunity_cost DESC
            """,
            (earliest_run_id,),
        ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["example_ids"] = json.loads(d["example_ids"])
        result.append(d)

    return result


def get_snapshot_run_ids(path: str) -> list[str]:
    """
    Return all distinct run_ids ordered by computed_at ascending.

    Used to count snapshots and determine the time window for trend
    reporting.  Read-only.
    """
    with _connect(path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT run_id, MIN(computed_at) AS ts
            FROM skill_gaps_v2
            GROUP BY run_id
            ORDER BY ts ASC
            """
        ).fetchall()

    return [row["run_id"] for row in rows]


# ---------------------------------------------------------------------------
# Scored listings with cached facts
# ---------------------------------------------------------------------------

def get_scored_listings_with_facts(
    path: str,
) -> list[dict[str, Any]]:
    """
    Return every scored listing joined with its cached extraction facts.

    Only listings with fit_score IS NOT NULL are included.
    Listings that have no entry in extraction_cache are silently skipped
    (they have no facts to analyse).

    Each returned dict merges all listing columns with the parsed
    facts_json keys (required_skills, nice_to_have, seniority, …).

    The join is done in Python because SQLite does not expose sha256().
    The same hashing logic as edgedash.agents.extractor.description_hash
    is replicated here so the keys match.
    """
    import hashlib as _hashlib

    with _connect(path) as conn:
        listing_rows = conn.execute(
            """
            SELECT id, title, company, location, fit_score,
                   fit_reason, posted_at, description
            FROM listings
            WHERE fit_score IS NOT NULL
            """
        ).fetchall()

        cache_rows = conn.execute(
            "SELECT description_hash, facts_json FROM extraction_cache"
        ).fetchall()

    cache: dict[str, dict[str, Any]] = {}
    for cr in cache_rows:
        try:
            cache[cr["description_hash"]] = json.loads(cr["facts_json"])
        except (ValueError, KeyError):
            pass

    result: list[dict[str, Any]] = []
    for lr in listing_rows:
        desc = (lr["description"] or "").strip()
        h = _hashlib.sha256(desc.encode("utf-8")).hexdigest()
        facts = cache.get(h)
        if facts is None:
            continue
        merged = dict(lr)
        merged.update(facts)
        result.append(merged)

    return result


# ---------------------------------------------------------------------------
# State inspection  (cheap scalar queries — counts and MAX timestamps only)
# ---------------------------------------------------------------------------

def latest_score_time(path: str) -> datetime | None:
    """
    Return the most recent scored_at timestamp across all listings.

    Used by read_state() to detect whether any scoring has happened
    since the last gap snapshot (gaps_stale check).

    Single MAX() query — no table scan.
    """
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT MAX(scored_at) FROM listings"
        ).fetchone()

    raw: str | None = row[0]
    if raw is None:
        return None
    return datetime.fromisoformat(raw)


def last_cycle(path: str) -> dict[str, Any] | None:
    """
    Return the most recent cycle_log row as a plain dict, or None.

    Columns returned: agent, started_at, finished_at, status, notes.
    Single ORDER BY + LIMIT 1 query — no table scan.
    """
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT agent, started_at, finished_at, status, notes
            FROM cycle_log
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# Verified cycle  (rule 38)
# ---------------------------------------------------------------------------

def get_recent_cycle_log(
    path: str,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """
    Return the most recent `limit` Orchestrator summary rows from
    cycle_log, ordered newest-first.

    Only rows where agent = 'Orchestrator' are returned — per-agent
    rows written inside a cycle are excluded.  The activity log shows
    ALL cycles including failed and degraded ones (rule 38 exception).

    Read-only.
    """
    with _connect(path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM cycle_log
            WHERE agent = 'Orchestrator'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(r) for r in rows]


def get_last_verified_cycle(path: str) -> dict[str, Any] | None:
    """
    Return the most recent Orchestrator cycle_log row whose notes contain
    'verdict=pass', or None if no passing cycle exists yet.

    The dashboard reads ONLY from this function (rule 38).  A failed or
    degraded cycle never overwrites last known-good data.

    Looks only at rows where agent = 'Orchestrator' so per-agent rows
    written inside a cycle are excluded.

    Read-only — no writes.
    """
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM cycle_log
            WHERE agent = 'Orchestrator'
              AND notes LIKE '%verdict=pass%'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()