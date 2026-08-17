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
        "occupation_group": {"label": "Mjukvaru- och systemutvecklare m.fl."},
        "occupation_field": {"label": "Data/IT"},
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
    # `source_category` used to be the raw Swedish SSYK leaf label, one of ~954, which is NOT
    # a role_category — so `taxonomy.classify` discarded it and the register's own answer was
    # fetched every run and thrown away, leaving 74% of this source `uncategorised`.
    #
    # The curated map this comment used to defer is now written (SSYK_FIELD_CATEGORIES /
    # SSYK_GROUP_CATEGORIES, 2026-08-09). The deferral was conditional on the vector path
    # possibly replacing the recall predicate that consumes a category; that gate was measured
    # on 2026-08-08 and came back negative, so the predicate stays and the map pays for itself
    # — 50.1% of this source's uncategorised ads recovered on a 2 000-ad live sample.
    p = PlatsbankenSource().normalize([_ad()])[0]
    assert p.company == "Toca Boca AB"
    assert p.location == "Stockholm, Stockholms län"
    assert p.source_category == "software_engineering"   # mapped, not the raw SSYK leaf
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


from ingestion.sources import platsbanken  # noqa: E402  (module handle for the SSYK tests)


# ---------------------------------------------------------------- SSYK → category ---
# Added 2026-08-09. Until then `source_category` carried `occupation.label` — the finest SSYK
# leaf, 954 distinct Swedish strings — which `classify` discards as non-canonical, so the
# register's own answer was fetched every run and thrown away. Mapping it recovered 50.1% of
# the source's uncategorised ads on a 2 000-ad live sample.


def test_every_mapped_ssyk_value_is_a_real_category():
    """A non-canonical value is discarded by `classify` rather than raising, so a typo here
    means the whole occupation group silently stays `uncategorised` — indistinguishable from
    the mapping not being wired up at all."""
    from service import taxonomy

    mapped = {**platsbanken.SSYK_FIELD_CATEGORIES, **platsbanken.SSYK_GROUP_CATEGORIES}
    unknown = {v for v in mapped.values() if v not in taxonomy.CATEGORIES}
    assert not unknown, f"SSYK map names categories that do not exist: {sorted(unknown)}"


def test_the_group_beats_the_field():
    """"Data/IT" defaults to software, but an IT support technician inside it is
    `customer_support`. Field-only would file every one of them as a developer and put them in
    the wrong subscriber's digest."""
    ad = {"occupation_field": {"label": "Data/IT"},
          "occupation_group": {"label": "Supporttekniker, IT"}}
    assert platsbanken._ssyk_category(ad) == "customer_support"


def test_a_group_may_veto_its_fields_answer_without_offering_one():
    """The technical field maps to `engineering`, but it also carries property managers and
    urban planners. Hinting those as engineering is the lookup-table failure this repo already
    has a rule for: a table in front of the matcher must be able to say "I don't know" and hand
    off, never name a category that is not true. Declining costs one uncategorised row;
    guessing puts a `Fastighetsförvaltare` in the digest of everyone who asked for mechanics."""
    technical = {"occupation_field": {"label": "Yrken med teknisk inriktning"}}
    assert platsbanken._ssyk_category(
        {**technical, "occupation_group": {"label": "Ingenjörer och tekniker inom maskinteknik"}}
    ) == "engineering"
    for vetoed in platsbanken.SSYK_GROUP_UNMAPPED:
        assert platsbanken._ssyk_category(
            {**technical, "occupation_group": {"label": vetoed}}) is None, vetoed


def test_the_veto_and_the_scorers_answer_key_do_not_drift():
    """`scripts/categorization_score.py` excludes the same groups from the ground truth, for
    the same reason — grading the classifier as *wrong* for declining to call a property
    manager an engineer scores it against an answer that is not true. Two copies of one
    judgement, so the copy is asserted rather than trusted (the `source_watchdog` lesson)."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.categorization_score import OUT_OF_SCOPE_GROUPS

    assert platsbanken.SSYK_GROUP_UNMAPPED == OUT_OF_SCOPE_GROUPS


def test_an_unknown_field_returns_none_rather_than_a_guess():
    assert platsbanken._ssyk_category({"occupation_field": {"label": "Militära yrken"},
                                       "occupation_group": {}}) is None
    assert platsbanken._ssyk_category({}) is None


# The care/manual fields un-excluded on 2026-08-09, plus the social field on 2026-08-17, each
# with the category the label resolves to. The heterogeneous fields (one like "Administration,
# ekonomi, juridik" spans finance, HR, legal and admin) resolve per-ad via title patterns and
# SSYK *group* mappings, so they deliberately have no field-level default — this list is only
# the homogeneous sector fields, which is exactly the set that must map.
_NEWLY_RANKABLE = {
    "Hälso- och sjukvård": "healthcare",
    "Transport, distribution, lager": "logistics_transport",
    "Pedagogik": "education",
    "Hotell, restaurang, storhushåll": "hospitality",
    "Installation, drift, underhåll": "skilled_trades",
    "Industriell tillverkning": "manufacturing_production",
    "Bygg och anläggning": "construction",
    "Hantverk": "skilled_trades",
    # 2026-08-17: excluded on 2026-08-09 for want of a category, which arrived on 2026-08-10.
    "Yrken med social inriktning": "social_care",
}

# Fields deliberately carrying no field-level default, because they mean several things. Naming
# them is what lets the guard below be a rule over `OCCUPATION_FIELDS` rather than a second copy
# of it: a field added without a mapping fails unless someone consciously declares it plural.
_HETEROGENEOUS_FIELDS = frozenset({
    "Försäljning, inköp, marknadsföring",
    "Administration, ekonomi, juridik",
    "Chefer och verksamhetsledare",
    "Kultur, media, design",
    "Naturvetenskap",
})


def test_the_un_excluded_fields_all_map_to_a_real_category():
    """Every sector field un-excluded since 2026-08-09 must map — via its label — to a real
    `role_category`, or it would land in `uncategorised` and reach the widened shortlist path,
    which is the harm the original exclusion guarded against. Fails if one of these is added to
    `OCCUPATION_FIELDS` without its `SSYK_FIELD_CATEGORIES` mapping (or the mapping is renamed
    out from under it)."""
    from service import taxonomy
    ingested = {name for _, name in platsbanken.OCCUPATION_FIELDS}
    for label, expected in _NEWLY_RANKABLE.items():
        assert label in ingested, f"{label!r} was dropped from OCCUPATION_FIELDS"
        assert platsbanken.SSYK_FIELD_CATEGORIES.get(label) == expected
        assert expected in taxonomy.CATEGORIES


def test_a_field_is_never_ingested_without_a_way_to_rank_it():
    """The general form of the test above, and the one that would have caught the 2026-08-17 gap.

    `SSYK_FIELD_CATEGORIES` claimed in prose to cover all 21 fields and did not cover
    `Yrken med social inriktning`, so un-excluding that field was a two-file change that read
    like a one-line one. A docstring cannot promise coverage; this asserts it over whatever
    `OCCUPATION_FIELDS` actually holds, so the next field cannot be ingested with no way to rank
    it unless someone adds it to `_HETEROGENEOUS_FIELDS` on purpose.
    """
    from service import taxonomy
    for _, label in platsbanken.OCCUPATION_FIELDS:
        if label in _HETEROGENEOUS_FIELDS:
            assert label not in platsbanken.SSYK_FIELD_CATEGORIES, (
                f"{label!r} is declared plural but carries a field-level default — one of the "
                f"two is wrong"
            )
            continue
        category = platsbanken.SSYK_FIELD_CATEGORIES.get(label)
        assert category, f"{label!r} is ingested with no field-level category to rank it"
        assert category in taxonomy.CATEGORIES, f"{label!r} -> {category!r} is not a category"


def test_the_social_field_is_ingested_under_its_verified_concept_id():
    """A wrong concept id is not an error, it is an empty field.

    `GazW_2TU_kJw` (SSYK code 16) came off the JobSearch API's own `occupation-field` facet and
    was confirmed by round-trip — filtering on it returns ads whose own
    `occupation_field.concept_id` is that id. Pinned so an edit to the literal has to be
    deliberate, since nothing offline can tell a live id from a plausible one.
    """
    assert ("GazW_2TU_kJw", "Yrken med social inriktning") in platsbanken.OCCUPATION_FIELDS


def test_the_social_fields_wrong_groups_decline_rather_than_guess():
    """The field maps to `social_care`, and for 67 of its 3 762 ads that is untrue.

    Clergy, funeral staff, wellness educators, tour guides and a residual service group sit
    inside `Yrken med social inriktning`; none of them is a social worker and none of them has a
    category. All 67 classify `uncategorised` on title alone, so the field default *is* the
    answer unless the group vetoes it — which is how a priest would otherwise reach the digest
    of everyone who asked for social work. Same rule, and same test, as
    `Fastighetsförvaltare` inside the technical field.
    """
    field = {"occupation_field": {"label": "Yrken med social inriktning"}}
    assert platsbanken._ssyk_category(
        {**field, "occupation_group": {"label": "Socialsekreterare"}}) == "social_care"
    for group in ("Präster", "Diakoner", "Begravnings- och krematoriepersonal",
                  "Friskvårdskonsulenter och hälsopedagoger m.fl.", "Guider och reseledare",
                  "Övrig servicepersonal"):
        assert group in platsbanken.SSYK_GROUP_UNMAPPED, group
        assert platsbanken._ssyk_category(
            {**field, "occupation_group": {"label": group}}) is None, group
    # Not vetoed: these are what the category is for.
    for group in ("Övriga yrken inom socialt arbete", "Fritidsledare m.fl."):
        assert platsbanken._ssyk_category(
            {**field, "occupation_group": {"label": group}}) == "social_care", group


def test_the_unrankable_fields_are_deliberately_left_out():
    """Five fields (sanitation, security, agriculture, beauty, military) map to no current
    category, so they stay excluded rather than pour 2 536 uncategorised ads into the widened
    path. Pinned so a future 'ingest everything' edit has to confront the reason.

    `Yrken med social inriktning` left this list on 2026-08-17: it was here for want of a
    category and `social_care` was created on 2026-08-10, so the exclusion outlived its cause.
    That is the shape to copy — a field leaves when a category arrives, not when the digest
    looks thin.
    """
    ingested = {name for _, name in platsbanken.OCCUPATION_FIELDS}
    for label in ("Sanering och renhållning", "Säkerhet och bevakning", "Naturbruk",
                  "Kropps- och skönhetsvård", "Militära yrken"):
        assert label not in ingested, f"{label!r} was ingested but maps to no category"
        assert label not in platsbanken.SSYK_FIELD_CATEGORIES
