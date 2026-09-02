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
