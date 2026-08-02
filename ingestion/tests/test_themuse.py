"""Tests for The Muse source.

Two guarantees, both of which this adapter would otherwise break quietly:

  - **stale listings never enter the store.** The Muse does not expire postings, and
    `store.query_shortlist_meta` orders by `first_seen_at`, so a 2025 listing ingested today
    sorts to the top of the freshest bucket and is emailed as new. Nothing downstream can
    tell; this filter is the only thing standing between the two.
  - **"Flexible / Remote" alongside three US cities is not a remote job.** `remote_signal`
    exempts a posting from the location gate outright.
"""

from datetime import date, datetime, timedelta, timezone

from ingestion.sources.themuse import TheMuseSource


def _fresh_iso(days_ago: int = 3) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace(
        "+00:00", "Z")


def _item(**over) -> dict:
    item = {
        "id": 21916588,
        "name": "Data Analyst",
        "publication_date": _fresh_iso(),
        "locations": [{"name": "Berlin, Germany"}],
        "categories": [{"name": "Data and Analytics"}],
        "company": {"name": "Celonis", "short_name": "celonis"},
        "contents": "<p>Build dashboards.</p>",
        "refs": {"landing_page": "https://www.themuse.com/jobs/celonis/data-analyst-abc"},
    }
    item.update(over)
    return item


def _source() -> TheMuseSource:
    return TheMuseSource(categories={"Data and Analytics": "data_analysis"}, locations=[])


def _one(**over):
    return _source().normalize([_item(**over)])[0]


def test_normalize():
    p = _one()
    assert p.title == "Data Analyst"
    assert p.company == "Celonis"
    assert p.location == "Berlin, Germany"
    assert p.source_category == "data_analysis"
    assert p.country_code is None  # left for geo.resolve_location, which reads the prose
    assert p.posted_at is not None


def test_us_state_codes_resolve_to_a_country():
    """"Austin, TX" names no country, and an unknown country is kept by the location gate.

    Left unresolved, every US posting enters every EU subscriber's shortlist to be rejected
    by the matcher — 345 of 475 postings in a full fetch on 2026-08-01.
    """
    assert _one(locations=[{"name": "Austin, TX"}]).country_code == "US"
    assert _one(locations=[{"name": "Austin, TX"},
                           {"name": "Boise, ID"}]).country_code == "US"
    # Remote alongside US offices is still a US job; remote alone names no country.
    assert _one(locations=[{"name": "Austin, TX"},
                           {"name": "Flexible / Remote"}]).country_code == "US"
    assert _one(locations=[{"name": "Flexible / Remote"}]).country_code is None


def test_a_mixed_location_list_claims_no_country():
    """An explicit country_code *wins* over the text in `geo.resolve_location`.

    So claiming "US" for "Austin, TX; London" would erase the London half and hide the role
    from the subscriber it actually fits — the Arbeitnow hardcoded-DE failure in miniature.
    """
    assert _one(locations=[{"name": "Austin, TX"},
                           {"name": "London, United Kingdom"}]).country_code is None
    assert _one(locations=[{"name": "Berlin, Germany"}]).country_code is None


def test_skips_item_with_no_landing_page():
    assert _source().normalize([_item(refs={})]) == []


def test_multiple_locations_are_all_kept():
    p = _one(locations=[{"name": "Austin, TX"}, {"name": "Boise, ID"}])
    assert p.location == "Austin, TX; Boise, ID"


def test_remote_only_when_remote_is_the_whole_location():
    assert _one(locations=[{"name": "Flexible / Remote"}]).remote_signal is True
    # An office job that also offers remote to some hires is not one a subscriber elsewhere
    # can take. Reading this as remote would exempt it from the location gate.
    assert _one(locations=[{"name": "Austin, TX"},
                           {"name": "Flexible / Remote"}]).remote_signal is False
    assert _one(locations=[]).remote_signal is False


def test_stale_listings_are_dropped():
    src = _source()
    assert src._is_fresh(_item(publication_date=_fresh_iso(3))) is True
    assert src._is_fresh(_item(publication_date="2025-03-05T13:10:16Z")) is False


def test_an_undated_listing_is_dropped_rather_than_kept():
    """The opposite call to the ATS adapters, and deliberately so.

    Elsewhere an unknown value is kept and left to the AI matcher. Here the corpus is known
    to hold multi-year-old rows and the date is the only thing that distinguishes them, so
    "no date" is not an unknown to be resolved later — it is the filter being bypassed.
    """
    assert _source()._is_fresh(_item(publication_date=None)) is False


def test_unmapped_category_yields_no_hint():
    """`source_category` becomes `role_category` verbatim when the title cannot classify."""
    p = _one(name="Coordinator", categories=[{"name": "Science and Engineering"}])
    assert p.source_category is None


def test_parse_date_returns_utc_calendar_date():
    assert TheMuseSource._parse_date("2026-07-14T19:22:35Z") == date(2026, 7, 14)
    assert TheMuseSource._parse_date("nonsense") is None
    assert TheMuseSource._parse_date(None) is None
