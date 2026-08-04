"""SmartRecruiters pages the whole tenant, and decides relevance on a title it can see.

The bug this replaces is the Himalayas bug in a second adapter, and it was live for the
adapter's whole life. `_list` issued **one** request per (tenant, term) — `limit=100`, no
`offset` — so a query reporting `totalFound=1866` contributed 100 rows and nothing said so.

Two separate things were wrong, which is why the fix changes the shape rather than a number:

* **No paging at all.** BoschGroup holds 4 714 postings; the adapter could not see past 100
  of any slice of them.
* **`q` was never a filter.** It is a *ranked* full-text search: at BoschGroup "software
  engineer" returns 1 866 of 4 714 and "data engineer" returns 2 109, and both hand back the
  same first row. So the term matrix did not select tech roles either — it truncated an
  arbitrary ranking. Measured 2026-08-04: paging every tenant costs 88 list requests against
  the old 96, and returns all 7 503 postings, of which `TECH_TITLE` keeps 2 607.

The relevance filter is the load-bearing half of that trade. These are industrial employers,
and without it a nightly run would put "Manifold Assembler I" into the *widened* retrieval
path, which drops the recall predicate entirely.
"""

from __future__ import annotations

import ingestion.sources.smartrecruiters as sr
from ingestion.sources.smartrecruiters import SmartRecruitersSource

SERVER_PAGE = 100  # what SmartRecruiters returns however large a `limit` you send


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _fake_api(titles: list[str], calls: list[dict]):
    """A tenant holding `titles`, capped at SERVER_PAGE rows per response."""
    def get(url, params=None, **kwargs):
        params = params or {}
        if "/postings/" in url:                      # a detail call
            pid = url.rsplit("/", 1)[1]
            return _Resp({"id": pid, "name": titles[int(pid)],
                          "jobAd": {"sections": {}}, "releasedDate": "2026-08-01T00:00:00.000Z"})
        offset = int(params.get("offset", 0))
        calls.append(params)
        page = [{"id": str(i), "name": titles[i], "releasedDate": "2026-08-01T00:00:00.000Z"}
                for i in range(offset, min(offset + SERVER_PAGE, len(titles)))]
        return _Resp({"content": page, "totalFound": len(titles)})
    return get


def test_it_pages_past_the_first_response(monkeypatch):
    calls: list[dict] = []
    titles = [f"Software Engineer {i}" for i in range(250)]
    monkeypatch.setattr(sr.requests, "get", _fake_api(titles, calls))
    got = SmartRecruitersSource(tenants=["t"]).fetch()
    assert len(got) == 250, "stopped at the first page — 100 is a cap, not the total"
    assert [c["offset"] for c in calls] == [0, 100, 200]


def test_it_advances_by_what_it_received(monkeypatch):
    """A stride of the *requested* size is how Himalayas skipped 80 jobs a page."""
    calls: list[dict] = []
    titles = [f"Data Engineer {i}" for i in range(145)]
    monkeypatch.setattr(sr.requests, "get", _fake_api(titles, calls))
    got = SmartRecruitersSource(tenants=["t"]).fetch()
    ids = [g["id"] for g in got]
    assert len(set(ids)) == len(ids) == 145
    assert [c["offset"] for c in calls] == [0, 100]


def test_asking_for_more_than_the_server_gives_skips_nothing(monkeypatch):
    """The stride rule only bites once someone raises PAGE_SIZE, so this is where it is held.

    While `PAGE_SIZE` is 100 and the server caps at 100 the two are the same number, and an
    `offset += PAGE_SIZE` stride is indistinguishable from a correct one. Raising the page
    size is an obvious-looking optimisation — SmartRecruiters accepts `limit=500` and simply
    returns 100 — and the moment it happens a fixed stride silently drops four rows in five.
    That is the Himalayas bug exactly.
    """
    calls: list[dict] = []
    titles = [f"Engineer {i}" for i in range(250)]
    monkeypatch.setattr(sr, "PAGE_SIZE", 500)
    monkeypatch.setattr(sr.requests, "get", _fake_api(titles, calls))
    got = SmartRecruitersSource(tenants=["t"]).fetch()
    assert len(got) == 250, "an offset stride wider than the response skipped postings"
    assert [c["offset"] for c in calls] == [0, 100, 200]


def test_the_page_guard_still_bounds_a_runaway_tenant(monkeypatch):
    calls: list[dict] = []
    titles = [f"Engineer {i}" for i in range(SERVER_PAGE * (sr.MAX_LIST_PAGES + 40))]
    monkeypatch.setattr(sr.requests, "get", _fake_api(titles, calls))
    SmartRecruitersSource(tenants=["t"]).fetch()
    assert len(calls) == sr.MAX_LIST_PAGES


def test_shop_floor_titles_never_reach_a_detail_call(monkeypatch):
    """Real BoschGroup titles, from the run that measured this.

    The industrial half of an industrial employer's board is the price of paging it, and this
    is where that price is paid. A false negative here costs one posting; a false positive
    puts assembly-line work in front of a subscriber whose shortlist had to be widened.
    """
    calls: list[dict] = []
    keep = ["Software Engineer (w/m/div.)", "AI/ML Expert", "Data Analyst Supply Chain",
            "Werkstudent Informatik", "DevOps Engineer", "UX Designer",
            "Softwareentwickler Embedded", "IT Security Specialist"]
    drop = ["Manifold Assembler I", "MSR-Techniker (w/m/div.) Gebäudeautomation",
            "HVAC Specialist (Water Source Heat Pumps/Geothermal)",
            "Monteur/Prüfer (w/m/div.)", "Fachbauleiter (w/m/div.)",
            "Technischer Vertriebsmitarbeiter im Innendienst"]
    monkeypatch.setattr(sr.requests, "get", _fake_api(keep + drop, calls))
    got = SmartRecruitersSource(tenants=["t"]).fetch()
    assert sorted(g["name"] for g in got) == sorted(keep)


def test_ai_ml_is_kept_because_a_substring_list_dropped_it(monkeypatch):
    """The first draft of TECH_TITLE matched `"ai "` and lost six real postings.

    Word boundaries, not substrings — and the mirror of it: "Trainer" contains "ai" and
    "Maintenance Planner" contains "ai", and neither may be admitted by it.
    """
    assert sr.SmartRecruitersSource._is_tech("AI/ML Expert")
    assert sr.SmartRecruitersSource._is_tech("Artificial Intelligence Researcher")
    assert not sr.SmartRecruitersSource._is_tech("Trainer Vertrieb")
    assert not sr.SmartRecruitersSource._is_tech("Maintenance Planner")
    assert not sr.SmartRecruitersSource._is_tech("Manifold Assembler I")
