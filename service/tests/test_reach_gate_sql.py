"""`geo.reach_predicate` against a real Postgres — "remote" is two questions, and this is the
second one.

**The production failure this file exists for.** On 2026-08-17 the owner's own subscription
received five jobs in one email: Data Engineer roles at 7N, ConnectPoint, Dev4Hire, AVENGA and
Pearson Carter, scored 7-8 by the matcher, every one of them `remote_signal = true` and every one
`remote_reach = 'country'` bound to Poland, India or the UK. He lives in Prague. Nothing was
broken: `location_predicate` exempted them because they were fully remote, the prompt told the
model a fully-remote posting is exempt from the location test, and the model obeyed. The column
that knew better — `remote_reach`, backfilled since 2026-08-14 — was read by neither the gate nor
the prompt. Right counts, healthy `digest_runs`, silent watchdog, five unusable jobs.

A string cannot show you what `WHERE` does with a NULL or an array, and this predicate is built by
concatenation into a fragment whose other `%s` are bound positionally — so it is tested here, on
the database, not by reading the SQL text.

Four things below cannot be checked by reading the code:

  1. **That an unknown reach is KEPT.** Refusing what a posting never claimed would be a stricter
     promise than the data supports: 1 507 active remote rows have no `remote_reach` at all and 487
     say `region` without an enumerable country list. `coalesce(case ... end, true)` is what turns
     those NULLs into *keep*, and a `case` arm returning `false` instead of `null` would silently
     delete them.
  2. **That the array branch survives a null `country_code`.** `reach_countries && ... or
     country_code = any(...)` yields NULL — not false — when the array misses and the country is
     unknown, and only the outer `coalesce` makes that a keep.
  3. **The parameter order.** The reach gate is written last in the SQL text so its three arrays
     bind last. Slip that against the city restriction and the predicate filters countries by a
     city list, raising nothing.
  4. **That it is applied on the live path**, not just in the standalone predicate — the failure
     `location_predicate`'s own docstring warns about, "filters that were silently not applied at
     one of two call sites".

Mutation-check: change the final `coalesce(..., true)` in `geo.reach_predicate` to `..., false)`
and the unknown-reach tests go red; delete the conjunct from `location_predicate` and the
Poland-bound tests go red.

    docker run --rm -d --name jd_test_pg -e POSTGRES_USER=jobmatch \\
        -e POSTGRES_PASSWORD=jobmatch -e POSTGRES_DB=jobmatch -p 5434:5432 pgvector/pgvector:pg16
    docker exec -i jd_test_pg psql -U jobmatch -d jobmatch < service/db/schema.sql
    TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5434/jobmatch python -m pytest \\
        service/tests/test_reach_gate_sql.py

Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import geo, store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "reachgate-"

#: (id, country_code, city, region, remote_signal, remote_reach, reach_countries)
#:
#: Written as explicit column values rather than built from `scope_raw` through `ingest`: what is
#: under test is the WHERE clause, and some of the rows that matter most — a `country` reach whose
#: country is unknown — are ones the classifier produces rarely and could stop producing without
#: this file noticing. `test_remote_reach.py` is where the classifier itself is graded.
ROWS = [
    # --- the production failure, in one row. "100% remote", remote *within Poland*.
    ("pl-only",            "PL", None,     "eu", True,  "country",  None),
    # Remote and Czech: the same shape, on the right side of the line for a Prague subscriber.
    ("cz-only",            "CZ", None,     "cz", True,  "country",  None),
    # Live anywhere. 177 rows corpus-wide — the case everyone imagines "remote" means.
    ("anywhere",           "US", None,     "us", True,  "anywhere", None),
    # A region that enumerates its countries and includes Czechia.
    ("region-with-cz",     "ES", None,     "eu", True,  "region",   ["CZ", "DE", "ES"]),
    # The Dataiku shape: a region that enumerates its countries and does NOT include Czechia.
    # Emailed to the owner at score 8 on 2026-08-17 as "Data Engineer I" — an exact seniority hit
    # he could not have taken.
    ("region-no-cz",       "DE", None,     "eu", True,  "region",   ["DE", "ES", "GB", "NL"]),
    # **The same shape as it actually occurs in production: `country_code` is NULL.** A scope that
    # enumerates six countries usually has no single home country to store, so the row above —
    # with its tidy 'DE' — was the unrealistic one, and its tidiness hid a live bug for a whole
    # deploy: `false or NULL` is NULL, and the gate's outer coalesce read that as keep. Six rows
    # walked through, Dataiku's among them. Any fixture for this predicate needs a null-country
    # twin.
    ("region-no-cz-null",  None, None,     "eu", True,  "region",   ["DE", "ES", "GB", "NL"]),
    # An unclassified reach that nonetheless carries a country list. `reach_countries` is filled
    # whether or not a posting is remote, so on a row whose breadth was never classified it is not
    # an eligibility enumeration — the `remote_reach is null` arm wins, in both languages.
    ("reach-null-array",   None, None,     "eu", True,  None,       ["DE", "GB"]),
    # "Europaweit" — a macro-region word we could not enumerate. Czechia is plausibly inside it.
    ("region-no-list",     "IT", None,     "eu", True,  "region",   None),
    # Never classified at all.
    ("reach-null",         "GB", None,     "uk", True,  None,       None),
    # Bound to one country and we do not know which. Unknown, not elsewhere.
    ("country-unknown-cc", None, None,     "eu", True,  "country",  None),
    # `country_code` says Poland, the array says it also reaches Czechia. The union wins — the
    # same "ORed, never ANDed" rule migration 023 established.
    ("pl-plus-cz-array",   "PL", None,     "eu", True,  "country",  ["CZ", "PL"]),
    # --- controls on the on-site arm, which the reach gate must not touch at all.
    ("onsite-prague",      "CZ", "prague", "cz", False, None,       None),
    ("onsite-brno",        "CZ", "brno",   "cz", False, None,       None),
    ("onsite-warsaw",      "PL", "warsaw", "eu", False, None,       None),
]

REMOTE_KEPT_FOR_CZ = {"cz-only", "anywhere", "region-with-cz", "region-no-list",
                      "reach-null", "reach-null-array", "country-unknown-cc", "pl-plus-cz-array"}
REMOTE_REFUSED_FOR_CZ = {"pl-only", "region-no-cz", "region-no-cz-null"}


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None
    with store.cursor(commit=True) as cur:
        for pid, cc, city, region, remote, reach, reach_countries in ROWS:
            cur.execute(
                "insert into postings (posting_id, source, title, company, url, location,"
                " country_code, city, region, remote_signal, remote_reach, reach_countries,"
                " eligibility, is_active)"
                " values (%s,'test','Data Engineer',%s,%s,'somewhere',%s,%s,%s,%s,%s,%s,"
                "'eligible',true)"
                " on conflict (posting_id) do nothing",
                (PREFIX + pid, f"Co {pid}", f"https://x.test/{pid}",
                 cc, city, region, remote, reach, reach_countries))
    try:
        yield
    finally:
        with store.cursor(commit=True) as cur:
            cur.execute("delete from postings where posting_id like %s", (PREFIX + "%",))
        store._POOL = None
        if previous_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_dsn
        store._POOL = None


def matching(profile: dict) -> set[str]:
    """The ids `location_predicate` admits for this profile, unprefixed."""
    sql, params = geo.location_predicate(profile, alias="p")
    with store.cursor() as cur:
        cur.execute(f"select p.posting_id from postings p where {sql} and p.posting_id like %s",
                    params + [PREFIX + "%"])
        return {r["posting_id"][len(PREFIX):] for r in cur.fetchall()}


CZ_WORLDWIDE = {"countries": ["CZ"], "cities": [], "remote_scope": "worldwide"}


# --- the failure itself -------------------------------------------------------------------


def test_a_remote_within_poland_role_is_refused_for_a_prague_subscriber():
    """The 2026-08-17 email, as a test. Before the reach gate all five of its jobs passed here."""
    found = matching(CZ_WORLDWIDE)
    assert "pl-only" not in found
    # And the reason it is refused is the reach, not the country: the identical row carrying an
    # array that names Czechia is kept. Without this half, a `country_code = any(...)` typo that
    # refused everything foreign would pass the assertion above for the wrong reason.
    assert "pl-plus-cz-array" in found


def test_an_enumerated_region_that_excludes_you_is_refused():
    """`region` is not a free pass — Dataiku's {DE,ES,GB,NL} names four countries and none is CZ."""
    found = matching(CZ_WORLDWIDE)
    assert "region-no-cz" not in found
    assert "region-no-cz-null" not in found      # the production shape: no country_code
    assert "region-with-cz" in found


def test_the_sql_gate_and_the_prompt_agree_on_every_row():
    """**The strongest test here, and the one that was missing.** The gate and
    `matcher._reach_for_model` are one rule written twice — SQL three-valued logic on one side,
    Python truthiness on the other — and everything downstream assumes they agree: the gate
    decides which rows spend a shortlist slot, the token decides what the model does with them.
    Drift is silent in both directions. A row the SQL keeps and the prompt calls
    `other-country-only` wastes a slot on a job the model will refuse; the reverse hands the model
    a job the gate meant to delete and tells it the reach is fine.

    This is not a hypothetical. Both halves were written together, reviewed together, tested
    together and deployed together — and diverged on six live rows, because `false or NULL` is
    NULL in Postgres and `False or None` is falsy in Python. The production shortlist is what
    caught it; this is what should have."""
    from service import matcher

    found = matching(CZ_WORLDWIDE)
    for pid, cc, _city, _region, remote, reach, reach_countries in ROWS:
        if not remote:
            continue                     # the reach rule does not apply to a job with an office
        verdict = matcher._reach_for_model(
            {"remote_signal": True, "remote_reach": reach,
             "reach_countries": reach_countries, "country_code": cc}, {"CZ"})
        admitted = pid in found
        assert admitted == (verdict != "reach=other-country-only"), (
            f"{pid}: the SQL gate {'admits' if admitted else 'refuses'} it while the prompt says "
            f"{verdict} — the two halves have drifted")


def test_the_eu_scope_is_where_this_leaked():
    """Poland is in the EEA, so `eu` scope waved every remote-within-Poland role straight through.

    `worldwide` gets the attention because its remote gate is the literal string `true`, but a
    subscriber who narrowed to "anywhere in the EU" was just as exposed and would have been the
    harder bug to see. The conjunct is ANDed for every scope so that this cannot differ by scope.
    """
    found = matching({"countries": ["CZ"], "cities": [], "remote_scope": "eu"})
    assert "pl-only" not in found
    assert "region-no-cz" not in found
    assert "cz-only" in found


def test_the_same_role_is_kept_for_a_subscriber_who_lives_there():
    """The row is not bad inventory — it is bad *for a Czech subscriber*. A Warsaw subscriber
    should get it, which is what makes this a targeting rule rather than a filter on quality."""
    found = matching({"countries": ["PL"], "cities": [], "remote_scope": "worldwide"})
    assert "pl-only" in found
    assert "pl-plus-cz-array" in found
    # ...and the mirror image: Czechia-only remote work is now the one that is out of reach.
    assert "cz-only" not in found


# --- unknowns are kept, which is the half that is easy to break ---------------------------


@pytest.mark.parametrize("pid,why", [
    ("reach-null", "never classified — 1 507 active remote rows"),
    ("region-no-list", "a macro-region word we could not enumerate — 487 rows"),
    ("country-unknown-cc", "bound to one country, and we do not know which"),
])
def test_an_unprovable_reach_is_kept_and_handed_to_the_matcher(pid, why):
    """Precision is what a miss costs here; recall is never lost. The same division of labour as
    an unresolved city — the gate keeps it, `_reach_for_model` renders `reach=?`, and the model
    reads the posting's own words. Turn the final `coalesce(..., true)` into `false` and all
    three of these go red, which is the mutation this parametrisation exists to catch."""
    assert pid in matching(CZ_WORLDWIDE), why


def test_anywhere_is_kept_and_a_czech_remote_role_is_too():
    found = matching(CZ_WORLDWIDE)
    assert "anywhere" in found
    assert "cz-only" in found


def test_the_full_verdict_matrix():
    """One assertion over every row, so a change that fixes one case by breaking another cannot
    pass. The set is spelled out rather than derived, because a derived expectation would encode
    the same logic under test."""
    found = matching(CZ_WORLDWIDE)
    assert found == REMOTE_KEPT_FOR_CZ | {"onsite-prague", "onsite-brno"}
    assert not (found & REMOTE_REFUSED_FOR_CZ)


# --- the on-site arm and the parameter order ----------------------------------------------


def test_the_reach_gate_does_not_touch_the_onsite_arm():
    """Where you may *live* is a category error for a job with an office — the on-site rows carry
    no `remote_reach` and must be judged purely on country and city, exactly as before."""
    found = matching(CZ_WORLDWIDE)
    assert {"onsite-prague", "onsite-brno"} <= found
    assert "onsite-warsaw" not in found


def test_the_city_restriction_still_binds_after_the_reach_params():
    """Three arrays were appended to a fragment whose params bind by position. If they landed
    ahead of the city list, this predicate would filter countries by cities: Brno would pass and
    the remote rows would vanish, both silently."""
    found = matching({"countries": ["CZ"], "cities": ["cz:prague"],
                      "remote_scope": "worldwide"})
    assert "onsite-prague" in found
    assert "onsite-brno" not in found          # the city restriction still bites
    assert "cz-only" in found                  # ...and does not reach the remote arm
    assert "pl-only" not in found              # ...while the reach gate still does


def test_a_legacy_profile_with_no_countries_is_unaffected():
    """`location_predicate` returns on the coarse `region` gate before it reaches the reach
    conjunct, and it has to: a profile with no `countries` gives `_reach_for_model` an empty set,
    which can prove nothing about anyone. Both sides then agree that this axis is unjudgeable for
    that subscriber, instead of the SQL refusing rows the prompt would have marked `reach=?`."""
    found = matching({"regions": ["eu"], "remote_scope": "worldwide"})
    assert "pl-only" in found
    assert "region-no-cz" in found


# --- and that it is actually wired into the live path -------------------------------------


def test_the_live_shortlist_gate_carries_it():
    """`store._hard_gate` is what the digest and the vector shadow both retrieve through. The
    standalone predicate passing proves nothing about the query that emails people."""
    where, params = store._hard_gate({"countries": ["CZ"], "cities": [],
                                      "remote_scope": "worldwide", "eligible_only": True})
    with store.cursor() as cur:
        cur.execute(f"select p.posting_id from postings p where {' and '.join(where)}"
                    f" and p.posting_id like %s", params + [PREFIX + "%"])
        found = {r["posting_id"][len(PREFIX):] for r in cur.fetchall()}
    assert "pl-only" not in found
    assert "region-no-cz" not in found
    assert {"cz-only", "anywhere", "onsite-prague"} <= found
