"""
EdgeDash — agent activity dashboard.

Read-only.  Never runs a cycle.  Never writes to the database.

Per rule 38:
  Every data panel (listings, gaps) reads from the LAST PASSING CYCLE only.
  The activity log is the exception — it shows ALL cycles including failed
  and degraded ones, because the failures are the point of that panel.

Run:
    streamlit run app.py
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Path resolution — find config.yaml and db from wherever streamlit runs
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent


def _db_path() -> str:
    """Return the db_path from config.yaml, or 'edgedash.db' as fallback."""
    try:
        import yaml  # type: ignore[import]
        cfg_file = _REPO_ROOT / "config.yaml"
        if cfg_file.exists():
            with cfg_file.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return str(data.get("db_path", "edgedash.db"))
    except Exception:
        pass
    return "edgedash.db"


_DB = str(_REPO_ROOT / _db_path()) if not Path(_db_path()).is_absolute() \
    else _db_path()


# ---------------------------------------------------------------------------
# Cached storage reads  (short TTL so the dashboard stays live)
# ---------------------------------------------------------------------------

import edgedash.storage as storage


@st.cache_data(ttl=30)
def _load_recent_cycles(limit: int = 30) -> list[dict]:
    try:
        storage.init_db(_DB)
        return storage.get_recent_cycle_log(_DB, limit=limit)
    except Exception:
        return []


@st.cache_data(ttl=30)
def _load_last_verified() -> dict | None:
    try:
        return storage.get_last_verified_cycle(_DB)
    except Exception:
        return None


@st.cache_data(ttl=30)
def _load_top_listings(limit: int = 10) -> list[dict]:
    try:
        return storage.get_listings(_DB, limit=limit,
                                    min_score=None)
    except Exception:
        return []


@st.cache_data(ttl=30)
def _load_top_gaps(limit: int = 10) -> list[dict]:
    try:
        rows = storage.get_latest_gap_snapshot(_DB)
        return rows[:limit]
    except Exception:
        return []


@st.cache_data(ttl=30)
def _load_counts() -> dict:
    try:
        with storage._connect(_DB) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM listings"
            ).fetchone()[0]
            scored = conn.execute(
                "SELECT COUNT(*) FROM listings WHERE fit_score IS NOT NULL"
            ).fetchone()[0]
        return {"total": total, "scored": scored}
    except Exception:
        return {"total": 0, "scored": 0}


# ---------------------------------------------------------------------------
# Note parsers — extract structured fields from cycle_log.notes
# ---------------------------------------------------------------------------

def _parse_outcome(notes: str) -> str:
    m = re.search(r"outcome=(\w+)", notes)
    return m.group(1) if m else "unknown"


def _parse_verdict(notes: str) -> str:
    m = re.search(r"verdict=(\w+)", notes)
    return m.group(1) if m else "n/a"


def _parse_retries(notes: str) -> int:
    m = re.search(r"retries=(\d+)", notes)
    return int(m.group(1)) if m else 0


def _parse_cycle_secs(notes: str) -> str:
    m = re.search(r"cycle=([\d.]+)s", notes)
    return f"{m.group(1)}s" if m else "—"


def _parse_agents_run(notes: str) -> str:
    """Return a comma-separated list of agent names that ran this cycle."""
    agents = re.findall(r"(\w+): (?:ok|failed|suspect) \(", notes)
    return ", ".join(agents) if agents else "—"


def _parse_skipped(notes: str) -> str:
    skipped = re.findall(r"(\w+): skipped \(", notes)
    return ", ".join(skipped) if skipped else "—"


def _parse_failed_check(notes: str) -> str:
    """Extract the first failing check + observed value from Verifier notes."""
    # Notes format: "... Verifier: failed (…ms, 0 records) ..."
    # Verifier.notes embedded via _run_agent: "VERDICT: fail — check: observed X"
    m = re.search(r"VERDICT: fail — ([^|]+)", notes)
    if m:
        detail = m.group(1).strip()
        # Trim to keep it readable in a table cell
        return detail[:80] + ("…" if len(detail) > 80 else "")
    return "—"


def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return ts[:19]


# ---------------------------------------------------------------------------
# Row colour helper for the activity log
# ---------------------------------------------------------------------------

_OUTCOME_COLOUR = {
    "complete":     "🟢",
    "nothing_to_do": "⚪",
    "partial":      "🟡",
    "degraded":     "🔴",
    "unknown":      "⚫",
}

_VERDICT_COLOUR = {
    "pass": "✅",
    "fail": "❌",
    "n/a":  "—",
}


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="EdgeDash",
    page_icon="🔍",
    layout="wide",
)

st.title("🔍 EdgeDash — Agent Activity Dashboard")
st.caption("Read-only · refreshes every 30 s · data from last passing cycle")

# ===========================================================================
# SECTION 1 — Header strip
# ===========================================================================

recent_cycles = _load_recent_cycles()
last_verified = _load_last_verified()
counts        = _load_counts()

# Newest Orchestrator row (may be failed/degraded)
newest = recent_cycles[0] if recent_cycles else None

newest_outcome = _parse_outcome(newest["notes"]) if newest else None
newest_verdict = _parse_verdict(newest["notes"]) if newest else None

# Stale-data banner — rule 38
if newest and newest_verdict == "fail":
    verified_ts = _fmt_ts(last_verified["started_at"]) if last_verified else "none"
    st.warning(
        f"⚠️  The most recent cycle **FAILED verification** "
        f"(outcome: {newest_outcome}). "
        f"The listings and gaps below are from the last **verified** cycle: "
        f"**{verified_ts}**. "
        f"Fresh unverified data is never shown here.",
        icon="⚠️",
    )
elif newest and newest_outcome == "degraded":
    verified_ts = _fmt_ts(last_verified["started_at"]) if last_verified else "none"
    st.warning(
        f"⚠️  The most recent cycle is **DEGRADED** — verification failed "
        f"after retry. "
        f"Data below is from last verified cycle: **{verified_ts}**.",
        icon="⚠️",
    )

# Header KPI strip
col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.metric(
        "Last successful cycle",
        _fmt_ts(last_verified["started_at"]) if last_verified else "none yet",
    )
with col2:
    st.metric("Total listings", counts["total"])
with col3:
    st.metric("Scored listings", counts["scored"])
with col4:
    st.metric(
        "Cycles run",
        len(recent_cycles),
    )
with col5:
    if not newest:
        verdict_display = "No cycles yet"
        delta_colour = "off"
    elif newest_verdict == "pass":
        verdict_display = "✅ PASS"
        delta_colour = "normal"
    elif newest_verdict == "fail":
        verdict_display = "❌ FAIL"
        delta_colour = "inverse"
    elif newest_outcome == "degraded":
        verdict_display = "🔴 DEGRADED"
        delta_colour = "inverse"
    else:
        verdict_display = newest_outcome or "—"
        delta_colour = "off"
    st.metric("Current verdict", verdict_display)

st.divider()

# ===========================================================================
# SECTION 2 — Agent Activity Log  (ALL cycles — failures are the point)
# ===========================================================================

st.subheader("📋 Agent Activity Log — last 30 cycles")
st.caption(
    "Shows ALL cycles including failed and degraded. "
    "🟢 complete  🟡 partial  🔴 degraded  ⚪ nothing_to_do"
)

if not recent_cycles:
    st.info("No cycles recorded yet. Run `python run_cycle.py` to start.")
else:
    rows = []
    for c in recent_cycles:
        notes   = c.get("notes", "")
        outcome = _parse_outcome(notes)
        verdict = _parse_verdict(notes)
        retries = _parse_retries(notes)
        icon    = _OUTCOME_COLOUR.get(outcome, "⚫")
        v_icon  = _VERDICT_COLOUR.get(verdict, "—")

        rows.append({
            "  ": icon,
            "Timestamp":    _fmt_ts(c.get("started_at")),
            "Outcome":      outcome,
            "Verdict":      f"{v_icon} {verdict}",
            "Retries":      retries if retries else "—",
            "Agents run":   _parse_agents_run(notes),
            "Skipped":      _parse_skipped(notes),
            "Failed check": _parse_failed_check(notes),
            "Duration":     _parse_cycle_secs(notes),
        })

    import pandas as pd  # bundled with streamlit
    df = pd.DataFrame(rows)

    # Highlight degraded / failed rows
    def _row_style(row):
        outcome_val = row["Outcome"]
        if outcome_val == "degraded":
            return ["background-color: #4a1010"] * len(row)
        if outcome_val == "partial":
            return ["background-color: #3a3a10"] * len(row)
        return [""] * len(row)

    st.dataframe(
        df.style.apply(_row_style, axis=1),
        use_container_width=True,
        height=420,
        hide_index=True,
    )

st.divider()

# ===========================================================================
# SECTION 3 — Compact panels: top listings + top gaps
# ===========================================================================

col_listings, col_gaps = st.columns(2)

# ── Top 10 scored listings ────────────────────────────────────────────────
with col_listings:
    st.subheader("🏆 Top 10 Scored Listings")
    if not last_verified:
        st.info("No verified cycle yet.")
    else:
        listings = _load_top_listings(10)
        scored   = [l for l in listings if l.get("fit_score") is not None]
        if not scored:
            st.info("No scored listings yet.")
        else:
            import pandas as pd
            df_l = pd.DataFrame([
                {
                    "Score":   l["fit_score"],
                    "Title":   l.get("title", ""),
                    "Company": l.get("company", ""),
                    "Reason":  (l.get("fit_reason") or "")[:80],
                }
                for l in scored[:10]
            ])
            st.dataframe(df_l, use_container_width=True,
                         hide_index=True, height=320)

# ── Top 10 skill gaps ────────────────────────────────────────────────────
with col_gaps:
    st.subheader("📉 Top 10 Skill Gaps")
    if not last_verified:
        st.info("No verified cycle yet.")
    else:
        gaps = _load_top_gaps(10)
        if not gaps:
            st.info("No gap snapshot yet.")
        else:
            import pandas as pd
            df_g = pd.DataFrame([
                {
                    "Skill":     g["skill"],
                    "N":         g["listings_blocked"],
                    "Cost":      round(g["opportunity_cost"], 2),
                    "Mean score": round(g["mean_score"], 1),
                    "⚠":         "low-conf" if g.get("low_confidence") else "",
                }
                for g in gaps
            ])
            st.dataframe(df_g, use_container_width=True,
                         hide_index=True, height=320)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.caption(
    "EdgeDash dashboard · read-only · "
    "rule 38: panels above read from last passing cycle only · "
    "activity log shows all cycles"
)
