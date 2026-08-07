"""Tests for `store.query_shortlist` — the retrieval half of retrieve-then-rerank.

The matcher can only be as good as the 120 rows handed to it, and a bad shortlist fails
silently: the model dutifully reads what it is given, finds nothing that fits, and the
subscriber just gets no digest. That is what happened on 2026-07-26 — a subscriber who
selected product + design + social_media + marketing received 117 marketing/admin rows,
2 design and 1 social, and the matcher correctly returned zero picks from it.

The SQL is built by string assembly with positional parameters, so these tests inspect the
statement rather than run it (there is no Postgres in unit tests). That is enough to pin the
two things that actually broke: parameter/placeholder alignment, and the round-robin.
"""

from __future__ import annotations

import re

import pytest

from service import store


class _RecordingCursor:
    def __init__(self, sink: dict):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        call = {"sql": sql, "params": list(params or [])}
        self._sink["calls"].append(call)
        # `sql`/`params` alias the FIRST statement, which is the one every test written
        # before the retrieval floor existed means. A widened re-run appends a second call
        # rather than overwriting, so those tests keep pinning what they were written to pin.
        self._sink.setdefault("sql", call["sql"])
        self._sink.setdefault("params", call["params"])

    def fetchall(self):
        return self._sink["rows"].pop(0) if self._sink["rows"] else []


@pytest.fixture
def query(monkeypatch):
    """Run query_shortlist and hand back the SQL + params it would have executed.

    `rows` is what each successive execute() returns, so a test can make the first query
    come back under the floor and observe the widening. Default: every query returns
    nothing, which for a profile with a recall predicate means the floor always fires.
    """
    sink: dict = {"calls": [], "rows": []}
    monkeypatch.setattr(store, "cursor", lambda commit=False: _RecordingCursor(sink))

    def run(profile: dict, limit: int = 120, rows: list | None = None):
        sink["rows"] = list(rows or [])
        store.query_shortlist(profile, limit=limit)
        return sink

    return run


def _full(n: int = 200) -> list[dict]:
    """A result set comfortably above SHORTLIST_FLOOR."""
    return [{"posting_id": str(i)} for i in range(n)]


PROFILE = {
    "regions": ["cz"],
    "role_categories": ["product", "design", "social_media", "other_tech_function"],
    "stack": ["figma", "excel"],
}


# ------------------------------------------------------------------ parameters ---

def test_placeholder_and_parameter_counts_match(query):
    """psycopg2 binds %s by position in the SQL *text*, not by the order the fragments were
    appended in Python. The partition clause is written above the inner WHERE, so its
    parameter has to be passed first — get that wrong and every subsequent value shifts by
    one, which Postgres reports as a type error somewhere unrelated (or, worse, silently
    filters on the wrong column when the types happen to line up)."""
    got = query(PROFILE)
    assert got["sql"].count("%s") == len(got["params"])


def test_the_bucket_parameter_is_bound_before_the_where_params(query):
    got = query(PROFILE)
    assert got["params"][0] == PROFILE["role_categories"]   # partition clause, written first
    assert got["params"][1] == PROFILE["regions"]           # then the WHERE
    assert got["params"][-1] == 120                         # limit last


@pytest.mark.parametrize("profile", [
    PROFILE,
    {"regions": ["cz"]},                                    # no categories, no stack
    {"role_categories": ["design"], "eligible_only": False},
    {"role_categories": [], "stack": [], "regions": []},     # everything empty
    {"role_categories": ["design"], "part_time_only": True},
])
def test_counts_match_for_every_shape_of_profile(query, profile):
    """Each optional clause adds placeholders at a different point in the statement.

    Checks *every* statement issued, so the widened re-run — which drops the recall
    parameters but keeps the partition and location ones — is held to the same rule.
    """
    got = query(profile)
    assert got["calls"], "no statement was executed"
    for call in got["calls"]:
        assert call["sql"].count("%s") == len(call["params"])


# -------------------------------------------------------------- round-robin ---

def test_slots_are_allocated_per_category(query):
    """Without the partition, `order by last_seen_at desc limit 120` hands every slot to
    whichever category has the most (and freshest) inventory. other_tech_function spans
    marketing, sales, finance, HR and legal — ~7k active CZ rows against design's ~120 —
    so it wins on volume every time and the narrow categories never reach the model."""
    sql = query(PROFILE)["sql"]
    assert "row_number() over" in sql
    assert "partition by case when d.role_category = any(%s)" in sql
    assert "'__other__'" in sql
    # Rank first, recency second: rank 1 of every category, then rank 2, and so on.
    assert re.search(r"order by\s+r\.bucket_rank\s*,", sql)


def test_keyword_only_hits_share_one_bucket(query):
    """A posting pulled in by a stack keyword rather than a selected category goes to
    `__other__`. One bucket for all of them, so they get roughly one category's worth of
    slots between them instead of displacing the categories actually chosen."""
    sql = query(PROFILE)["sql"]
    assert "else '__other__'" in sql


def test_no_categories_degenerates_to_freshest_first(query):
    """With nothing selected the bucket expression matches nothing, so there is a single
    partition and bucket_rank increases in recency order — i.e. the original behaviour.
    Worth pinning: it is why this change is safe for profiles that select no roles."""
    got = query({"regions": ["cz"]})
    assert got["params"][0] == []
    assert "partition by case when d.role_category = any(%s)" in got["sql"]


def test_part_time_only_still_sorts_within_each_bucket(query):
    sql = query({"role_categories": ["design"], "part_time_only": True})["sql"]
    assert "d.is_part_time desc, d.first_seen_at desc, d.rotation" in sql   # inside the window
    assert "r.is_part_time desc, r.first_seen_at desc, r.rotation" in sql   # outer tiebreak


def test_window_orders_by_first_seen_not_last_seen(query):
    """`last_seen_at` is stamped once per ingest run, so ordering by it orders nothing: on
    2026-07-28, 12 390 of 19 439 active postings shared a single value and the 120-row window
    was resolved by physical scan order — stable, so the same subscriber saw the same 120
    candidates every day forever. Ordering must be by `first_seen_at`, with a per-day hash
    breaking the remaining ties so the backlog is sampled differently each day."""
    sql = query(PROFILE)["sql"]
    assert "d.first_seen_at desc, d.rotation" in sql
    assert "last_seen_at desc" not in sql.split(") d")[1]   # not in the ranking, only dedup
    assert "md5(p.posting_id || current_date::text) as rotation" in sql


def test_dedup_still_happens_before_ranking(query):
    """One row per dedup_key must be settled first — otherwise near-duplicate copies of the
    same job occupy several of a category's allocated slots."""
    sql = query(PROFILE)["sql"]
    inner = sql.index("distinct on (coalesce(p.dedup_key, p.posting_id))")
    assert inner > sql.index("row_number() over")   # the dedup subquery is nested deeper


# --------------------------------------------------------------- retrieval floor ---
# The generalisation of the social_media failure. A subscriber whose field the taxonomy does
# not model (sales, cybersecurity, IT support) selects categories that fit them badly and
# types keywords that may miss entirely — so `category OR keyword` retrieves almost nothing
# and they are emailed nothing, silently, forever. Below the floor the recall predicate is
# dropped and the AI matcher gets a broad slice of live jobs in their location instead. The
# subscriber may still get nothing, but then it is the model's judgement on a fair shortlist
# rather than a filter that could never have matched.

def test_a_starved_shortlist_is_retried_without_the_recall_predicate(query):
    got = query(PROFILE, rows=[[]])                 # first query comes back empty
    assert len(got["calls"]) == 2, "the floor did not fire"
    narrow, wide = got["calls"]
    assert "search_tsv @@" in narrow["sql"]         # keyword recall in the first
    assert "role_category = any(%s)" in narrow["sql"].split("partition by")[1]
    assert "search_tsv @@" not in wide["sql"]       # and gone from the retry
    # The category reference that survives is the partition, not a filter — the widened
    # query still spreads slots across buckets instead of handing them to the biggest one.
    assert "partition by case when d.role_category = any(%s)" in wide["sql"]
    assert wide["sql"].split("partition by")[1].count("role_category = any(%s)") == 1


def test_location_and_eligibility_survive_the_widening(query):
    """Widening must not become "email them anything". A CZ-only subscriber still gets CZ
    jobs — dropping the location gate would flood them with roles they cannot take."""
    wide = query(PROFILE, rows=[[]])["calls"][1]
    assert "p.eligibility = any(%s)" in wide["sql"]
    assert wide["params"][1] == PROFILE["regions"]   # the location predicate's parameter


def test_the_eligibility_allowlist_follows_the_subscriber_countries(query):
    """`postings.eligibility` encodes a judgement about *a Czech candidate* — that is what its
    classifier docstring still says — so the allowlist reading it cannot be a constant.

    Measured 2026-08-07: the constant that used to sit here withheld 21 291 of 21 677 active US
    postings from every subscriber, including one who had explicitly selected the United States,
    and `eligible_only` defaults true with no UI control to turn it off. Both queries behind the
    product share `store.eligibility_allowlist` so they cannot drift.
    """
    def allowlist(call):
        """Find it by content, never by index.

        Selecting countries adds location parameters ahead of this one, so a hardcoded
        position silently reads a different value — the shift this module's own
        `test_placeholder_and_parameter_counts_match` exists to catch.
        """
        found = [p for p in call["params"]
                 if isinstance(p, list) and "eligible" in p]
        assert len(found) == 1, f"expected exactly one eligibility allowlist, got {found}"
        return found[0]

    # The fixture's sink accumulates: `run()` resets `rows` but not `calls`, so a second
    # query appends rather than replacing. Take the offset, or you re-read the first query
    # and the test passes for the wrong reason.
    first = query(PROFILE, rows=[[]])["calls"]
    eu_only = allowlist(first[0])
    assert "likely needs US work auth" not in eu_only

    offset = len(first)
    us = allowlist(query({**PROFILE, "countries": ["US", "DE"]}, rows=[[]])["calls"][offset])
    assert "likely needs US work auth" in us

    # Never unlockable by a country preference: a clearance or citizenship requirement is not
    # something picking the US can satisfy.
    assert "blocked (clearance/US-only)" not in us
    # And 'verify UK right-to-work' stays unconditional — it is advisory, and making it
    # conditional would narrow what existing subscribers already see.
    assert "verify UK right-to-work" in eu_only


def test_a_healthy_shortlist_is_not_widened(query):
    """The floor is a fallback, not a default: a subscriber whose categories work keeps the
    precise shortlist. Widening every profile would hand the model mostly-irrelevant rows."""
    got = query(PROFILE, rows=[_full()])
    assert len(got["calls"]) == 1


def test_a_profile_with_nothing_to_narrow_on_is_not_queried_twice(query):
    """No categories and no keywords means the two statements are identical — the retry
    would be pure cost for a result already known."""
    got = query({"regions": ["cz"]}, rows=[[]])
    assert len(got["calls"]) == 1


def test_widening_is_reported_to_the_caller(monkeypatch):
    """`widened` reaches digest_runs, which is the whole point: a widened shortlist is the
    fingerprint of a subscriber the taxonomy does not serve, and it must stay visible rather
    than being quietly papered over by the fallback that rescued them."""
    sink: dict = {"calls": [], "rows": [[], _full()]}
    monkeypatch.setattr(store, "cursor", lambda commit=False: _RecordingCursor(sink))
    rows, meta = store.query_shortlist_meta(PROFILE)
    assert meta["widened"] is True
    assert meta["n_narrow"] == 0 and meta["n"] == len(_full())
    assert len(rows) == len(_full())


def test_no_widening_reports_the_same_counts(monkeypatch):
    sink: dict = {"calls": [], "rows": [_full(30)]}
    monkeypatch.setattr(store, "cursor", lambda commit=False: _RecordingCursor(sink))
    _, meta = store.query_shortlist_meta(PROFILE)
    assert meta == {"n": 30, "n_narrow": 30, "widened": False}


# ------------------------------------------------------- what the label may search for ---
# A label is a free-text *name* for the search, so `_shortlist_terms` mines it for keywords.
# Words that restate a filter must not become keywords: the filter already applies, so
# dropping the word cannot lose anything the subscriber asked for, while keeping it lets
# every posting that merely mentions the word compete for the same 120 slots. All three
# cases below were observed in the fifty-persona cohort on 2026-07-28.

def _terms(label: str, **rest) -> list[str]:
    return store._shortlist_terms({"label": label, **rest})


def test_label_place_names_do_not_become_search_terms():
    """"Brno design" searching for **brno** returned a personal banker, a tobacconist's
    assistant, two librarians and an upholsterer — all in Brno, none design. The location
    gate had already restricted the search to Brno; the word added only noise."""
    assert "brno" not in _terms("Brno design")
    assert "design" in _terms("Brno design")
    assert "prague" not in _terms("Prague only")
    # The worst case: one label word matched an entire source. arbeitnow appends "Find more
    # English Speaking Jobs in Germany" to all 1 267 of its descriptions, so a Go/AWS
    # engineer whose label was "Germany" was shown eight Steuerberater adverts.
    assert "germany" not in _terms("Germany")


def test_label_seniority_and_arrangement_words_do_not_become_search_terms():
    """"Junior designer" filled ~30 of 120 slots with Junior HR Specialist, Junior IT Admin,
    Junior Payroll Admin and Junior Key Account Manager — while `seniorities` was already
    filtering on precisely that word."""
    assert _terms("Junior designer") == ["designer"]
    assert "remote" not in _terms("Remote data work")
    assert "data" in _terms("Remote data work")


def test_stack_and_categories_are_never_filtered_this_way():
    """Only the *label* is mined. `stack` is subject matter the subscriber typed on purpose —
    someone whose stack is literally "senior" or a city name still gets it searched, because
    a dropped stack term is a preference silently discarded, which this system does not do."""
    terms = _terms("My digest", stack=["junior", "brno", "python"])
    assert terms == ["junior", "brno", "python"]
