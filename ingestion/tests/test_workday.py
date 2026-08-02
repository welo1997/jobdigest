"""Tests for the Workday source.

The interesting cases are the two places Workday's payload lies about location and remoteness
if taken at face value: `locationsText` says "7 Locations" for a multi-office requisition, and
`remoteType` says "Flexible" for a job that is nothing of the sort.
"""

from datetime import date

from ingestion.sources.workday import WorkdaySource

from service import geo


def _detail(**over) -> dict:
    item = {
        "_tenant": "philips", "_host": "wd3", "_site": "jobs-and-careers",
        "_path": "/job/Eindhoven/Software-Engineer_577694",
        "title": "Software Engineer",
        "jobDescription": "<p>Build imaging software.</p>",
        "location": "Eindhoven",
        "country": {"descriptor": "Netherlands"},
        "startDate": "2026-07-31",
        "remoteType": None,
    }
    item.update(over)
    return item


def _one(**over):
    return WorkdaySource(sites=[], terms=[]).normalize([_detail(**over)])[0]


def test_normalize():
    p = _one()
    assert p.title == "Software Engineer"
    assert p.company == "philips"
    assert p.posted_at == date(2026, 7, 31)
    assert p.url.endswith("/jobs-and-careers/job/Eindhoven/Software-Engineer_577694")


def test_the_country_name_is_appended_so_geo_can_read_it():
    """Workday's `location` is often a bare city; the country arrives as a separate object.

    Deliberately joined into the text rather than mapped to a code here — `geo.COUNTRY_ALIASES`
    is the one table that already knows the country names, in several languages.
    """
    p = _one()
    assert p.location == "Eindhoven, Netherlands"
    assert geo.resolve_location(p.location, p.country_code) == ("NL", "eindhoven")


def test_the_country_is_not_repeated_when_the_location_already_names_it():
    assert _one(location="Israel, Yokneam",
                country={"descriptor": "Israel"}).location == "Israel, Yokneam"


def test_a_missing_location_still_yields_the_country():
    assert _one(location=None, country={"descriptor": "Germany"}).location == "Germany"


def test_flexible_is_not_remote():
    """`remote_signal` exempts a posting from the location gate entirely.

    "Flexible" reads permissive but means the employer decides per hire — not that the role
    can be done from another country. Only an explicit remote claim may set this.
    """
    assert _one(remoteType="Flexible").remote_signal is False
    assert _one(remoteType="Hybrid").remote_signal is False
    assert _one(remoteType="On-site").remote_signal is False
    assert _one(remoteType=None).remote_signal is False
    assert _one(remoteType="Remote").remote_signal is True


def test_a_hybrid_description_still_overrides_a_remote_type():
    p = _one(remoteType="Remote",
             jobDescription="We use a hybrid work model of 3 days in the office per week.")
    assert p.remote_signal is True
    assert geo.work_mode(p.location, p.description, p.remote_signal) == "hybrid"


def test_age_rank_orders_what_survives_the_detail_cap():
    rank = WorkdaySource._age_rank
    assert rank({"postedOn": "Posted Today"}) == 0
    assert rank({"postedOn": "Posted Yesterday"}) == 0
    assert rank({"postedOn": "Posted 5 Days Ago"}) == 5
    assert rank({"postedOn": "Posted 30+ Days Ago"}) == 30
    # Unparseable sorts last rather than first, so garbage never displaces a fresh posting.
    assert rank({"postedOn": "Posted a while back"}) == 9999
    assert rank({}) == 9999


def test_skips_a_record_with_no_path_and_no_url():
    assert WorkdaySource(sites=[], terms=[]).normalize(
        [{"title": "Ghost", "_tenant": "philips"}]) == []
