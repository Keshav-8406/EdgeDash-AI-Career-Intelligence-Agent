"""
test_source_isolation.py
========================
Proves steering rule 12: a source that raises is caught per-source, logged
with status "failed" to cycle_log, and the Fetcher continues to the next
source — the cycle never crashes.

Safe to run at any time:
  - Uses an isolated test database (test_isolation.db), NOT edgedash.db.
  - Deletes test_isolation.db on exit.
  - Does not modify any existing source, agent, storage, or config file.
  - The FailingSource registration lives only in this process.

Run:
    python test_source_isolation.py
"""

from __future__ import annotations

import os
import sqlite3
import sys

from edgedash.agents.fetcher import Fetcher
from edgedash.config import load_config
from edgedash.sources.base import SOURCES, register
from edgedash.sources.http import SourceError
import edgedash.storage as storage

TEST_DB = "test_isolation.db"


# ---------------------------------------------------------------------------
# Temporary failing source — registered only in this process
# ---------------------------------------------------------------------------

@register
class FailingSource:
    """Deliberately raises SourceError to simulate a dead job board."""
    name = "failing_test"

    def fetch(self, config):  # type: ignore[override]
        raise SourceError("simulated: connection timed out after 10 s")


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_test() -> bool:
    print("=" * 60)
    print("  SOURCE ISOLATION TEST (steering rule 12)")
    print("=" * 60)
    print(f"\n  Test DB : {TEST_DB}  (will be deleted on exit)")
    print(f"  Sources : ['failing_test', 'arbeitnow']")
    print()

    # Build a config that points at the test database only.
    config = load_config()
    config.db_path = TEST_DB
    config.sources = ["failing_test", "arbeitnow"]

    # Initialise the test database schema.
    storage.init_db(TEST_DB)

    # ── Run the Fetcher directly (no orchestrator, no edgedash.db) ──────────
    print("  Running Fetcher...")
    try:
        result = Fetcher().run(config, TEST_DB)
    except Exception as exc:
        print(f"\n  FAIL — Fetcher raised instead of isolating: {exc}")
        return False

    print(f"\n  Fetcher returned without raising. ✓")
    print(f"  AgentResult.notes  : {result.notes}")
    print(f"  AgentResult.status : {result.status}")

    # ── Inspect cycle_log ────────────────────────────────────────────────────
    conn = sqlite3.connect(TEST_DB)
    log_rows = conn.execute(
        "SELECT agent, status, notes FROM cycle_log ORDER BY id"
    ).fetchall()
    listing_count = conn.execute(
        "SELECT COUNT(*) FROM listings"
    ).fetchone()[0]
    conn.close()

    print(f"\n  cycle_log rows ({len(log_rows)} total):")
    for agent, status, notes in log_rows:
        icon = "✅" if status == "ok" else "❌"
        print(f"    {icon}  agent={agent:<30} status={status}  notes={notes[:60]}")

    # ── Assertions ───────────────────────────────────────────────────────────
    failed_rows = [(a, s, n) for a, s, n in log_rows if s == "failed"]
    ok_rows     = [(a, s, n) for a, s, n in log_rows if s == "ok"]

    passed = True

    def check(condition: bool, label: str) -> None:
        nonlocal passed
        ok = "PASS" if condition else "FAIL"
        print(f"\n  [{ok}] {label}")
        if not condition:
            passed = False

    check(
        any("failing_test" in a for a, _, _ in failed_rows),
        "failing_test has a cycle_log row with status='failed'",
    )
    check(
        any("SourceError" in n or "simulated" in n
            for a, _, n in failed_rows if "failing_test" in a),
        "failed row captures the exception message",
    )
    check(
        any("arbeitnow" in a for a, _, _ in ok_rows),
        "arbeitnow continued and logged status='ok'",
    )
    check(
        listing_count > 0,
        f"listings table has rows from arbeitnow ({listing_count} rows)",
    )
    check(
        "FAILED" in result.notes and "arbeitnow" in result.notes,
        "AgentResult.notes contains both FAILED and arbeitnow",
    )

    print()
    print("=" * 60)
    print(f"  {'ALL CHECKS PASSED ✓' if passed else 'SOME CHECKS FAILED ✗'}")
    print("=" * 60)
    return passed


# ---------------------------------------------------------------------------
# Entry point — always clean up the test database
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        success = run_test()
    finally:
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
            print(f"\n  Cleaned up {TEST_DB}")
    sys.exit(0 if success else 1)
