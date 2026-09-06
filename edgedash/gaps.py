"""
Morning gap dashboard.

    python -m edgedash.gaps                  # latest snapshot table
    python -m edgedash.gaps --trend          # trend across all snapshots
    python -m edgedash.gaps --suggest-aliases  # alias suggestions (read-only)
    python -m edgedash.gaps --ranking-proof  # proof: cost rank vs freq rank

Reads the latest skill_gaps_v2 snapshot and prints a ranked table to
stdout.  Read-only — never writes to the database.

Columns (default view)
----------------------
  Rank   Gap skill name
  N      listings_blocked  (sample size, rule 27)
  Cost   opportunity_cost  (ranking key, rule 24)
  Mean   mean fit score of blocking listings
  Top    highest fit score of any blocking listing
  Bar    visual bar scaled to opportunity_cost
  Flags  ⚠ low-confidence  (< 3 listings, rule 27)
         ★ also a nice-to-have in some listings

Columns (--trend view)
----------------------
  Rank   Current rank
  Skill  Canonical skill name
  First  opportunity_cost at the earliest snapshot
  Last   opportunity_cost at the latest snapshot
  Δ      Absolute change  (Last − First)
  %      Percent change relative to First
  Tag    NEW (not in earliest top-10), or blank
"""

from __future__ import annotations

import sys
from pathlib import Path

import edgedash.storage as storage
from edgedash.config import load_config


# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

_BAR_WIDTH   = 20
_BAR_CHAR    = "█"
_BAR_EMPTY   = "░"
_DIVIDER     = "─" * 88
_THIN        = "·" * 88

_HEADER = (
    f"{'#':>3}  "
    f"{'SKILL':<30}  "
    f"{'N':>4}  "
    f"{'COST':>6}  "
    f"{'MEAN':>5}  "
    f"{'TOP':>4}  "
    f"{'BAR':<{_BAR_WIDTH}}  "
    f"FLAGS"
)

_TREND_HEADER = (
    f"{'#':>3}  "
    f"{'SKILL':<30}  "
    f"{'FIRST':>6}  "
    f"{'LAST':>6}  "
    f"{'Δ':>7}  "
    f"{'%':>7}  "
    f"TAG"
)


# ---------------------------------------------------------------------------
# Bar renderer
# ---------------------------------------------------------------------------

def _bar(value: float, max_value: float) -> str:
    if max_value <= 0:
        return _BAR_EMPTY * _BAR_WIDTH
    filled = round((value / max_value) * _BAR_WIDTH)
    filled = max(0, min(_BAR_WIDTH, filled))
    return _BAR_CHAR * filled + _BAR_EMPTY * (_BAR_WIDTH - filled)


# ---------------------------------------------------------------------------
# Default snapshot row renderer
# ---------------------------------------------------------------------------

def _render_row(rank: int, gap: dict, max_cost: float) -> str:
    flags: list[str] = []
    if gap["low_confidence"]:
        flags.append("⚠ low-confidence")
    if gap["also_nice_to_have"] > 0:
        flags.append(f"★ nice-to-have in {gap['also_nice_to_have']}")

    flag_str = "  ".join(flags)

    return (
        f"{rank:>3}  "
        f"{gap['skill']:<30}  "
        f"{gap['listings_blocked']:>4}  "
        f"{gap['opportunity_cost']:>6.1f}  "
        f"{gap['mean_score']:>5.1f}  "
        f"{gap['top_score']:>4}  "
        f"{_bar(gap['opportunity_cost'], max_cost):<{_BAR_WIDTH}}  "
        f"{flag_str}"
    )


# ---------------------------------------------------------------------------
# Trend helpers
# ---------------------------------------------------------------------------

def _fmt_change(delta: float) -> str:
    """Format an absolute delta with a leading sign."""
    if delta > 0:
        return f"+{delta:6.1f}"
    return f"{delta:7.1f}"


def _fmt_pct(pct: float) -> str:
    """Format a percent change with a leading sign."""
    if pct > 0:
        return f"+{pct:6.1f}%"
    return f"{pct:7.1f}%"


def _snapshot_date(rows: list[dict]) -> str:
    """Extract the date portion of computed_at from any row in a snapshot."""
    if not rows:
        return "unknown"
    ts = rows[0].get("computed_at", "unknown")
    # ISO timestamp — take up to the first 'T' or first 19 chars.
    return ts[:19].replace("T", " ")


def _print_trend(db_path: str) -> None:
    run_ids = storage.get_snapshot_run_ids(db_path)
    n_snapshots = len(run_ids)

    print()
    print("  EDGEDASH — SKILL GAP TREND")
    print(_DIVIDER)

    # ----------------------------------------------------------------
    # Not enough data — say so honestly, no fabrication
    # ----------------------------------------------------------------
    if n_snapshots == 0:
        print("  No snapshots found.  Run a full cycle first:")
        print("    python run_cycle.py")
        print()
        return

    if n_snapshots == 1:
        snap_date = _snapshot_date(storage.get_latest_gap_snapshot(db_path))
        print(f"  Only 1 snapshot exists  ({snap_date}).")
        print()
        print("  A trend requires at least 2 snapshots on different days.")
        print("  Run the cycle on at least 1 more day to see movement.")
        print()
        print("  Days of runs still needed : 1")
        print()
        return

    # ----------------------------------------------------------------
    # Two or more snapshots — compute real trend
    # ----------------------------------------------------------------
    earliest = storage.get_earliest_gap_snapshot(db_path)
    latest   = storage.get_latest_gap_snapshot(db_path)

    earliest_date = _snapshot_date(earliest)
    latest_date   = _snapshot_date(latest)

    # Build lookup: skill -> opportunity_cost for each endpoint
    earliest_costs: dict[str, float] = {
        r["skill"]: r["opportunity_cost"] for r in earliest
    }
    earliest_skills: set[str] = set(earliest_costs)

    latest_top10 = latest[:10]
    latest_skills: set[str] = {r["skill"] for r in latest_top10}

    # Skills in earliest top-10 that are no longer in latest top-10
    dropped: set[str] = earliest_skills - latest_skills

    print(f"  Comparing  : {earliest_date}  →  {latest_date}")
    print(f"  Snapshots  : {n_snapshots} total")
    print(_THIN)
    print(_TREND_HEADER)
    print(_DIVIDER)

    for rank, gap in enumerate(latest_top10, start=1):
        skill = gap["skill"]
        last_cost  = gap["opportunity_cost"]
        first_cost = earliest_costs.get(skill)

        if first_cost is None:
            # Skill not present in earliest snapshot — it is NEW
            delta_str = "      n/a"
            pct_str   = "      n/a"
            tag = "NEW"
        else:
            delta = last_cost - first_cost
            pct   = ((delta / first_cost) * 100) if first_cost != 0 else 0.0
            delta_str = _fmt_change(delta)
            pct_str   = _fmt_pct(pct)
            tag = ""

        print(
            f"{rank:>3}  "
            f"{skill:<30}  "
            f"{first_cost if first_cost is not None else '  n/a':>6}  "
            f"{last_cost:>6.1f}  "
            f"{delta_str}  "
            f"{pct_str}  "
            f"{tag}"
        )

    # ----------------------------------------------------------------
    # Dropped-out section
    # ----------------------------------------------------------------
    if dropped:
        print(_THIN)
        print("  DROPPED OUT of top 10 since earliest snapshot:")
        for skill in sorted(dropped):
            old_cost = earliest_costs[skill]
            print(f"      {skill:<30}  was {old_cost:.1f}")

    print(_DIVIDER)
    print(
        "\n  Δ = Last − First  ·  "
        "% = change relative to first snapshot  ·  "
        "NEW = not in earliest top-10\n"
    )


# ---------------------------------------------------------------------------
# Default snapshot view
# ---------------------------------------------------------------------------

def _print_latest(db_path: str) -> None:
    gaps = storage.get_latest_gap_snapshot(db_path)

    print()
    print("  EDGEDASH — SKILL GAP REPORT  (latest snapshot)")
    print(_DIVIDER)

    if not gaps:
        print("  No gap snapshot found.  Run a full cycle first:")
        print("    python run_cycle.py")
        print()
        return

    computed_at = gaps[0].get("computed_at", "unknown")
    run_id      = gaps[0].get("run_id", "unknown")
    print(f"  Snapshot  : {computed_at}")
    print(f"  Run ID    : {run_id}")
    print(_THIN)

    print(_HEADER)
    print(_DIVIDER)

    max_cost = gaps[0]["opportunity_cost"] if gaps else 1.0

    for rank, gap in enumerate(gaps, start=1):
        print(_render_row(rank, gap, max_cost))

    print(_DIVIDER)

    low_conf_count = sum(1 for g in gaps if g["low_confidence"])
    print(
        f"\n  {len(gaps)} gaps shown  ·  "
        f"N = listings blocked  ·  "
        f"Cost = Σ(score/100) over blocking listings"
    )
    if low_conf_count:
        print(
            f"  ⚠  {low_conf_count} gap(s) computed from fewer than 3 listings "
            f"— treat as low confidence."
        )
    print(
        "  ★  'nice-to-have' count is tracked separately and "
        "does not affect ranking.\n"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    here = Path(__file__).resolve().parent.parent
    config_path = here / "config.yaml"

    try:
        config = load_config(config_path)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # Ensure the schema (including skill_gaps_v2) exists.
    # init_db is idempotent — safe to call on any existing database.
    storage.init_db(config.db_path)

    if "--trend" in sys.argv:
        _print_trend(config.db_path)
    elif "--suggest-aliases" in sys.argv:
        from edgedash.skills import _run_suggest_aliases
        _run_suggest_aliases()
    elif "--ranking-proof" in sys.argv:
        from edgedash.ranking_proof import compute_ranking_proof, print_ranking_proof
        listings = storage.get_scored_listings_with_facts(config.db_path)
        aliases: dict[str, str] = {
            str(k).lower().strip(): str(v).lower().strip()
            for k, v in (getattr(config, "skill_aliases", None) or {}).items()
        }
        proof = compute_ranking_proof(listings, aliases)
        print_ranking_proof(proof)
    else:
        _print_latest(config.db_path)


if __name__ == "__main__":
    main()