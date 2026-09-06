"""
Skill canonicalisation — deterministic only.

No LLM call is made anywhere in this file.
Same input always produces the same output.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def canonical(raw: str, aliases: dict[str, str]) -> str:
    """
    Normalise a raw skill string and apply the alias map.

    Steps (in order):
        1. Lowercase and strip leading/trailing whitespace.
        2. Drop a trailing parenthetical qualifier,
           e.g. "kubernetes (eks)" -> "kubernetes".
        3. Remove surrounding punctuation characters.
        4. Collapse internal runs of whitespace to a single space.
        5. Strip again after collapsing.
        6. Look up the result in the alias map; return the canonical
           form if found, otherwise return the normalised string.

    Args:
        raw:     The skill string as it came from the extractor.
        aliases: Mapping of normalised variant -> canonical form.
                 Keys should already be lower-cased and stripped
                 (i.e. the same normalisation applied here).

    Returns:
        The canonical skill name, or the normalised input if no alias
        matches.  Returns "" for blank / whitespace-only input.
    """
    if not raw:
        return ""

    # Step 1 — lowercase + outer whitespace
    text = raw.lower().strip()

    # Step 2 — drop trailing parenthetical qualifier BEFORE stripping
    #   punctuation, so the closing ")" is still present for the regex.
    #   "kubernetes (eks)"    -> "kubernetes"
    #   "python (3.x)"        -> "python"
    #   "node.js (lts)"       -> "node.js"
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()

    # Step 3 — strip surrounding punctuation (but keep internal chars
    #           like "/" which are meaningful, e.g. "ci/cd")
    text = text.strip(".,;:!?\"'`()[]{}\\|")

    # Step 4 & 5 — collapse internal whitespace
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return ""

    # Step 6 — alias lookup
    return aliases.get(text, text)


# ---------------------------------------------------------------------------
# Audit CLI  (python -m edgedash.skills --audit)
# ---------------------------------------------------------------------------

_TOP_N = 40
_SEPARATOR = "-" * 72


def _load_aliases_from_config() -> dict[str, str]:
    """
    Read skill_aliases from config.yaml without importing the full
    Config dataclass (keeps this module dependency-free at import time).
    """
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        print("ERROR: PyYAML is required for --audit.  pip install pyyaml")
        sys.exit(1)

    # Walk up to find config.yaml next to the repo root.
    here = Path(__file__).resolve().parent
    candidates = [here.parent / "config.yaml", Path("config.yaml")]

    for candidate in candidates:
        if candidate.exists():
            with candidate.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            return {
                str(k).lower().strip(): str(v).lower().strip()
                for k, v in (data.get("skill_aliases") or {}).items()
            }

    print("WARNING: config.yaml not found — alias map will be empty.")
    return {}


def _load_db_path() -> str:
    """Return db_path from config.yaml, defaulting to edgedash.db."""
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        return "edgedash.db"

    here = Path(__file__).resolve().parent
    candidates = [here.parent / "config.yaml", Path("config.yaml")]

    for candidate in candidates:
        if candidate.exists():
            with candidate.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            return str(data.get("db_path", "edgedash.db"))

    return "edgedash.db"


def _read_raw_skills(db_path: str) -> list[str]:
    """
    Pull every required_skill value stored in extraction_cache.

    The extractor stores facts_json with a "required_skills" list.
    We unpack every element and return them as a flat list of raw strings.

    Read-only — no writes, no schema changes.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            "SELECT facts_json FROM extraction_cache"
        ).fetchall()
    finally:
        conn.close()

    import json

    skills: list[str] = []
    for row in rows:
        try:
            facts = json.loads(row["facts_json"])
        except (ValueError, KeyError):
            continue

        for s in facts.get("required_skills") or []:
            if isinstance(s, str) and s.strip():
                skills.append(s.strip())

    return skills


def _run_audit() -> None:
    """
    Print an audit report to stdout.

    Sections:
        A) Top-40 raw skill strings with count and canonical form.
        B) Singletons — raw strings seen exactly once (likely noise).
    """
    aliases = _load_aliases_from_config()
    db_path = _load_db_path()

    # Resolve db path relative to repo root when run from anywhere.
    db_file = Path(db_path)
    if not db_file.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent
        db_file = repo_root / db_path

    if not db_file.exists():
        print(f"ERROR: database not found at '{db_file}'")
        sys.exit(1)

    raw_skills = _read_raw_skills(str(db_file))

    if not raw_skills:
        print("No extracted skills found in extraction_cache.")
        return

    counts: Counter[str] = Counter(raw_skills)
    total_raw = len(raw_skills)
    unique_raw = len(counts)

    print()
    print("EDGEDASH — SKILL AUDIT REPORT")
    print(_SEPARATOR)
    print(f"Total skill occurrences : {total_raw}")
    print(f"Unique raw strings      : {unique_raw}")
    print(f"Alias map size          : {len(aliases)} entries")
    print(_SEPARATOR)

    # ---- Section A: top-N raw strings ------------------------------------
    print(f"\nTOP {_TOP_N} RAW SKILL STRINGS")
    print(f"{'COUNT':>6}  {'RAW SKILL':<40}  CANONICAL FORM")
    print(f"{'------':>6}  {'-' * 40}  {'-' * 30}")

    for raw, count in counts.most_common(_TOP_N):
        canon = canonical(raw, aliases)
        # Mark with * if alias actually changed the value
        changed = "→ " if canon != raw.lower().strip() else "  "
        print(f"{count:>6}  {raw:<40}  {changed}{canon}")

    # ---- Section B: singletons -------------------------------------------
    singletons = sorted(
        raw for raw, count in counts.items() if count == 1
    )

    print()
    print(_SEPARATOR)
    print(f"SINGLETONS  ({len(singletons)} raw strings seen exactly once)")
    print("These are candidates for typos, junk, or full sentences")
    print("mistakenly captured as a skill by the extractor.")
    print(_SEPARATOR)

    if singletons:
        for s in singletons:
            canon = canonical(s, aliases)
            changed = f"  → {canon}" if canon != s.lower().strip() else ""
            print(f"  {s}{changed}")
    else:
        print("  (none)")

    print()


# ---------------------------------------------------------------------------
# Alias suggestions  (deterministic, no LLM, no network)
# ---------------------------------------------------------------------------

# Noise suffixes that commonly appear after a real skill name and
# add no information.  Order matters for multi-word suffixes: longer
# ones are checked first so "sql skills" beats "skills" alone.
_NOISE_SUFFIXES: tuple[str, ...] = (
    "programming language",
    "programming languages",
    "programming",
    "development",
    "engineering",
    "proficiency",
    "technologies",
    "technology",
    "knowledge",
    "experience",
    "practices",
    "concepts",
    "skills",
    "tools",
    "skill",
    "tool",
)


class AliasSuggestion:
    """One suggested alias pair with a deterministic explanation."""

    __slots__ = ("raw", "canonical_form", "reason", "raw_count", "canonical_count")

    def __init__(
        self,
        raw: str,
        canonical_form: str,
        reason: str,
        raw_count: int,
        canonical_count: int,
    ) -> None:
        self.raw            = raw
        self.canonical_form = canonical_form
        self.reason         = reason
        self.raw_count      = raw_count
        self.canonical_count = canonical_count

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"AliasSuggestion({self.raw!r} -> {self.canonical_form!r}, "
            f"reason={self.reason!r})"
        )


def suggest_aliases(
    counts: dict[str, int],
    existing_aliases: dict[str, str],
) -> list[AliasSuggestion]:
    """
    Analyse a {raw_skill: count} frequency map and return a list of
    suggested alias pairs.

    Deterministic — same inputs always produce the same output in the
    same order.  No LLM, no network, no randomness.

    Three passes are run in order; a pair is emitted at most once
    (the first pass that detects it wins):

    Pass 1 — Plural/singular
        If raw string B == raw string A + "s" and both exist in counts,
        suggest B -> A.  Catches "cloud platforms" / "cloud platform".

    Pass 2 — Noise suffix stripping
        If raw string ends with a known noise word (e.g. "skills",
        "programming", "development") and the base without that suffix
        also exists in counts, suggest raw -> base.
        Catches "sql skills" -> "sql", "python programming" -> "python".

    Pass 3 — Whole-word prefix containment
        If normalised string A is a strict whole-word prefix of
        normalised string B (i.e. B starts with A + " "), suggest B -> A.
        The shorter string is treated as canonical.
        Catches "machine learning" / "machine learning engineer".

    Pairs are suppressed when:
        - raw == canonical_form (already the same after normalisation)
        - raw is already a key in existing_aliases
        - canonical_form does not exist as a raw skill in counts
          (we never suggest mapping to something we haven't seen)
        - raw_count == 0

    Results are sorted by raw_count descending so the most-frequent
    fragmentation appears first.

    Args:
        counts:           {normalised_raw_skill: occurrence_count}
        existing_aliases: The current alias map (normalised keys).

    Returns:
        List of AliasSuggestion, sorted by raw_count descending.
    """
    suggestions: dict[str, AliasSuggestion] = {}   # raw -> suggestion

    already_aliased: set[str] = set(existing_aliases.keys())

    # Pre-sort skills by length then alphabetically for determinism
    # across all three passes.
    skills_sorted: list[str] = sorted(counts.keys(), key=lambda s: (len(s), s))

    def _emit(
        raw: str,
        canonical_form: str,
        reason: str,
    ) -> None:
        """Record a suggestion, suppressing duplicates and no-ops."""
        if raw == canonical_form:
            return
        if raw in already_aliased:
            return
        if canonical_form not in counts:
            return
        if raw in suggestions:
            return   # first pass wins
        suggestions[raw] = AliasSuggestion(
            raw=raw,
            canonical_form=canonical_form,
            reason=reason,
            raw_count=counts[raw],
            canonical_count=counts[canonical_form],
        )

    # ------------------------------------------------------------------
    # Pass 1 — Plural/singular  (trailing "s")
    # ------------------------------------------------------------------
    for skill in skills_sorted:
        if skill.endswith("s") and len(skill) > 2:
            base = skill[:-1]
            if base in counts:
                _emit(
                    skill,
                    base,
                    f"plural form: '{skill}' ends with 's' and '{base}' also exists",
                )

    # ------------------------------------------------------------------
    # Pass 2 — Noise suffix stripping
    # ------------------------------------------------------------------
    for skill in skills_sorted:
        for suffix in _NOISE_SUFFIXES:
            if skill.endswith(" " + suffix) and len(skill) > len(suffix) + 1:
                base = skill[: -(len(suffix) + 1)].strip()
                if base and base in counts:
                    _emit(
                        skill,
                        base,
                        f"noise suffix: '{suffix}' stripped from '{skill}'",
                    )
                    break   # only one suffix match per skill

    # ------------------------------------------------------------------
    # Pass 3 — Whole-word prefix containment
    # ------------------------------------------------------------------
    for short in skills_sorted:
        prefix = short + " "
        for long_ in skills_sorted:
            if long_ == short:
                continue
            if long_.startswith(prefix):
                _emit(
                    long_,
                    short,
                    f"prefix match: '{short}' is a leading word of '{long_}'",
                )

    # Sort by raw_count descending, then raw alphabetically for stability.
    return sorted(
        suggestions.values(),
        key=lambda s: (-s.raw_count, s.raw),
    )


def _run_suggest_aliases() -> None:
    """
    Print alias suggestions to stdout.

    Reads raw skill counts from the database (same source as --audit).
    Read-only — no writes, no config.yaml modifications.
    """
    aliases = _load_aliases_from_config()
    db_path = _load_db_path()

    db_file = Path(db_path)
    if not db_file.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent
        db_file = repo_root / db_path

    if not db_file.exists():
        print(f"ERROR: database not found at '{db_file}'")
        sys.exit(1)

    raw_skills = _read_raw_skills(str(db_file))

    if not raw_skills:
        print("No extracted skills found in extraction_cache.")
        return

    # Normalise counts the same way as --audit (lowercase + strip,
    # no alias applied — we want raw fragmentation, not post-alias).
    normalised: list[str] = [s.lower().strip() for s in raw_skills if s.strip()]
    counts: Counter[str] = Counter(normalised)

    suggestions = suggest_aliases(dict(counts), aliases)

    print()
    print("EDGEDASH — ALIAS SUGGESTIONS")
    print("=" * 72)
    print("  !! SUGGESTIONS ONLY — nothing will be changed automatically. !!")
    print("  Review each pair and decide manually whether to add it to")
    print("  the skill_aliases section of config.yaml.")
    print("=" * 72)
    print()

    if not suggestions:
        print("  No fragmentation patterns detected in current data.")
        print("  Your alias map appears to cover the visible collisions.")
        print()
        return

    print(
        f"  {'RAW FORM':<35}  {'SUGGESTS CANONICAL':<30}  "
        f"{'RAW_N':>5}  {'CAN_N':>5}  REASON"
    )
    print("  " + "-" * 110)

    for s in suggestions:
        print(
            f"  {s.raw:<35}  {s.canonical_form:<30}  "
            f"{s.raw_count:>5}  {s.canonical_count:>5}  {s.reason}"
        )

    print()
    print(
        f"  {len(suggestions)} suggestion(s)  ·  "
        "RAW_N = occurrences of the raw form  ·  "
        "CAN_N = occurrences of the canonical form"
    )
    print()
    print("  To add an alias, edit skill_aliases in config.yaml, e.g.:")
    print('      "raw form": "canonical form"')
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--audit" in sys.argv:
        _run_audit()
    elif "--suggest-aliases" in sys.argv:
        _run_suggest_aliases()
    else:
        print("Usage: python -m edgedash.skills --audit")
        print("       python -m edgedash.skills --suggest-aliases")
        sys.exit(1)
