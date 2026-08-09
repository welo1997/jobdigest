"""The title-classification exchange: what the routine returns is untrusted input.

Two things here matter more than the rest.

**The trust boundary.** `categories.json` is written by a claude.ai routine and travels via
cloud storage. A returned category that is not in `taxonomy.CATEGORIES` must never reach
`postings.role_category` — that is exactly the 2026-08-08 failure, where five adapters passed
a raw third-party string and 14 135 active postings ended up in categories no subscriber
could select and no query could match, with nothing reporting it. A model's answer is a
third-party string too, and these tests are written to fail if it is ever trusted.

**The export carries no personal data.** Unlike `shortlists.json`, this file is public job
titles and integer indices. The test asserts on the whole serialised payload rather than on
the fields the export happens to build, so a future edit that adds a company, a URL or —
worst — a subscriber, goes red.
"""

from __future__ import annotations

import json

import pytest

from service import categorize_exchange as cx
from service import taxonomy


# ------------------------------------------------------------------- the key ---

@pytest.mark.parametrize("a,b", [
    ("Data Analyst", "data analyst"),                  # case
    ("Data  Analyst", "Data Analyst"),                 # collapsed whitespace
    (" Data Analyst\n", "Data Analyst"),               # trimmed
    ("Data Analyst -", "Data Analyst"),                # trailing junk
    ("Undersköterska", "UNDERSKÖTERSKA"),              # non-ASCII casefold
])
def test_titles_that_are_the_same_job_share_a_key(a, b):
    assert cx.normalise_title(a) == cx.normalise_title(b)


@pytest.mark.parametrize("a,b", [
    # The employer is deliberately NOT stripped. Merging these would file one job under the
    # other's category, and nothing downstream could tell. Cache misses are recoverable;
    # wrong answers are not.
    ("Butiksmedarbetare, Willys Helsingborg", "Butiksmedarbetare, ICA Maxi Lund"),
    ("Front-end Developer", "Frontend Developer"),
    ("Sjuksköterska", "Sjuksköterskor"),
])
def test_titles_that_are_not_the_same_job_do_not(a, b):
    assert cx.normalise_title(a) != cx.normalise_title(b)


def test_a_key_is_bounded_and_never_none():
    assert cx.normalise_title(None) == ""
    assert cx.normalise_title("-  -") == ""
    assert len(cx.normalise_title("x" * 5000)) == 300


# ---------------------------------------------------------------- the export ---

def _export(tmp_path, monkeypatch, titles, asked=frozenset(), limit=10):
    monkeypatch.setattr(cx.store, "asked_title_keys", lambda: set(asked))
    monkeypatch.setattr(cx.store, "uncategorised_titles", lambda: list(titles))
    path = tmp_path / "titles.json"
    n = cx.export_uncategorised(str(path), limit=limit)
    return n, json.loads(path.read_text(encoding="utf-8"))


def test_the_export_carries_titles_and_nothing_else(tmp_path, monkeypatch):
    """Asserted against the serialised file, not the dict the code built: the point is what
    leaves the machine. `shortlists.json` earned this test the hard way — an email address in
    a file that travels to cloud storage is a privacy-policy change, not a field."""
    _n, payload = _export(tmp_path, monkeypatch,
                          [{"title": "Extrajobb på lager", "n": 12}])
    assert payload["titles"] == [{"i": 0, "title": "Extrajobb på lager"}]
    assert set(payload) == {"generated_at", "instructions", "categories", "titles"}
    for banned in ("@", "posting_id", "profile_id", "company", "http"):
        assert banned not in json.dumps(payload["titles"], ensure_ascii=False)


def test_the_export_offers_the_real_vocabulary(tmp_path, monkeypatch):
    """The routine is told which words are answers. A category list that drifts from the
    taxonomy is how a model returns something reasonable that this system cannot store."""
    _n, payload = _export(tmp_path, monkeypatch, [{"title": "Ställare", "n": 1}])
    assert payload["categories"] == list(taxonomy.CATEGORIES)
    assert taxonomy.UNCATEGORISED in payload["categories"]


def test_a_title_already_asked_about_is_not_asked_again(tmp_path, monkeypatch):
    """Including a recorded decline. Without this the residue never shrinks: every run
    re-asks about the same unreadable titles and the answers are the same every time."""
    n, payload = _export(tmp_path, monkeypatch,
                         [{"title": "Ställare", "n": 5}, {"title": "Extrajobb", "n": 9}],
                         asked={cx.normalise_title("Extrajobb")})
    assert n == 1
    assert [t["title"] for t in payload["titles"]] == ["Ställare"]


def test_titles_that_differ_only_in_shape_are_asked_about_once(tmp_path, monkeypatch):
    n, payload = _export(tmp_path, monkeypatch,
                         [{"title": "Ställare", "n": 3}, {"title": "ställare ", "n": 4}])
    assert n == 1
    assert payload["titles"][0]["title"] in {"Ställare", "ställare "}


def test_a_truncated_export_takes_the_titles_that_stand_for_the_most_postings(
        tmp_path, monkeypatch, caplog):
    """And says what it dropped. A silent cap reads as "we covered everything", which is the
    failure mode this repo names for every other bounded step."""
    titles = [{"title": "Rare title", "n": 1}, {"title": "Common title", "n": 400}]
    with caplog.at_level("WARNING"):
        n, payload = _export(tmp_path, monkeypatch, titles, limit=1)
    assert n == 1
    assert [t["title"] for t in payload["titles"]] == ["Common title"]
    assert "did NOT fit the limit" in caplog.text


# ---------------------------------------------------------------- the import ---

def _import(tmp_path, monkeypatch, answers, asked_titles=("Ställare", "Extrajobb")):
    written: list = []
    monkeypatch.setattr(cx.store, "upsert_title_categories",
                        lambda rows, **kw: written.extend(rows) or len(list(rows)))
    asked = tmp_path / "titles.json"
    asked.write_text(json.dumps(
        {"titles": [{"i": i, "title": t} for i, t in enumerate(asked_titles)]}),
        encoding="utf-8")
    answers_path = tmp_path / "categories.json"
    answers_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
    cx.import_categories(str(answers_path), str(asked))
    return written


def test_a_valid_answer_is_stored_under_the_normalised_key(tmp_path, monkeypatch):
    written = _import(tmp_path, monkeypatch,
                      {"categories": [{"i": 0, "category": "manufacturing_production"}]})
    assert written == [(cx.normalise_title("Ställare"), "manufacturing_production",
                        "Ställare")]


def test_a_category_that_does_not_exist_is_dropped(tmp_path, monkeypatch):
    """The 2026-08-08 hint guard, arriving by a new route. `role_category` must only ever
    hold a value a subscriber can select and a query can match."""
    written = _import(tmp_path, monkeypatch, {"categories": [
        {"i": 0, "category": "Industriell tillverkning"},   # a real SSYK label, not a category
        {"i": 1, "category": "SALES"},                      # right word, wrong case
    ]})
    assert written == []


def test_an_index_nobody_asked_about_is_dropped(tmp_path, monkeypatch):
    """The file cannot invent a title to classify — only indices the export wrote are real."""
    written = _import(tmp_path, monkeypatch, {"categories": [
        {"i": 99, "category": "sales"},
        {"i": -1, "category": "sales"},
        {"i": "0", "category": "sales"},     # a string index is not an index
        {"i": True, "category": "sales"},    # ...and a bool is not an int here either
    ]})
    assert written == []


def test_a_malformed_record_is_skipped_not_fatal(tmp_path, monkeypatch):
    """One bad row must not cost the whole run, exactly as in `import_picks`."""
    written = _import(tmp_path, monkeypatch, {"categories": [
        "not a dict", None, {"i": 0}, {"category": "sales"},
        {"i": 1, "category": "logistics_transport"},
    ]})
    assert written == [(cx.normalise_title("Extrajobb"), "logistics_transport", "Extrajobb")]


def test_a_decline_is_stored_so_it_is_not_asked_again(tmp_path, monkeypatch):
    written = _import(tmp_path, monkeypatch,
                      {"categories": [{"i": 0, "category": taxonomy.UNCATEGORISED}]})
    assert written == [(cx.normalise_title("Ställare"), taxonomy.UNCATEGORISED, "Ställare")]


def test_answers_without_an_export_to_check_them_against_are_refused(tmp_path, monkeypatch):
    """An unreadable or empty asked-file means nothing can be validated. Importing anyway
    would mean trusting the answer file's own account of what it was asked."""
    monkeypatch.setattr(cx.store, "upsert_title_categories",
                        lambda rows, **kw: pytest.fail("nothing may be written"))
    asked = tmp_path / "titles.json"
    asked.write_text(json.dumps({"titles": []}), encoding="utf-8")
    answers = tmp_path / "categories.json"
    answers.write_text(json.dumps({"categories": [{"i": 0, "category": "sales"}]}),
                       encoding="utf-8")
    assert cx.import_categories(str(answers), str(asked)) == 0


# ------------------------------------------------------------ what ingest does ---
# The cache only decides rows nothing else could. That ordering is what keeps a model's
# answer from overriding the two answers this repo can actually reason about — a reviewable
# pattern, and an occupation code assigned by the publisher.

def test_the_cache_is_consulted_only_where_everything_else_declined():
    from collections import Counter

    from service import ingest

    cache = {cx.normalise_title("Ställare"): "logistics_transport",
             cx.normalise_title("Sjuksköterska"): "sales"}
    stats: Counter = Counter()

    # A title the patterns read: the cache must not get a vote, however confident it is.
    assert ingest._classify("Sjuksköterska", None, cache, stats) == "healthcare"
    # A hint from the publisher also outranks it.
    assert ingest._classify("Ställare", "manufacturing_production", cache, stats) == \
        "manufacturing_production"
    assert stats == Counter(), "neither row was residue"

    # Only what nothing else could read reaches the cache.
    assert ingest._classify("Zzz Oklassificerbart", None,
                            {cx.normalise_title("zzz oklassificerbart"): "operations"},
                            stats) == "operations"
    assert stats["residue"] == 1 and stats["cache_hit"] == 1


def test_a_missing_or_declined_answer_leaves_the_row_exactly_as_it_was():
    """The cache can only ever add a category, never remove or change one — so an empty,
    stale or unavailable cache degrades to the behaviour from before it existed."""
    from collections import Counter

    from service import ingest

    stats: Counter = Counter()
    for cache in ({}, None, {cx.normalise_title("Extrajobb"): taxonomy.UNCATEGORISED}):
        assert ingest._classify("Extrajobb", None, cache, stats) == taxonomy.UNCATEGORISED
    assert stats["cache_hit"] == 0


def test_a_bare_list_is_accepted_in_either_file(tmp_path, monkeypatch):
    """The routine writes JSON by hand; accepting the obvious alternative shape costs nothing
    and a rejected file costs a week — this runs weekly, so a rejection is not retried until
    the next one."""
    written = _import(tmp_path, monkeypatch, [{"i": 0, "category": "sales"}])
    assert written == [(cx.normalise_title("Ställare"), "sales", "Ställare")]

    # ...and the same on the asked side.
    stored: list = []
    monkeypatch.setattr(cx.store, "upsert_title_categories",
                        lambda rows, **kw: stored.extend(rows) or len(rows))
    asked = tmp_path / "bare-titles.json"
    asked.write_text(json.dumps([{"i": 0, "title": "Ställare"}]), encoding="utf-8")
    answers = tmp_path / "bare-categories.json"
    answers.write_text(json.dumps([{"i": 0, "category": "sales"}]), encoding="utf-8")
    assert cx.import_categories(str(answers), str(asked)) == 1
    assert stored == [(cx.normalise_title("Ställare"), "sales", "Ställare")]
