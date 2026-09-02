"""
Shared HTTP helper for all EdgeDash sources (steering rule 11).

Rules enforced here:
  - 10-second timeout on every request.
  - 2 retries with exponential back-off (1 s → 2 s).
  - A descriptive User-Agent header.
  - No bare requests.get() anywhere else in the project.
"""

from __future__ import annotations

import time
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class SourceError(RuntimeError):
    """Raised when an HTTP request fails after all retry attempts."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_USER_AGENT = "EdgeDash/1.0 (career-intelligence-agent; contact: user@edgedash.local)"
_TIMEOUT    = 10          # seconds
_MAX_TRIES  = 3           # 1 initial attempt + 2 retries
_BACKOFF    = 1.0         # seconds; doubled on each retry


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """
    Perform a GET request and return the parsed JSON body.

    Args:
        url:     Full URL to request.
        params:  Optional query-string parameters.
        headers: Optional extra headers merged with the default set.

    Returns:
        Parsed JSON (dict, list, or scalar).

    Raises:
        SourceError: If every attempt fails (network error or non-2xx status).
    """
    base_headers: dict[str, str] = {"User-Agent": _USER_AGENT}
    if headers:
        base_headers.update(headers)

    delay = _BACKOFF
    last_error: Exception | None = None

    for attempt in range(1, _MAX_TRIES + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=base_headers,
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt < _MAX_TRIES:
                time.sleep(delay)
                delay *= 2   # exponential back-off

    raise SourceError(
        f"GET {url} failed after {_MAX_TRIES} attempts: {last_error}"
    ) from last_error
