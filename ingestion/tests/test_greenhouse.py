"""Tests for Greenhouse source."""

from ingestion.sources.greenhouse import GreenhouseSource


SAMPLE_JOBS = [
    {
        "title": "Data Engineer",
        "absolute_url": "https://boards.greenhouse.io/stripe/jobs/123",
        "content": "<p>Build pipelines.</p>",
        "location": {"name": "Remote"},
        "updated_at": "2026-04-15T10:00:00Z",
        "_board_token": "stripe",
    },
    {
        "title": "Product Manager",
        "absolute_url": "https://boards.greenhouse.io/stripe/jobs/456",
        "content": "<p>Lead product.</p>",
        "location": {"name": "San Francisco, CA"},
        "updated_at": "2026-04-14",
        "_board_token": "stripe",
    },
]


def test_normalize():
    source = GreenhouseSource(board_tokens=[])
    postings = source.normalize(SAMPLE_JOBS)
    assert len(postings) == 2
    assert postings[0].source == "greenhouse"
    assert postings[0].company == "stripe"
    assert postings[0].location == "Remote"


def test_skips_no_url():
    source = GreenhouseSource(board_tokens=[])
    postings = source.normalize([{"title": "Ghost"}])
    assert len(postings) == 0
