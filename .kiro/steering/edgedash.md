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

---

## Intelligence & Scoring

### 15 — Single LLM Gateway
- All LLM calls go through `edgedash/llm.py`, which exposes one function.
- The provider and model name come from config — never hardcoded.
- Rate-limit to stay inside a free tier: **1 request per second**, max **15 per
  minute**. No other file imports an LLM SDK directly.

### 16 — Model Extracts Facts; Python Scores
- Never ask a model for a final score, ranking, or numeric rating.
- The model extracts structured facts only.
- All scoring arithmetic is deterministic Python in **one function**.
- The model never sees the scoring weights.

### 17 — Validate Every Model Response
- Every model response is validated against an explicit schema before use.
- A response that fails validation is retried once, then logged as a failure
  for **that listing only** — it must not crash the cycle or stop remaining
  listings from being processed.
- Never call `json.loads` on raw model text without a validation and repair
  path.

### 18 — Idempotent Scoring and Description Cache
- Never re-score a listing that already has a score. Query only listings
  `WHERE fit_score IS NULL`.
- Cache extraction results keyed on a hash of the job description so the
  same text is never sent to the model twice.

### 19 — Reasons Generated by Code, Not the Model
- Every score carries a human-readable reason **generated from the score
  components by our code** — never free text written by the model.

### 20 — Log Score Distribution Per Run
- Log the score distribution (count, min, max, mean, spread) to `cycle_log`
  on every scoring run.
- A run where all scores fall within a 10-point band is a **suspect run** and
  must be logged as such.

### 21 — Batch Size Cap
- Cap listings scored per cycle at a configurable batch size (default **25**).
- This makes a cost or rate-limit blowup structurally impossible regardless of
  how many unscored listings accumulate.

---

## Aggregate Analysis

### 22 — Deterministic Aggregates Only
- Aggregate analysis is deterministic SQL and Python.
- No LLM call may produce, adjust, or rank an aggregate number.
- A model may only **suggest** canonical groupings for a human to approve.

### 23 — Explicit Skill Alias Map
- Skill names are canonicalised through an explicit alias map in `config.yaml`
  that the user owns and can read.
- Never auto-merge skill names by model judgement or string-similarity alone.

### 24 — Fit-Score-Weighted Gap Ranking
- Gap ranking is weighted by the fit score of the listing the gap came from.
- A gap in a listing scored 20 is worth far less than a gap in a listing
  scored 85.
- Never rank gaps by raw frequency alone.

### 25 — Timestamped Snapshots; Never Overwrite
- Every gap report run writes a timestamped **SNAPSHOT**.
- Never overwrite the previous report.
- Trend over time is a first-class output, not an afterthought.

### 26 — Full Traceability to Source Rows
- Every aggregate number must be traceable to the rows that produced it.
- Any reported gap must be able to list the specific **listing IDs** it was
  computed from.
- No number appears in the dashboard that cannot be drilled into.

### 27 — Report Sample Size Alongside Every Aggregate
- Always report the sample size next to every aggregate figure.
- A gap computed from 3 listings and a gap computed from 90 listings must
  never be presented as equally reliable.


---

## Orchestration

### 28 — State-Driven Execution, Not Fixed Sequence
- The Orchestrator reads system state and decides which agents to run.
- It never runs a fixed sequence.
- Skipping an agent because there is no work for it is a **successful**
  outcome, not a failure.

### 29 — Explicit Goals and Stop Conditions
- Every delegation carries an explicit goal and an explicit stop condition
  (max items, max duration).
- A sub-agent never decides its own limits — the Orchestrator sets them.

### 30 — Orchestrator Does Not Do Agent Work
- The Orchestrator reads state, delegates, collects results, and logs.
- No fetching, scoring, or analysis logic belongs in the Orchestrator.

### 31 — Plan Before Executing
- The Orchestrator prints and logs its **PLAN** before executing it:
  which agents will run, which are skipped, and the state value that
  caused each decision.

### 32 — One Agent Failing Does Not Stop the Cycle
- Log the failure, continue with the remaining plan, and mark the cycle
  **partial**.

### 33 — One Summary Row Per Cycle
- Every cycle writes exactly one summary row: what ran, what was skipped,
  why, duration per agent, and the outcome.

---

## Verification

### 34 — Verifier Judges, Never Repairs
- The Verifier judges output plausibility and **never** repairs, rewrites,
  or adjusts data.
- It returns a verdict and a reason.
- The Orchestrator decides what to do about a failure.

### 35 — Plausibility, Not Correctness
- Verification checks plausibility, never correctness.
- There is no ground truth for a fit score.
- Checks assert properties of the output distribution and shape, not
  the accuracy of any single value.

### 36 — At Most One Retry
- A failed verification triggers at most **one** retry of the failing agent
  with adjusted context.
- After that the cycle is marked "degraded" and stops.
- Never retry in an unbounded loop.

### 37 — Log the Failing Check and Observed Value
- Every verdict is logged to `cycle_log` with the check that failed and
  the observed value that failed it.
- Never log just "failed" — the reason must be specific.

### 38 — Stale Verified Data Beats Fresh Unverified Data
- Only cycles with a passing verdict may be read by the dashboard.
- A failed cycle must never overwrite the last known-good data.
- Stale verified data always beats fresh unverified data.

### 39 — Thresholds in config.yaml, Not in Code
- Verification thresholds live in `config.yaml`, not in code.
- Every threshold must have a comment explaining what failure it is
  designed to catch.
