"""
Proves steering rule 12: a raising source is isolated and the cycle
continues to the next source without crashing.

Run:  python prove_isolation.py
"""

from edgedash.config import load_config
from edgedash.orchestrator import run_cycle
from edgedash.sources.base import SOURCES, register
from edgedash.config import Config
import sqlite3


# ---------------------------------------------------------------------------
# Register a deliberately broken source
# ---------------------------------------------------------------------------

@register
class BrokenSource:
    name = "broken_test"

    def fetch(self, config: Config) -> list[dict]:
        raise ConnectionError("simulated network timeout")


# ---------------------------------------------------------------------------
# Run the cycle with broken_test first, then arbeitnow
# ---------------------------------------------------------------------------

config = load_config()
config.sources = ["broken_test", "arbeitnow"]

run_cycle(config)

# ---------------------------------------------------------------------------
# Confirm cycle_log captured the failure row
# ---------------------------------------------------------------------------

conn = sqlite3.connect(config.db_path)
failed_rows = conn.execute(
    "SELECT agent, status, notes FROM cycle_log WHERE status='failed'"
).fetchall()
ok_rows = conn.execute(
    "SELECT agent, status, records_touched FROM cycle_log WHERE status='ok' AND agent LIKE 'Fetcher/%'"
).fetchall()
conn.close()

print()
print("=== cycle_log: FAILED rows ===")
for r in failed_rows:
    print(f"  agent={r[0]}  status={r[1]}  notes={r[2]}")

print()
print("=== cycle_log: Fetcher/* OK rows ===")
for r in ok_rows:
    print(f"  agent={r[0]}  status={r[1]}  records_touched={r[2]}")
