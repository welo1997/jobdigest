"""USAJOBS: the country constant is sound, the paging stops honestly, and a key is required.

Three things worth pinning.

**`country_code` is always US.** Unlike Arbeitnow's wrong `"DE"` constant, this one is correct
by definition — USAJOBS is the US federal register — and the test states that so a future edit
does not "fix" it into resolving from text and start mislabelling overseas embassy posts.

**Paging stops on a short page OR a page that adds nothing new.** Federal listings repeat
heavily across keyword searches, so `new == 0` is the common terminator; page count alone would
re-request the same rows `MAX_PAGES_PER_TERM` times.

**A missing key or email skips the source**, the Adzuna contract — `gather()` catches the
`KeyError` and moves on rather than crashing the whole export.
"""

from __future__ import annotations

from datetime import date

import pytest

from ingestion.sources.usajobs import RESULTS_PER_PAGE, USAJobsSource


def _item(mid: str, **over) -> dict:
    d = {
        "PositionTitle": "Data Scientist",
        "PositionURI": f"https://www.usajobs.gov/job/{mid}",
        "OrganizationName": "Department of the Treasury",
        "PositionLocationDisplay": "Washington, District of Columbia",
        "PublicationStartDate": "2026-08-01T00:00:00.0000",
        "PositionRemuneration": [{"MinimumRange": "99000", "MaximumRange": "153000"}],
        "UserArea": {"Details": {"JobSummary": "Analyse federal data."}},
    }
    d.update(over)
    return {"MatchedObjectId": mid, "MatchedObjectDescriptor": d}


def _src() -> USAJobsSource:
    return USAJobsSource(api_key="k", email="dev@example.com")


# --- normalize -------------------------------------------------------------------------

def test_normalize_maps_the_core_fields_and_forces_us():
    [p] = _src().normalize([_item("1001")])
    assert p.source == "usajobs"
    assert p.title == "Data Scientist"
    assert p.company == "Department of the Treasury"
    assert p.url == "https://www.usajobs.gov/job/1001"
    assert p.description == "Analyse federal data."
    assert p.country_code == "US"          # the source-level constant, and it is correct
    assert p.currency == "USD"
    assert p.salary_raw == "99000 - 153000"
    assert p.posted_at == date(2026, 8, 1)


def test_a_row_without_a_url_is_dropped_not_stored_blank():
    d = _item("1002")
    d["MatchedObjectDescriptor"].pop("PositionURI")
    d["MatchedObjectDescriptor"].pop("ApplyURI", None)
    assert _src().normalize([d]) == []


def test_remote_signal_is_inferred_from_the_title_or_location():
    [p] = _src().normalize([_item("1003", PositionTitle="Remote Software Engineer")])
    assert p.remote_signal is True
    [q] = _src().normalize([_item("1004")])          # Washington DC, on-site
    assert q.remote_signal is None


# --- paging ----------------------------------------------------------------------------

def test_fetch_stops_on_a_short_page(monkeypatch):
    calls = []

    def fake(term, page):
        calls.append((term, page))
        return [_item(f"{term}-{page}-{i}") for i in range(RESULTS_PER_PAGE if page == 1 else 3)]

    src = _src()
    monkeypatch.setattr(src, "_fetch_page", fake)
    src.fetch()
    # Every term reaches page 2 (page 1 was full) and stops there (page 2 is short).
    assert max(p for _, p in calls) == 2


def test_fetch_stops_when_a_page_adds_nothing_new(monkeypatch):
    def fake(term, page):
        return [_item("dup") for _ in range(RESULTS_PER_PAGE)]   # same id every page

    src = _src()
    monkeypatch.setattr(src, "_fetch_page", fake)
    out = src.fetch()
    assert len(out) == 1        # the one unique id, de-duplicated across terms and pages


# --- credentials -----------------------------------------------------------------------

def test_missing_key_or_email_raises_keyerror(monkeypatch):
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    monkeypatch.delenv("USAJOBS_EMAIL", raising=False)
    with pytest.raises(KeyError):
        USAJobsSource()
    with pytest.raises(KeyError):
        USAJobsSource(api_key="k", email="  ")
