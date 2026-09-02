"""
ArbeitnowSource — fetches jobs from the free Arbeitnow public API.

API docs: https://www.arbeitnow.com/api/job-board-api

No API key or signup required.

Pagination:
  Pages up to MAX_PAGES. Stops early if a page returns no keyword matches,
  so we don't burn requests on irrelevant pages once the signal dies.

Filtering (steering rule 12-style resilience applied internally):
  1. Keep listings whose title or description contains at least one config
     keyword (case-insensitive).
  2. Of those, keep listings whose location contains config.target_city.
  3. If step 2 leaves fewer than MIN_RESULTS, relax the city filter and
     log that we did — returning all keyword-matched listings instead.

Rate limiting (steering rule 14): 1-second sleep between pages.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from edgedash.config import Config
from edgedash.sources.base import register
from edgedash.sources.http import SourceError, get_json

logger = logging.getLogger(__name__)

_API_BASE   = "https://www.arbeitnow.com/api/job-board-api"
_MAX_PAGES  = 5
_MIN_RESULTS = 5
_PAGE_DELAY  = 1.0   # seconds between page requests (rule 14)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _matches_keywords(listing: dict, keywords: list[str]) -> bool:
    """True if any keyword appears in the listing title or description."""
    text = (
        (listing.get("title") or "")
        + " "
        + (listing.get("description") or "")
    ).lower()
    return any(kw.lower() in text for kw in keywords)


def _matches_city(listing: dict, city: str) -> bool:
    """True if the listing location contains the target city."""
    location = (listing.get("location") or "").lower()
    return city.lower() in location


def _normalise(listing: dict) -> dict:
    """Map an Arbeitnow listing dict to the EdgeDash normalised contract."""
    slug = listing.get("slug") or ""

    # Convert Unix timestamp to ISO-8601 date string.
    posted_at: str | None = None
    raw_ts = listing.get("created_at")
    if raw_ts:
        try:
            posted_at = datetime.fromtimestamp(
                int(raw_ts), tz=timezone.utc
            ).date().isoformat()
        except (ValueError, OSError):
            posted_at = None

    return {
        "source":       "arbeitnow",
        "external_id":  slug,          # stable slug, not a hash (rule 10)
        "title":        listing.get("title") or None,
        "company":      listing.get("company_name") or None,
        "location":     listing.get("location") or None,
        "url":          listing.get("url") or None,
        "description":  listing.get("description") or None,
        "posted_at":    posted_at,
        "raw":          listing,       # full original payload preserved
    }


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

@register
class ArbeitnowSource:
    name: str = "arbeitnow"

    def fetch(self, config: Config) -> list[dict]:
        """
        Fetch and filter job listings from Arbeitnow.

        Returns a list of normalised dicts (steering rule 10 contract).
        """
        if not config.keywords:
            logger.warning(
                "ArbeitnowSource: config.keywords is empty; "
                "all listings will be returned unfiltered."
            )

        keyword_matched: list[dict] = []
        pages_fetched = 0

        for page in range(1, _MAX_PAGES + 1):
            try:
                data = get_json(_API_BASE, params={"page": page})
            except SourceError as exc:
                logger.error("ArbeitnowSource: page %d failed — %s", page, exc)
                break

            raw_listings: list[dict] = data.get("data", [])
            if not raw_listings:
                logger.info(
                    "ArbeitnowSource: page %d returned no listings; stopping.",
                    page,
                )
                break

            pages_fetched += 1
            page_matches = [l for l in raw_listings if _matches_keywords(l, config.keywords)]
            keyword_matched.extend(page_matches)

            logger.info(
                "ArbeitnowSource: page %d — %d raw, %d keyword-matched so far.",
                page, len(raw_listings), len(keyword_matched),
            )

            # Stop paging when a page has no keyword matches — the API sorts
            # by recency, so further pages are unlikely to be more relevant.
            if not page_matches:
                logger.info(
                    "ArbeitnowSource: no keyword matches on page %d; stopping early.",
                    page,
                )
                break

            # Check if the API signals there is a next page.
            links = data.get("links", {})
            if not links.get("next"):
                break

            if page < _MAX_PAGES:
                time.sleep(_PAGE_DELAY)

        logger.info(
            "ArbeitnowSource: fetched %d page(s), %d keyword-matched listings total.",
            pages_fetched, len(keyword_matched),
        )

        # --- City filter (relax if too few results) --------------------------
        city_filtered = [
            l for l in keyword_matched
            if _matches_city(l, config.target_city)
        ]

        if len(city_filtered) < _MIN_RESULTS and len(city_filtered) < len(keyword_matched):
            logger.info(
                "ArbeitnowSource: city filter ('%s') left %d result(s), "
                "below threshold of %d. Relaxing location filter — "
                "returning all %d keyword-matched listings instead.",
                config.target_city,
                len(city_filtered),
                _MIN_RESULTS,
                len(keyword_matched),
            )
            final = keyword_matched
        else:
            final = city_filtered

        normalised = [_normalise(l) for l in final]

        print(
            f"  ArbeitnowSource: {len(keyword_matched)} raw keyword matches "
            f"→ {len(normalised)} after location filter "
            f"({'relaxed' if final is keyword_matched else config.target_city})."
        )

        return normalised
