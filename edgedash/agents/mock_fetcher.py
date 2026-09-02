"""
MockFetcher — returns 12 realistic fake listings with no network calls.

Deduplication behaviour (the point of this mock):

  Run 1 : 12 offered, 12 new
  Run 2+: 12 offered,  4 new

  - 8 STABLE listings  : hardcoded source + URL → same hash every run.
    INSERT OR IGNORE silently skips all 8 on run 2+.

  - 4 VARYING listings : URL contains a uuid4() generated fresh on every
    call → new hash every run → always inserted as new rows.
    Simulates a live board surfacing fresh results on each poll.
"""

from __future__ import annotations

import uuid

from edgedash.agents.base import AgentResult
from edgedash.config import Config
import edgedash.storage as storage


# ---------------------------------------------------------------------------
# 8 stable listings — URLs never change, hashes never change
# ---------------------------------------------------------------------------

_STABLE: list[dict] = [
    {
        "title": "Data Analyst",
        "company": "Flipkart",
        "location": "Bengaluru, Karnataka",
        "url": "https://careers.flipkart.com/jobs/da-001",
        "source": "mock",
        "description": (
            "Own dashboards in Tableau and Power BI, write complex SQL queries, "
            "and work with Python (Pandas, NumPy) to clean large datasets. "
            "1-3 years experience."
        ),
        "posted_at": "2026-08-25",
    },
    {
        "title": "Senior Data Analyst",
        "company": "Swiggy",
        "location": "Bengaluru, Karnataka",
        "url": "https://careers.swiggy.com/jobs/sda-042",
        "source": "mock",
        "description": (
            "Lead A/B test design and analysis for growth experiments. "
            "Strong SQL, Python, and statistics required. "
            "Apache Spark and dbt a plus. 4+ years."
        ),
        "posted_at": "2026-08-26",
    },
    {
        "title": "Business Intelligence Analyst",
        "company": "Razorpay",
        "location": "Bengaluru, Karnataka",
        "url": "https://razorpay.com/careers/bi-analyst-007",
        "source": "mock",
        "description": (
            "Build and maintain BI reports using Looker and BigQuery. "
            "Strong SQL essential; Python scripting for ETL pipelines. 2-5 years."
        ),
        "posted_at": "2026-08-27",
    },
    {
        "title": "Data Analyst — Growth",
        "company": "Zepto",
        "location": "Bengaluru, Karnataka",
        "url": "https://jobs.zepto.com/da-growth-19",
        "source": "mock",
        "description": (
            "Analyse customer retention and cohort behaviour. "
            "SQL and Excel daily; Python scripting a bonus. 1-2 years."
        ),
        "posted_at": "2026-08-28",
    },
    {
        "title": "Junior Data Analyst",
        "company": "CRED",
        "location": "Bengaluru, Karnataka",
        "url": "https://careers.cred.club/jda-003",
        "source": "mock",
        "description": (
            "Entry-level: data cleaning, Matplotlib/Seaborn visualisation, "
            "ad-hoc SQL reporting. ML pipeline exposure a bonus. 0-1 years."
        ),
        "posted_at": "2026-08-20",
    },
    {
        "title": "Data Analyst — Risk",
        "company": "PhonePe",
        "location": "Bengaluru, Karnataka",
        "url": "https://phonepe.com/careers/risk-analyst-88",
        "source": "mock",
        "description": (
            "Build risk scorecards using logistic regression and decision trees. "
            "SQL and Python mandatory. Airflow for scheduling. 2-4 years fintech."
        ),
        "posted_at": "2026-08-21",
    },
    {
        "title": "Product Analyst",
        "company": "Meesho",
        "location": "Bengaluru, Karnataka",
        "url": "https://meesho.io/careers/product-analyst-11",
        "source": "mock",
        "description": (
            "Define and track product KPIs. Funnel analysis via SQL and Mixpanel. "
            "Tableau dashboards for stakeholders. 2-3 years."
        ),
        "posted_at": "2026-08-22",
    },
    {
        "title": "Data Analyst — Supply Chain",
        "company": "Amazon India",
        "location": "Bengaluru, Karnataka",
        "url": "https://amazon.jobs/en/jobs/supply-chain-da-2026",
        "source": "mock",
        "description": (
            "Optimise last-mile delivery metrics. SQL, Excel, Python automation. "
            "AWS QuickSight preferred. 2-4 years."
        ),
        "posted_at": "2026-08-23",
    },
]


# ---------------------------------------------------------------------------
# 4 varying listing templates — URL gets a fresh uuid4() on every call
# ---------------------------------------------------------------------------

_VARYING_TEMPLATES: list[dict] = [
    {
        "title": "Analytics Engineer",
        "company": "Dunzo",
        "location": "Bengaluru, Karnataka",
        "url_prefix": "https://dunzo.com/jobs/analytics-engineer/",
        "source": "mock",
        "description": (
            "Own the dbt transformation layer on BigQuery. "
            "Modular SQL, data tests, lineage docs. Python for glue. 2-5 years."
        ),
        "posted_at": "2026-08-19",
    },
    {
        "title": "Data Analyst — Marketing",
        "company": "boAt Lifestyle",
        "location": "Bengaluru, Karnataka",
        "url_prefix": "https://boat-lifestyle.com/careers/mkt-analyst/",
        "source": "mock",
        "description": (
            "Attribution modelling, campaign performance, CAC/LTV reporting. "
            "SQL, Python, Google Analytics, Tableau. 1-3 years D2C."
        ),
        "posted_at": "2026-08-18",
    },
    {
        "title": "Senior Analyst — Data Platform",
        "company": "Infosys BPM",
        "location": "Bengaluru, Karnataka",
        "url_prefix": "https://infosysbpm.com/careers/data-platform-sa/",
        "source": "mock",
        "description": (
            "Architect data pipelines with Apache Spark and Hive. "
            "Strong Python and SQL. Mentor juniors. 5+ years; cloud required."
        ),
        "posted_at": "2026-08-17",
    },
    {
        "title": "Data Analyst — Healthcare",
        "company": "Practo",
        "location": "Bengaluru, Karnataka",
        "url_prefix": "https://practo.com/jobs/da-health/",
        "source": "mock",
        "description": (
            "Patient engagement and clinical outcomes analysis. "
            "SQL, Python (Pandas, SciPy), statistics. HIPAA experience a plus. 2-3 years."
        ),
        "posted_at": "2026-08-16",
    },
]


def _build_varying_listings() -> list[dict]:
    """Materialise the 4 varying templates with a fresh uuid4 URL each call."""
    listings = []
    for template in _VARYING_TEMPLATES:
        row = {k: v for k, v in template.items() if k != "url_prefix"}
        row["url"] = template["url_prefix"] + str(uuid.uuid4())
        listings.append(row)
    return listings


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class MockFetcher:
    name: str = "MockFetcher"

    def run(self, config: Config, db_path: str) -> AgentResult:
        try:
            listings = _STABLE + _build_varying_listings()
            new_count = storage.upsert_listings(db_path, listings)
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=new_count,
                notes=f"{len(listings)} listings offered, {new_count} new.",
            )
        except Exception as exc:
            raise RuntimeError(f"MockFetcher failed: {exc}") from exc
