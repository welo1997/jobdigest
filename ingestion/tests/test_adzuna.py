"""Tests for Adzuna source."""

from ingestion.sources.adzuna import AdzunaSource


SAMPLE_RESULTS = [
    {
        "id": "abc123",
        "redirect_url": "https://adzuna.com/jobs/abc123",
        "title": "Data Analyst",
        "company": {"display_name": "DataCo"},
        "description": "SQL and Python required.",
        "location": {"display_name": "Warsaw"},
        "salary_min": 50000,
        "salary_max": 70000,
        "created": "2026-04-15T12:00:00Z",
        "_country": "pl",
    },
    {
        "id": "def456",
        "redirect_url": "https://adzuna.com/jobs/def456",
        "title": "Product Manager",
        "company": {"display_name": "StartupX"},
        "description": "Lead product strategy.",
        "location": {"display_name": "Berlin"},
        "salary_min": None,
        "salary_max": None,
        "created": "2026-04-14T09:00:00Z",
        "_country": "de",
    },
    {
        # Missing redirect_url — should be skipped
        "id": "ghi789",
        "title": "Ghost",
    },
]


def test_normalize_produces_correct_postings():
    source = AdzunaSource(app_id="test", api_key="test")
    postings = source.normalize(SAMPLE_RESULTS)

    assert len(postings) == 2

    p1 = postings[0]
    assert p1.source == "adzuna"
    assert p1.title == "Data Analyst"
    assert p1.company == "DataCo"
    assert p1.country_code == "PL"
    assert p1.salary_raw == "50000 - 70000"
    assert p1.currency == "PLN"
    assert p1.remote_signal is None

    p2 = postings[1]
    assert p2.salary_raw is None
    assert p2.currency == "EUR"
    assert p2.country_code == "DE"


def test_normalize_skips_entries_without_url():
    source = AdzunaSource(app_id="test", api_key="test")
    postings = source.normalize([{"id": "nope", "title": "No URL"}])
    assert len(postings) == 0


def test_currency_mapping():
    source = AdzunaSource(app_id="test", api_key="test")
    assert source._currency_for_country("gb") == "GBP"
    assert source._currency_for_country("us") == "USD"
    assert source._currency_for_country("xx") is None
