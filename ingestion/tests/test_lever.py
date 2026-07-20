"""Tests for Lever source."""

from ingestion.sources.lever import LeverSource


SAMPLE_POSTINGS = [
    {
        "text": "Senior Software Engineer",
        "hostedUrl": "https://jobs.lever.co/netlify/abc-123",
        "descriptionPlain": "Build the web.",
        "categories": {"location": "Remote, US"},
        "createdAt": 1744700000000,  # epoch ms
        "_org": "netlify",
    },
]


def test_normalize():
    source = LeverSource(orgs=[])
    postings = source.normalize(SAMPLE_POSTINGS)
    assert len(postings) == 1
    assert postings[0].title == "Senior Software Engineer"
    assert postings[0].company == "netlify"
    assert postings[0].location == "Remote, US"
    assert postings[0].posted_at is not None


def test_skips_no_url():
    source = LeverSource(orgs=[])
    postings = source.normalize([{"text": "Ghost"}])
    assert len(postings) == 0
