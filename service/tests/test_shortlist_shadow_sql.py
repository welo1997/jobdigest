"""The vector shadow shortlist, executed against a real Postgres.

`shortlist_shadow` exists to answer one question — would vector ranking have surfaced what the
keyword path did? — and it is only worth anything if three things hold. The shadow must be
gated **identically** to the live shortlist, or the comparison measures the gates rather than
the ranking. It must never be able to cost a subscriber a digest, because it runs inside the
export that decides tomorrow's email under `set -euo pipefail`. And it must not silently
record a truncated ranking, which is the specific way this query fails.

That last one is not hypothetical. Measured on production 2026-08-08, `order by embedding <=>`
under a selective WHERE returned **4 rows for a LIMIT of 120** on one live profile and **0 rows
at every K** on another: pgvector's HNSW gathers `hnsw.ef_search` (default 40) candidates and
applies the WHERE *afterwards*, so a selective gate starves it, with no error and no log. The
fix is `hnsw.iterative_scan`, and `test_the_scan_is_told_not_to_stop_at_ef_search` fails if
that line is ever removed — the behavioural version would need tens of thousands of indexed
rows to reproduce, so the mechanism is pinned directly instead.

Same harness and reason as `test_geo_sql.py`, but **do not reuse that file's recipe**: it
starts `postgres:16`, which has no pgvector, so every case here skips itself and the run reads
green. This file needs its own database, and the port is deliberately not 5434 — the plain
`postgres:16` the other SQL tests use is usually already sitting there.

    docker build -f deploy/db.Dockerfile -t jobdigest-db:pg16-pgvector .
    docker run --rm -d --name jd_test_pg -e POSTGRES_USER=jobmatch \\
      -e POSTGRES_PASSWORD=jobmatch -e POSTGRES_DB=jobmatch \\
      -e POSTGRES_INITDB_ARGS="--locale=en_US.utf8" -p 5435:5432 jobdigest-db:pg16-pgvector
    docker exec -i jd_test_pg psql -U jobmatch -d jobmatch < service/db/schema.sql
    TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5435/jobmatch \\
      python -m pytest service/tests -q

`schema.sql` alone is enough — it carries the migration 015 shape — and the fixture reports
which half is missing rather than failing obscurely: no `vector` extension and no
`shortlist_shadow` skip with different reasons. **A skip here is a configuration failure, not
a pass**; CI enforces that by failing the build if any `test_*_sql.py` file skips.
Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import contextlib
import os

import pytest

from service import embed, matcher, store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "shadowtest-"
DIM = 384


def _vec(lead: float) -> str:
    """A unit-ish vector whose first component carries the signal, rest fixed.

    Distances only need to be *ordered* here, not meaningful, so one moving component keeps
    the fixtures readable and the expected ranking obvious.
    """
    return "[" + ",".join([f"{lead}"] + ["0.01"] * (DIM - 1)) + "]"


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    with store.cursor(commit=True) as cur:
        cur.execute("select count(*) as n from pg_extension where extname = 'vector'")
        if not cur.fetchone()["n"]:
            pytest.skip("pgvector is not installed in TEST_DATABASE_URL")
        cur.execute("select to_regclass('shortlist_shadow') as t")
        if not cur.fetchone()["t"]:
            pytest.skip("migration 015 has not been applied to TEST_DATABASE_URL")

    # Three CZ postings at decreasing similarity, plus one identical-but-German row that only
    # the location gate can exclude, and one with no vector at all.
    rows = [
        (f"{PREFIX}near", "cz", _vec(0.99)),
        (f"{PREFIX}mid", "cz", _vec(0.60)),
        (f"{PREFIX}far", "cz", _vec(0.20)),
        (f"{PREFIX}foreign", "de", _vec(0.995)),
        (f"{PREFIX}novec", "cz", None),
    ]
    with store.cursor(commit=True) as cur:
        for pid, country, vec in rows:
            cur.execute(
                "insert into postings (posting_id, source, url, title, company, region, "
                "  country_code, eligibility, seniority, work_type, role_category, "
                "  dedup_key, is_active, embedding, embedding_model) "
                "values (%s,'test',%s,'Data Analyst','Test','cz',%s,'eligible','mid',"
                "  'permanent','data_analysis',%s,true,%s::halfvec,%s) "
                "on conflict (posting_id) do nothing",
                (pid, f"https://x.test/{pid}", country.upper(), pid, vec,
                 "test-model" if vec else None))
    try:
        yield
    finally:
        with store.cursor(commit=True) as cur:
            cur.execute("delete from shortlist_shadow where posting_id like %s",
                        (PREFIX + "%",))
            cur.execute("delete from postings where posting_id like %s", (PREFIX + "%",))
            cur.execute("delete from profiles where email like %s", (PREFIX + "%",))
        store._POOL = None
        if previous_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_dsn


@pytest.fixture
def profile():
    """A CZ-only subscriber whose vector sits nearest `{PREFIX}near`."""
    email = f"{PREFIX}a@example.test"
    with store.cursor(commit=True) as cur:
        cur.execute("delete from profiles where email = %s", (email,))
        cur.execute(
            "insert into profiles (email, label, countries, embedding, embedding_model) "
            "values (%s, 'shadow test', %s, %s::halfvec, 'test-model') "
            "returning *", (email, ["CZ"], _vec(1.0)))
        return dict(cur.fetchone())


def test_the_shadow_is_gated_exactly_like_the_live_shortlist(profile):
    """The German row is the *closest* vector in the fixture and must still not appear.

    If the shadow stopped applying `_hard_gate` it would rank first, and the resulting
    comparison against the live shortlist would look like disagreement about ranking when it
    was really disagreement about admissibility.
    """
    ranked = store.query_shortlist_vector(profile, limit=10)
    ids = [r["posting_id"] for r in ranked]

    assert f"{PREFIX}foreign" not in ids, "location gate not applied to the shadow"
    assert ids[0] == f"{PREFIX}near", "vector ordering is not nearest-first"
    assert ids == [f"{PREFIX}near", f"{PREFIX}mid", f"{PREFIX}far"]
    assert f"{PREFIX}novec" not in ids, "a posting with no vector cannot be retrieved"
    # Similarity is stored the way a reader expects: bigger is better, and it decreases.
    sims = [r["similarity"] for r in ranked]
    assert sims == sorted(sims, reverse=True)
    assert [r["rank"] for r in ranked] == [1, 2, 3]


def test_the_scan_is_told_not_to_stop_at_ef_search(profile, monkeypatch):
    """Pins the fix for the starvation described in the module docstring.

    Reproducing the truncation behaviourally needs an HNSW index over tens of thousands of
    rows, which no unit test should build — so this asserts the mechanism instead: the
    statement that defeats the post-filter must be issued, and it must be issued *before* the
    ranking query rather than after it.
    """
    seen: list[str] = []
    real_cursor = store.cursor

    @contextlib.contextmanager
    def recording_cursor(*a, **kw):
        with real_cursor(*a, **kw) as cur:
            class Proxy:
                def __getattr__(self, name):
                    return getattr(cur, name)

                def execute(self, sql, params=None):
                    seen.append(str(sql))
                    return cur.execute(sql, params)
            yield Proxy()

    monkeypatch.setattr(store, "cursor", recording_cursor)
    store.query_shortlist_vector(profile, limit=10)

    iterative = [i for i, s in enumerate(seen) if "hnsw.iterative_scan" in s]
    ranking = [i for i, s in enumerate(seen) if "<=>" in s and "from postings" in s]
    assert iterative, ("query_shortlist_vector no longer defeats the HNSW post-filter; it "
                       "will silently return fewer rows than asked for under a selective gate")
    assert ranking and iterative[0] < ranking[0], \
        "iterative_scan must be set before the ranking query, not after it"


def test_in_live_records_agreement_with_the_keyword_shortlist(profile):
    """`in_live` is the column the whole table exists for and can only be computed now."""
    ranked = store.query_shortlist_vector(profile, limit=10)
    live = [{"posting_id": f"{PREFIX}near"}, {"posting_id": f"{PREFIX}far"}]

    n = store.record_shortlist_shadow(profile["id"], ranked,
                                      {c["posting_id"] for c in live})
    assert n == 3

    with store.cursor() as cur:
        cur.execute("select posting_id, rank, in_live from shortlist_shadow "
                    " where profile_id = %s and day = current_date order by rank",
                    (profile["id"],))
        got = {r["posting_id"]: r["in_live"] for r in cur.fetchall()}
    assert got == {f"{PREFIX}near": True, f"{PREFIX}mid": False, f"{PREFIX}far": True}


def test_re_exporting_the_same_day_replaces_rather_than_accumulates(profile):
    """The export can be re-run by hand; the table must hold one ranking per profile per day."""
    ranked = store.query_shortlist_vector(profile, limit=10)
    store.record_shortlist_shadow(profile["id"], ranked, set())
    store.record_shortlist_shadow(profile["id"], ranked[:1], set())

    with store.cursor() as cur:
        cur.execute("select count(*) as n from shortlist_shadow "
                    " where profile_id = %s and day = current_date", (profile["id"],))
        assert cur.fetchone()["n"] == 1


def test_a_profile_with_no_vector_yields_nothing_and_does_not_raise():
    """Null embeddings are always valid — the same rule as work_mode and an unresolved city.

    A profile saved today has no vector until the next embed run, and that must be an absence
    of research data rather than an exception inside the export.
    """
    email = f"{PREFIX}b@example.test"
    with store.cursor(commit=True) as cur:
        cur.execute("delete from profiles where email = %s", (email,))
        cur.execute("insert into profiles (email, label, countries) "
                    "values (%s,'no vector',%s) returning *", (email, ["CZ"]))
        p = dict(cur.fetchone())

    assert store.query_shortlist_vector(p, limit=10) == []
    assert store.record_shortlist_shadow(p["id"], [], set()) == 0


def test_a_failing_shadow_cannot_cost_a_subscriber_their_digest(profile, monkeypatch):
    """The guarantee that lets this run inside the export at all.

    `jobdigest-match.sh` runs under `set -euo pipefail`, so an exception raised here would
    abort the export and every subscriber after this one would lose their digest — to a
    measurement that is read by nothing on the delivery path.
    """
    def boom(*a, **kw):
        raise RuntimeError("pgvector went away")

    monkeypatch.setattr(store, "query_shortlist_vector", boom)
    matcher._record_shadow(profile, [{"posting_id": f"{PREFIX}near"}])  # must not raise


def test_nothing_on_the_delivery_path_touches_the_shadow_table():
    """The shadow must stay invisible to everything that decides what a person is shown.

    Writing it is `store.py` (the SQL) and `matcher.py` (the export calling the writer), and
    those two are the whole of it. The modules listed here are what builds, sends and renders
    — if any of them starts consulting the shadow, the experiment has quietly become the
    product, which is exactly what running the two rankings side by side exists to prevent.
    `select` is checked separately because the danger is a *read*: the table's own writer
    necessarily names it.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    delivery = ["digest.py", "mailer.py", "webapp.py", "pipeline.py", "api.py",
                "transactional.py", "watchdog.py"]
    for name in delivery:
        text = (root / name).read_text(encoding="utf-8")
        assert "shortlist_shadow" not in text, \
            f"{name} is on the delivery path and must not reference shortlist_shadow"

    # And nowhere in the package does anything read it: the only SQL allowed to name the table
    # after `from` is the writer's own `delete from`. A `select ... from shortlist_shadow`
    # appearing anywhere in `service/` is the failure this guards. The comparison queries live
    # in `scripts/`, deliberately outside the code that ships in the image.
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        start = 0
        while (i := text.find("from shortlist_shadow", start)) != -1:
            assert text[max(0, i - 10):i].strip().endswith("delete"), \
                f"{path.name} reads shortlist_shadow; the shadow is write-only in service/"
            start = i + 1


def test_the_query_text_excludes_what_measured_as_noise():
    """Pins the three exclusions from the 2026-08-08 measurement.

    Each was measured against subscribers' real picks, and `other_tech_function` alone moved
    recall@120 from 2.7% to 42.7% on the profile that carried it. These are cheap to undo by
    accident — adding `stack` back looks like restoring lost signal — so they are pinned with
    the reason attached rather than left to a comment.
    """
    p = {
        "label": "My digest",
        "role_categories": ["other_tech_function", "social_media"],
        # Tokens chosen to appear ONLY in `stack`, so the assertion below tests that the field
        # is excluded rather than that a word is absent — the CV line legitimately names tools.
        "stack": ["figma", "capcut"],
        "sectors": ["ecommerce"],
        "cv_summary": "Detected: social media - ~2 yrs",
    }
    text = embed.profile_text(p)

    assert "other tech function" not in text, \
        "the grab-bag category embeds to a centroid of nothing; see UNEMBEDDABLE_CATEGORIES"
    assert "My digest" not in text, "the label is the default on every live profile"
    assert "figma" not in text and "capcut" not in text, \
        "tool names swamp the role words in the query vector"
    assert "social media" in text, "the subscriber's actual subject must survive"
    assert "Detected: social media" in text, "the CV line is the best query text measured"


def test_a_profile_with_only_an_unembeddable_category_still_gets_a_query():
    """An empty query string embeds to ~0 and matches arbitrarily — worse than a noisy one.

    Someone whose only category is `other_tech_function` is precisely the subscriber the
    taxonomy does not serve, and they must not also be the one the vector path silently
    ignores. Falls back to what they did state.
    """
    text = embed.profile_text({
        "label": "Sales roles in Brno",
        "role_categories": ["other_tech_function"],
        "stack": ["salesforce"],
    })
    assert text.strip(), "fell through to an empty query string"
    assert "salesforce" in text


def test_changing_the_query_recipe_re_embeds_stored_profiles():
    """The recipe version is part of the stored identity, or a change is invisible.

    `_embed_profiles` re-embeds only rows whose vector is null or whose stored identity
    differs. If that identity carried the model alone, editing `profile_text` would leave every
    existing profile meaning the old recipe while new profiles meant the new one — the column
    meaning two things at once, which is what `backfill_geo` and `backfill_education` exist to
    prevent for their columns.
    """
    assert embed.PROFILE_EMBEDDING_ID != embed.EMBEDDING_MODEL_ID
    assert embed.EMBEDDING_MODEL_ID in embed.PROFILE_EMBEDDING_ID, \
        "the model half must stay comparable with postings.embedding_model"
    assert str(embed.PROFILE_TEXT_VERSION) in embed.PROFILE_EMBEDDING_ID

    # Behavioural, not a source grep: asserting the constant appears in the function body
    # passes on a comment that merely mentions it, which is how the first version of this test
    # survived the mutation it was written to catch. Instead, flip one profile's stored
    # identity to the current one and require the work queue to shrink by exactly that row.
    import service.backfill_embeddings as bf

    email = f"{PREFIX}stale@example.test"
    with store.cursor(commit=True) as cur:
        cur.execute("delete from profiles where email = %s", (email,))
        cur.execute(
            "insert into profiles (email, label, status, embedding, embedding_model) "
            "values (%s,'stale recipe','active',%s::halfvec,%s) returning id::text as id",
            (email, _vec(0.5), embed.EMBEDDING_MODEL_ID))   # model right, recipe stale
        pid = cur.fetchone()["id"]
    try:
        before = bf._embed_profiles(dry_run=True)
        with store.cursor(commit=True) as cur:
            cur.execute("update profiles set embedding_model = %s where id = %s::uuid",
                        (embed.PROFILE_EMBEDDING_ID, pid))
        after = bf._embed_profiles(dry_run=True)
        assert after == before - 1, (
            "a profile whose stored recipe version is stale was not queued for re-embedding; "
            "_embed_profiles is comparing the model alone and is blind to a recipe change")
    finally:
        with store.cursor(commit=True) as cur:
            cur.execute("delete from profiles where email = %s", (email,))
