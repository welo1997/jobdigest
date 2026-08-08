"""Tests for the SmartRecruiters source.

The two that matter are the location ones. `remote_signal` exempts a posting from the
location gate entirely, so a hybrid Berlin role that arrives claiming to be remote is an
office job in the wrong city landing in someone's inbox — the 2026-07-28 failure. This
adapter is the only one that receives "hybrid" as structured data, and the only way it
reaches `geo.work_mode` is through the location text, so that plumbing is pinned here.
"""

from datetime import date, timedelta

from ingestion.sources.smartrecruiters import SmartRecruitersSource

from service import geo


def _posting(**over) -> dict:
    item = {
        "id": "744000000000001",
        "_tenant": "DeliveryHero",
        "name": "Data Engineer",
        "company": {"identifier": "DeliveryHero", "name": "Delivery Hero"},
        "releasedDate": "2026-07-30T13:25:34.283Z",
        "location": {"city": "Berlin", "country": "de", "remote": False,
                     "hybrid": False, "fullLocation": "Berlin, , Germany"},
        "function": {"id": "information_technology", "label": "Information Technology"},
        "postingUrl": "https://jobs.smartrecruiters.com/DeliveryHero/744000000000001",
        "jobAd": {"sections": {
            "jobDescription": {"text": "<p>Build pipelines.</p>"},
            "qualifications": {"text": "<p>SQL.</p>"},
            "companyDescription": {"text": "<p>We deliver food.</p>"},
        }},
    }
    item.update(over)
    return item


def _one(**over):
    return SmartRecruitersSource(tenants=[]).normalize([_posting(**over)])[0]


def test_normalize():
    p = _one()
    assert p.title == "Data Engineer"
    assert p.company == "Delivery Hero"
    assert p.country_code == "DE"
    assert p.posted_at == date(2026, 7, 30)
    assert p.source_category == "software_engineering"
    assert "Build pipelines" in p.description
    # Company boilerplate is kept but last, so a truncated description still leads with the role.
    assert p.description.index("Build pipelines") < p.description.index("We deliver food")


def test_skips_posting_with_no_identifiers():
    assert SmartRecruitersSource(tenants=[]).normalize(
        [{"name": "Ghost", "location": {}}]) == []


def test_url_is_reconstructed_when_the_detail_omits_it():
    p = _one(postingUrl=None, applyUrl=None)
    assert p.url == "https://jobs.smartrecruiters.com/DeliveryHero/744000000000001"


def test_hybrid_flag_survives_into_work_mode():
    """A hybrid ATS flag must end up as `hybrid`, never as remote and never as unknown."""
    p = _one(location={"city": "Berlin", "country": "de", "remote": False,
                       "hybrid": True, "fullLocation": "Berlin, , Germany"})
    assert p.remote_signal is False
    assert geo.work_mode(p.location, p.description, p.remote_signal) == "hybrid"
    # And the city is still readable, because the gate has to know *where* the office is.
    assert geo.resolve_location(p.location, p.country_code) == ("DE", "berlin")


def test_remote_flag_survives_into_work_mode():
    p = _one(location={"city": "Berlin", "country": "de", "remote": True,
                       "hybrid": False, "fullLocation": "Berlin, , Germany"})
    assert p.remote_signal is True
    assert geo.work_mode(p.location, p.description, p.remote_signal) == "remote"


def test_a_hybrid_description_still_overrides_a_remote_flag():
    """The board's flag is a claim. `geo.work_mode` gets the last word, and must keep it."""
    p = _one(
        location={"city": "Berlin", "country": "de", "remote": True,
                  "hybrid": False, "fullLocation": "Berlin, , Germany"},
        jobAd={"sections": {"jobDescription": {
            "text": "We use a hybrid work model of 3 days in the office per week."}}},
    )
    assert p.remote_signal is True
    assert geo.work_mode(p.location, p.description, p.remote_signal) == "hybrid"


def test_stale_postings_are_dropped_before_the_detail_stage():
    old = _posting(releasedDate=(date.today() - timedelta(days=400)).isoformat() + "T00:00:00Z")
    new = _posting(releasedDate=(date.today() - timedelta(days=2)).isoformat() + "T00:00:00Z")
    assert SmartRecruitersSource._is_fresh(new) is True
    assert SmartRecruitersSource._is_fresh(old) is False
    # An unstated date is kept — the same call `resolve_location` makes for unknown cities.
    assert SmartRecruitersSource._is_fresh(_posting(releasedDate=None)) is True


def test_unmapped_function_label_yields_no_hint_rather_than_a_guess():
    """`source_category` becomes `role_category` when the title can't classify — so it has to
    already be a real category.

    An invented value would be a filter matching nothing and reporting no error — the exact
    `social_media_specialist` failure CLAUDE.md records. `None` falls through to
    `uncategorised`, which is a first-class value.

    `taxonomy.classify` now also discards a non-canonical hint, so a raw label leaking through
    here can no longer reach the column. That guard is the backstop, not the design: mapping
    belongs where the source's vocabulary is known, which is this file. Keep returning `None`
    rather than leaning on it — the guard cannot tell "Supply Chain" from a category we forgot.
    """
    p = _one(function={"label": "Supply Chain"})
    assert p.source_category is None
