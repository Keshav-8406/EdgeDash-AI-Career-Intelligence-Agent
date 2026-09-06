"""
Load project configuration from config.yaml at the repo root.

All user-specific values (role, city, skills, etc.) live here —
never hardcoded elsewhere in the codebase.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class Config:
    target_role: str
    target_seniority: str
    target_city: str
    keywords: list[str]
    my_skills: list[str]
    experience_years: int
    db_path: str
    min_fit_score: int
    sources: list[str]
    use_mock_fetcher: bool

    # LLM configuration
    llm_provider: str
    llm_model: str
    llm_batch_size: int

    # Scoring configuration
    score_batch_size: int

    # Orchestration / planning thresholds
    fetch_interval_hours: int    # re-fetch if hours since last fetch >= this
    fetch_max_pages: int         # stop-condition passed to Fetcher
    fetch_max_listings: int      # stop-condition passed to Fetcher
    score_max_seconds: int       # stop-condition passed to Scorer
    analyse_max_seconds: int     # stop-condition passed to GapAnalyzer

    # Verification thresholds (rule 39)
    min_score_spread: int        # check_score_spread: catches score inflation
    min_score_stdev: float       # check_score_spread: catches score clustering
    max_empty_extraction_pct: float  # check_extraction_sanity: catches broken extractor
    max_skills_per_listing: int  # check_extraction_sanity: catches sentence-as-skill
    min_gap_sample: int          # check_gap_sample_size: catches ranking a rumour
    max_data_age_days: int       # check_freshness: catches stale data


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "target_role": "Software Engineer",
    "target_seniority": "mid",
    "target_city": "Berlin",
    "keywords": [],
    "my_skills": [],
    "experience_years": 0,
    "db_path": "edgedash.db",
    "min_fit_score": 60,
    "sources": ["arbeitnow"],
    "use_mock_fetcher": False,

    # LLM defaults
    "llm_provider": "gemini",
    "llm_model": "gemini-2.0-flash",
    "llm_batch_size": 10,

    # Scoring defaults
    "score_batch_size": 25,

    # Orchestration / planning defaults
    "fetch_interval_hours": 6,
    "fetch_max_pages":      10,
    "fetch_max_listings":   200,
    "score_max_seconds":    300,
    "analyse_max_seconds":  120,

    # Verification defaults (rule 39)
    "min_score_spread":          10,
    "min_score_stdev":            5.0,
    "max_empty_extraction_pct":  20.0,
    "max_skills_per_listing":    20,
    "min_gap_sample":             3,
    "max_data_age_days":          3,
}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _find_repo_root() -> Path:
    """Walk up from this file until we find config.yaml or hit the fs root."""
    current = Path(__file__).resolve().parent

    while True:
        candidate = current / "config.yaml"

        if candidate.exists():
            return current

        parent = current.parent

        if parent == current:
            break

        current = parent

    return Path(os.getcwd())


def load_config(config_path: str | Path | None = None) -> Config:
    """
    Read config.yaml and return a Config instance.

    Args:
        config_path: explicit path to config.yaml; auto-discovered if None.

    Raises:
        FileNotFoundError: if config.yaml cannot be found.
    """

    if config_path is None:
        repo_root = _find_repo_root()
        config_path = repo_root / "config.yaml"

    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at '{config_path}'. "
            "Copy config.yaml.example to config.yaml and fill in your details."
        )

    with config_path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    merged = {**_DEFAULTS, **raw}

    return Config(
        target_role=str(merged["target_role"]),
        target_seniority=str(merged["target_seniority"]),
        target_city=str(merged["target_city"]),
        keywords=list(merged["keywords"]),
        my_skills=list(merged["my_skills"]),
        experience_years=int(merged["experience_years"]),
        db_path=str(merged["db_path"]),
        min_fit_score=int(merged["min_fit_score"]),
        sources=list(merged["sources"]),
        use_mock_fetcher=bool(merged["use_mock_fetcher"]),

        # LLM configuration
        llm_provider=str(merged["llm_provider"]),
        llm_model=str(merged["llm_model"]),
        llm_batch_size=int(merged["llm_batch_size"]),

        # Scoring configuration
        score_batch_size=int(merged["score_batch_size"]),

        # Orchestration / planning thresholds
        fetch_interval_hours=int(merged["fetch_interval_hours"]),
        fetch_max_pages=int(merged["fetch_max_pages"]),
        fetch_max_listings=int(merged["fetch_max_listings"]),
        score_max_seconds=int(merged["score_max_seconds"]),
        analyse_max_seconds=int(merged["analyse_max_seconds"]),

        # Verification thresholds
        min_score_spread=int(merged["min_score_spread"]),
        min_score_stdev=float(merged["min_score_stdev"]),
        max_empty_extraction_pct=float(merged["max_empty_extraction_pct"]),
        max_skills_per_listing=int(merged["max_skills_per_listing"]),
        min_gap_sample=int(merged["min_gap_sample"]),
        max_data_age_days=int(merged["max_data_age_days"]),
    )