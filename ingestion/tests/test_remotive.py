"""Tests for Remotive source."""

from ingestion.sources.remotive import RemotiveSource


SAMPLE_RESPONSE = {
    "jobs": [
        {
            "id": 1,
            "url": "https://remotive.com/jobs/1",
            "title": "Senior Data Engineer",
            "company_name": "Acme Corp",
            "description": "<p>Build data pipelines with dbt and Snowflake.</p>",
            "candidate_required_location": "Worldwide",
            "publication_date": "2026-04-15T10:00:00",
            "salary": "80000 - 120000 USD",
        },
        {
            "id": 2,
            "url": "https://remotive.com/jobs/2",
            "title": "Frontend Developer",
            "company_name": "Widget Inc",
            "description": "<p>React and TypeScript.</p>",
            "candidate_required_location": "Europe",
            "publication_date": "2026-04-14T08:00:00",
            "salary": None,
        },
        {
            # Missing URL — should be skipped
            "id": 3,
            "title": "Ghost Job",
            "company_name": "Nobody",
        },
    ]
}


def test_normalize_produces_correct_postings():
    source = RemotiveSource()
    postings = source.normalize(SAMPLE_RESPONSE["jobs"])

    assert len(postings) == 2

    p1 = postings[0]
    assert p1.source == "remotive"
    assert p1.title == "Senior Data Engineer"
    assert p1.company == "Acme Corp"
    assert p1.remote_signal is True
    assert p1.salary_raw == "80000 - 120000 USD"
    assert p1.posted_at is not None
    assert p1.posting_id  # non-empty hash

    p2 = postings[1]
    assert p2.salary_raw is None
    assert p2.location == "Europe"


def test_normalize_skips_entries_without_url():
    source = RemotiveSource()
    postings = source.normalize([{"id": 99, "title": "No URL"}])
    assert len(postings) == 0


def test_posting_id_is_deterministic():
    source = RemotiveSource()
    postings_a = source.normalize(SAMPLE_RESPONSE["jobs"])
    postings_b = source.normalize(SAMPLE_RESPONSE["jobs"])
    assert postings_a[0].posting_id == postings_b[0].posting_id


def test_as_tuple_length():
    source = RemotiveSource()
    postings = source.normalize(SAMPLE_RESPONSE["jobs"])
    assert len(postings[0].as_tuple()) == 12
