"""NAV — the guarantees that are expensive to get wrong.

Three groups matter more than the rest, and each exists because the failure is SILENT:

  - **Personal data.** 95.5% of NAV ads carry a structured contact (measured over 2 000 live
    ads). `contactList` is never read, and free text is scrubbed on top. Nothing raises if a
    recruiter's mobile reaches `postings.description`; it simply becomes personal data the
    privacy policy does not cover (security rule 4).
  - **The change log.** The feed is append-only, so an ad unchanged for a month appears in no
    recent window while `store.deactivate_stale(days=7)` deactivates anything not re-seen in
    seven days. If the mirror stops emitting the whole active set, Norwegian inventory decays
    to nothing within a week with no error anywhere.
  - **The scope filter.** It refuses an ad only when nothing can rank it (`_in_scope`), because
    the harm it guards is *unrankable* inventory reaching the widened retrieval path, which
    drops the recall predicate. Until 2026-08-26 it was a major-group filter instead, and the
    difference was half the register: 2 729 of 5 445 ads refused, 1 004 of them titles no
    pattern can read and only the publisher's code can answer for.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from ingestion.sources import nav
from ingestion.sources.nav import NavSource
from service import taxonomy


# --- fixtures ------------------------------------------------------------------------------

def _content(**over) -> dict:
    base = {
        "title": "Sykepleier til hjemmetjenesten",
        "employer": {"name": "Oslo kommune", "orgnr": "123456789"},
        "description": "<p>Vi soker deg.</p><p>Ring Kari pa 22 33 44 55.</p>",
        "workLocations": [{"country": "NORGE", "city": "OSLO", "municipal": "OSLO"}],
        "expires": "2099-01-01T00:00:00+02:00",
        "published": "2026-08-09T18:50:39+02:00",
        "link": "https://arbeidsplassen.nav.no/stillinger/stilling/u1",
        "categoryList": [{"categoryType": "STYRK08", "code": "2223", "score": 1.0}],
        "contactList": [{"name": "Kari Nordmann", "email": "kari@oslo.no",
                         "phone": "+47 22 33 44 55"}],
    }
    base.update(over)
    return base


def _item(uuid="u1", status="ACTIVE", modified="2026-08-10T09:00:00+02:00") -> dict:
    return {"url": f"/api/v1/feedentry/{uuid}", "date_modified": modified,
            "_feed_entry": {"uuid": uuid, "status": status}}


class _Resp:
    def __init__(self, payload=None, status=200, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        if self._payload is None:
            raise AssertionError("must not parse a response that was not meant to be read")
        return self._payload


def _fake_nav(monkeypatch, pages, entries, token="a.b.c"):
    """Fake NAV. `pages` is a list of item-lists; entries maps uuid -> ad_content."""
    state = {"page": 0}

    def fake_get(url, headers=None, timeout=None, **kw):
        headers = headers or {}
        if url.endswith("/api/publicToken"):
            return _Resp(text=f"Current public token:\n {token} \n")
        bearer = headers.get("Authorization", "").removeprefix("Bearer ")
        if len(bearer.split(".")) != 3:
            return _Resp(status=401)          # NAV's answer to a malformed token
        if "/feedentry/" in url:
            uuid = url.rsplit("/", 1)[-1]
            if uuid not in entries:
                return _Resp(status=404)
            return _Resp({"uuid": uuid, "status": "ACTIVE", "ad_content": entries[uuid]})
        i = state["page"]
        state["page"] += 1
        items = pages[i] if i < len(pages) else []
        nxt = f"/api/v1/feed/p{i + 1}" if i + 1 < len(pages) else None
        return _Resp({"items": items, "next_url": nxt})

    monkeypatch.setattr(nav.requests, "get", fake_get)
    monkeypatch.setattr(nav.politeness, "throttle", lambda url: None)
    monkeypatch.setattr(nav.politeness, "robots_allows", lambda url: True)


def _src(tmp_path, **kw) -> NavSource:
    return NavSource(state_dir=tmp_path, **kw)


# --- personal data -------------------------------------------------------------------------

def test_recruiter_contact_details_never_reach_a_posting(tmp_path, monkeypatch):
    """The whole posting, not just the description — a future edit could add any field, and
    this is the assertion that would still catch it."""
    _fake_nav(monkeypatch, [[_item()]], {"u1": _content()})
    postings = _src(tmp_path).run()
    assert len(postings) == 1
    blob = json.dumps(postings[0].__dict__, ensure_ascii=False, default=str)
    for leak in ("Kari Nordmann", "kari@oslo.no", "22 33 44 55", "123456789"):
        assert leak not in blob, f"{leak!r} reached a posting"


def test_the_contact_block_is_never_read_so_it_cannot_be_mirrored(tmp_path, monkeypatch):
    """The primary defence is not the scrubber, it is that `contactList` is never read at all.
    Asserted on the persisted mirror, because the mirror is a NEW place personal data could
    come to rest — one that outlives the process and sits in a Docker volume."""
    _fake_nav(monkeypatch, [[_item()]], {"u1": _content()})
    _src(tmp_path).fetch()
    mirror = (tmp_path / "nav_mirror.json").read_text(encoding="utf-8")
    for leak in ("Kari Nordmann", "kari@oslo.no", "contactList", "orgnr"):
        assert leak not in mirror, f"{leak!r} was persisted to the mirror"


def test_description_is_scrubbed_but_still_reads_as_norwegian():
    out = nav._scrub(nav._text("<p>Vi soker deg.</p><p>Ring Kari pa 22 33 44 55 "
                               "eller kari@oslo.no.</p>"))
    assert "22 33 44 55" not in out and "kari@oslo.no" not in out
    assert "[kontaktinfo fjernet]" in out
    assert "Vi soker deg." in out and "Ring Kari pa" in out


@pytest.mark.parametrize("number", [
    "+47 22 33 44 55", "+4722334455", "22334455", "22 33 44 55", "934 56 789",
    "934-56-789", "+47 934 56 789",
])
def test_scrub_handles_the_shapes_employers_actually_write(number):
    """Matched by DIGIT COUNT rather than fixed groups — the platsbanken lesson. Norwegian
    numbers are written 2-2-2-2, 3-2-3 and unspaced, and a pattern built for one misses the
    others. Over-matching is the safe direction: a false positive only redacts."""
    assert "[kontaktinfo fjernet]" in nav._scrub(f"Kontakt oss pa {number} i dag")


def test_scrub_leaves_ordinary_numbers_alone():
    """A salary and a year must survive, or the scrubber quietly destroys the ad's content."""
    kept = nav._scrub("Lonn 550 000 kr per ar, oppstart 2026, 3 ledige stillinger")
    assert "550 000" in kept and "2026" in kept and "[kontaktinfo fjernet]" not in kept


def test_an_employer_without_a_name_stays_none_rather_than_becoming_something_else(
        tmp_path, monkeypatch):
    """An anonymous employer must stay anonymous. The failure this guards against is a
    fallback chain that reaches past the name into an address or an org number."""
    _fake_nav(monkeypatch, [[_item()]], {"u1": _content(employer={"orgnr": "123456789"})})
    posting = _src(tmp_path).run()[0]
    assert posting.company is None
    assert "123456789" not in json.dumps(posting.__dict__, default=str)


# --- HTML ----------------------------------------------------------------------------------

def test_tags_are_stripped_before_unescaping_and_that_order_is_load_bearing():
    """Norwegian employers write quotation marks as ASCII angle brackets, escaped in the
    source: `&lt;losninger&gt;`. Unescaping first turns those into `<losninger>`, which the tag
    pattern then eats — SILENTLY deleting the quoted word from the ad. Three ads in 2 000 carry
    the construction, and nothing about the wrong order raises."""
    out = nav._text("<p>Vi selger &lt;losninger&gt; som virker.</p>")
    assert out == "Vi selger <losninger> som virker."


def test_paragraph_structure_survives_but_markup_does_not():
    """Tags are stripped because the description feeds `search_tsv` and markup in a tsvector is
    junk lexemes; newlines are kept because these are long ads and the breaks are the
    readability."""
    out = nav._text("<h1>Tittel</h1><p>Forste avsnitt.</p><p>Andre avsnitt.</p>")
    assert "<" not in out and ">" not in out
    assert "Forste avsnitt." in out and "Andre avsnitt." in out
    assert "\n" in out


# --- scope filter --------------------------------------------------------------------------

def test_an_ad_is_ingested_when_the_publishers_code_can_rank_it(tmp_path, monkeypatch):
    """The rule changed shape on 2026-08-26: rankability, not major group.

    `frozenset("123")` refused half the register (2 729 of 5 445 ads on the refreshed answer
    key) to bound one harm — unrankable inventory reaching the widened retrieval path, which
    drops the recall predicate. A retail ad coded 5223 is not that: `sales` is a category a
    subscriber can select and the predicate can filter on. `platsbanken` has ingested majors
    4-9 on exactly this rule since 2026-08-09.
    """
    pages = [[_item("prof"), _item("retail")]]
    entries = {"prof": _content(categoryList=[{"categoryType": "STYRK08", "code": "2223"}]),
               "retail": _content(title="Butikkmedarbeider",
                                  categoryList=[{"categoryType": "STYRK08", "code": "5223"}])}
    _fake_nav(monkeypatch, pages, entries)
    got = {p.title: p.source_category for p in _src(tmp_path).run()}
    assert got == {"Sykepleier til hjemmetjenesten": "healthcare",
                   "Butikkmedarbeider": "sales"}


def test_an_ad_is_never_ingested_without_a_way_to_rank_it():
    """The general form, and the harm the old major filter was really guarding against.

    Same shape as `platsbanken`'s `test_a_field_is_never_ingested_without_a_way_to_rank_it`.
    A code outside majors 1-3 buys its way in with a category or not at all — cleaners (9112,
    100 ads and the single largest refusal), hairdressers (5141), security (5411), spa (5142),
    sailors (8350) and general office clerks (4110) have no counterpart in `CATEGORIES`, and
    mapping them to the nearest one is what the answer key's first rule forbids.
    """
    for code in ("9112", "5141", "5411", "5142", "8350", "4110", "4224"):
        assert not nav._in_scope([code], nav.STYRK_MAJOR_KEEP), f"{code} has no category"
        assert nav._styrk_category([code]) is None
    for code in ("5223", "5311", "5321", "5322", "7115", "8332", "9412"):
        assert nav._in_scope([code], nav.STYRK_MAJOR_KEEP), f"{code} maps and must be kept"
        assert nav._styrk_category([code]) in taxonomy.CATEGORIES


def test_a_white_collar_ad_is_kept_even_when_its_code_maps_to_nothing():
    """1112 senior officials, 2621 archivists: majors 1-3 are kept on the *title* being
    readable, so an unmapped code there is not a reason to refuse the ad. Losing that would be
    a regression dressed as a simplification."""
    for code in ("1112", "2621", "3411"):
        assert nav._styrk_category([code]) is None, f"{code} is mapped — pick another example"
        assert nav._in_scope([code], nav.STYRK_MAJOR_KEEP)


def test_an_ad_with_no_code_at_all_is_kept():
    """Refusing an ad for carrying less metadata is the wrong direction: the title is all
    there is, and it may well read."""
    assert nav._in_scope([], nav.STYRK_MAJOR_KEEP)


def test_the_bpa_family_arrives_with_a_category_its_title_cannot_supply(tmp_path, monkeypatch):
    """The reason this filter was worth changing, stated as the case it fixes.

    Norway's BPA (*brukerstyrt personlig assistanse*) is 229 ads in the refreshed key and
    **203 of them are titles no pattern can read** — "Er du min nye BPA-assistent?" names no
    occupation at all. Adding vocabulary was measured at +22/+26 on the graded buckets and
    **zero corpus postings**, because the ads were refused at ingest. The code answers what the
    title cannot, and it also sidesteps the `personlig assistent` false friend: in Norwegian
    that title can be an office PA, and 5322 cannot.
    """
    title = "Er du min nye BPA-assistent?"
    assert taxonomy.classify(title) == taxonomy.UNCATEGORISED, "premise: the title says nothing"

    _fake_nav(monkeypatch, [[_item("bpa")]],
              {"bpa": _content(title=title,
                               categoryList=[{"categoryType": "STYRK08", "code": "5322"}])})
    posting, = _src(tmp_path).run()
    assert posting.source_category == "social_care"
    assert taxonomy.classify(posting.title, posting.source_category) == "social_care"


def test_the_filter_bites_in_fetch_so_out_of_scope_ads_are_never_mirrored(
        tmp_path, monkeypatch):
    """Asserting only on `normalize` would leave the real path untested and let the mirror grow
    with half the register — the same point `test_fetch_applies_the_isco_filter` makes for
    mpsv."""
    _fake_nav(monkeypatch, [[_item("d")]],
              {"d": _content(categoryList=[{"categoryType": "STYRK08", "code": "9112"}])})
    _src(tmp_path).fetch()
    mirror = json.loads((tmp_path / "nav_mirror.json").read_text(encoding="utf-8"))
    assert mirror["ads"] == {}, "an out-of-scope ad must not occupy the mirror"


# --- the category hint ---------------------------------------------------------------------

def test_every_mapped_styrk_value_is_a_real_category():
    """A typo here is silent: `classify` discards a non-canonical hint, and before it did, five
    adapters put 14% of the corpus into categories no query could name."""
    assert not {v for v in nav.STYRK_CATEGORIES.values() if v not in taxonomy.CATEGORIES}


def test_a_longer_styrk_prefix_wins():
    assert nav._styrk_category(["2511"]) == "software_engineering"   # 251 beats 21
    assert nav._styrk_category(["2149"]) == "engineering"            # falls back to 21


def test_codes_that_disagree_produce_no_hint_at_all():
    """13.6% of ads carry more than one STYRK08 code and every one is scored 1.0, so there is
    no best one to take. `classify` uses a hint as the fallback for titles its patterns cannot
    read, so a wrong hint is not a near miss — it is a category no query can correct."""
    assert nav._styrk_category(["2223", "2511"]) is None
    assert nav._styrk_category(["2223", "2221"]) == "healthcare"     # agreeing codes are fine


def test_an_unmapped_code_returns_none_rather_than_a_guess():
    assert nav._styrk_category(["9999"]) is None
    assert nav._styrk_category([]) is None


def test_the_title_still_wins_over_the_code():
    assert taxonomy.classify("Data Engineer", "healthcare") == "data_engineering"


# --- normalisation -------------------------------------------------------------------------

def test_country_is_read_from_the_posting_and_not_assumed_to_be_norway(tmp_path, monkeypatch):
    """NAV is Norway's register and a country constant is the obvious shortcut. It is wrong:
    6 of 2 000 live ads are in Spain, Greece, the USA and Denmark. That is the Arbeitnow
    mistake, where a source constant overrode the posting's own text and hid UK roles from UK
    subscribers. The country NAME goes into the location so `service.geo` resolves it once."""
    _fake_nav(monkeypatch, [[_item("es")]],
              {"es": _content(workLocations=[{"country": "SPANIA", "city": "MALAGA"}])})
    posting = _src(tmp_path).run()[0]
    assert posting.country_code is None, "the adapter must not resolve the country itself"
    assert "Malaga" in posting.location and "Spania" in posting.location


def test_remote_signal_is_never_claimed(tmp_path, monkeypatch):
    """No structured remote field exists in this payload. Inventing one from the text is the
    2026-07-28 false-remote bug; `geo.work_mode` reads the posting's own words downstream."""
    _fake_nav(monkeypatch, [[_item()]],
              {"u1": _content(description="<p>Hjemmekontor og full fleksibilitet!</p>")})
    assert _src(tmp_path).run()[0].remote_signal is None


def test_posting_id_hashes_the_uuid_so_a_link_change_cannot_churn_the_corpus(
        tmp_path, monkeypatch):
    """`posting_id = md5(url)` re-creates a whole inventory when a source changes its URL
    scheme, and NAV has already moved host once. The uuid is immutable, so the id survives any
    future link repair — the mpsv split, where `ID_URL` and `JOB_URL` are deliberately
    different strings."""
    _fake_nav(monkeypatch, [[_item()]], {"u1": _content()})
    first = _src(tmp_path).run()[0]
    _fake_nav(monkeypatch, [[_item()]],
              {"u1": _content(link="https://arbeidsplassen.nav.no/NEW/SCHEME/u1")})
    second = _src(tmp_path / "b").run()[0]
    assert first.posting_id == second.posting_id, "the id must not follow the link"
    assert second.url.endswith("/NEW/SCHEME/u1"), "but the link itself must be refreshed"


def test_a_missing_link_falls_back_to_the_canonical_ad_url(tmp_path, monkeypatch):
    """A posting with no usable URL is dropped by `service.ingest`, and the uuid is enough to
    rebuild NAV's own permalink — so an empty `link` costs nothing rather than a whole ad."""
    _fake_nav(monkeypatch, [[_item()]], {"u1": _content(link="")})
    assert _src(tmp_path).run()[0].url == \
        "https://arbeidsplassen.nav.no/stillinger/stilling/u1"


# --- the change log and the mirror ----------------------------------------------------------

def test_the_whole_active_set_is_emitted_every_run_even_when_nothing_changed(
        tmp_path, monkeypatch):
    """**The reason this adapter has a mirror at all.** The feed is an append-only change log,
    so an ad unchanged for a month appears in no recent window — while
    `store.deactivate_stale(days=7)` deactivates anything not re-seen in seven days, with no
    per-source exemption. If a quiet day emits nothing, Norwegian inventory decays to zero
    within a week, silently."""
    _fake_nav(monkeypatch, [[_item()]], {"u1": _content()})
    src = _src(tmp_path)
    assert len(src.run()) == 1
    # second run: the feed reports NOTHING new at all
    _fake_nav(monkeypatch, [[]], {})
    again = _src(tmp_path).run()
    assert len(again) == 1, "a quiet day must still re-emit the mirrored ad"
    assert again[0].title == "Sykepleier til hjemmetjenesten"


def test_an_ad_that_goes_inactive_is_removed_from_the_mirror(tmp_path, monkeypatch):
    """`/vilkar-api` requires a withdrawn ad to be removed *straks*. Because the mirror re-emits
    everything, an ad left in it would be re-seen daily for ever and `deactivate_stale` could
    never expire it — the mirror would defeat the very mechanism it exists to satisfy."""
    _fake_nav(monkeypatch, [[_item()]], {"u1": _content()})
    assert len(_src(tmp_path).run()) == 1
    _fake_nav(monkeypatch, [[_item(status="INACTIVE")]], {"u1": _content()})
    assert _src(tmp_path).run() == []


def test_an_ad_created_and_withdrawn_inside_one_walk_never_survives(tmp_path, monkeypatch):
    """One uuid appears many times in a change log and the LAST entry wins."""
    _fake_nav(monkeypatch, [[_item(), _item(status="INACTIVE")]], {"u1": _content()})
    assert _src(tmp_path).run() == []


def test_an_expired_ad_is_dropped_even_though_the_feed_never_mentions_it_again(
        tmp_path, monkeypatch):
    """`expires` is the other half of the obligation, and the only one the change log cannot
    deliver: an ad that simply runs out its clock generates no feed entry at all."""
    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    _fake_nav(monkeypatch, [[_item()]], {"u1": _content(expires=past)})
    assert _src(tmp_path).run() == []


def test_an_ad_with_no_expiry_is_kept_rather_than_guessed_at():
    assert nav._expired(None, date(2026, 8, 11)) is False
    assert nav._expired("not a date", date(2026, 8, 11)) is False
    assert nav._expired("2026-08-10T00:00:00+02:00", date(2026, 8, 11)) is True


def test_the_cursor_is_persisted_so_the_next_run_resumes_rather_than_re_walking(
        tmp_path, monkeypatch):
    _fake_nav(monkeypatch, [[_item()], []], {"u1": _content()})
    _src(tmp_path).fetch()
    state = json.loads((tmp_path / "nav_mirror.json").read_text(encoding="utf-8"))
    assert state["cursor"], "a cursor must be stored or every run is a cold start"


def test_a_corrupt_mirror_starts_cold_instead_of_crashing(tmp_path, monkeypatch):
    """A half-written mirror must cost one expensive run, never the source. The write is atomic
    for the same reason."""
    (tmp_path / "nav_mirror.json").write_text("{not json", encoding="utf-8")
    _fake_nav(monkeypatch, [[_item()]], {"u1": _content()})
    assert len(_src(tmp_path).run()) == 1


def test_a_dead_feed_yields_nothing_rather_than_raising(tmp_path, monkeypatch):
    def dead(url, headers=None, timeout=None, **kw):
        if url.endswith("/api/publicToken"):
            return _Resp(text="a.b.c")
        return _Resp(status=503)

    monkeypatch.setattr(nav.requests, "get", dead)
    monkeypatch.setattr(nav.politeness, "throttle", lambda url: None)
    monkeypatch.setattr(nav.politeness, "robots_allows", lambda url: True)
    assert _src(tmp_path).fetch() == []


def test_a_robots_refusal_stops_the_source_before_it_fetches(tmp_path, monkeypatch):
    """The SmartRecruiters lesson, written into the adapter that came after it: an adapter that
    leaves the machine must ASK, not merely send an honest user agent."""
    calls = []
    monkeypatch.setattr(nav.politeness, "robots_allows", lambda url: calls.append(url) or False)
    monkeypatch.setattr(nav.requests, "get", lambda *a, **k:
                        pytest.fail("fetched despite a robots refusal"))
    assert _src(tmp_path).fetch() == []
    assert calls, "robots_allows must actually be consulted"


def test_the_adapter_throttles_every_request_it_makes(tmp_path, monkeypatch):
    """`politeness.throttle` is the 1 s/host guarantee. SmartRecruiters imported the module and
    took only HEADERS from it, then made ~6 150 requests a night at 8-way concurrency."""
    throttled = []
    _fake_nav(monkeypatch, [[_item()]], {"u1": _content()})
    monkeypatch.setattr(nav.politeness, "throttle", lambda url: throttled.append(url))
    _src(tmp_path).fetch()
    assert len(throttled) >= 3, "token, feed and detail must each be throttled"


def test_the_weekday_trap_is_detected_rather_than_keying_2023(tmp_path, monkeypatch, caplog):
    """A hand-built `If-Modified-Since` whose weekday does not match its date is SILENTLY
    ignored and NAV serves its 2023-06-14 head — a 200 with a full page of real, three-year-old
    ads. The adapter builds the header with `email.utils.format_datetime`; this asserts it
    notices if the feed answers with the old head anyway."""
    _fake_nav(monkeypatch, [[_item(modified="2023-06-14T12:21:40+02:00")]], {"u1": _content()})
    with caplog.at_level("ERROR"):
        assert _src(tmp_path).fetch() == []
    assert any("2023" in r.message for r in caplog.records)


def test_the_detail_budget_bounds_a_cold_start(tmp_path, monkeypatch):
    """`MAX_DETAILS` is what stops a cold start eating the export window on its first day. The
    mirror fills over the next few runs instead, which is why no backfill is needed."""
    items = [_item(f"u{n}") for n in range(10)]
    entries = {f"u{n}": _content(link=f"https://arbeidsplassen.nav.no/stillinger/stilling/u{n}")
               for n in range(10)}
    _fake_nav(monkeypatch, [items], entries)
    assert len(_src(tmp_path, max_details=3).run()) == 3


def test_normalize_re_applies_the_scope_filter_to_an_ALREADY_MIRRORED_ad(tmp_path):
    """The mirror outlives the code, which is what makes this second filter load-bearing
    rather than redundant.

    `fetch` filters on the way in, so under normal operation `normalize` never sees an
    out-of-scope ad — which is exactly why a mutation removing this check survived every other
    test in the file. But the mirror is persisted state: if the scope is ever narrowed, every
    ad already stored under the old rule stays stored, and an ad nobody edits is never
    re-fetched, so the entry-side filter can never revisit it. Without this check those ads
    would be emitted daily, for ever.

    This is the one class of bug the mirror introduces that no other source in the repo can
    have, so it is pinned here rather than left to be rediscovered.
    """
    stale_mirror = [{
        "uuid": "old", "title": "Renholder til hytter", "employer": "Havrent AS",
        "description": "Vi soker deg.", "location": "Oslo", "country": "NORGE",
        "expires": "2099-01-01T00:00:00+02:00", "published": "2026-08-01T00:00:00+02:00",
        "link": "https://arbeidsplassen.nav.no/stillinger/stilling/old",
        # 9112 cleaners: no counterpart in CATEGORIES, so unrankable under `_in_scope` too.
        # It used to say 5223 (retail), which the rankability rule now admits as `sales` —
        # the example had to move for the guarantee to still be tested.
        "styrk": ["9112"],
    }]
    assert NavSource(state_dir=tmp_path).normalize(stale_mirror) == []


def test_an_unchanged_ad_costs_no_detail_call_on_the_next_run(tmp_path, monkeypatch):
    """**The difference between a few requests a day and ~1 600.**

    The feed re-announces an ad whenever anything about it moves, and it carries `sistEndret`
    on the entry itself. Without comparing that stamp the daily cost is one detail request per
    feed ENTRY rather than per genuinely changed ad — measured at ~1 625 entries a day against
    ~2 673 distinct active ads, so most of them are re-announcements.

    A changed stamp still means re-read: the employer may have edited the title, the location
    or the closing date."""
    calls = []
    entries = {"u1": _content()}

    def counting(monkeypatch_):
        real = nav.requests.get

        def wrapped(url, **kw):
            if "/feedentry/" in url:
                calls.append(url)
            return real(url, **kw)
        monkeypatch_.setattr(nav.requests, "get", wrapped)

    _fake_nav(monkeypatch, [[_item(modified="2026-08-10T09:00:00+02:00")]], entries)
    monkeypatch.setattr(nav, "_ITEM_STAMP", None, raising=False)
    # give the feed entry a sistEndret the mirror can remember
    stamped = _item()
    stamped["_feed_entry"]["sistEndret"] = "2026-08-10T09:00:00+02:00"
    _fake_nav(monkeypatch, [[stamped]], entries)
    counting(monkeypatch)
    assert len(_src(tmp_path).run()) == 1
    first = len(calls)
    assert first == 1, "the first sighting must fetch the detail"

    # same stamp -> no call at all
    _fake_nav(monkeypatch, [[stamped]], entries)
    counting(monkeypatch)
    assert len(_src(tmp_path).run()) == 1, "and the ad is still emitted from the mirror"
    assert len(calls) == first, "an unchanged ad must cost no detail request"

    # edited -> fetched again
    edited = _item()
    edited["_feed_entry"]["sistEndret"] = "2026-08-11T09:00:00+02:00"
    _fake_nav(monkeypatch, [[edited]], entries)
    counting(monkeypatch)
    _src(tmp_path).run()
    assert len(calls) == first + 1, "a changed stamp must be re-read"


def test_a_bounded_cold_start_carries_its_backlog_instead_of_losing_it(tmp_path, monkeypatch):
    """**The bug this file did not catch until it was found on the box.**

    The walk advances the cursor past every entry it reads, but only `max_details` of them can
    be fetched in one run. A cold start reports ~7 471 changed ads against a budget of 1 200 —
    so without carrying the remainder forward, the cursor moves past ~6 200 ads that were never
    mirrored and they become invisible until an employer happens to edit one. Norway would
    stall at one batch instead of climbing to the full register, and nothing would say so: the
    run succeeds, the mirror is written, the log looks healthy.

    The second run below reports NO new feed entries at all, which is the case that separates a
    persisted backlog from a lost one."""
    items = [_item(f"u{n}") for n in range(10)]
    entries = {f"u{n}": _content(link=f"https://arbeidsplassen.nav.no/stillinger/stilling/u{n}")
               for n in range(10)}
    _fake_nav(monkeypatch, [items], entries)
    assert len(_src(tmp_path, max_details=4).run()) == 4

    state = json.loads((tmp_path / "nav_mirror.json").read_text(encoding="utf-8"))
    assert len(state["pending"]) == 6, "the un-fetched remainder must be persisted"

    # second run: the feed has nothing new to say, and the backlog must still drain
    _fake_nav(monkeypatch, [[]], entries)
    assert len(_src(tmp_path, max_details=4).run()) == 8, "the backlog must be drained"
    _fake_nav(monkeypatch, [[]], entries)
    assert len(_src(tmp_path, max_details=4).run()) == 10, "and drained to completion"
    state = json.loads((tmp_path / "nav_mirror.json").read_text(encoding="utf-8"))
    assert state["pending"] == []
