from types import SimpleNamespace
from datetime import datetime, timezone, timedelta

from edgedash.scoring import score_listing


def make_config():
    return SimpleNamespace(
        my_skills=["Python", "SQL", "Excel", "Pandas"],
        target_seniority="mid",
        target_city="Berlin",
    )


def test_exact_seniority_and_matching_skills():
    config = make_config()

    listing = {
        "location": "Berlin",
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }

    facts = {
        "required_skills": ["Python", "SQL"],
        "nice_to_have": ["Excel"],
        "seniority": "mid",
        "remote_ok": False,
    }

    result = score_listing(listing, facts, config)

    assert result["score"] >= 80
    assert result["components"]["seniority_fit"] == 1.0


def test_one_band_seniority_difference():
    config = make_config()

    listing = {
        "location": "Berlin",
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }

    facts = {
        "required_skills": ["Python", "SQL"],
        "nice_to_have": [],
        "seniority": "senior",
        "remote_ok": False,
    }

    result = score_listing(listing, facts, config)

    assert result["components"]["seniority_fit"] == 0.6


def test_missing_required_skill():
    config = make_config()

    listing = {
        "location": "Berlin",
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }

    facts = {
        "required_skills": ["Java"],
        "nice_to_have": [],
        "seniority": "senior",
        "remote_ok": False,
    }

    result = score_listing(listing, facts, config)

    assert result["score"] < 60
    assert "java" in result["reason"].lower()


def test_remote_listing_gets_full_location_fit():
    config = make_config()

    listing = {
        "location": "Munich",
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }

    facts = {
        "required_skills": ["Python"],
        "nice_to_have": [],
        "seniority": "mid",
        "remote_ok": True,
    }

    result = score_listing(listing, facts, config)

    assert result["components"]["location_fit"] == 1.0


def test_old_elsewhere_listing_scores_low():
    config = make_config()

    old_date = (
        datetime.now(timezone.utc) - timedelta(days=31)
    ).isoformat()

    listing = {
        "location": "London",
        "posted_at": old_date,
    }

    facts = {
        "required_skills": ["Python", "SQL"],
        "nice_to_have": [],
        "seniority": "junior",
        "remote_ok": False,
    }

    result = score_listing(listing, facts, config)

    assert result["components"]["location_fit"] == 0.1
    assert result["components"]["recency"] == 0.0
    assert result["score"] < 60


def test_unknown_seniority_and_missing_date():
    config = make_config()

    listing = {
        "location": "Berlin",
        "posted_at": None,
    }

    facts = {
        "required_skills": ["Python"],
        "nice_to_have": [],
        "seniority": "unknown",
        "remote_ok": None,
    }

    result = score_listing(listing, facts, config)

    assert result["components"]["seniority_fit"] == 0.5
    assert result["components"]["recency"] == 0.5
    assert 0 <= result["score"] <= 100