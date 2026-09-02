# EdgeDash — Project Steering Rules

## Project

EdgeDash is an autonomous AI career intelligence agent. It runs as a scheduled
loop that fetches live job listings, scores them for fit against a user profile,
surfaces skill gaps, verifies its own output, and publishes results to a
Streamlit dashboard.

---

## Architecture

```
Trigger (scheduled)
  └── Orchestrator
        ├── Fetcher       (sub-agent)
        ├── Scorer        (sub-agent)
        └── GapAnalyzer   (sub-agent)
              └── Verifier
                    └── Storage
                          └── Dashboard (read-only)
```

**Do not deviate from this architecture without telling the user first.**

- The **Orchestrator** reads state and delegates work to sub-agents. It never
  fetches job listings or scores them directly.
- Each **sub-agent** has exactly one goal and one stop condition.
- The **Dashboard** is read-only — it consumes from Storage and never writes.

---

## Hard Rules

### 1 — Python & Dependencies
- Target **Python 3.11+**.
- Prefer the standard library. Before adding any third-party dependency, explain
  what real work it saves and wait for approval.

### 2 — Storage Access
- ALL database access goes through a single `storage` module that exposes a thin
  interface.
- **No other module may import `sqlite3` directly.**
- The project will migrate from SQLite to hosted Postgres in week 4. That swap
  must be achievable by editing one file only.

### 3 — No Hardcoded User Data
- Never hardcode role, city, keywords, skills, or any other user-specific value
  in source code.
- Everything user-specific lives in `config` (a config file or environment
  variables loaded through the config layer).

### 4 — No Secrets in Code
- API keys, credentials, and tokens go in environment variables only.
- They are loaded in exactly one place (e.g. `config.py` or equivalent).

### 5 — Cycle Logging
- Every agent run must write a row to a `cycle_log` table containing:
  - which agent ran
  - start timestamp
  - number of records touched
  - pass / fail status
  - retry reason (if applicable)

### 6 — Fail Loudly
- No bare `except: pass` or silent swallowing of errors.
- If something goes wrong, raise or log with enough context to diagnose it.

### 7 — Type Hints & Docstrings
- Every function signature must have type hints (parameters and return type).
- Docstrings only where the intent is not obvious from the name.

### 8 — File Size
- Keep individual files under ~150 lines.
- Split a file before it approaches that limit — don't wait until it's a problem.

---

## Style

- Small, testable functions. One clear responsibility per function.
- Plain, readable Python over clever Python.
- **When asked to build one module, build one module.** Do not scaffold the
  whole application unless explicitly asked.

---

## Network & Sources

### 9 — Source Interface
- Every external job source lives behind a `Source` class with a uniform
  interface.
- The Fetcher never contains source-specific parsing logic.
- Adding a new source must never require editing the Fetcher.

### 10 — Normalised Output Contract
- Every `Source` returns a list of dicts with **exactly** these keys:
  `source`, `external_id`, `title`, `company`, `location`, `url`,
  `description`, `posted_at`, `raw`.
- Missing values are `None`. Never empty string, never `"N/A"`.

### 11 — Network Helper
- All HTTP calls go through one shared helper that enforces:
  - 10-second timeout (default)
  - 2 retry attempts with exponential backoff
  - A real `User-Agent` header
- No bare `requests.get()` anywhere else in the codebase.

### 12 — Per-Source Fault Isolation
- A source failing must **never** kill the cycle.
- Catch exceptions per source, write a `cycle_log` row with `status="failed"`,
  and continue to the next source.
- One dead job board must not stop other sources from running.

### 13 — Secrets via .env
- Source API keys come from environment variables loaded from a `.env` file.
- `.env` is gitignored. No literal key in code, no key in `config.yaml`.
- If a required key is absent, that source skips itself and logs a clear
  message — it does not raise or crash the cycle.

### 14 — Rate Limiting & Politeness
- At most 1 request per second per source.
- Always send a descriptive `User-Agent` header.
- Honour any documented page limits for the source.
