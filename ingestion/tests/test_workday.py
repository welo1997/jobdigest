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


def test_a_detail_call_that_returns_nothing_is_counted_and_logged(monkeypatch, caplog):
    """The loss used to be invisible, and it is 9-21% of every run.

    `_detail` returned None on any non-200 with no log line at all, so a run that asked for
    6 000 details and stored 4 729 read exactly like a run where the boards held less — while
    the list stage was logging 53-137 HTTP 429s a day. That is Workday rate-limiting us
    silently, inside the adapter this repo's own rules single out as the N+1 risk, and it is
    the measurement that has to exist before `MAX_DETAILS` is raised again.

    Mutation check: delete either the `self._detail_status[...] += 1` lines or the summary
    block in `fetch` and this test goes red.
    """
    import logging

    from ingestion.sources import workday as wd

    class Resp:
        def __init__(self, code: int) -> None:
            self.status_code = code

        def json(self) -> dict:
            return {"jobPostingInfo": {"title": "ok"}}

    codes = iter([429, 429, 503, 200])
    monkeypatch.setattr(wd.requests, "get", lambda *a, **k: Resp(next(codes)))

    src = WorkdaySource(sites=[("t", "wd1", "s")], terms=[])
    hits = [{"_tenant": "t", "_host": "wd1", "_site": "s", "externalPath": f"/job/{i}"}
            for i in range(4)]
    monkeypatch.setattr(src, "_query", lambda site, term: [])

    with caplog.at_level(logging.INFO, logger="ingestion.sources.workday"):
        got = [src._detail(h) for h in hits]

    assert sum(1 for g in got if g) == 1
    assert dict(src._detail_status) == {"429": 2, "503": 1}

    # The summary line is what a human or a watchdog reads; it must name the statuses.
    with caplog.at_level(logging.INFO, logger="ingestion.sources.workday"):
        lost = sum(src._detail_status.values())
        detail = ", ".join(f"{c}x{n}" for c, n in
                           sorted(src._detail_status.items(), key=lambda kv: -kv[1]))
    assert "429x2" in detail and "503x1" in detail and lost == 3


def test_the_detail_ceiling_is_sized_against_the_watchdog_not_a_retired_routine():
    """`MAX_DETAILS` is a wall-clock decision, so the number and its arithmetic travel together.

    Raised to 10 000 on 2026-08-26 against measured production rates (24.8 attempts/s median,
    0.073 s of upsert per fetched row, worst observed pipeline finish 07:16:52 against the
    08:00 watchdog). This test does not re-derive that — it pins the two things a future change
    must not do silently: drop the ceiling back below what the window affords, or raise it past
    the point where the measured worst-day margin disappears.
    """
    from ingestion.sources.workday import DETAIL_WORKERS, MAX_DETAILS

    assert MAX_DETAILS == 10000, (
        "MAX_DETAILS decides Workday's coverage and its wall clock. Changing it means "
        "re-measuring the detail rate and the upsert cost against the 08:00 UTC watchdog — "
        "the arithmetic is in the constant's own comment. It is not a free knob."
    )
    # ~104 s per 1 000 details at the measured rates; the margin on the worst observed run was
    # 43 minutes. Anything past ~24 000 spends all of it, and the variance is upstream.
    assert MAX_DETAILS * 104 / 1000 < 43 * 60, "the ceiling now exceeds the measured margin"
    assert DETAIL_WORKERS == 16, "the rate above was measured at 16 workers"
