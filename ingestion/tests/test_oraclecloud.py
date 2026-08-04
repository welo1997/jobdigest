"""Oracle Recruiting Cloud: the four guards that are expensive to get wrong.

Every test here is written against a failure that has actually happened in this repo, to a
different adapter, and would happen again here in the same shape:

* **Paging by the requested size** rather than the received size is the Himalayas bug —
  capped at 20 for its whole life, `offset += PAGE_SIZE`, no error anywhere. Oracle caps a
  response at 200 however large a `limit` you send, so the identical mistake is available.
* **Folding hybrid into remote** is the 2026-07-28 incident (3 744 of 3 744 jobs.cz postings
  wrongly flagged, 4 830 rows repaired, 40 of 50 personas affected). `remote_signal` exempts
  a posting from the location gate, so it is an on-site job in the wrong inbox.
* **Keeping stale requisitions** is The Muse's bug: `query_shortlist_meta` orders by
  `first_seen_at`, so a two-year-old posting ingested today is emailed as new.
* **A source-level country constant** is the Arbeitnow bug — 207 of 375 "German" postings
  were German, and the constant overrode a real London location.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ingestion.sources.oraclecloud import (
    MAX_AGE_DAYS, PAGE_SIZE, SITES, OracleCloudSource, _clean, _posted,
)


def _row(**over) -> dict:
    base = {
        "Id": "25027513",
        "Title": "Senior Electrical Design Engineer",
        "PostedDate": date.today().isoformat(),
        "PrimaryLocation": "Nove Mesto nad Vahom, Slovakia",
        "PrimaryLocationCountry": "SK",
        "ShortDescriptionStr": "Design and validate power distribution systems.",
        "WorkplaceTypeCode": "ORA_ON_SITE",
        "WorkplaceType": "On-site",
        "JobFamily": None,
        "JobFunction": None,
        "_tenant": "egup",
        "_region": "us2",
        "_site": "CX",
        "_company": "Vertiv",
    }
    base.update(over)
    return base


# --- remote_signal ---------------------------------------------------------------------

def test_hybrid_is_not_remote():
    """Hybrid is a commute. Flagging it remote would skip the location gate entirely."""
    p = OracleCloudSource().normalize([_row(WorkplaceTypeCode="ORA_HYBRID",
                                            WorkplaceType="Hybrid")])[0]
    assert p.remote_signal is False


def test_only_the_explicit_remote_code_sets_the_flag():
    remote = OracleCloudSource().normalize([_row(WorkplaceTypeCode="ORA_REMOTE")])[0]
    onsite = OracleCloudSource().normalize([_row(WorkplaceTypeCode="ORA_ON_SITE")])[0]
    assert remote.remote_signal is True
    assert onsite.remote_signal is False


def test_unset_workplace_type_is_not_remote():
    """64% of rows carry no workplace type, and the label field is '' rather than null."""
    for missing in (None, "", "Flexible"):
        p = OracleCloudSource().normalize([_row(WorkplaceTypeCode=missing,
                                                WorkplaceType="")])[0]
        assert p.remote_signal is False, f"{missing!r} must not read as remote"


# --- freshness -------------------------------------------------------------------------

def test_stale_requisitions_are_dropped():
    """Vertiv's tail reaches 791 days; ordering is by first_seen_at, so these look new."""
    old = (date.today() - timedelta(days=MAX_AGE_DAYS + 1)).isoformat()
    assert OracleCloudSource().normalize([_row(PostedDate=old)]) == []


def test_a_posting_inside_the_window_is_kept():
    fresh = (date.today() - timedelta(days=MAX_AGE_DAYS - 1)).isoformat()
    assert len(OracleCloudSource().normalize([_row(PostedDate=fresh)])) == 1


def test_undated_postings_are_dropped():
    """The Muse's call, and the opposite of this repo's usual 'unknown is kept'."""
    assert OracleCloudSource().normalize([_row(PostedDate=None)]) == []


def test_posted_parses_iso_dates_and_refuses_junk():
    assert _posted("2026-08-04") == date(2026, 8, 4)
    assert _posted("2026-08-04T09:30:00Z") == date(2026, 8, 4)
    assert _posted(None) is None
    assert _posted("not a date") is None


# --- identity and location -------------------------------------------------------------

def test_country_comes_from_the_posting_not_a_constant():
    rows = OracleCloudSource().normalize(
        [_row(PrimaryLocationCountry="cz"), _row(Id="2", PrimaryLocationCountry=None)])
    assert rows[0].country_code == "CZ"
    assert rows[1].country_code is None, "unknown country must stay unknown"


def test_company_comes_from_the_curated_list():
    """Every company-name field Oracle exposes is null, so the list is data, not a comment."""
    p = OracleCloudSource().normalize([_row(LegalEmployer=None, Organization=None)])[0]
    assert p.company == "Vertiv"


def test_url_is_built_from_the_full_triple():
    p = OracleCloudSource().normalize([_row()])[0]
    assert p.url == ("https://egup.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/"
                     "sites/CX/job/25027513")


def test_non_breaking_spaces_are_folded_not_left_glued():
    """221 real nbsp across 200 Vertiv rows, mid-sentence — they would glue search terms.

    Written with `\\u00a0` escapes rather than the character itself: a literal one here looks
    exactly like a space, so a well-meaning edit could delete the only thing under test and
    leave a passing assertion behind.
    """
    p = OracleCloudSource().normalize(
        [_row(ShortDescriptionStr="The\u00a0Sr.\u00a0Test Engineer\n\nassists  others.")])[0]
    assert "\u00a0" not in p.description
    assert p.description == "The Sr. Test Engineer assists others."


def test_clean_handles_empty_and_whitespace_only():
    assert _clean(None) is None
    assert _clean("") is None
    assert _clean("   \u00a0  \n ") is None


def test_rows_without_id_or_title_are_skipped():
    assert OracleCloudSource().normalize([_row(Id=None), _row(Title="  ")]) == []


# --- the curated site list -------------------------------------------------------------

def test_sites_are_unique_and_fully_specified():
    """A triple is (tenant, region, site) — Honeywell's region is `ocs`, Vertiv's site `CX`."""
    assert len(SITES) == len({(t, r, s) for t, r, s, _ in SITES})
    assert len({name for *_, name in SITES}) == len(SITES), "one row per company"
    for tenant, region, site_no, company in SITES:
        assert tenant and region and site_no and company
        assert tenant.islower(), f"{tenant} becomes a hostname"


# --- paging ----------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, rows, total):
        self.status_code = 200
        self._rows, self._total = rows, total

    def json(self):
        return {"items": [{"requisitionList": self._rows, "TotalJobsCount": self._total}]}


def test_paging_strides_by_rows_received_not_rows_requested(monkeypatch):
    """Oracle caps a response at 200 whatever `limit` says — the Himalayas bug's shape.

    The server here returns 50 rows per call while claiming a total of 200. An adapter that
    advanced by `PAGE_SIZE` would ask for offset 200 on its second call, receive nothing, and
    finish with 50 of 200 postings — a plausible number, no error, no warning.
    """
    served, offsets = 200, []

    def fake_get(url, headers=None, timeout=None, params=None):
        offset = int(params["finder"].split("offset=")[1])
        offsets.append(offset)
        rows = [{"Id": str(offset + i), "Title": "Engineer"}
                for i in range(min(50, served - offset))]
        return _FakeResp(rows, served)

    monkeypatch.setattr("ingestion.sources.oraclecloud.requests.get", fake_get)
    monkeypatch.setattr("ingestion.sources.oraclecloud.politeness.throttle", lambda url: None)

    rows = OracleCloudSource(sites=[("egup", "us2", "CX", "Vertiv")]).fetch()
    assert offsets == [0, 50, 100, 150], f"strode wrongly: {offsets}"
    assert len(rows) == served
    assert PAGE_SIZE == 200, "the request size is Oracle's cap, not the stride"


def test_duplicate_requisition_ids_collapse(monkeypatch):
    """Oracle repeats a requisition across page boundaries — 1 in 812 on Emerson."""
    def fake_get(url, headers=None, timeout=None, params=None):
        offset = int(params["finder"].split("offset=")[1])
        if offset == 0:
            return _FakeResp([{"Id": "A", "Title": "X"}, {"Id": "B", "Title": "Y"}], 3)
        if offset == 2:
            return _FakeResp([{"Id": "B", "Title": "Y"}], 3)
        return _FakeResp([], 3)

    monkeypatch.setattr("ingestion.sources.oraclecloud.requests.get", fake_get)
    monkeypatch.setattr("ingestion.sources.oraclecloud.politeness.throttle", lambda url: None)

    rows = OracleCloudSource(sites=[("egup", "us2", "CX", "Vertiv")]).fetch()
    assert {r["Id"] for r in rows} == {"A", "B"}
    assert len(rows) == 2


def test_a_dark_tenant_yields_nothing_and_does_not_abort_the_run(monkeypatch):
    class _Dead:
        status_code = 503

        def json(self):
            raise AssertionError("must not be parsed")

    monkeypatch.setattr("ingestion.sources.oraclecloud.requests.get",
                        lambda *a, **k: _Dead())
    monkeypatch.setattr("ingestion.sources.oraclecloud.politeness.throttle", lambda url: None)
    assert OracleCloudSource(sites=[("egup", "us2", "CX", "Vertiv")]).fetch() == []


@pytest.mark.parametrize("boom", [ValueError("bad json"), TypeError("nope")])
def test_a_malformed_response_is_survived(monkeypatch, boom):
    class _Bad:
        status_code = 200

        def json(self):
            raise boom

    monkeypatch.setattr("ingestion.sources.oraclecloud.requests.get", lambda *a, **k: _Bad())
    monkeypatch.setattr("ingestion.sources.oraclecloud.politeness.throttle", lambda url: None)
    assert OracleCloudSource(sites=[("egup", "us2", "CX", "Vertiv")]).fetch() == []
