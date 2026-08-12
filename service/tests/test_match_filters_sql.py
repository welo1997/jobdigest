"""The /matches display filters, executed against a real Postgres.

`test_hidden_sql.py` pins the skill filter and the hidden/visible split. This file pins the
two filters added alongside them — work setup and "great fits only" — and the one property
that makes a multi-filter page trustworthy rather than merely functional:

  1. **Lockstep.** `matched_jobs` renders the rows, `match_count` renders the header above
     them. They are separate queries, so a filter applied to one and not the other reads as
     "12 matches" over a list that stops at 8. Both now route through `store._match_filters`,
     and the point of testing every combination is that the *routing* is what must not rot —
     a future filter added straight into one query would pass every other test in this file.

  2. **Each facet skips its own filter and applies the others.** This is the property that
     stops a menu lying. With one filter, "compute facets unfiltered" was equivalent; with
     three it is not, and the wrong version shows "Excel 12" while the list holds 2.

  3. **A null `work_mode` is excluded by a work-setup filter and never offered as an
     option.** The matcher's keep-unknown rule is about not discarding postings; an explicit
     request for "remote" is a different question, and the menu must not advertise a filter
     that silently drops rows.

Same harness and the same reason as `test_geo_sql.py` — see its docstring for the throwaway
database recipe. Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import geo, store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "matchfilt-"

# (work_mode, score, skills). Chosen so every filter has something to exclude and the
# combinations do not collapse into each other:
#   - index 4 has a NULL work_mode  — the ad never said (must drop under a work-setup filter)
#   - index 5 has NULL skills       — contributes to no skill facet but is still a match
#   - the score ladder straddles 8  — three at or above, three below
FIXTURES = [
    ("remote", 10, ["excel"]),
    ("remote", 9, ["excel"]),
    ("hybrid", 8, ["excel", "sql"]),
    ("onsite", 5, ["python"]),
    (None, 4, ["excel"]),
    ("remote", 3, None),
]
POSTINGS = [f"{PREFIX}{i}" for i in range(len(FIXTURES))]
GREAT = 8          # the threshold under test; the endpoint's own lives in webapp


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    with store.cursor(commit=True) as cur:
        for pid, (mode, _score, skills) in zip(POSTINGS, FIXTURES):
            cur.execute(
                "insert into postings (posting_id, source, url, title, company, region, "
                "  eligibility, seniority, work_type, role_category, dedup_key, skills, "
                "  work_mode, is_active) "
                "values (%s,'test',%s,'Data Analyst','Test','cz','eligible','mid',"
                "  'permanent','data_analysis',%s,%s,%s,true) "
                "on conflict (posting_id) do nothing",
                (pid, f"https://x.test/{pid}", pid, skills, mode),
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


@pytest.fixture
def profile():
    email = f"{PREFIX}sub@example.com"
    with store.cursor(commit=True) as cur:
        cur.execute("delete from profiles where email = %s", (email,))
        cur.execute("insert into profiles (email, label) values (%s, 'filter test') "
                    "returning id::text as id", (email,))
        pid = cur.fetchone()["id"]
    for posting, (_mode, score, _skills) in zip(POSTINGS, FIXTURES):
        store.upsert_match(pid, posting, score, "why")
    return pid


def _ids(profile_id, **filters) -> set[str]:
    rows = store.matched_jobs(profile_id, limit=10 ** 9, **filters)
    return {r["posting_id"] for r in rows}


# --- the filters themselves ---------------------------------------------------------

def test_work_mode_filter_selects_only_that_setup(profile):
    assert _ids(profile, work_modes=["remote"]) == {POSTINGS[0], POSTINGS[1], POSTINGS[5]}
    assert _ids(profile, work_modes=["hybrid"]) == {POSTINGS[2]}
    # Multi-select is OR, matching the skill filter's settled behaviour.
    assert _ids(profile, work_modes=["hybrid", "onsite"]) == {POSTINGS[2], POSTINGS[3]}


def test_a_posting_whose_ad_never_said_is_excluded_by_a_work_mode_filter(profile):
    """`work_mode` is NULL for index 4. It is a real match and must appear unfiltered, but
    asking for a *named* setup must not return it — `= any(...)` is never true for NULL, and
    this test exists so a future rewrite to `coalesce`/`or is null` fails loudly."""
    assert POSTINGS[4] in _ids(profile)
    for mode in geo.WORK_MODES:
        assert POSTINGS[4] not in _ids(profile, work_modes=[mode])


def test_great_fits_keeps_only_the_top_of_the_ladder(profile):
    assert _ids(profile, min_score=GREAT) == {POSTINGS[0], POSTINGS[1], POSTINGS[2]}
    # The boundary is inclusive: index 2 scores exactly 8.
    assert POSTINGS[2] in _ids(profile, min_score=GREAT)


def test_filters_compose_as_and_across_and_or_within(profile):
    """Two different filters narrow together; values inside one widen."""
    assert _ids(profile, work_modes=["remote"], min_score=GREAT) == {POSTINGS[0], POSTINGS[1]}
    assert _ids(profile, work_modes=["remote"], min_score=GREAT,
                skills_filter=["sql"]) == set()
    assert _ids(profile, skills_filter=["python", "sql"]) == {POSTINGS[2], POSTINGS[3]}


# --- lockstep: the header can never promise more than the list holds -----------------

@pytest.mark.parametrize("filters", [
    {},
    {"work_modes": ["remote"]},
    {"work_modes": ["onsite", "hybrid"]},
    {"min_score": GREAT},
    {"skills_filter": ["excel"]},
    {"skills_filter": ["excel"], "work_modes": ["remote"]},
    {"skills_filter": ["excel"], "min_score": GREAT},
    {"work_modes": ["remote"], "min_score": GREAT},
    {"skills_filter": ["excel"], "work_modes": ["remote"], "min_score": GREAT},
])
def test_count_agrees_with_the_rows_under_every_combination(profile, filters):
    assert store.match_count(profile, **filters) == len(_ids(profile, **filters))


# --- facets: skip your own filter, apply the others ----------------------------------

def test_skill_facet_reflects_the_other_filters(profile):
    """Ticking a work setup must re-count the skill chips. The unfiltered version of this
    said Excel 4 while a remote-filtered list could only ever show 2 of them."""
    facets = store.match_facets(profile, work_modes=["remote"])
    assert facets["skills"] == [{"skill": "excel", "count": 2}]


def test_skill_facet_does_not_narrow_itself(profile):
    """Its own filter is skipped, so the other chips stay reachable — without this, one tick
    collapses the menu to the single thing already ticked and there is no way back."""
    facets = store.match_facets(profile, skills_filter=["python"])
    assert [f["skill"] for f in facets["skills"]] == ["excel", "python", "sql"]


def test_work_mode_facet_skips_itself_but_honours_the_score_filter(profile):
    unfiltered = store.match_facets(profile)["work_modes"]
    assert unfiltered == [{"work_mode": "onsite", "count": 1},
                          {"work_mode": "hybrid", "count": 1},
                          {"work_mode": "remote", "count": 3}]
    # Ticking "remote" must not remove hybrid/onsite from the menu...
    assert store.match_facets(profile, work_modes=["remote"])["work_modes"] == unfiltered
    # ...but "great fits only" must re-count it, since that filter is not its own.
    assert store.match_facets(profile, min_score=GREAT)["work_modes"] == [
        {"work_mode": "hybrid", "count": 1},
        {"work_mode": "remote", "count": 2},
    ]


def test_work_mode_facet_is_in_canonical_order_and_never_offers_null(profile):
    """A fixed three-value menu must not reshuffle under the cursor as counts change, and
    the null bucket must not appear — it is not a setup anyone can ask for."""
    modes = [f["work_mode"] for f in store.match_facets(profile)["work_modes"]]
    assert modes == [m for m in geo.WORK_MODES if m in modes]
    assert None not in modes
    assert sum(f["count"] for f in store.match_facets(profile)["work_modes"]) == 5  # not 6


def test_great_fit_count_reflects_the_other_filters_but_not_itself(profile):
    assert store.match_facets(profile, great_fit_score=GREAT)["great_fit_count"] == 3
    assert store.match_facets(profile, work_modes=["remote"],
                              great_fit_score=GREAT)["great_fit_count"] == 2
    # Already filtering by score must not make the toggle's own preview collapse to itself.
    assert store.match_facets(profile, min_score=GREAT,
                              great_fit_score=GREAT)["great_fit_count"] == 3
    # Absent when nothing asked for it, so the caller can hide the toggle rather than
    # render a zero it invented.
    assert store.match_facets(profile)["great_fit_count"] is None


def test_facets_and_filters_respect_hidden(profile):
    """Hiding is a move, not a delete: a hidden job must leave both the filtered list and
    every facet count, on the same terms as the skill filter already guarantees."""
    store.set_matches_hidden(profile, [POSTINGS[0]], True)
    assert _ids(profile, work_modes=["remote"]) == {POSTINGS[1], POSTINGS[5]}
    assert store.match_count(profile, work_modes=["remote"]) == 2
    assert store.match_facets(profile)["work_modes"][-1] == {"work_mode": "remote", "count": 2}
    assert store.match_facets(profile, great_fit_score=GREAT)["great_fit_count"] == 2
    # The hidden view is the same query read the other way, and the filters apply there too.
    assert _ids(profile, hidden=True, work_modes=["remote"]) == {POSTINGS[0]}
