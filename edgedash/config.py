"""
Load project configuration from config.yaml at the repo root.

All user-specific values (role, city, skills, etc.) live here — never
hardcoded elsewhere in the codebase.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# PyYAML is the one dependency this module needs; stdlib has no YAML parser.
# `pip install pyyaml` — it is tiny, well-maintained, and saves writing one.
import yaml


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class Config:
    target_role: str
    target_city: str
    keywords: list[str]
    my_skills: list[str]
    experience_years: int
    db_path: str
    min_fit_score: int
    sources: list[str]
    use_mock_fetcher: bool


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "target_role": "Software Engineer",
    "target_city": "Bengaluru",
    "keywords": [],
    "my_skills": [],
    "experience_years": 0,
    "db_path": "edgedash.db",
    "min_fit_score": 60,
    "sources": ["arbeitnow"],
    "use_mock_fetcher": False,
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
    # Fall back to the working directory so tests can place config.yaml there.
    return Path(os.getcwd())


def load_config(config_path: str | Path | None = None) -> Config:
    """
    Read config.yaml and return a Config instance.

    Args:
        config_path: explicit path to config.yaml; auto-discovered if None.

    Raises:
        FileNotFoundError: if config.yaml cannot be found.
        KeyError / TypeError: if a present field has the wrong type.
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
        target_city=str(merged["target_city"]),
        keywords=list(merged["keywords"]),
        my_skills=list(merged["my_skills"]),
        experience_years=int(merged["experience_years"]),
        db_path=str(merged["db_path"]),
        min_fit_score=int(merged["min_fit_score"]),
        sources=list(merged["sources"]),
        use_mock_fetcher=bool(merged["use_mock_fetcher"]),
    )
