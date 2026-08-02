"""Tests for Adzuna source."""

import pytest
import requests

from ingestion.sources.adzuna import (
    COUNTRY_CONFIG, DAILY_REQUEST_BUDGET, SEARCH_TERMS,
    AdzunaAuthError, AdzunaSource,
)


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


def test_every_configured_country_has_a_currency():
    """A country added to COUNTRY_CONFIG without a currency stores amounts with no unit."""
    source = AdzunaSource(app_id="test", api_key="test")
    missing = [c for c, _ in COUNTRY_CONFIG if source._currency_for_country(c) is None]
    assert missing == []


def test_a_run_stays_inside_the_free_tier_quota():
    """The cost is a product of two lists, so it grows faster than either edit looks.

    Adzuna allows 250 calls/day per app id and answers an overrun with an HTTP 429 that
    `_fetch_page` swallows into an empty list — so busting the quota does not fail, it just
    makes the source quietly stop halfway through. This is where that arithmetic is checked.
    """
    planned = sum(pages for _, pages in COUNTRY_CONFIG) * len(SEARCH_TERMS)
    assert planned <= DAILY_REQUEST_BUDGET, (
        f"an Adzuna run now costs {planned} calls, over the {DAILY_REQUEST_BUDGET} budget; "
        "drop a country's page count or a search term"
    )


def test_rejected_credentials_raise_instead_of_returning_nothing(monkeypatch):
    """A dead key must not look like a quiet day on the job market.

    Adzuna's credentials were failing AUTH_FAIL on every country on 2026-08-01 and the only
    trace was a per-page warning; `fetch` returned an empty list and the run reported success.
    """
    class _Resp:
        status_code = 401

        def json(self):  # pragma: no cover - never reached
            return {}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    with pytest.raises(AdzunaAuthError):
        AdzunaSource(app_id="test", api_key="test").fetch()


def test_a_transient_page_failure_is_still_skipped(monkeypatch):
    """The other half of the same behaviour: one flaky page must not stop the source."""
    def _boom(*a, **k):
        raise requests.ConnectionError("reset by peer")

    monkeypatch.setattr(requests, "get", _boom)
    assert AdzunaSource(app_id="test", api_key="test").fetch() == []
