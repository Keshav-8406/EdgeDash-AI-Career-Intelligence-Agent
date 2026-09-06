"""
Tests for edgedash.skills.suggest_aliases().

All tests are pure — no database, no filesystem, no network.
The function under test is a deterministic transformation of a
{skill: count} dict and an existing alias map.
"""

from __future__ import annotations

import pytest

from edgedash.skills import AliasSuggestion, suggest_aliases


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raws(suggestions: list[AliasSuggestion]) -> list[str]:
    """Return just the raw forms from a suggestion list."""
    return [s.raw for s in suggestions]


def _pair(suggestions: list[AliasSuggestion]) -> list[tuple[str, str]]:
    """Return (raw, canonical_form) pairs from a suggestion list."""
    return [(s.raw, s.canonical_form) for s in suggestions]


# ---------------------------------------------------------------------------
# Pass 1 — Plural / singular
# ---------------------------------------------------------------------------

class TestPluralSingular:
    def test_simple_plural_detected(self):
        counts = {"platform": 5, "platforms": 3}
        result = suggest_aliases(counts, {})
        assert ("platforms", "platform") in _pair(result)

    def test_reason_says_plural_form(self):
        counts = {"platform": 5, "platforms": 3}
        result = suggest_aliases(counts, {})
        match = next(s for s in result if s.raw == "platforms")
        assert "plural form" in match.reason
        assert "platforms" in match.reason
        assert "platform" in match.reason

    def test_counts_attached_correctly(self):
        counts = {"platform": 5, "platforms": 3}
        result = suggest_aliases(counts, {})
        match = next(s for s in result if s.raw == "platforms")
        assert match.raw_count == 3
        assert match.canonical_count == 5

    def test_singular_not_suggested_as_alias_of_plural(self):
        # "platform" -> "platforms" should never be suggested
        counts = {"platform": 5, "platforms": 3}
        result = suggest_aliases(counts, {})
        assert ("platform", "platforms") not in _pair(result)

    def test_plural_without_singular_not_suggested(self):
        # "platforms" exists but "platform" doesn't — no suggestion
        counts = {"platforms": 3, "tools": 2}
        result = suggest_aliases(counts, {})
        raws = _raws(result)
        assert "platforms" not in raws

    def test_two_char_word_not_matched(self):
        # "as" ends with "s" but len == 2 — guard clause skips it
        counts = {"as": 4, "a": 10}
        result = suggest_aliases(counts, {})
        assert "as" not in _raws(result)

    def test_multi_word_plural_detected(self):
        counts = {"cloud platform": 8, "cloud platforms": 2}
        result = suggest_aliases(counts, {})
        assert ("cloud platforms", "cloud platform") in _pair(result)


# ---------------------------------------------------------------------------
# Pass 2 — Noise suffix stripping
# ---------------------------------------------------------------------------

class TestNoiseSuffix:
    def test_skills_suffix_stripped(self):
        counts = {"sql": 10, "sql skills": 4}
        result = suggest_aliases(counts, {})
        assert ("sql skills", "sql") in _pair(result)

    def test_programming_suffix_stripped(self):
        counts = {"python": 15, "python programming": 3}
        result = suggest_aliases(counts, {})
        assert ("python programming", "python") in _pair(result)

    def test_development_suffix_stripped(self):
        counts = {"software": 8, "software development": 5}
        result = suggest_aliases(counts, {})
        assert ("software development", "software") in _pair(result)

    def test_reason_names_the_suffix(self):
        counts = {"sql": 10, "sql skills": 4}
        result = suggest_aliases(counts, {})
        match = next(s for s in result if s.raw == "sql skills")
        assert "noise suffix" in match.reason
        assert "skills" in match.reason

    def test_base_must_exist_in_counts(self):
        # "java programming" -> "java", but "java" not in counts
        counts = {"java programming": 3}
        result = suggest_aliases(counts, {})
        assert "java programming" not in _raws(result)

    def test_only_one_suffix_match_per_skill(self):
        # "python programming skills" ends with both "skills" and
        # "programming skills" — only the first matching suffix fires
        counts = {"python": 10, "programming": 5, "python programming": 3}
        result = suggest_aliases(counts, {})
        python_prog = [s for s in result if s.raw == "python programming"]
        assert len(python_prog) == 1

    def test_knowledge_suffix_stripped(self):
        counts = {"docker": 7, "docker knowledge": 2}
        result = suggest_aliases(counts, {})
        assert ("docker knowledge", "docker") in _pair(result)

    def test_experience_suffix_stripped(self):
        counts = {"kubernetes": 6, "kubernetes experience": 2}
        result = suggest_aliases(counts, {})
        assert ("kubernetes experience", "kubernetes") in _pair(result)


# ---------------------------------------------------------------------------
# Pass 3 — Whole-word prefix containment
# ---------------------------------------------------------------------------

class TestPrefixContainment:
    def test_simple_prefix_detected(self):
        counts = {"machine learning": 12, "machine learning engineer": 3}
        result = suggest_aliases(counts, {})
        assert ("machine learning engineer", "machine learning") in _pair(result)

    def test_reason_says_prefix_match(self):
        counts = {"machine learning": 12, "machine learning engineer": 3}
        result = suggest_aliases(counts, {})
        match = next(s for s in result if s.raw == "machine learning engineer")
        assert "prefix match" in match.reason
        assert "machine learning" in match.reason

    def test_partial_word_not_matched(self):
        # "data" is NOT a whole-word prefix of "database" — no space boundary
        counts = {"data": 10, "database": 8}
        result = suggest_aliases(counts, {})
        assert ("database", "data") not in _pair(result)

    def test_short_not_suggested_as_alias_of_long(self):
        # Direction is long -> short, never short -> long
        counts = {"machine learning": 12, "machine learning engineer": 3}
        result = suggest_aliases(counts, {})
        assert ("machine learning", "machine learning engineer") not in _pair(result)

    def test_multi_prefix_all_suggested(self):
        # Both "cloud provider" and "cloud platform" start with "cloud"
        counts = {"cloud": 20, "cloud provider": 4, "cloud platform": 3}
        result = suggest_aliases(counts, {})
        pairs = _pair(result)
        assert ("cloud provider", "cloud") in pairs
        assert ("cloud platform", "cloud") in pairs

    def test_canonical_must_exist_in_counts(self):
        # "machine learning engineer" has "machine learning" as prefix,
        # but "machine learning" isn't in counts — no suggestion
        counts = {"machine learning engineer": 3}
        result = suggest_aliases(counts, {})
        assert "machine learning engineer" not in _raws(result)


# ---------------------------------------------------------------------------
# Suppression rules
# ---------------------------------------------------------------------------

class TestSuppression:
    def test_already_aliased_raw_suppressed(self):
        counts = {"sql": 10, "sql skills": 4}
        existing = {"sql skills": "sql"}
        result = suggest_aliases(counts, existing)
        assert "sql skills" not in _raws(result)

    def test_raw_equals_canonical_suppressed(self):
        # Shouldn't happen in practice but guard is there
        counts = {"python": 10, "pythons": 3}
        # Force canonical == raw by making base same
        result = suggest_aliases(counts, {})
        # "pythons" -> "python" is valid; "python" -> "python" must not appear
        assert ("python", "python") not in _pair(result)

    def test_canonical_not_in_counts_suppressed(self):
        counts = {"machine learning engineer": 3}
        result = suggest_aliases(counts, {})
        # "machine learning" is not in counts — must not be suggested
        assert "machine learning engineer" not in _raws(result)


# ---------------------------------------------------------------------------
# First-pass-wins (no duplicate suggestions for the same raw)
# ---------------------------------------------------------------------------

class TestFirstPassWins:
    def test_plural_wins_over_noise_suffix(self):
        # "tools" ends with "s" (pass 1) AND could be seen as noise suffix
        # "tool" (pass 1 detects it first as plural of "tool")
        counts = {"tool": 5, "tools": 3}
        result = suggest_aliases(counts, {})
        match = next((s for s in result if s.raw == "tools"), None)
        assert match is not None
        assert "plural form" in match.reason   # pass 1, not pass 2

    def test_same_raw_appears_at_most_once(self):
        counts = {"python": 15, "python programming": 4}
        result = suggest_aliases(counts, {})
        raws = _raws(result)
        assert raws.count("python programming") == 1


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

class TestSorting:
    def test_higher_raw_count_comes_first(self):
        counts = {
            "docker": 10,
            "kubernetes": 20,
            "docker containers": 2,
            "kubernetes cluster": 5,
        }
        result = suggest_aliases(counts, {})
        # "kubernetes cluster" (raw_count=5) should precede
        # "docker containers" (raw_count=2)
        raws = _raws(result)
        assert raws.index("kubernetes cluster") < raws.index("docker containers")

    def test_equal_count_sorted_alphabetically(self):
        counts = {
            "sql": 10,
            "python": 10,
            "sql skills": 3,
            "python skills": 3,
        }
        result = suggest_aliases(counts, {})
        raws = _raws(result)
        # Both have raw_count=3; "python skills" < "sql skills" alphabetically
        assert raws.index("python skills") < raws.index("sql skills")


# ---------------------------------------------------------------------------
# Empty / trivial inputs
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_counts_returns_empty(self):
        assert suggest_aliases({}, {}) == []

    def test_single_skill_returns_empty(self):
        assert suggest_aliases({"python": 5}, {}) == []

    def test_no_fragmentation_returns_empty(self):
        counts = {"python": 5, "docker": 3, "kubernetes": 7}
        assert suggest_aliases(counts, {}) == []

    def test_all_already_aliased_returns_empty(self):
        counts = {"sql": 10, "sql skills": 4}
        existing = {"sql skills": "sql"}
        result = suggest_aliases(counts, existing)
        assert result == []

    def test_result_is_list_of_alias_suggestion(self):
        counts = {"python": 5, "python programming": 2}
        result = suggest_aliases(counts, {})
        assert all(isinstance(s, AliasSuggestion) for s in result)

    def test_alias_suggestion_fields_present(self):
        counts = {"python": 5, "python programming": 2}
        result = suggest_aliases(counts, {})
        s = result[0]
        assert s.raw == "python programming"
        assert s.canonical_form == "python"
        assert isinstance(s.reason, str) and s.reason
        assert s.raw_count == 2
        assert s.canonical_count == 5

    def test_deterministic_same_output_twice(self):
        counts = {"python": 5, "python programming": 2,
                  "docker": 8, "docker containers": 3}
        r1 = suggest_aliases(counts, {})
        r2 = suggest_aliases(counts, {})
        assert _pair(r1) == _pair(r2)
