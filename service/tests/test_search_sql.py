"""The public job search — executed against a real Postgres, and read through the API.

`store.search_postings` is assembled as SQL text, and every interesting way it can be wrong
is semantic rather than syntactic: a `distinct on` that collapses the wrong rows, an `or`
that binds the wrong side, an offset page that repeats a row it already showed. Reading the
string cannot catch any of those, so these tests run it. The API half is here rather than in
`test_webapp_lifecycle.py` for the same reason — the facet guard is only meaningful against
rows a database actually holds.

Needs a throwaway database, and deliberately does NOT read `DATABASE_URL`: this file inserts
and deletes rows, and `DATABASE_URL` on a developer's machine can point at the live box.

    docker run --rm -d --name jd_test_pg -e POSTGRES_USER=jobmatch \\
        -e POSTGRES_PASSWORD=jobmatch -e POSTGRES_DB=jobmatch -p 5434:5432 pgvector/pgvector:pg16
    docker exec -i jd_test_pg psql -U jobmatch -d jobmatch < service/db/schema.sql
    TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5434/jobmatch python -m pytest \\
        service/tests/test_search_sql.py

Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from service import store, taxonomy, webapp

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "searchtest-"

#: A nonsense token planted in every fixture's description, and ANDed into every query this
#: file makes.
#:
#: Without it these tests silently assume the `postings` table holds nothing but their own
#: rows. That is true in CI (a throwaway container, schema loaded, nothing else) and false on
#: any developer machine that has run `dev/seed.py` — and the failure is not a clean one: the
#: facet assertions start reading *other people's* categories and countries, so the test fails
#: for a reason that has nothing to do with the code under test. It failed exactly that way on
#: 2026-08-12 against a database with 45 seeded rows.
#:
#: `plainto_tsquery` ANDs its terms, so appending this to a real query narrows to the
#: intersection rather than replacing it, and every test below still exercises the predicate
#: it claims to.
MARKER = "zzsearchfixture"

# posting_id -> (title, company, country, city, work_mode, seniority, category,
#                remote_signal, dedup_key, is_active)
#
# Two rows deliberately share a dedup_key and sit on different sources: the same job carried
# by a national register and by the employer's own ATS board is the case the feed collapses,
# and it is real — `mpsv` and `greenhouse` both carry Czech employers.
FIXTURES: dict[str, tuple] = {
    "dup-ats":     ("Senior Data Engineer", "Acme", "CZ", "prague", "hybrid", "senior",
                    "data_engineering", False, "acme|senior data engineer", True),
    "dup-register": ("Senior Data Engineer", "Acme", "CZ", "prague", None, "senior",
                     "data_engineering", False, "acme|senior data engineer", True),
    "designer":    ("Junior Designer", "Beta", "DE", "berlin", "onsite", "junior",
                    "design", False, "beta|junior designer", True),
    "remote-dev":  ("Remote Python Developer", "Ceta", "DE", None, "remote", "mid",
                    "software_engineering", True, "ceta|remote python developer", True),
    # The 2026-08-08 raw-hint residue: `role_category` holding a Swedish SSYK *label* rather
    # than a category. Still ~1 058 rows in production on 2026-08-12, draining over the
    # staleness window. It must never be offered as a filter on a public page.
    "ssyk-label":  ("Butiksbitrade", "Delta", "SE", "stockholm", None, None,
                    "Butikssäljare, fackhandel", False, "delta|butiksbitrade", True),
    # An honest decline. Reachable when nothing is ticked; never offered as a chip.
    "uncat":       ("Mystery Specialist", "Eps", "SE", None, None, None,
                    "uncategorised", False, "eps|mystery specialist", True),
    "expired":     ("Old Data Engineer", "Zeta", "CZ", "prague", None, "senior",
                    "data_engineering", False, "zeta|old data engineer", False),
    # Hybrid in a country that also has a genuinely remote row — the pair is what makes
    # "hybrid is not remote" a test rather than an assertion about one row.
    "hybrid-cz":   ("Hybrid Analyst", "Eta", "CZ", "brno", "hybrid", "mid",
                    "data_analysis", False, "eta|hybrid analyst", True),
}


def _pid(key: str) -> str:
    return PREFIX + key


@pytest.fixture(scope="module", autouse=True)
def fixtures():
    """Point the store at the test database, insert the corpus, remove only our own rows.

    Rebinding `DATABASE_URL` and clearing `store._POOL` is the same dance every SQL-backed
    file here does, and it is not ceremony: the module-level pool is process-wide, so running
    this file *after* one that pointed the store somewhere else — or with no `DATABASE_URL`
    at all, which is the normal case in CI — connects to the wrong database or raises. Doing
    it in the fixture rather than at import time keeps the skip working when the variable is
    unset.
    """
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    ids = [_pid(k) for k in FIXTURES]
    with store.cursor(commit=True) as cur:
        cur.execute("delete from postings where posting_id = any(%s)", (ids,))
        for key, (title, company, cc, city, mode, sen, cat, remote, dedup, active) \
                in FIXTURES.items():
            cur.execute(
                """insert into postings
                   (posting_id, source, title, company, url, description, country_code,
                    city, work_mode, seniority, role_category, remote_signal, dedup_key,
                    eligibility, is_active)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (_pid(key), "greenhouse" if "ats" in key else "mpsv", title, company,
                 f"https://example.invalid/{key}",
                 f"{MARKER} A job description mentioning {title}.",
                 cc, city, mode, sen, cat, remote, PREFIX + dedup, "eligible", active))
    try:
        yield
    finally:
        with store.cursor(commit=True) as cur:
            cur.execute("delete from postings where posting_id = any(%s)", (ids,))
        store._POOL = None
        if previous_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_dsn


def ids_of(rows: list[dict]) -> set[str]:
    """Our fixture ids only — the table may hold a developer's other rows."""
    return {r["posting_id"] for r in rows if str(r["posting_id"]).startswith(PREFIX)}


def scoped(**kw) -> dict:
    """The caller's arguments with `MARKER` ANDed into the text query."""
    kw["q"] = f"{MARKER} {kw['q']}" if kw.get("q") else MARKER
    return kw


def search(**kw) -> set[str]:
    rows, _, _ = store.search_postings(limit=store.SEARCH_LIMIT_MAX, **scoped(**kw))
    return ids_of(rows)


# --------------------------------------------------------------------- the store ----

def test_a_job_carried_twice_is_one_result():
    """The same job on a register and on an ATS board is one row in the feed.

    `/matches` is deliberately NOT deduplicated — it is a subscriber's complete record. This
    is a stranger browsing inventory, where two identical cards read as a broken site.
    """
    got = search(q="senior data engineer")
    assert got & {_pid("dup-ats"), _pid("dup-register")}
    assert len(got & {_pid("dup-ats"), _pid("dup-register")}) == 1


def test_an_unclassified_posting_is_reachable_when_no_category_is_ticked():
    """The rule every lookup table in front of the matcher already follows, at the UI layer.

    A search with no category filter must return postings the taxonomy declined. Hiding them
    would silently withhold 21 774 production rows behind a filter nobody applied — the
    failure this repo keeps rediscovering, arriving through a new page.
    """
    assert _pid("uncat") in search()
    assert _pid("ssyk-label") in search()


def test_an_inactive_posting_is_never_returned():
    """`is_active` is the freshness contract. A dead posting in a public feed is a dead link
    a stranger clicks, and `check_links.py` cannot see it because nothing refetches a URL."""
    assert _pid("expired") not in search()
    assert _pid("expired") not in search(q="data engineer")
    assert _pid("expired") not in search(countries=["CZ"])


def test_hybrid_is_not_remote():
    """`remote_signal` is the posting's own words; `work_mode='hybrid'` is a commute. Folding
    hybrid into remote is how someone filtering for remote work gets an office job."""
    got = search(remote_only=True)
    assert _pid("remote-dev") in got
    assert _pid("hybrid-cz") not in got
    assert _pid("dup-ats") not in got


def test_a_city_filter_cannot_escape_its_country():
    """Cities are stored qualified (`cz:prague`) and filtered as (country, city) pairs. A bare
    slug would match a same-named city in another country's table the day one is added."""
    got = search(cities=["CZ:prague"])
    assert _pid("dup-ats") in got
    assert _pid("designer") not in got
    # A malformed pair is dropped rather than widening the search to everything.
    assert search(cities=["not-a-city"]) == search()


def test_paging_is_stable_and_reaches_every_row():
    """Offset paging over a non-unique order repeats one row and skips another, silently.

    `first_seen_at` ties are the normal case — an ingest stamps thousands of rows inside one
    second — so `posting_id` is the last sort key on every path. Walk the corpus one row at a
    time: every fixture must appear exactly once.
    """
    seen: list[str] = []
    for offset in range(0, len(FIXTURES) + 6):
        rows, _, _ = store.search_postings(limit=1, offset=offset, **scoped())
        seen.extend(r["posting_id"] for r in rows if str(r["posting_id"]).startswith(PREFIX))
    # 8 fixtures, less the inactive one and the collapsed duplicate.
    assert len(seen) == len(set(seen)), f"a row was served on two pages: {seen}"
    assert set(seen) == search()


def test_the_total_is_bounded_and_says_so(monkeypatch):
    """A public endpoint must not sort the corpus to produce a number the UI renders as
    '500+'. The cap comes back with a flag, so nothing quotes a ceiling as a fact."""
    monkeypatch.setattr(store, "SEARCH_TOTAL_CAP", 2)
    _, total, capped = store.search_postings(limit=10, **scoped())
    assert total == 2 and capped is True


def test_the_returned_columns_are_a_list_not_a_star():
    """`description`, `eligibility` and the lifecycle timestamps must not leave the box. A
    `select *` here would ship whatever column is added next without anyone deciding to."""
    rows, _, _ = store.search_postings(limit=1, **scoped(q="senior data engineer"))
    assert rows, "fixture missing"
    for banned in ("description", "eligibility", "embedding", "last_seen_at", "search_tsv"):
        assert banned not in rows[0], f"{banned} reached the public query"


def test_a_facet_does_not_collapse_the_menu_it_came_from():
    """Ticking Czechia must not reduce the country menu to Czechia — otherwise a visitor can
    narrow once and never widen again without knowing to clear the filter first."""
    facets = store.search_facets(countries=["CZ"], **scoped())
    countries = {f["value"] for f in facets["countries"]}
    assert {"CZ", "DE", "SE"} <= countries
    # The *other* facet does narrow, which is the half that makes the counts meaningful.
    cats = {f["value"] for f in facets["categories"]}
    assert "design" not in cats, "a DE-only category was counted under a CZ filter"


# ----------------------------------------------------------------------- the API ----

@pytest.fixture()
def client(monkeypatch):
    # The global rate limiter is process-local state shared across tests; reset it so a
    # neighbouring test cannot make this one fail by exhausting the window.
    monkeypatch.setattr(webapp, "_jobs_total", 0)
    return TestClient(webapp.app)


def test_the_category_menu_offers_only_real_categories(client):
    """The guard that makes this column safe to put on a public page.

    Production holds ~1 058 rows whose `role_category` is a raw Swedish SSYK label. A
    `select distinct role_category` into a dropdown would offer `Butikssäljare, fackhandel`
    as a filter, and `uncategorised` as a search nobody means to run.
    """
    body = client.get("/jobs", params={"q": MARKER}).json()
    offered = {f["value"] for f in body["facets"]["categories"]}
    assert "Butikssäljare, fackhandel" not in offered
    assert taxonomy.UNCATEGORISED not in offered
    assert offered <= set(taxonomy.CATEGORIES)


def test_an_unknown_filter_is_refused_not_ignored(client):
    """A hand-edited URL that silently drops its filter renders a full unfiltered page under
    a heading claiming a filter. Same reasoning as `_check_role_categories`: the only outcome
    a visitor can see is the request failing."""
    assert client.get("/jobs", params={"category": "softwar"}).status_code == 400
    assert client.get("/jobs", params={"country": "ZZ"}).status_code == 400
    assert client.get("/jobs", params={"seniority": "principal"}).status_code == 400
    assert client.get("/jobs", params={"work_mode": "onsight"}).status_code == 400


def test_facets_are_sent_once_and_not_blanked_on_later_pages(client):
    """Absent, never empty. An empty list reads as 'nothing to filter by' and would blank the
    menu the visitor is currently using."""
    assert "facets" in client.get("/jobs").json()
    assert "facets" not in client.get("/jobs", params={"offset": 20}).json()


def test_the_search_writes_nothing(client):
    """A GET must not change state, and this one must not even record what was searched for.

    A search term is a statement about somebody's job hunt and the privacy policy makes no
    promise covering it. If this test ever has to change, `PrivacyEn.tsx` and `PrivacyCs.tsx`
    change in the same commit.

    Counts *every* table rather than the one somebody would think to log to: the point is
    that nothing anywhere grows, and naming `events` alone would pass a change that recorded
    searches somewhere new.
    """
    def snapshot() -> dict[str, int]:
        with store.cursor() as cur:
            cur.execute("select tablename from pg_tables where schemaname = 'public'")
            names = sorted(r["tablename"] for r in cur.fetchall())
            out = {}
            for name in names:
                cur.execute(f'select count(*) as n from "{name}"')
                out[name] = cur.fetchone()["n"]
            return out

    # Retried once, and the retry is not flake-tolerance dressed up: this file is run against
    # a shared developer database as often as against CI's throwaway one, and *another*
    # process writing between the two reads fails this test for a reason that has nothing to
    # do with /jobs. A real write from /jobs happens on every call and so survives the retry;
    # a neighbouring writer would have to land in the same millisecond window twice. Observed
    # once on 2026-08-12, which is why this is written down rather than deleted.
    for attempt in (1, 2):
        before = snapshot()
        assert client.get("/jobs", params={"q": "data engineer", "country": "CZ"}).status_code == 200
        after = snapshot()
        if after == before:
            return
    grew = {t: (before[t], after[t]) for t in after if after[t] != before.get(t)}
    raise AssertionError(f"GET /jobs wrote to the database: {grew}")


def test_the_rate_limit_sheds_load_rather_than_serving_a_scraper(client, monkeypatch):
    """Unauthenticated and indexed: without a ceiling this is a full corpus dump at wire
    speed. The limiter is the inner bound; Cloudflare is the outer one."""
    monkeypatch.setattr(webapp, "JOBS_MAX_GLOBAL", 3)
    monkeypatch.setattr(webapp, "_jobs_window_start", 0.0)
    monkeypatch.setattr(webapp, "_jobs_total", 0)
    codes = [client.get("/jobs").status_code for _ in range(5)]
    assert codes.count(200) == 3
    assert codes[-1] == 503
