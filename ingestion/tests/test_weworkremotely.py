"""Tests for WeWorkRemotely source."""

from ingestion.sources.weworkremotely import WeWorkRemotelySource


SAMPLE_ENTRIES = [
    {
        "link": "https://weworkremotely.com/listings/acme-senior-backend-dev",
        "title": "Acme: Senior Backend Developer",
        "summary": "<p>Build APIs with Python.</p>",
        "published_parsed": (2026, 4, 15, 10, 0, 0, 0, 0, 0),
    },
    {
        "link": "https://weworkremotely.com/listings/widget-designer",
        "title": "Widget Inc: UX Designer",
        "summary": "<p>Design things.</p>",
        "published_parsed": (2026, 4, 14, 8, 0, 0, 0, 0, 0),
    },
]


def test_normalize():
    source = WeWorkRemotelySource()
    postings = source.normalize(SAMPLE_ENTRIES)
    assert len(postings) == 2
    assert postings[0].company == "Acme"
    assert postings[0].remote_signal is True


def test_company_extraction():
    assert WeWorkRemotelySource._extract_company("Acme: Data Eng") == "Acme"
    assert WeWorkRemotelySource._extract_company("No colon here") is None
