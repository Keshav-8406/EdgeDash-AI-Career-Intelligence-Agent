# EdgeDash

EdgeDash is an autonomous career intelligence agent that runs on a schedule,
fetches live job listings from configured sources, scores each listing for fit
against your skills profile, identifies the skill gaps appearing most frequently
in roles you are targeting, verifies its own output for consistency, and
publishes the results to a Streamlit dashboard — giving you a daily, ranked
view of the market without any manual searching.

---

## Architecture

```
Trigger (scheduled)
        |
        v
   Orchestrator
   /     |      \
Fetcher  Scorer  GapAnalyzer
        |
        v
     Verifier
        |
        v
     Storage
        |
        v
  Dashboard (read-only)
```

The Orchestrator reads state and delegates; it never fetches or scores directly.
Each sub-agent has one goal and one stop condition.
Storage is the only layer that touches the database.
The Dashboard only reads — it never writes.

---

## Current status

### Week 1 — foundation (complete)

- [x] `edgedash/config.py` — `Config` dataclass, loads from `config.yaml`
- [x] `edgedash/storage.py` — sole SQLite interface; `listings`, `skill_gaps`, `cycle_log` tables
- [x] `edgedash/agents/base.py` — `Agent` protocol, `AgentResult` dataclass
- [x] `edgedash/agents/mock_fetcher.py` — **temporary mock**; 12 fake listings (4 stable, 8 varying) to prove dedup logic; will be replaced by `RealFetcher` in week 2
- [x] `edgedash/orchestrator.py` — `run_cycle()` with agent registry, state read, cycle logging, console summary
- [x] `run_cycle.py` — entry point

### Week 2 — live data (upcoming)

- [ ] `RealFetcher` — scrapes or calls job board APIs; replaces `MockFetcher` in the registry (one-line swap)
- [ ] `Scorer` — LLM-assisted fit scoring against `my_skills` and `target_role`

### Week 3 — analysis and verification (upcoming)

- [ ] `GapAnalyzer` — aggregates missing skills across listings into `skill_gaps`
- [ ] `Verifier` — sanity-checks Scorer and GapAnalyzer output before storage commit

### Week 4 — production (upcoming)

- [ ] Migrate storage from SQLite to hosted Postgres (one-file change in `storage.py`)
- [ ] Streamlit dashboard reading from `listings` and `skill_gaps`
- [ ] Scheduled trigger (cron or cloud scheduler)

---

## Current Skill Gap

> Based on the latest snapshot (1 run to date). Trend data is not yet available —
> at least one additional run on a different day is needed before movement can be reported.

The gap analyzer ranked **cloud computing** as the #1 priority gap in the
current snapshot:

| Metric | Value |
|---|---|
| Listings blocked | 7 |
| Opportunity cost | 3.87 |
| Mean fit score of those listings | 55.3 |

**Opportunity cost** is `Σ(fit_score / 100)` across the 7 listings that require
this skill. A listing scored 55 contributes 0.55; a listing scored 20
contributes 0.20. Cloud computing ranks first because it blocks more high-fit
listings than any other missing skill — not merely because it appears most
often.

**Recommendation:** Complete one structured cloud fundamentals course (AWS Cloud
Practitioner, Google Cloud Digital Leader, or equivalent) and add at least one
hands-on project — deploying a scheduled workload or a storage pipeline — to a
public portfolio. This directly addresses the skill that is blocking the most
opportunity in the current target-role market.

This recommendation reflects the latest snapshot only and may change as
EdgeDash collects further cycles and the ranked gap list evolves.

---

## Setup

**Python 3.11 or later is required.**

Install dependencies:

```bash
pip install pyyaml
```

Edit `config.yaml` at the repo root to match your profile:

```yaml
target_role: "Data Analyst"
target_city: "Bengaluru"
keywords:
  - "SQL"
  - "Python"
my_skills:
  - "Python"
  - "SQL"
  - "Pandas"
experience_years: 2
db_path: "edgedash.db"
min_fit_score: 60
```

All user-specific values live here. Never edit source files to change them.

Run one cycle:

```bash
python run_cycle.py
```

The database file (`edgedash.db` by default) is created automatically on first
run. Run the command a second time to see deduplication in action — the mock
fetcher will report 12 offered and 4 new.

---

## Design decisions

**Storage is isolated behind one module.**
`edgedash/storage.py` is the only file that imports `sqlite3`. Every other
module calls its thin interface. When we migrate to Postgres in week 4, only
`storage.py` changes — no other file needs to know the database moved.

**Listing IDs are stable hashes of source + URL.**
`make_listing_id(source, url)` produces a deterministic SHA-256 digest. The
same job posting from the same source always maps to the same ID regardless of
when it was fetched, so `INSERT OR IGNORE` silently skips duplicates and the
`records_touched` count in `cycle_log` reflects genuinely new listings only.

**The Orchestrator delegates instead of doing the work itself.**
Keeping fetch, score, and analysis in separate agents means each one can be
tested, replaced, or retried independently. The Orchestrator only reads state
and decides what runs next — if a sub-agent fails, the Orchestrator logs it and
moves on without corrupting the other agents' results.
