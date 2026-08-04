"""Workable: the country comes from the posting, and remote is the employer's own flag.

This adapter exists to carry Slovak inventory (see the module docstring), and Slovakia is
where a country mistake would be least visible — there are only ~29 Slovak postings in the
whole corpus, so a handful mislabelled `CZ` would not look wrong in any aggregate.
"""

from __future__ import annotations

from ingestion.sources.workable import ACCOUNTS, WorkableSource, _country, _text


def _job(**over) -> dict:
    base = {
        "title": "Backend Software Engineer (Python)",
        "url": "https://apply.workable.com/j/21789749FF",
        "shortlink": "https://apply.workable.com/j/21789749FF",
        "description": "<p></p><h3>About the Role</h3><p>Senior Python Developer.</p>",
        "published_on": "2026-02-17",
        "created_at": "2026-02-12",
        "telecommuting": False,
        "city": "Bratislava",
        "country": "Slovakia",
        "locations": [{"country": "Slovakia", "countryCode": "SK", "city": "Bratislava"}],
        "department": "Solution Teams",
        "_account": "innovatrics",
        "_account_name": "Innovatrics",
    }
    base.update(over)
    return base


def test_country_comes_from_the_posting():
    """One account posts in several countries — Innovatrics hires in Bratislava *and* Brno."""
    rows = WorkableSource().normalize([
        _job(),
        _job(city="Brno", country="Czechia",
             locations=[{"country": "Czechia", "countryCode": "CZ", "city": "Brno"}]),
    ])
    assert [r.country_code for r in rows] == ["SK", "CZ"]


def test_missing_or_malformed_country_is_none_not_a_guess():
    """Unknown country is kept and left to the matcher; inventing one would exclude wrongly."""
    assert _country(_job(locations=[])) is None
    assert _country(_job(locations=[{"country": "Slovakia"}])) is None
    assert _country(_job(locations=[{"countryCode": "Slovakia"}])) is None


def test_remote_signal_follows_the_employer_flag():
    assert WorkableSource().normalize([_job(telecommuting=True)])[0].remote_signal is True
    assert WorkableSource().normalize([_job(telecommuting=False)])[0].remote_signal is False
    assert WorkableSource().normalize([_job(telecommuting=None)])[0].remote_signal is False


def test_description_is_plain_text():
    p = WorkableSource().normalize([_job()])[0]
    assert "<p>" not in p.description and "<h3>" not in p.description
    assert "Senior Python Developer." in p.description


def test_company_prefers_the_account_display_name():
    assert WorkableSource().normalize([_job()])[0].company == "Innovatrics"
    assert WorkableSource().normalize([_job(_account_name=None)])[0].company == "innovatrics"


def test_posted_at_prefers_published_over_created():
    assert WorkableSource().normalize([_job()])[0].posted_at.isoformat() == "2026-02-17"
    assert WorkableSource().normalize([_job(published_on=None)])[0].posted_at.isoformat() \
        == "2026-02-12"


def test_job_without_url_or_title_is_skipped():
    assert WorkableSource().normalize([_job(url=None, shortlink=None), _job(title=" ")]) == []


def test_accounts_are_lowercase_and_unique():
    assert ACCOUNTS == [a.lower() for a in ACCOUNTS]
    assert len(ACCOUNTS) == len(set(ACCOUNTS))


def test_text_helper_handles_empty():
    assert _text(None) is None
    assert _text("<p></p>") is None
