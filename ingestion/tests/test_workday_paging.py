"""Workday's pager never says "done", so the adapter has to work it out.

Measured 2026-08-04 against the live API: `philips` holds 1 017 postings and answered 4 000
hits for a single search term, stopping only when the measuring harness did. Across its nine
terms it returned 9 304 rows of which **708 were distinct** — a 92% duplicate rate. `nvidia`
was 68%.

`len(hits) < PAGE_SIZE` therefore almost never fires, and before this the page ceiling was
not a runaway guard but the thing deciding how much of each board we saw. Raising it bought
duplicate requests roughly as fast as it bought postings.
"""

from __future__ import annotations

import ingestion.sources.workday as wd
from ingestion.sources.workday import WorkdaySource

SITE = ("acme", "wd3", "Careers")


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _looping_api(real: int, calls: list[int]):
    """A board with `real` postings whose pager wraps round instead of ending."""
    def post(url, headers=None, timeout=None, json=None, **kwargs):
        offset = int((json or {}).get("offset", 0))
        calls.append(offset)
        page = [{"externalPath": f"/job/{(offset + i) % real}", "postedOn": "Posted Today"}
                for i in range(wd.PAGE_SIZE)]
        return _Resp({"jobPostings": page})
    return post


def test_a_page_with_nothing_new_ends_the_query(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(wd.requests, "post", _looping_api(real=45, calls=calls))
    hits = WorkdaySource(sites=[SITE], terms=["x"])._query(SITE, "x")
    paths = [h["externalPath"] for h in hits]
    assert len(set(paths)) == len(paths) == 45, "the same posting was returned twice"
    # 45 real postings at 20 a page: three pages reach them all, the third already repeating,
    # and the fourth is what proves there is nothing left.
    assert len(calls) <= 4, f"kept paging a board that had run out ({len(calls)} requests)"


def test_the_ceiling_still_bounds_a_board_that_never_repeats(monkeypatch):
    """The stop condition must not replace the guard — a big board still has to be capped."""
    calls: list[int] = []
    monkeypatch.setattr(wd.requests, "post", _looping_api(real=100_000, calls=calls))
    hits = WorkdaySource(sites=[SITE], terms=["x"])._query(SITE, "x")
    assert len(calls) == wd.MAX_PAGES_PER_QUERY
    assert len(hits) == wd.MAX_PAGES_PER_QUERY * wd.PAGE_SIZE


def test_a_short_page_still_ends_the_query(monkeypatch):
    """The original stop condition is kept: some sites do end their results properly."""
    calls: list[int] = []

    def post(url, headers=None, timeout=None, json=None, **kwargs):
        offset = int((json or {}).get("offset", 0))
        calls.append(offset)
        remaining = max(0, 30 - offset)
        return _Resp({"jobPostings": [{"externalPath": f"/job/{offset + i}",
                                       "postedOn": "Posted Today"}
                                      for i in range(min(wd.PAGE_SIZE, remaining))]})

    monkeypatch.setattr(wd.requests, "post", post)
    hits = WorkdaySource(sites=[SITE], terms=["x"])._query(SITE, "x")
    assert len(hits) == 30
    assert len(calls) == 2
