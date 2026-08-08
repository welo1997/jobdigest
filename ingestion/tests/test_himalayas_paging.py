"""Himalayas pages by what the server returns, not by what we asked for.

This is a regression test for a bug that was invisible from the outside for the life of the
adapter. Himalayas caps a response at 20 jobs however large a `limit` you send. The loop
advanced `offset` by the *requested* PAGE_SIZE (100) and stopped on `len(jobs) < PAGE_SIZE`
— so the very first response, 20 against a request for 100, ended the loop every time.

Two failures in one, and neither raises: only one page was ever fetched, and had it
continued it would have skipped 80 jobs between each offset. 20 postings is a perfectly
plausible number for a remote board, so nothing looked wrong. Fixing it took the source from
20 to 300 postings.
"""

from __future__ import annotations

import ingestion.sources.himalayas as himalayas
from ingestion.sources.himalayas import HimalayasSource

SERVER_PAGE = 20  # what Himalayas actually returns, regardless of `limit`


class _Resp:
    def __init__(self, jobs):
        self._jobs = jobs

    def raise_for_status(self):
        pass

    def json(self):
        return {"jobs": self._jobs}


def _fake_api(total: int, calls: list[dict]):
    """A server holding `total` jobs that never returns more than SERVER_PAGE at a time."""
    def get(url, params=None, **kwargs):
        params = params or {}
        offset = int(params.get("offset", 0))
        calls.append(params)
        page = [{"title": f"job-{i}", "applicationLink": f"https://x/{i}"}
                for i in range(offset, min(offset + SERVER_PAGE, total))]
        return _Resp(page)
    return get


def test_it_keeps_paging_past_the_first_short_response(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(himalayas.requests, "get", _fake_api(total=200, calls=calls))
    jobs = HimalayasSource().fetch()
    assert len(jobs) == 200, "stopped early — a short page is not the end of the results"
    assert len(calls) > 1


def test_it_advances_by_what_it_received_so_no_job_is_skipped(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(himalayas.requests, "get", _fake_api(total=200, calls=calls))
    jobs = HimalayasSource().fetch()
    titles = [j["title"] for j in jobs]
    assert titles == sorted(titles, key=lambda t: int(t.split("-")[1]))
    assert len(set(titles)) == len(titles), "an offset stride mismatch duplicated results"
    assert [c["offset"] for c in calls][:4] == [0, 20, 40, 60]


def test_it_still_stops_when_the_results_run_out(monkeypatch):
    """Emptiness ends the loop — a *short* page must not, which is the whole bug.

    The cost is one extra request to discover the end (20 + 20 + 5, then an empty fourth).
    That is the deliberate trade: the old code used a short page as the stop signal, and the
    very first page is always short.
    """
    calls: list[dict] = []
    monkeypatch.setattr(himalayas.requests, "get", _fake_api(total=45, calls=calls))
    assert len(HimalayasSource().fetch()) == 45
    assert [c["offset"] for c in calls] == [0, 20, 40, 45]


def test_max_pages_still_bounds_the_loop(monkeypatch):
    """The stop condition moved; the ceiling must not have moved with it."""
    calls: list[dict] = []
    monkeypatch.setattr(himalayas.requests, "get", _fake_api(total=10_000, calls=calls))
    jobs = HimalayasSource().fetch()
    assert len(calls) == himalayas.MAX_PAGES
    assert len(jobs) == himalayas.MAX_PAGES * SERVER_PAGE


# --- the feed sometimes serves the field name instead of the value -------------------

def test_the_literal_string_name_is_not_an_employer():
    """Measured 2026-08-08: `?limit=100&offset=0` — the exact request this adapter makes —
    returned `companyName: "name"` for all 20 rows, repeatably, from a 66-minute-old CDN
    cache, while other parameter combinations returned real employers at the same moment.

    Storing it would have put "name" in subscribers' digests as the employer, and nothing
    downstream could tell: the count, the titles and the links were all correct."""
    p = HimalayasSource().normalize([
        {"applicationLink": "https://x/1", "title": "Data Engineer", "companyName": "name"}])[0]
    assert p.company is None
    assert p.title == "Data Engineer", "only the employer is in doubt, not the posting"


def test_a_real_employer_survives_untouched():
    p = HimalayasSource().normalize([
        {"applicationLink": "https://x/1", "title": "Data Engineer",
         "companyName": " Zayo Group "}])[0]
    assert p.company == "Zayo Group"


def test_dropping_the_employer_is_the_safe_direction_for_dedupe():
    """`digest.dedupe_key` treats an empty key as always unique, never a match — so a missing
    employer suppresses nothing. A wrong *uniform* one is what does damage: it makes every
    Himalayas posting look like one company, so two employers advertising the same role in
    the same city collapse and the second is never emailed."""
    from service.digest import dedupe_key

    assert dedupe_key(None, "Data Engineer") == "", (
        "an employerless posting must produce an empty (never-matching) key")

    assert dedupe_key("name", "Data Engineer") == dedupe_key("name", "Data Engineer") != "", (
        "two different employers under the placeholder collapse into one key — the damage "
        "this guard prevents")


def test_a_run_where_every_posting_shares_one_employer_is_shouted_about(caplog):
    """The placeholder is the case we know. The next degraded payload will pick some other
    string, and the shape — a multi-employer board reporting one employer — is what to watch."""
    items = [{"applicationLink": f"https://x/{i}", "title": f"Role {i}",
              "companyName": "Definitely Ltd"} for i in range(12)]
    with caplog.at_level("ERROR"):
        HimalayasSource().normalize(items)
    assert any("one employer" in r.message for r in caplog.records)
