"""The scorer is the gate, so the gate itself has to be un-gameable.

`scripts/categorization_score.py` decides whether a categorisation change ships. Its whole
job is to stop the loop optimising "percent uncategorised", which is trivially improved by
assigning everything to something — a change that would make the product strictly worse while
every reported number got better.

Three properties keep it honest, and each is pinned here:

  - **The answer key is independent of the classifier.** Truth comes from the publisher's
    occupation code; the classifier sees only the title. If truth were ever derived from
    `taxonomy.classify`, accuracy would be 100% by construction and the gate would be
    decorative.
  - **A field with no matching category is excluded, never mapped to the nearest one.**
    Swedish social work, sanitation, agriculture and security have no counterpart in
    `CATEGORIES`. Mapping them somewhere close would grade the classifier against a wrong
    answer and reward guessing — precisely the behaviour the gate exists to prevent.
  - **Every mapped value is a real category.** A typo in the map silently grades a whole
    category as 0%, which reads as a classifier regression and would send the loop chasing a
    bug that does not exist. Same failure as the `source_category` hint guard: a value that
    no consumer can name.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from service import taxonomy

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "categorization_score.py"


def _load():
    spec = importlib.util.spec_from_file_location("categorization_score", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["categorization_score"] = module
    spec.loader.exec_module(module)
    return module


cs = _load()


# --- the map must only ever name real categories ------------------------------------------


def test_every_mapped_value_is_a_real_category():
    """A typo grades that category as 0% and reads as a regression that never happened."""
    mapped = set(cs.FIELD_MAP.values()) | set(cs.GROUP_MAP.values())
    unknown = mapped - set(taxonomy.CATEGORIES)
    assert not unknown, f"answer key names categories that do not exist: {sorted(unknown)}"


def test_uncategorised_is_never_an_expected_answer():
    """`uncategorised` is what the classifier says when it declines. Grading against it would
    reward declining, which is the opposite of what the gate is for."""
    assert taxonomy.UNCATEGORISED not in set(cs.FIELD_MAP.values())
    assert taxonomy.UNCATEGORISED not in set(cs.GROUP_MAP.values())


def test_out_of_scope_fields_are_excluded_rather_than_mapped():
    """The honest answer for a Swedish social-work ad is 'this taxonomy has no category',
    not 'healthcare, near enough'. Mapping it would score the classifier against a wrong key."""
    for field in cs.OUT_OF_SCOPE:
        assert field not in cs.FIELD_MAP, f"{field} is both out of scope and mapped"
        assert cs.truth_for({"field": field, "group": ""}) is None


def test_a_group_mapping_beats_its_field():
    """"Data/IT" spans software, IT support and platform work. Field-level truth would grade
    three distinct categories as one and report the classifier wrong when it was right."""
    assert cs.truth_for({"field": "Data/IT",
                         "group": "Supporttekniker, IT"}) == "customer_support"
    assert cs.truth_for({"field": "Data/IT",
                         "group": "Systemadministratörer"}) == "devops_platform"


def test_forklift_operators_are_logistics_not_construction():
    """ISCO 834 is 'mobile plant operators' — earthmoving and crane work, which is construction,
    EXCEPT 8344 (lifting-truck/forklift operators), which is warehouse logistics. The classifier
    reads "Skladník, obsluha manipulačních vozíků" as logistics and is right; before the 8344
    override the '834' prefix graded that correct answer as a construction miss (82 rows)."""
    assert cs.truth_for_isco("83443") == ("logistics_transport", True)   # longest prefix wins
    assert cs.truth_for_isco("8344") == ("logistics_transport", True)
    assert cs.truth_for_isco("8342") == ("construction", True)           # earthmoving, unchanged
    assert cs.truth_for_isco("83432") == ("construction", True)          # crane, unchanged


# --- scoring arithmetic --------------------------------------------------------------------


def test_score_counts_only_graded_rows():
    rows = [
        {"title": "Sjuksköterska", "field": "Hälso- och sjukvård", "group": ""},
        {"title": "Socialsekreterare", "field": "Yrken med social inriktning", "group": ""},
        {"title": "Något helt annat", "field": "Ingen aning", "group": ""},
    ]
    s = cs.score(rows)
    assert s["graded"] == 1, "out-of-scope and unmapped rows must not be graded"
    assert s["out_of_scope"] == 1
    assert s["unmapped"] == 1


def test_declining_counts_as_a_miss_not_a_pass():
    """The dangerous regression is a classifier that answers `uncategorised` everywhere and
    is scored as correct for it."""
    rows = [{"title": "zzzz nothing matches this", "field": "Hälso- och sjukvård", "group": ""}]
    s = cs.score(rows)
    assert s["graded"] == 1
    assert s["correct"] == 0
    assert s["missed_as_uncategorised"] == 1


def test_accuracy_is_reported_per_category():
    rows = [{"title": "Sjuksköterska", "field": "Hälso- och sjukvård", "group": ""},
            {"title": "Lärare", "field": "Pedagogik", "group": ""}]
    s = cs.score(rows)
    assert set(s["per_cat"]) == {"healthcare", "education"}


def test_coverage_needs_no_answer_key():
    uncat, total = cs.coverage_of(["Sjuksköterska", "zzzz", "Data Engineer"])
    assert total == 3 and uncat == 1


# --- coverage sees hints, accuracy must not -----------------------------------------------


def test_coverage_counts_a_hint_classified_row_as_covered():
    """The register sources are classified by the publisher's occupation code, not the title.
    A title-only coverage number reported the 1 534 Czech vacancies recovered on 2026-08-09 as
    zero improvement — the change looked worthless and the loop would have moved on."""
    uncat, total = cs.coverage_of([("Neklasifikovatelný název", "healthcare")])
    assert (uncat, total) == (0, 1)


def test_accuracy_never_reads_the_hint():
    """Grading a hint-classified row against the code that produced the hint is circular —
    100% by construction, and the gate becomes decorative for the sources it matters most for.

    `score` takes rows of {title, field, group} and must classify from the title alone.
    """
    import inspect

    src = inspect.getsource(cs.score)
    assert "classify(row.get(\"title\"))" in src, (
        "score() must call classify with the title only; passing a hint here would make "
        "accuracy circular for every register source"
    )


# --- the Norwegian key: NAV's feed, and the ways it lies quietly ---------------------------
#
# Every test here is written against a failure that is SILENT in production. NAV answers 200
# with well-formed JSON in each case; only the content is wrong, and a key built from wrong
# content reports a classifier number that is wrong in the same direction, with nothing to
# say so. That is the whole reason this key is worth having.


class _Resp:
    """Minimal stand-in for `requests.Response` — status, text and json() only."""

    def __init__(self, payload=None, status=200, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise AssertionError("must not parse a response that was not meant to be read")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _feed_item(uuid, status="ACTIVE", modified="2026-08-10T09:00:00+02:00"):
    return {"url": f"/api/v1/feedentry/{uuid}", "date_modified": modified,
            "_feed_entry": {"uuid": uuid, "status": status, "title": f"title {uuid}"}}


def _entry(uuid, codes, title="Sykepleier til hjemmetjenesten", **extra):
    content = {"title": title,
               "categoryList": [{"categoryType": "STYRK08", "code": c, "name": "x",
                                 "score": 1.0} for c in codes]}
    content.update(extra)
    return {"uuid": uuid, "status": "ACTIVE", "ad_content": content}


def _nav(monkeypatch, items, entries, token_text="token: a.b.c"):
    """Wire a fake NAV in place of `requests` inside the scorer module.

    **The fake validates the bearer token, and that is not decoration.** The first version of
    this helper ignored the `Authorization` header, and the mutation that sends the whole
    token blurb as a credential SURVIVED — every test still passed, because a fake that never
    checks a credential cannot tell a good one from a sentence. Real NAV answers 401. So the
    fake answers 401 too, on anything that is not a bare three-segment JWT.
    """

    def fake_get(url, **kw):
        if url.endswith("/api/publicToken"):
            return _Resp(text=token_text)
        bearer = (kw.get("headers") or {}).get("Authorization", "")
        token = bearer.removeprefix("Bearer ")
        if len(token.split(".")) != 3 or any(c.isspace() for c in token) or not token:
            return _Resp(status=401)          # exactly what NAV does with a malformed token
        if "/feedentry/" in url:
            uuid = url.rsplit("/", 1)[-1]
            return _Resp(entries[uuid]) if uuid in entries else _Resp(status=404)
        return _Resp({"items": items, "next_url": None})

    fake_requests = type("R", (), {"get": staticmethod(fake_get),
                                   "RequestException": Exception})()
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    from ingestion import politeness
    monkeypatch.setattr(politeness, "throttle", lambda url: None)


def _both_buckets(**kw):
    """The key is a partition, so a fixture of a few ads lands in whichever half its uuid
    hashes to. Tests that care about content, not partitioning, read both."""
    return cs.fetch_no(bucket=0, **kw) + cs.fetch_no(bucket=1, **kw)


def test_the_weekday_trap_raises_instead_of_keying_three_year_old_ads(monkeypatch):
    """A hand-built `If-Modified-Since` whose weekday does not match its date is SILENTLY
    ignored by NAV, which then serves its 2023-06-14 head instead of the window asked for.

    Verified live 2026-08-11, same date and host one second apart: `Sun, 09 Aug 2026` returned
    the 2026 window, `Mon, 09 Aug 2026` returned 2023. Nothing in the response says so — it is
    a 200 with a full page of real ads. Without this guard the key is built from ads three
    years stale and every number computed from it is wrong with nothing to indicate it."""
    _nav(monkeypatch, [_feed_item("a", modified="2023-06-14T12:21:40+02:00")], {})
    with pytest.raises(RuntimeError, match="2023"):
        cs.fetch_no(limit=5)


def test_an_ad_whose_codes_disagree_is_dropped_rather_than_picked_from(monkeypatch):
    """~9% of NAV ads carry more than one STYRK08 code and every one is scored 1.0, so there
    is no "best" one to take. Taking the first would grade the classifier against an arbitrary
    choice. The map's standing rule is never to map to something close, and a smaller key is
    better than one that guesses."""
    items = [_feed_item("keep"), _feed_item("drop")]
    entries = {
        # 2223 and 2221 are both nursing -> both healthcare, so they agree and it survives.
        "keep": _entry("keep", ["2223", "2221"]),
        # 2223 healthcare vs 2512 software developers — a real disagreement.
        "drop": _entry("drop", ["2223", "2512"], title="Sykepleier og utvikler"),
    }
    _nav(monkeypatch, items, entries)
    titles = {r["title"] for r in _both_buckets(limit=10)}
    assert "Sykepleier til hjemmetjenesten" in titles, "agreeing codes must be kept"
    assert "Sykepleier og utvikler" not in titles, "disagreeing codes must be dropped"


def test_an_ad_that_went_inactive_mid_walk_never_enters_the_key(monkeypatch):
    """The feed is an append-only CHANGE LOG, so one uuid appears many times and the LAST
    entry wins. An ad created Monday and withdrawn Wednesday appears twice in a Monday-to-
    Friday walk; keying the first would grade the classifier on a vacancy that is gone."""
    items = [_feed_item("gone", status="ACTIVE"), _feed_item("gone", status="INACTIVE")]
    _nav(monkeypatch, items, {"gone": _entry("gone", ["2223"])})
    assert _both_buckets(limit=10) == [], "an entry that ended INACTIVE must not be keyed"


def test_the_key_holds_a_title_and_a_code_and_no_personal_data(monkeypatch):
    """NAV ads name a contact in the large majority of cases. The key stores a public job
    title and an occupation code; `contactList`, `employer` and `description` are never read,
    which is what keeps this out of the privacy policy (security rule 4).

    Asserted over the whole row rather than over named fields, because a future edit could add
    any field — the same shape as the adapters' own contact-leak tests."""
    entries = {"a": _entry("a", ["2223"],
                           contactList=[{"name": "Kari Nordmann", "email": "kari@x.no",
                                         "phone": "+47 22 33 44 55"}],
                           employer={"name": "Oslo kommune", "orgnr": "123456789"},
                           description="Ring Kari paa 22 33 44 55 eller kari@x.no")}
    _nav(monkeypatch, [_feed_item("a")], entries)
    rows = _both_buckets(limit=10)
    assert len(rows) == 1
    blob = " ".join(str(v) for v in rows[0].values())
    for leak in ("Kari", "kari@x.no", "22 33 44 55", "Oslo kommune", "123456789"):
        assert leak not in blob, f"{leak!r} reached the answer key"
    assert set(rows[0]) == {"title", "isco"}, "the key holds exactly title and code"


def test_the_buckets_are_disjoint_and_are_a_property_of_the_ad(monkeypatch):
    """The holdout is a partition on the ad's own immutable uuid, not a second sample — so the
    halves stay disjoint as the feed grows and a re-run does not reshuffle them. Vocabulary
    written by reading bucket 0 must be scored on bucket 1, or the number measures
    memorisation rather than classification."""
    uuids = [f"uuid-{n}" for n in range(40)]
    items = [_feed_item(u) for u in uuids]
    entries = {u: _entry(u, ["2223"], title=f"Sykepleier {u}") for u in uuids}
    _nav(monkeypatch, items, entries)
    b0 = {r["title"] for r in cs.fetch_no(limit=100, bucket=0)}
    b1 = {r["title"] for r in cs.fetch_no(limit=100, bucket=1)}
    assert b0 and b1, "both halves must be non-empty across 40 ads"
    assert not (b0 & b1), "the halves must be disjoint"
    assert len(b0 | b1) == 40, "together they must cover every ad exactly once"
    assert {r["title"] for r in cs.fetch_no(limit=100, bucket=0)} == b0, "and be stable"


def test_the_bearer_token_is_extracted_from_the_blurb_not_sent_whole(monkeypatch):
    """`GET /api/publicToken` returns a human-readable sentence with the JWT inside it, not a
    bare token. `resp.text.strip()` would send the whole blurb as a credential and every later
    call would 401 — which downstream is indistinguishable from a quiet day."""
    _nav(monkeypatch, [_feed_item("a")], {"a": _entry("a", ["2223"])},
         token_text="Current public token for Nav Job Vacancy Feed:\n  aaa.bbb.ccc  \n")
    assert len(_both_buckets(limit=10)) == 1, "the JWT must be found inside the blurb"


def test_an_ad_with_no_styrk_code_is_skipped_not_guessed_at(monkeypatch):
    """An ad the publisher did not code cannot grade anything. It is skipped and counted,
    never resolved to a nearby category."""
    items = [_feed_item("coded"), _feed_item("bare")]
    entries = {"coded": _entry("coded", ["2223"]),
               "bare": _entry("bare", [], title="Vi soeker deg")}
    _nav(monkeypatch, items, entries)
    assert [r["title"] for r in _both_buckets(limit=10)] == ["Sykepleier til hjemmetjenesten"]
