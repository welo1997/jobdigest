"""What the location gate actually lets through — run against a real Postgres.

`geo.location_predicate` is assembled as SQL text, and the interesting failures are semantic
rather than syntactic: an `or` that binds the wrong side, a null that fails a comparison
instead of passing it, a parameter that lands in the wrong placeholder. Inspecting the string
cannot catch any of those, so these tests execute it.

Needs a throwaway database, and deliberately does NOT read `DATABASE_URL`: this file inserts
rows, and `DATABASE_URL` on a developer's machine can point at the live box.

    docker run --rm -d --name jd_test_pg -e POSTGRES_USER=jobmatch \\
        -e POSTGRES_PASSWORD=jobmatch -e POSTGRES_DB=jobmatch -p 5434:5432 postgres:16
    docker exec -i jd_test_pg psql -U jobmatch -d jobmatch < service/db/schema.sql
    TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5434/jobmatch python -m pytest \\
        service/tests/test_geo_sql.py

Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import geo, store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "geotest-"

# posting_id -> (country_code, city, remote_signal, region, location, work_mode)
#
# `work_mode` is null on most of these on purpose: it is null on most real postings, because
# the ad never said. Only the rows whose name claims an arrangement carry one, so the "unknown
# always passes" rule is exercised by the majority of the fixture set rather than by one case.
FIXTURES = {
    "prague-onsite":    ("CZ", "prague", False, "cz", "Praha 4", None),
    "brno-onsite":      ("CZ", "brno", False, "cz", "Brno", None),
    "brno-hybrid":      ("CZ", "brno", False, "cz", "Brno (hybrid)", "hybrid"),
    "prague-hybrid":    ("CZ", "prague", False, "cz", "Praha (hybrid)", "hybrid"),
    "prague-office":    ("CZ", "prague", False, "cz", "Praha, on site", "onsite"),
    "cz-remote":        ("CZ", None, True, "cz", "Remote, Czechia", "remote"),
    "berlin-onsite":    ("DE", "berlin", False, "eu", "Berlin", None),
    "unknown-onsite":   (None, None, False, "other", "Somewhereville", None),
    "cz-unknown-city":  ("CZ", None, False, "cz", "Kolín", None),
    "us-remote":        ("US", None, True, "us", "Remote (US)", "remote"),
    "worldwide-remote": (None, None, True, "worldwide", "Remote, worldwide", "remote"),
    "de-remote":        ("DE", None, True, "eu", "Remote (Germany)", "remote"),
    # US and GB became selectable on 2026-08-05. An on-site US/GB role must now surface for a
    # subscriber who picked that country, and stay hidden from an EEA-only one.
    "us-onsite":        ("US", "san-francisco", False, "us", "San Francisco", None),
    "gb-onsite":        ("GB", "london", False, "uk", "London", None),
}


@pytest.fixture(scope="module", autouse=True)
def db():
    """Point the store at the test database and insert the fixture postings.

    Rows are namespaced and deleted afterwards, so this leaves nothing behind even if the
    database is a shared dev one.
    """
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    rows = [
        {"posting_id": PREFIX + pid, "source": "test", "url": f"https://x.test/{pid}",
         "title": "Data Analyst", "company": "Test", "description": "",
         "country_code": cc, "city": city, "remote_signal": remote, "region": region,
         "location": loc, "work_mode": mode, "eligibility": "eligible", "seniority": "mid",
         "work_type": "permanent", "role_category": "data_analysis",
         "dedup_key": PREFIX + pid}
        for pid, (cc, city, remote, region, loc, mode) in FIXTURES.items()
    ]
    store.upsert_postings(rows)
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


def allowed(profile: dict) -> set[str]:
    """The fixture postings this profile's location gate lets through."""
    sql, params = geo.location_predicate(profile)
    with store.cursor() as cur:
        cur.execute(
            f"select posting_id from postings p where p.posting_id like %s and ({sql})",
            [PREFIX + "%", *params],
        )
        return {r["posting_id"][len(PREFIX):] for r in cur.fetchall()}


PRAGUE_ONLY = {"countries": ["CZ"], "cities": ["cz:prague"], "remote_scope": "eu"}


# ------------------------------------------------------------------ the reported bug ---

def test_an_onsite_job_in_another_city_is_excluded():
    """The bug this feature exists to fix: a Prague subscriber was emailed on-site roles in
    Brno. Both the plain on-site and the hybrid Brno posting must be gone — hybrid is not
    remote, it is "be in Brno most weeks"."""
    got = allowed(PRAGUE_ONLY)
    assert "prague-onsite" in got
    assert "brno-onsite" not in got
    assert "brno-hybrid" not in got


def test_a_remote_job_ignores_the_city_list():
    """Cities constrain commuting, not remote work — otherwise picking Prague would silently
    throw away every remote job, which is the opposite of what the subscriber asked for."""
    assert "cz-remote" in allowed(PRAGUE_ONLY)


def test_a_country_that_was_not_picked_is_excluded():
    assert "berlin-onsite" not in allowed(PRAGUE_ONLY)


def test_unknown_locations_are_kept_for_the_matcher_to_judge():
    """`country_code` is null for whole sources and a typed city can never equal a resolved
    slug, so dropping unresolved rows would be a stricter promise than the data supports. They
    pass the gate and reach the AI matcher, which reads the raw location text. Recall is never
    lost here; only precision."""
    got = allowed(PRAGUE_ONLY)
    assert "unknown-onsite" in got      # no country resolved at all
    assert "cz-unknown-city" in got     # right country, unrecognised town


# ------------------------------------------------------------------- remote scope ---

def test_scope_country_keeps_remote_work_inside_the_chosen_countries():
    got = allowed({**PRAGUE_ONLY, "remote_scope": "country"})
    assert "cz-remote" in got
    assert "de-remote" not in got
    assert "worldwide-remote" not in got
    assert "us-remote" not in got


def test_scope_eu_admits_remote_work_from_any_eu_country():
    got = allowed({**PRAGUE_ONLY, "remote_scope": "eu"})
    assert {"cz-remote", "de-remote", "worldwide-remote"} <= got
    assert "us-remote" not in got
    # Still only a remote exemption — a German *on-site* role is not in scope.
    assert "berlin-onsite" not in got


def test_scope_worldwide_admits_every_remote_role():
    got = allowed({**PRAGUE_ONLY, "remote_scope": "worldwide"})
    assert {"cz-remote", "de-remote", "worldwide-remote", "us-remote"} <= got
    assert "brno-onsite" not in got


# ------------------------------------------------------------------ multi-country ---

def test_cities_restrict_only_the_country_they_were_named_for():
    """"Prague only in Czechia, anywhere in Germany" — the case an unqualified city list
    cannot express, and the reason `cities` stores `cz:prague` rather than `prague`."""
    got = allowed({"countries": ["CZ", "DE"], "cities": ["cz:prague"], "remote_scope": "eu"})
    assert {"prague-onsite", "berlin-onsite"} <= got
    assert "brno-onsite" not in got


def test_no_cities_means_any_city_in_the_country():
    got = allowed({"countries": ["CZ"], "cities": [], "remote_scope": "eu"})
    assert {"prague-onsite", "brno-onsite", "brno-hybrid"} <= got
    assert "berlin-onsite" not in got


# ------------------------------------------------------------------ US / GB selectable ---

def test_a_us_onsite_role_surfaces_for_a_us_subscriber_but_not_an_eea_one():
    """The point of making US selectable (2026-08-05): a US citizen wants on-site US roles,
    not only remote ones. The same posting must stay hidden from an EEA-only subscriber."""
    assert "us-onsite" in allowed({"countries": ["US"], "remote_scope": "eu"})
    assert "gb-onsite" in allowed({"countries": ["GB"], "remote_scope": "eu"})
    assert "us-onsite" not in allowed(PRAGUE_ONLY)
    assert "gb-onsite" not in allowed(PRAGUE_ONLY)


def test_eu_scope_excludes_a_us_remote_role_even_for_a_us_subscriber():
    """The EEA_COUNTRIES decoupling: picking the US for on-site work must not turn `eu` scope
    ("Anywhere in the EU or EEA") into a channel for US fully-remote roles. The on-site US
    role is admitted; the remote US role needs `worldwide`."""
    eu = allowed({"countries": ["US"], "remote_scope": "eu"})
    assert "us-onsite" in eu and "us-remote" not in eu
    wide = allowed({"countries": ["US"], "remote_scope": "worldwide"})
    assert {"us-onsite", "us-remote"} <= wide


def test_several_cities_in_one_country():
    got = allowed({"countries": ["CZ"], "cities": ["cz:prague", "cz:brno"],
                   "remote_scope": "country"})
    assert {"prague-onsite", "brno-onsite", "brno-hybrid"} <= got


# -------------------------------------------------------------------- work setup ---

def test_hybrid_is_still_gated_by_city_not_by_the_remote_scope():
    """The point of storing `hybrid` separately is *not* to loosen the location rule. Two days
    a week in a Brno office is still a commute to Brno, so a Prague subscriber must not see it
    however wide their remote scope is — and a Prague hybrid role must still reach them."""
    got = allowed({**PRAGUE_ONLY, "remote_scope": "worldwide",
                   "work_modes": ["onsite", "hybrid", "remote"]})
    assert "brno-hybrid" not in got
    assert "prague-hybrid" in got


def test_all_three_modes_selected_filters_nothing():
    """The default, and what every existing subscriber was migrated into. It must be
    byte-identical to no work-setup filter at all, or migration 012 silently changed
    somebody's digest."""
    wide = allowed({**PRAGUE_ONLY, "work_modes": ["onsite", "hybrid", "remote"]})
    assert wide == allowed(PRAGUE_ONLY)
    assert geo.work_mode_predicate({"work_modes": list(geo.WORK_MODES)}) == ("true", [])


def test_deselecting_onsite_drops_proven_office_jobs_but_keeps_the_unknowns():
    """The honest half-measure, and the reason it is written this way. `prague-office` says
    on-site in its own text and goes; `prague-onsite` never said, so it stays and the AI
    matcher judges it. Dropping unknowns here would delete most of the inventory on a guess."""
    got = allowed({**PRAGUE_ONLY, "work_modes": ["hybrid", "remote"]})
    assert "prague-office" not in got
    assert "prague-hybrid" in got
    assert "cz-remote" in got
    assert {"prague-onsite", "unknown-onsite", "cz-unknown-city"} <= got


def test_remote_only_is_a_filter_that_genuinely_works():
    """`remote` is the one mode positively detected, so this selection is exact — no hybrid
    and no office job survives it, whatever city they are in."""
    got = allowed({**PRAGUE_ONLY, "remote_scope": "worldwide", "work_modes": ["remote"]})
    assert {"cz-remote", "de-remote", "worldwide-remote", "us-remote"} <= got
    assert "prague-hybrid" not in got
    assert "brno-hybrid" not in got
    assert "prague-office" not in got
    # …and the unknowns still pass, for the same reason as above: nobody proved they are not
    # remote. This is the cost of the rule, stated rather than hidden.
    assert "prague-onsite" in got


def test_an_empty_work_mode_selection_widens_rather_than_matching_nothing():
    """A subscriber who unticks every box has stated no preference. Reading that as "nothing
    is acceptable" would empty their digest with no error anywhere — the failure mode this
    codebase keeps meeting."""
    assert allowed({**PRAGUE_ONLY, "work_modes": []}) == allowed(PRAGUE_ONLY)


def test_the_work_mode_gate_survives_the_legacy_region_path():
    """A profile with no `countries` returns early from `location_predicate`. The mode gate has
    to be ANDed onto *that* branch too, and its parameter has to land in the right placeholder
    — which is exactly what an inspection of the SQL string cannot tell you."""
    got = allowed({"regions": ["cz"], "work_modes": ["remote"]})
    assert "cz-remote" in got
    assert "prague-office" not in got
    assert "brno-hybrid" not in got


# ----------------------------------------------------------------- legacy profiles ---

def test_a_profile_without_countries_falls_back_to_the_region_bucket():
    """A row that predates migration 010, or a client that only sent `regions`. It must keep
    working exactly as before rather than matching nothing."""
    got = allowed({"regions": ["cz"]})
    assert {"prague-onsite", "brno-onsite", "cz-remote"} <= got
    assert "berlin-onsite" not in got


def test_an_empty_profile_filters_nothing():
    sql, params = geo.location_predicate({})
    assert (sql, params) == ("true", [])


# --------------------------------------------------------- through the real query ---

@pytest.mark.parametrize("profile", [
    PRAGUE_ONLY,
    {"countries": ["CZ", "DE"], "cities": [], "remote_scope": "worldwide"},
    {"countries": ["CZ"], "cities": ["cz:prague"], "remote_scope": "country",
     "role_categories": ["data_analysis"], "stack": ["sql"]},
    {"regions": ["cz"], "role_categories": ["data_analysis"]},
])
def test_query_shortlist_executes_with_the_gate_in_place(profile):
    """The predicate is spliced into a statement that already binds parameters before and
    after it. psycopg2 binds `%s` by position in the SQL *text*, so a mis-ordered fragment
    shifts every later value — Postgres then reports a type error somewhere unrelated, or
    silently filters on the wrong column when the types happen to line up."""
    rows = store.query_shortlist(profile, limit=50)
    assert isinstance(rows, list)
    ours = {r["posting_id"][len(PREFIX):] for r in rows
            if r["posting_id"].startswith(PREFIX)}
    assert "brno-onsite" not in ours or not profile.get("cities")


def test_query_candidates_applies_the_same_gate():
    rows = store.query_candidates(PRAGUE_ONLY, limit=50)
    ours = {r["posting_id"][len(PREFIX):] for r in rows if r["posting_id"].startswith(PREFIX)}
    assert "prague-onsite" in ours
    assert "brno-onsite" not in ours
