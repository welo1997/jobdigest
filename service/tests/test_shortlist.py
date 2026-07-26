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
        self._sink["sql"] = sql
        self._sink["params"] = list(params or [])

    def fetchall(self):
        return []


@pytest.fixture
def query(monkeypatch):
    """Run query_shortlist and hand back the SQL + params it would have executed."""
    sink: dict = {}
    monkeypatch.setattr(store, "cursor", lambda commit=False: _RecordingCursor(sink))

    def run(profile: dict, limit: int = 120):
        store.query_shortlist(profile, limit=limit)
        return sink

    return run


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
    """Each optional clause adds placeholders at a different point in the statement."""
    got = query(profile)
    assert got["sql"].count("%s") == len(got["params"])


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
    assert "d.is_part_time desc, d.last_seen_at desc" in sql       # inside the window
    assert "r.is_part_time desc, r.last_seen_at desc" in sql       # and the outer tiebreak


def test_dedup_still_happens_before_ranking(query):
    """One row per dedup_key must be settled first — otherwise near-duplicate copies of the
    same job occupy several of a category's allocated slots."""
    sql = query(PROFILE)["sql"]
    inner = sql.index("distinct on (coalesce(p.dedup_key, p.posting_id))")
    assert inner > sql.index("row_number() over")   # the dedup subquery is nested deeper
