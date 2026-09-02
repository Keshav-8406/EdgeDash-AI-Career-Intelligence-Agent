"""
Source protocol and registry for EdgeDash job sources.

Every external job board sits behind a class that satisfies the Source
protocol. Adding a new source requires only:

    1. Create a module in edgedash/sources/
    2. Decorate the class with @register

Nothing else needs editing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from edgedash.config import Config


# ---------------------------------------------------------------------------
# Normalised row contract (steering rule 10)
# ---------------------------------------------------------------------------
# Every Source.fetch() returns a list of dicts with EXACTLY these keys.
# Missing values are None — never empty string, never "N/A".

REQUIRED_KEYS: frozenset[str] = frozenset({
    "source",
    "external_id",
    "title",
    "company",
    "location",
    "url",
    "description",
    "posted_at",
    "raw",
})


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Source(Protocol):
    """
    Anything with a name and a fetch() method is a valid source.

    fetch() must return a list of normalised dicts containing exactly the
    keys in REQUIRED_KEYS. Missing values must be None.
    """

    name: str

    def fetch(self, config: Config) -> list[dict]:
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SOURCES: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    """Class decorator that registers a Source in the global registry."""
    SOURCES[cls.name] = cls
    return cls
