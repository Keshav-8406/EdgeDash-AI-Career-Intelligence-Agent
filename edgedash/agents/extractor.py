"""
EdgeDash job-description extractor.

The extractor uses the configured LLM only to extract facts explicitly
stated in a job listing. It does not evaluate candidate fit or calculate
scores.

Extraction results are cached by a stable SHA-256 hash of the job
description so the same description is never sent to the LLM twice.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..llm import LLMError, complete_json
from ..storage import get_extraction_cache, save_extraction_cache


# ---------------------------------------------------------------------------
# Extraction schema
# ---------------------------------------------------------------------------

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "required_skills",
        "nice_to_have",
        "seniority",
        "years_required",
        "remote_ok",
    ],
    "properties": {
        "required_skills": {
            "type": "array",
        },
        "nice_to_have": {
            "type": "array",
        },
        "seniority": {
            "type": "string",
            "enum": [
                "junior",
                "mid",
                "senior",
                "lead",
                "unknown",
            ],
        },
        "years_required": {
            "type": ["integer", "null"],
        },
        "remote_ok": {
            "type": ["boolean", "null"],
        },
    },
}


# ---------------------------------------------------------------------------
# Description hashing
# ---------------------------------------------------------------------------

def description_hash(description: str | None) -> str:
    """
    Return a stable SHA-256 hash for a job description.

    Empty or missing descriptions are represented by an empty string.
    """
    normalized = (description or "").strip()

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize extracted facts into the stable EdgeDash format.

    Skills are converted to lowercase and surrounding whitespace is removed.
    """
    required_skills = facts.get("required_skills", [])
    nice_to_have = facts.get("nice_to_have", [])

    if not isinstance(required_skills, list):
        raise ValueError("required_skills must be a list")

    if not isinstance(nice_to_have, list):
        raise ValueError("nice_to_have must be a list")

    normalized_required = [
        str(skill).strip().lower()
        for skill in required_skills
        if str(skill).strip()
    ]

    normalized_nice = [
        str(skill).strip().lower()
        for skill in nice_to_have
        if str(skill).strip()
    ]

    seniority = facts.get("seniority", "unknown")

    if seniority not in {
        "junior",
        "mid",
        "senior",
        "lead",
        "unknown",
    }:
        raise ValueError(
            "seniority must be junior, mid, senior, lead, or unknown"
        )

    years_required = facts.get("years_required")

    if years_required is not None:
        if (
            not isinstance(years_required, int)
            or isinstance(years_required, bool)
        ):
            raise ValueError(
                "years_required must be an integer or null"
            )

        if years_required < 0:
            raise ValueError(
                "years_required cannot be negative"
            )

    remote_ok = facts.get("remote_ok")

    if remote_ok is not None and not isinstance(remote_ok, bool):
        raise ValueError(
            "remote_ok must be boolean or null"
        )

    return {
        "required_skills": normalized_required,
        "nice_to_have": normalized_nice,
        "seniority": seniority,
        "years_required": years_required,
        "remote_ok": remote_ok,
    }


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _build_prompt(description: str) -> str:
    """
    Build a fact-extraction-only prompt.

    The model is explicitly prohibited from evaluating the candidate.
    """
    return f"""
Extract only facts explicitly stated in the job listing below.

Do NOT:
- infer missing requirements
- guess years of experience
- evaluate a candidate
- compare the listing with a candidate
- calculate a score
- rank the job
- mention candidate skills
- make assumptions about remote work
- invent information

Rules:
- required_skills: skills explicitly required by the listing
- nice_to_have: skills explicitly described as preferred, optional,
  bonus, or nice-to-have
- seniority: choose only from junior, mid, senior, lead, unknown
- years_required: integer only when the listing explicitly states a
  number of years; otherwise null
- remote_ok: true only when remote work is explicitly allowed;
  false only when the listing explicitly says remote work is not allowed;
  otherwise null
- If a fact is not stated, use the appropriate null/unknown value.
- Return ONLY JSON matching the requested schema.

JOB LISTING:
{description}
""".strip()


# ---------------------------------------------------------------------------
# Public extractor
# ---------------------------------------------------------------------------

def extract(
    listing: dict[str, Any],
    *,
    config: Any,
) -> dict[str, Any]:
    """
    Extract structured facts from a listing.

    Cache is checked first using the SHA-256 hash of the job description.
    The LLM is called only when no cached extraction exists.

    Args:
        listing: Listing dictionary containing at least "description".
        config: EdgeDash configuration.

    Returns:
        Normalized extraction facts.

    Raises:
        LLMError: if the LLM request fails.
        ValueError: if extracted facts are invalid.
    """
    description = listing.get("description") or ""
    description = str(description).strip()

    cache_key = description_hash(description)

    # ---------------------------------------------------------------
    # Cache-first behavior
    # ---------------------------------------------------------------

    cached = get_extraction_cache(
        config.db_path,
        cache_key,
    )

    if cached is not None:
        return _normalize_facts(cached)

    # ---------------------------------------------------------------
    # Empty description
    # ---------------------------------------------------------------

    if not description:
        facts = {
            "required_skills": [],
            "nice_to_have": [],
            "seniority": "unknown",
            "years_required": None,
            "remote_ok": None,
        }

        save_extraction_cache(
            config.db_path,
            cache_key,
            facts,
        )

        return facts

    # ---------------------------------------------------------------
    # LLM extraction
    # ---------------------------------------------------------------

    prompt = _build_prompt(description)

    facts = complete_json(
        prompt,
        EXTRACTION_SCHEMA,
        config=config,
        max_retries=1,
    )

    if not isinstance(facts, dict):
        raise ValueError("Extractor response must be a JSON object")

    normalized = _normalize_facts(facts)

    # ---------------------------------------------------------------
    # Save only validated/normalized facts
    # ---------------------------------------------------------------

    save_extraction_cache(
        config.db_path,
        cache_key,
        normalized,
    )

    return normalized