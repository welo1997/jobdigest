"""Platsbanken: personal data must not leak, and the query ceiling must not truncate silently.

Two of these matter more than the rest.

**The privacy tests.** Arbetsförmedlingen's ads name real recruiters — `application_contacts`
is populated on 29% of them, and `employer` carries its own `email` and `phone_number`.
Storing any of it would add a personal-data category `PrivacyEn.tsx` does not cover, which
security rule 4 makes a policy change, not a schema change. The adapter's defence is that it
never reads those fields; the test's job is to fail if a future edit starts to, which is why
it asserts on the *whole posting* rather than on the description alone.

**The ceiling test.** `offset` may not exceed 2 000, so a slice holding more than 2 100 ads
loses its tail — and the symptom is a smaller number, not an error. This is the Himalayas
failure with a different cause, and the only thing standing between it and production is
that slices are sized to stay under the ceiling and warn when they stop being.
"""

from __future__ import annotations

from datetime import date

from ingestion.sources.platsbanken import (
    MAX_OFFSET, OCCUPATION_FIELDS, PAGE_SIZE, SORT_NEWEST, PlatsbankenSource,
    _country, _location, _posted, _scrub,
)

RECRUITER_MAIL = "anna.svensson@exempelbolaget.se"
RECRUITER_PHONE = "+46 70 123 45 67"


def _ad(**over) -> dict:
    base = {
        "id": "31316246",
        "webpage_url": "https://arbetsformedlingen.se/platsbanken/annonser/31316246",
        "headline": "Data Scientist till Toca Boca",
        "removed": False,
        "publication_date": "2026-08-01T09:12:00",
        "description": {"text": "Vi söker en data scientist.\n\nHör av dig till "
                                f"{RECRUITER_MAIL} eller {RECRUITER_PHONE}."},
        "employer": {"name": "Toca Boca AB", "workplace": "Toca Boca",
                     "email": RECRUITER_MAIL, "phone_number": RECRUITER_PHONE},
        "application_contacts": [{"name": "Anna Svensson", "email": RECRUITER_MAIL,
                                  "telephone": RECRUITER_PHONE}],
        "workplace_address": {"municipality": "Stockholm", "city": "Stockholm",
                              "region": "Stockholms län", "country": "Sverige",
                              "country_code": "199"},
        "occupation": {"label": "Data scientist"},
        "salary_description": None,
    }
    base.update(over)
    return base


# --- personal data ---------------------------------------------------------------------

def test_recruiter_contact_details_never_reach_a_posting():
    """The whole posting, not just the description — a future edit could add any field."""
    p = PlatsbankenSource().normalize([_ad()])[0]
    blob = " ".join(str(v) for v in vars(p).values())
    assert RECRUITER_MAIL not in blob, "recruiter email leaked into a stored posting"
    assert "070 123 45 67" not in blob and "+46 70 123 45 67" not in blob
    assert "Anna Svensson" not in blob, "application_contacts must never be read"


def test_an_employer_without_a_name_falls_back_to_a_name_not_to_its_email():
    """`employer` carries `email` and `phone_number` beside `name` and `workplace`.

    With a name present, any fallback chain looks correct no matter what it falls back *to* —
    so this drops the name and checks where it actually lands. A mutation putting
    `employer.get("email")` in the chain passed every other test in this file.
    """
    p = PlatsbankenSource().normalize([_ad(employer={
        "name": None, "workplace": "Toca Boca",
        "email": RECRUITER_MAIL, "phone_number": RECRUITER_PHONE})])[0]
    assert p.company == "Toca Boca"

    nameless = PlatsbankenSource().normalize([_ad(employer={
        "name": None, "workplace": None,
        "email": RECRUITER_MAIL, "phone_number": RECRUITER_PHONE})])[0]
    assert nameless.company is None, \
        "an anonymous employer must stay anonymous, not become an address"
    assert RECRUITER_MAIL not in " ".join(str(v) for v in vars(nameless).values())


def test_description_is_scrubbed_but_still_readable():
    p = PlatsbankenSource().normalize([_ad()])[0]
    assert "Vi söker en data scientist." in p.description
    assert "[kontaktuppgift borttagen]" in p.description


def test_scrub_handles_the_shapes_employers_actually_use():
    assert "@" not in (_scrub("mejla oss på jobb@foo.se") or "")
    for number in ("+46701234567", "+46 70 123 45 67", "070-123 45 67", "08-123 456 78"):
        cleaned = _scrub(f"ring {number} idag") or ""
        assert number not in cleaned, f"{number!r} survived scrubbing"


def test_scrub_keeps_paragraphs():
    """Ads here have a median length of 4 634 characters; newlines are what make them read."""
    out = _scrub("Om rollen\n\nDu ska  koda.") or ""
    assert "\n\n" in out
    assert "Du ska koda." in out


# --- country ---------------------------------------------------------------------------

def test_country_is_read_from_the_posting_not_assumed_to_be_sweden():
    """A 100-ad sample held a Swiss one; a source-level constant is the Arbeitnow mistake."""
    se = PlatsbankenSource().normalize([_ad()])[0]
    ch = PlatsbankenSource().normalize(
        [_ad(workplace_address={"city": "Zürich", "country": "Schweiz"})])[0]
    assert se.country_code == "SE"
    assert ch.country_code == "CH"


def test_an_unrecognised_country_is_kept_as_unknown():
    """Unknown country is kept and left to the AI matcher — never guessed."""
    p = PlatsbankenSource().normalize(
        [_ad(workplace_address={"city": "X", "country": "Vinterland"})])[0]
    assert p.country_code is None


def test_country_helper_ignores_case_and_blanks():
    assert _country({"country": "SVERIGE"}) == "SE"
    assert _country({}) is None


# --- remote ----------------------------------------------------------------------------

def test_remote_signal_is_never_claimed():
    """There is no remote field in this payload; inventing one skips the location gate."""
    p = PlatsbankenSource().normalize([_ad()])[0]
    assert p.remote_signal is None


# --- ordinary normalisation ------------------------------------------------------------

def test_removed_ads_are_skipped():
    assert PlatsbankenSource().normalize([_ad(removed=True)]) == []


def test_ads_without_url_or_headline_are_skipped():
    assert PlatsbankenSource().normalize([_ad(webpage_url=None), _ad(headline=" ")]) == []


def test_company_location_and_category_are_carried():
    # `source_category` is the raw Swedish SSYK leaf label, one of ~954. It is NOT a
    # role_category, so `taxonomy.classify` discards it — this asserts the adapter carries the
    # field, not that the value is usable. Making it usable means a curated SSYK map here, the
    # shape `smartrecruiters.FUNCTION_HINTS` uses; deliberately not written while the vector
    # path may replace the recall predicate that would consume it.
    p = PlatsbankenSource().normalize([_ad()])[0]
    assert p.company == "Toca Boca AB"
    assert p.location == "Stockholm, Stockholms län"
    assert p.source_category == "Data scientist"
    assert p.posted_at == date(2026, 8, 1)


def test_location_skips_blanks_and_does_not_repeat_itself():
    assert _location({"city": "Malmö", "region": "Malmö"}) == "Malmö"
    assert _location({}) is None


def test_posted_parses_both_shapes_and_refuses_junk():
    assert _posted("2026-08-01T09:12:00") == date(2026, 8, 1)
    assert _posted("2026-08-01") == date(2026, 8, 1)
    assert _posted(None) is None
    assert _posted("igår") is None


# --- the curated field list ------------------------------------------------------------

def test_occupation_fields_are_unique_and_identified_by_concept_id():
    ids = [cid for cid, _ in OCCUPATION_FIELDS]
    assert len(ids) == len(set(ids))
    for cid, label in OCCUPATION_FIELDS:
        assert cid and label
        assert "_" in cid, f"{cid!r} is not an Arbetsförmedlingen concept id"


# --- paging ----------------------------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


def _fake_api(monkeypatch, handler):
    monkeypatch.setattr("ingestion.sources.platsbanken.politeness.throttle", lambda u: None)
    monkeypatch.setattr("ingestion.sources.platsbanken.requests.get",
                        lambda url, headers=None, timeout=None, params=None: handler(params))


def test_paging_strides_by_hits_received(monkeypatch):
    """The stride rule that survives the API changing its own maximum.

    The server here returns 60 rows per call while claiming 250 — deliberately *fewer* than
    `PAGE_SIZE`. That gap is the whole test: with a server that returns exactly the requested
    size, striding by the requested size and striding by the received size produce identical
    offsets, so the assertion passes either way and proves nothing. (It did, until a mutation
    run caught it.) Striding by `PAGE_SIZE` here would ask for 0, 100, 200 and finish with
    180 of 250 ads — a plausible number, no error.
    """
    offsets, total, served = [], 250, 60

    def handler(params):
        off = params["offset"]
        offsets.append(off)
        hits = [{"id": str(off + i), "publication_date": f"2026-08-04T00:00:{off + i:02d}"}
                for i in range(min(served, max(0, total - off)))]
        return _Resp({"total": {"value": total}, "hits": hits})

    _fake_api(monkeypatch, handler)
    rows = PlatsbankenSource(fields=[("F1", "Data/IT")]).fetch()
    assert offsets == [0, 60, 120, 180, 240], f"strode wrongly: {offsets}"
    assert len(rows) == total


def test_a_field_is_queried_by_field_id_newest_first(monkeypatch):
    """One occupation filter and a deterministic order — keyset paging needs both."""
    seen = []

    def handler(params):
        seen.append(params)
        return _Resp({"total": {"value": 1},
                      "hits": [{"id": "A", "publication_date": "2026-08-04T10:00:00"}]})

    _fake_api(monkeypatch, handler)
    PlatsbankenSource(fields=[("F1", "Data/IT")]).fetch()
    assert seen[0]["occupation-field"] == "F1"
    assert seen[0]["sort"] == SORT_NEWEST, "unordered paging cannot be resumed by date"
    assert "occupation-group" not in seen[0], (
        "an occupation-group sent beside occupation-field does not narrow — the API ORs "
        "filters of the same family, so the pair returns the whole field"
    )


def test_a_field_larger_than_the_ceiling_is_read_in_date_windows(monkeypatch):
    """`offset` stops at 2 000, so 2 500 ads are only reachable by re-opening the query.

    Truncating here is invisible: 2 100 of 2 500 is a plausible number with no error. This is
    the Himalayas failure with a different cause, and the reason the adapter walks backwards
    with `published-before` instead of paging a single query.
    """
    total = 2500
    ads = [{"id": str(n), "publication_date": f"2026-08-04T{n // 3600:02d}:"
                                              f"{n // 60 % 60:02d}:{n % 60:02d}"}
           for n in range(total)]
    ads.sort(key=lambda a: a["publication_date"], reverse=True)
    windows = []

    def handler(params):
        before = params.get("published-before")
        if before:
            pool = [a for a in ads if a["publication_date"] < before]
        else:
            pool = ads
        if params["offset"] == 0:
            windows.append(before)
        page = pool[params["offset"]:params["offset"] + PAGE_SIZE]
        return _Resp({"total": {"value": len(pool)}, "hits": page})

    _fake_api(monkeypatch, handler)
    rows = PlatsbankenSource(fields=[("F1", "Data/IT")]).fetch()
    assert len(windows) > 1, "never re-opened the query, so the tail was dropped"
    assert len(rows) == total, f"read {len(rows)} of {total}"
    assert len({r["id"] for r in rows}) == total


def test_overlapping_windows_do_not_duplicate_an_ad(monkeypatch):
    """`published-before` re-includes the boundary ad by design; it must not become two rows."""
    def handler(params):
        return _Resp({"total": {"value": 1},
                      "hits": [{"id": "SAME", "publication_date": "2026-08-04T10:00:00"}]})

    _fake_api(monkeypatch, handler)
    rows = PlatsbankenSource(fields=[("F1", "A"), ("F2", "B")]).fetch()
    assert len(rows) == 1


def test_paging_stops_at_the_offset_limit(monkeypatch):
    """Never issue the request that returns 400 — the ceiling is known, not discovered.

    The server returns 300 rows per call, *more* than `PAGE_SIZE`, so the offset advances
    faster than the per-window page cap can bound it. That is the point: with a server
    returning one row per call, the page cap stops the loop first and this test passes even
    with the offset guard deleted — which is what it did until a mutation run said so. Only a
    stride larger than the request size makes the offset guard the binding constraint.
    """
    offsets, served = [], 300

    def handler(params):
        off = params["offset"]
        offsets.append(off)
        return _Resp({"total": {"value": 99999},
                      "hits": [{"id": f"{params.get('published-before')}-{off + i}",
                                "publication_date": "2026-08-04T10:00:00"}
                               for i in range(served)]})

    _fake_api(monkeypatch, handler)
    PlatsbankenSource(fields=[("F1", "Data/IT")]).fetch()
    assert max(offsets) <= MAX_OFFSET, f"asked for offset {max(offsets)}, which is a 400"


def test_a_window_that_finds_nothing_new_ends_the_field(monkeypatch):
    """Ads sharing one timestamp would otherwise re-open the same window forever."""
    calls = []

    def handler(params):
        calls.append(params)
        # Always the same ad at the same instant: `published-before` can never advance.
        return _Resp({"total": {"value": 99999},
                      "hits": [{"id": "STUCK", "publication_date": "2026-08-04T10:00:00"}]})

    _fake_api(monkeypatch, handler)
    rows = PlatsbankenSource(fields=[("F1", "Data/IT")]).fetch()
    assert len(rows) == 1
    assert len(calls) < 200, f"looped {len(calls)} times instead of giving up"


def test_a_dead_api_yields_nothing_rather_than_raising(monkeypatch):
    class _Dead:
        status_code = 503

        def json(self):
            raise AssertionError("must not parse a failed response")

    monkeypatch.setattr("ingestion.sources.platsbanken.politeness.throttle", lambda u: None)
    monkeypatch.setattr("ingestion.sources.platsbanken.requests.get",
                        lambda *a, **k: _Dead())
    assert PlatsbankenSource(fields=[("F1", "Data/IT")]).fetch() == []
