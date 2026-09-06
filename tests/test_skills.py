"""
Tests for edgedash.skills.canonical().

Every test is deterministic — no network, no model, no filesystem.
The alias map is passed in explicitly so tests are independent of
config.yaml.
"""

from __future__ import annotations

import pytest

from edgedash.skills import canonical


# ---------------------------------------------------------------------------
# Shared fixture — a small but realistic alias map
# ---------------------------------------------------------------------------

ALIASES: dict[str, str] = {
    "k8s":                     "kubernetes",
    "node":                    "node.js",
    "nodejs":                  "node.js",
    "node js":                 "node.js",
    "postgresql":              "postgres",
    "psql":                    "postgres",
    "google cloud":            "gcp",
    "google cloud platform":   "gcp",
    "ml":                      "machine learning",
    "ci cd":                   "ci/cd",
    "cicd":                    "ci/cd",
    "ci-cd":                   "ci/cd",
}


# ---------------------------------------------------------------------------
# Case normalisation
# ---------------------------------------------------------------------------

class TestCaseNormalisation:
    def test_uppercase_is_lowercased(self):
        assert canonical("Python", ALIASES) == "python"

    def test_mixed_case_is_lowercased(self):
        assert canonical("PostgreSQL", ALIASES) == "postgres"

    def test_all_caps_is_lowercased(self):
        assert canonical("SQL", ALIASES) == "sql"


# ---------------------------------------------------------------------------
# Whitespace handling
# ---------------------------------------------------------------------------

class TestWhitespace:
    def test_leading_trailing_whitespace_stripped(self):
        assert canonical("  python  ", ALIASES) == "python"

    def test_internal_multiple_spaces_collapsed(self):
        assert canonical("machine  learning", ALIASES) == "machine learning"

    def test_tab_collapsed_to_space(self):
        assert canonical("machine\tlearning", ALIASES) == "machine learning"

    def test_newline_collapsed_to_space(self):
        assert canonical("machine\nlearning", ALIASES) == "machine learning"

    def test_whitespace_only_returns_empty_string(self):
        assert canonical("   ", ALIASES) == ""

    def test_whitespace_around_alias_key_still_resolves(self):
        # "  node js  " should collapse to "node js" then alias to "node.js"
        assert canonical("  node js  ", ALIASES) == "node.js"


# ---------------------------------------------------------------------------
# Parenthetical qualifier removal
# ---------------------------------------------------------------------------

class TestParentheses:
    def test_trailing_qualifier_dropped(self):
        assert canonical("kubernetes (eks)", ALIASES) == "kubernetes"

    def test_version_qualifier_dropped(self):
        assert canonical("python (3.x)", ALIASES) == "python"

    def test_qualifier_with_extra_space_dropped(self):
        assert canonical("node.js  (lts)", ALIASES) == "node.js"

    def test_qualifier_after_alias_key_resolved(self):
        # Parenthetical is removed first, then alias lookup applies.
        assert canonical("k8s (on-prem)", ALIASES) == "kubernetes"

    def test_no_parens_unchanged(self):
        assert canonical("docker", ALIASES) == "docker"

    def test_internal_parens_not_removed(self):
        # Only a *trailing* parenthetical is stripped; internal ones
        # are uncommon but must not corrupt the string.
        # "(optional) python" has no trailing qualifier — left as-is
        # after punctuation stripping of leading "(".
        result = canonical("python (web) developer", ALIASES)
        # parenthetical is at end of "python (web)" but "developer" follows,
        # so the regex does NOT match — full string is kept intact.
        assert "python" in result


# ---------------------------------------------------------------------------
# Alias map resolution
# ---------------------------------------------------------------------------

class TestAliasResolution:
    def test_known_alias_resolves(self):
        assert canonical("k8s", ALIASES) == "kubernetes"

    def test_alias_key_with_mixed_case_resolves(self):
        assert canonical("K8S", ALIASES) == "kubernetes"

    def test_postgres_variant_psql(self):
        assert canonical("psql", ALIASES) == "postgres"

    def test_postgres_variant_postgresql(self):
        assert canonical("PostgreSQL", ALIASES) == "postgres"

    def test_gcp_long_form(self):
        assert canonical("Google Cloud Platform", ALIASES) == "gcp"

    def test_ml_alias(self):
        assert canonical("ML", ALIASES) == "machine learning"

    def test_cicd_variants(self):
        assert canonical("CI/CD", ALIASES) == "ci/cd"   # already canonical
        assert canonical("cicd",  ALIASES) == "ci/cd"
        assert canonical("ci cd", ALIASES) == "ci/cd"
        assert canonical("ci-cd", ALIASES) == "ci/cd"

    def test_node_variants_stay_separate_from_javascript(self):
        # node → node.js, NOT javascript
        assert canonical("node",   ALIASES) == "node.js"
        assert canonical("nodejs", ALIASES) == "node.js"
        assert canonical("node js", ALIASES) == "node.js"

    def test_javascript_has_no_alias_returns_itself(self):
        # "javascript" is not in the alias map — it should pass through
        assert canonical("javascript", ALIASES) == "javascript"

    def test_javascript_not_confused_with_node(self):
        assert canonical("javascript", ALIASES) != "node.js"


# ---------------------------------------------------------------------------
# Term with no alias — pass-through
# ---------------------------------------------------------------------------

class TestNoAlias:
    def test_unknown_term_returned_normalised(self):
        assert canonical("terraform", ALIASES) == "terraform"

    def test_unknown_term_mixed_case_returned_lower(self):
        assert canonical("Terraform", ALIASES) == "terraform"

    def test_unknown_term_with_whitespace_returned_stripped(self):
        assert canonical("  terraform  ", ALIASES) == "terraform"


# ---------------------------------------------------------------------------
# Empty / blank input
# ---------------------------------------------------------------------------

class TestEmptyInput:
    def test_empty_string_returns_empty(self):
        assert canonical("", ALIASES) == ""

    def test_whitespace_only_returns_empty(self):
        assert canonical("   ", ALIASES) == ""

    def test_punctuation_only_returns_empty(self):
        # A string that is nothing but stripped punctuation
        assert canonical("...", ALIASES) == ""


# ---------------------------------------------------------------------------
# Surrounding punctuation stripping
# ---------------------------------------------------------------------------

class TestSurroundingPunctuation:
    def test_leading_comma_stripped(self):
        assert canonical(",python", ALIASES) == "python"

    def test_trailing_period_stripped(self):
        assert canonical("python.", ALIASES) == "python"

    def test_quoted_skill_stripped(self):
        assert canonical('"python"', ALIASES) == "python"

    def test_punctuation_does_not_break_internal_slash(self):
        # ci/cd has an internal slash that must be preserved
        assert canonical("ci/cd", ALIASES) == "ci/cd"
