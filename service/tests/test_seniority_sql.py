"""A NULL seniority means "the title did not say", and the two sides of the product read that
differently **on purpose**. Executed as real SQL, because the whole question is what `WHERE`
does with NULL and a string cannot show you that.

    TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5434/jobmatch python -m pytest \\
        service/tests/test_seniority_sql.py

Skipped when TEST_DATABASE_URL is unset.

**The two polarities, and why they are not drift:**

*Display filters* (`/jobs`, `/matches`) are equality — ticking "Senior" narrows to postings
that actually said senior. Someone clicking a chip is deliberately narrowing a list in front
of them, and an unstated posting is not evidence of anything to narrow *to*.

*The digest path* does not gate on seniority at all: `store._hard_gate` carries location,
education and eligibility, and nothing else. Seniority reaches the AI matcher as prompt text,
which is the 2026-07-23 soft/AI-only decision, and the matcher is told explicitly that a
missing level is not a mismatch. So an unstated posting still reaches a junior-only
subscriber's shortlist and is judged on overall fit.

That second half is the load-bearing one and it is invisible in the schema: 62.2% of the live
corpus is NULL, so a seniority predicate quietly added to `_hard_gate` would delete nearly
two thirds of every subscriber's candidate pool, and the symptom would be thin digests with
no error anywhere. That is what `test_the_digest_path_does_not_gate_on_seniority` is for.
"""

from __future__ import annotations

import os

import pytest

from service import store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "seniority-sql-"

# (seniority, title). The NULL row is the point of the file — it is the majority case in
# production, and every assertion here is about which queries admit it.
FIXTURES = [
    ("junior", "Junior Data Analyst"),
    ("mid", "Data Analyst II"),
    ("senior", "Senior Data Analyst"),
    ("lead", "Head of Data"),
    ("intern", "Werkstudent Data Analytics"),
    ("entry_level", "Graduate Data Analyst"),
    (None, "Data Analyst"),            # the title named no level: 62% of the real corpus
]
POSTINGS = [f"{PREFIX}{i}" for i in range(len(FIXTURES))]
UNSTATED = POSTINGS[-1]


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    with store.cursor(commit=True) as cur:
        for pid, (level, title) in zip(POSTINGS, FIXTURES):
            cur.execute(
                # `search_tsv` is a generated column — it derives from title/company/
                # description and cannot be written.
                "insert into postings (posting_id, source, url, title, company, region, "
                "  country_code, city, eligibility, seniority, work_type, role_category, "
                "  dedup_key, is_active) "
                "values (%s,'test',%s,%s,'Test','cz','CZ','prague','eligible',%s,"
                "  'permanent','data_analysis',%s,true) "
                "on conflict (posting_id) do nothing",
                (pid, f"https://x.test/{pid}", title, level, pid),
            )
    try:
        yield
    finally:
        with store.cursor(commit=True) as cur:
            cur.execute("delete from postings where posting_id like %s", (PREFIX + "%",))
            cur.execute("delete from profiles where email like %s", (PREFIX + "%",))
        store._POOL = None
        if previous_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_dsn


def _search(**filters) -> set[str]:
    rows, _total, _capped = store.search_postings(limit=10 ** 6, **filters)
    return {r["posting_id"] for r in rows} & set(POSTINGS)


# --- the display side: equality, so NULL is excluded ---------------------------------

@pytest.mark.parametrize("level,expected_index", [
    ("intern", 4), ("entry_level", 5), ("junior", 0),
    ("mid", 1), ("senior", 2), ("lead", 3),
])
def test_each_level_selects_exactly_its_own_postings(level, expected_index):
    """Six rungs, six disjoint answers. Written per-level rather than as one loop so a rung
    that stops matching names itself in the failure."""
    assert _search(seniorities=[level]) == {POSTINGS[expected_index]}


def test_a_level_filter_excludes_the_unstated_posting():
    """The chip means "postings that said this". An unstated one said nothing, so it is not an
    answer to any of the six — and `= any(...)` is what makes that true, because NULL is never
    equal to anything, including itself."""
    for level in ("intern", "entry_level", "junior", "mid", "senior", "lead"):
        assert UNSTATED not in _search(seniorities=[level])


def test_no_level_filter_returns_everything_including_the_unstated_one():
    """A filter nobody applied must never narrow anything — the same rule `uncategorised`
    follows. This is the half that matters for a corpus that is 62% NULL: a visitor who has
    not touched the Level menu must still see those postings."""
    assert _search() == set(POSTINGS)
    assert UNSTATED in _search()


def test_several_levels_union_rather_than_intersect():
    assert _search(seniorities=["junior", "senior"]) == {POSTINGS[0], POSTINGS[2]}


def test_the_facet_menu_never_offers_unstated_as_an_option():
    """"Unstated" is the absence of an answer, not a seventh rung. If it reached the menu it
    would read as a level a visitor could search for, and 62% of the corpus would sit behind
    a chip that means "we could not tell"."""
    facets = store.search_facets()
    values = {f["value"] for f in facets.get("seniorities", [])}
    assert None not in values
    assert "unstated" not in values


# --- the digest side: no gate at all, so NULL survives -------------------------------

def test_the_digest_path_does_not_gate_on_seniority():
    """**The invariant this whole file exists for.** `_hard_gate` must not grow a seniority
    predicate: 62.2% of the corpus is NULL, so one would silently delete two thirds of every
    subscriber's candidate pool, and the failure would look like thin digests rather than an
    error.

    Break it deliberately — add `p.seniority = any(%s)` to `_hard_gate` — and this goes red."""
    where, _params = store._hard_gate({"seniorities": ["junior"], "countries": ["CZ"]})
    assert not any("seniority" in clause for clause in where), (
        "seniority must not be a SQL gate on the delivery path — it is enforced by the AI "
        "matcher, which is told an unstated level is not a mismatch"
    )


def test_an_unstated_posting_still_reaches_a_junior_only_subscriber():
    """The behavioural half of the test above, through the real shortlist query rather than
    through its fragments."""
    profile = {
        "seniorities": ["junior"],
        "countries": ["CZ"], "cities": [], "remote_scope": "none", "regions": ["cz"],
        "role_categories": ["data_analysis"], "eligible_only": True,
    }
    got = {r["posting_id"] for r in store.query_shortlist(profile, limit=10 ** 4)}
    assert UNSTATED in got, "an unstated posting must not be filtered out before the matcher"
    assert POSTINGS[2] in got, "nor must a senior one — the model, not SQL, decides that"
