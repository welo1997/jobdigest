"""`reach_countries` against a real Postgres — the filter, the facet that must agree with it,
and the digest gate.

`postings.country_code` holds one country because `city` has to agree with it, so before migration
023 every *other* country a posting named was reachable by nothing: not the `/jobs` Country filter,
not `location_predicate`, not the facet counts. A string cannot show you what `WHERE` does with an
array, which is why these run against the database rather than against the SQL text.

Four things here cannot be checked by reading the code:

  1. **The positional bind.** `store._UPSERT_SQL` gained a column in the middle of a hand-written
     `%s` template, moving every column after it. A shifted bind writes one text column into
     another and both sides type-check — nothing raises until a filter returns the wrong rows.
  2. **The facet agreeing with the filter.** The country menu counted `country_code` alone while
     the filter now also matches `reach_countries`, so the menu would promise fewer rows than
     ticking it returns. That is the same class of failure `_search_where`'s `skip` argument
     exists to prevent, arriving through a different door.
  3. **`location_predicate`'s parameter order.** psycopg2 binds `%s` by position in the SQL
     *text*, and this change added two `%s` to a fragment built by string concatenation. Getting
     the order wrong does not raise — it silently filters by the wrong array.
  4. **That the widening is a union, not a replacement.** A posting must still be reachable
     through its own `country_code`.

    docker run --rm -d --name jd_test_pg -e POSTGRES_USER=jobmatch \\
        -e POSTGRES_PASSWORD=jobmatch -e POSTGRES_DB=jobmatch -p 5434:5432 pgvector/pgvector:pg16
    docker exec -i jd_test_pg psql -U jobmatch -d jobmatch < service/db/schema.sql
    TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5434/jobmatch python -m pytest \\
        service/tests/test_reach_countries_sql.py

Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import geo, ingest, store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "rcsql-"


class FakePosting:
    def __init__(self, pid, location=None, scope_raw=None, remote_signal=None,
                 description="Build things."):
        self.posting_id = PREFIX + pid
        self.source = "test"
        self.title = "Data Analyst"
        # A distinct employer per row, because `dedup_key` is a normalised (company, title, city)
        # and the facet counts over `distinct on (dedup_key)`. Four rows sharing one employer are
        # one job as far as this codebase is concerned — which is correct, and would have made
        # every count below a 1 no matter what the filter did.
        self.company = f"Test {pid}"
        self.url = f"https://x.test/{pid}"
        self.description = description
        self.location = location
        self.country_code = None
        self.remote_signal = remote_signal
        self.salary_raw = None
        self.currency = None
        self.posted_at = None
        self.source_category = None
        self.scope_raw = scope_raw


POSTINGS = [
    # The live shape: stored under Spain, genuinely open in six countries.
    FakePosting("cloudtalk", location="Spain", remote_signal=True,
                scope_raw="Spain; Czechia; United Kingdom; Ireland; Portugal; Netherlands"),
    # Two offices, on-site. Holdable in Latvia and in Germany; asking where its holder may *live*
    # is a category error, which is why only this column is filled for it.
    FakePosting("mintos", location="Rīga, LV", remote_signal=False,
                scope_raw="Rīga, LV; Berlin, DE"),
    # A single-country remote role: `reach_countries` stays null and Germany reaches it the
    # ordinary way. If this row ever gained an array, EU-International would fill with 13 000
    # work-from-home postings and mean nothing.
    FakePosting("berlin-remote", location="Remote, Germany", remote_signal=True),
    # An on-site job in one place — the majority of the corpus, and the control.
    FakePosting("prague-onsite", location="Praha, Czechia", remote_signal=False),
    # Reaches two selectable countries, **neither of them in the EEA**. This is the row that
    # tells `eu` remote scope apart from `worldwide`: a Czech subscriber at `eu` scope must not
    # be shown it, and would be the moment `reach_countries` were tested against `COUNTRIES`
    # instead of `EEA_COUNTRIES`. GB is the standing counterexample — selectable, not EEA.
    FakePosting("uk-us-remote", location="Remote, United Kingdom", remote_signal=True,
                scope_raw="United Kingdom; United States"),
]


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None
    store.upsert_postings([ingest.build_row(p) for p in POSTINGS])
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


def stored() -> dict[str, dict]:
    with store.cursor() as cur:
        cur.execute("select posting_id, country_code, city, reach_countries, reach_areas, "
                    "       remote_reach, scope_raw, education_min, salary_raw "
                    "from postings where posting_id like %s", (PREFIX + "%",))
        return {r["posting_id"]: dict(r) for r in cur.fetchall()}


def ids_matching(**kwargs) -> set[str]:
    """The `/jobs` feed's own query, reduced to the posting ids it returns."""
    where, params = store._search_where(**kwargs)
    with store.cursor() as cur:
        cur.execute(f"select p.posting_id from postings p where {' and '.join(where)} "
                    f"and p.posting_id like %s", params + [PREFIX + "%"])
        return {r["posting_id"] for r in cur.fetchall()}


def test_the_upsert_writes_reach_countries_to_the_right_column():
    rows = stored()
    assert rows[PREFIX + "cloudtalk"]["reach_countries"] == \
        ["CZ", "ES", "GB", "IE", "NL", "PT"]
    assert rows[PREFIX + "mintos"]["reach_countries"] == ["DE", "LV"]
    # Null, not an empty array — `upsert_postings` maps `[]` to NULL because psycopg2 renders a
    # bare `[]` as an untyped array Postgres cannot coerce.
    assert rows[PREFIX + "berlin-remote"]["reach_countries"] is None
    assert rows[PREFIX + "prague-onsite"]["reach_countries"] is None
    # Nothing shifted into the neighbouring columns.
    assert all(r["education_min"] is None and r["salary_raw"] is None for r in rows.values())


def test_the_column_is_not_gated_on_being_remote():
    """The asymmetry with `remote_reach` and `reach_areas`, asserted rather than assumed.

    An on-site posting listed at offices in two countries has two countries and no reach. Gating
    this column on remoteness — the obvious symmetry — would lose exactly the postings the
    Teamtailor probe found most of.
    """
    mintos = stored()[PREFIX + "mintos"]
    assert mintos["reach_countries"] == ["DE", "LV"]
    assert mintos["remote_reach"] is None
    assert mintos["reach_areas"] is None


def test_a_secondary_country_now_finds_the_posting():
    """The whole point. Before this column, five of CloudTalk's six countries returned nothing."""
    for country in ("CZ", "GB", "IE", "NL", "PT"):
        assert PREFIX + "cloudtalk" in ids_matching(countries=[country]), country
    # And the on-site office pair, through its second country.
    assert PREFIX + "mintos" in ids_matching(countries=["DE"])


def test_the_primary_country_still_finds_it_too():
    """A union, not a replacement — the failure mode of every OR written as a swap."""
    assert PREFIX + "cloudtalk" in ids_matching(countries=["ES"])
    assert PREFIX + "mintos" in ids_matching(countries=["LV"])
    assert PREFIX + "prague-onsite" in ids_matching(countries=["CZ"])


def test_a_country_the_posting_never_named_still_does_not_match():
    """The widening has to stop somewhere, and this is where."""
    assert ids_matching(countries=["SE"]) == set()
    assert PREFIX + "cloudtalk" not in ids_matching(countries=["PL"])
    # A single-country remote role is reachable through its own country and no other.
    assert PREFIX + "berlin-remote" in ids_matching(countries=["DE"])
    assert PREFIX + "berlin-remote" not in ids_matching(countries=["FR"])


def test_the_country_facet_counts_what_ticking_that_country_returns():
    """The menu and the results must agree, or the menu is lying about its own filter.

    Counted over `country_code` alone, Ireland would show 0 while ticking Ireland returned the
    CloudTalk row — and a facet that promises less than it delivers reads as a broken filter.
    """
    facets = store.search_facets()
    counts = {r["value"]: r["count"] for r in facets["countries"]}
    for country in ("ES", "CZ", "GB", "IE", "NL", "PT"):
        listed = counts.get(country, 0)
        actual = len(ids_matching(countries=[country]))
        assert listed >= actual, (f"{country}: menu says {listed}, ticking it returns "
                                  f"at least {actual}")


def test_a_posting_is_counted_once_per_country_not_twice():
    """`country_code` is usually *also* in `reach_countries`, so a naive unnest double-counts it.

    Inflating the very counts this change exists to correct would be a quiet way to undo it.

    Read through `search_facets` rather than by re-writing its SQL here: a copy of the query is a
    second definition, and it would keep passing after the real one changed — the failure this
    repo has paid for in `REMOTE_SQL`, in `source_watchdog`'s source list, and in the CI skip
    check's glob.
    """
    counts = {r["value"]: r["count"] for r in store.search_facets()["countries"]}
    # Spain is CloudTalk's `country_code` *and* the first entry of its scope. It is one posting.
    assert counts["ES"] == 1
    # Latvia likewise, for the on-site office pair.
    assert counts["LV"] == 1
    # Germany is two *different* postings: the Berlin remote role and Mintos' second office.
    assert counts["DE"] == 2


def test_the_digest_gate_reaches_a_secondary_country_too():
    """`location_predicate` is the digest's half of the same question, and it has two `%s` more
    than it did. A wrong bind order does not raise — it filters by the wrong array."""
    sql, params = geo.location_predicate(
        {"countries": ["IE"], "cities": [], "remote_scope": "country"}, alias="p")
    with store.cursor() as cur:
        cur.execute(f"select posting_id from postings p where {sql} and p.posting_id like %s",
                    params + [PREFIX + "%"])
        found = {r["posting_id"] for r in cur.fetchall()}
    assert PREFIX + "cloudtalk" in found
    assert PREFIX + "prague-onsite" not in found


def test_the_digest_gate_still_binds_its_city_restriction_correctly():
    """The city arm's params sit *after* the two new ones. If the order slipped, this predicate
    would filter countries by a city list and quietly return nothing or everything."""
    sql, params = geo.location_predicate(
        {"countries": ["CZ"], "cities": ["cz:prague"], "remote_scope": "country"}, alias="p")
    with store.cursor() as cur:
        cur.execute(f"select posting_id from postings p where {sql} and p.posting_id like %s",
                    params + [PREFIX + "%"])
        found = {r["posting_id"] for r in cur.fetchall()}
    assert PREFIX + "prague-onsite" in found
    # CloudTalk reaches Czechia remotely; a city restriction on Czechia must not exclude it,
    # because a fully-remote posting is judged by the remote scope and not by its city.
    assert PREFIX + "cloudtalk" in found


def test_the_eu_remote_scope_reads_reach_countries_against_the_eea():
    """`eu` means `EEA_COUNTRIES`, not `COUNTRIES` — the rule GB keeps being the counterexample to.

    A remote posting reaching an EEA country through `reach_countries` is reachable at `eu` scope;
    the array must be tested against the same EEA set the `country_code` half is.
    """
    sql, params = geo.location_predicate(
        {"countries": ["CZ"], "cities": [], "remote_scope": "eu"}, alias="p")
    with store.cursor() as cur:
        cur.execute(f"select posting_id from postings p where {sql} and p.posting_id like %s",
                    params + [PREFIX + "%"])
        found = {r["posting_id"] for r in cur.fetchall()}
    assert PREFIX + "cloudtalk" in found
    # And the half that makes the assertion above mean something: a role reaching only GB and US
    # reaches two *selectable* countries and no EEA one, so `eu` scope must refuse it. Testing
    # the array against `COUNTRIES` would admit it and tell a Czech subscriber a UK/US-only role
    # is open to them.
    assert PREFIX + "uk-us-remote" not in found
