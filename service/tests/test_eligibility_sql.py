"""What the eligibility gate actually lets through — run against a real Postgres.

`postings.eligibility` is written by `search_jobs.eligibility`, whose docstring still reads
*"Coarse EU-eligibility flag for a Czech-based candidate"*. It encodes a **subscriber-specific
judgement in a posting-level column**, and for most of this repo's life the query reading it
used a hardcoded allowlist — `('eligible','verify UK right-to-work','unknown')`.

Measured on production 2026-08-07, that constant withheld **21 291 of 21 677 active US postings
(98%)** from every subscriber. `profiles.eligible_only` defaults **true** (`schema.sql`) and has
**no UI control** — it appears only as a type in `web/lib/api.ts`, and no form writes it — so a
subscriber who ticked "United States" received essentially no US on-site roles and had no way to
turn the filter off. The US half of the 2026-08-05 "US is now selectable" change had therefore
never worked. Nothing failed, nothing logged, and it bit nobody only because all three
subscribers were CZ-only.

Inspecting the SQL string cannot show this, which is why these tests execute it. Two properties
are load-bearing and both are here:

  - the allowlist **follows `profile["countries"]`**, so US inventory reaches a US subscriber;
  - it is applied on the **widened** retrieval path too, so the floor cannot re-admit a role the
    subscriber cannot legally take.

Needs a throwaway database, and deliberately does NOT read `DATABASE_URL`: this file inserts
rows, and `DATABASE_URL` on a developer's machine can point at the live box.

    docker run --rm -d --name jd_test_pg -e POSTGRES_USER=jobmatch \\
        -e POSTGRES_PASSWORD=jobmatch -e POSTGRES_DB=jobmatch -p 5434:5432 postgres:16
    docker exec -i jd_test_pg psql -U jobmatch -d jobmatch < service/db/schema.sql
    TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5434/jobmatch python -m pytest \\
        service/tests/test_eligibility_sql.py

Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import geo, store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "eligtest-"

#: posting_id -> (country_code, region, remote_signal, eligibility).
#: `us-remote` exists to isolate this gate from the location gate: a fully-remote posting is
#: exempt from the location predicate under a `worldwide` scope, so whether it comes back
#: depends on eligibility **alone**. `us-onsite` is the headline regression — the row a US
#: subscriber is supposed to receive and did not.
FIXTURES = {
    "cz-eligible":  ("CZ", "cz", False, "eligible"),
    "cz-unknown":   ("CZ", "cz", False, "unknown"),
    "gb-verify":    ("GB", "uk", False, "verify UK right-to-work"),
    "us-onsite":    ("US", "us", False, "likely needs US work auth"),
    "us-remote":    ("US", "us", True,  "likely needs US work auth"),
    "us-blocked":   ("US", "us", False, "blocked (clearance/US-only)"),
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
         "country_code": cc, "city": None, "remote_signal": remote, "region": region,
         "location": cc, "work_mode": "remote" if remote else "onsite",
         "education_min": None, "eligibility": elig, "seniority": "mid",
         "work_type": "permanent", "role_category": "data_analysis",
         "dedup_key": PREFIX + pid}
        for pid, (cc, region, remote, elig) in FIXTURES.items()
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


def _shortlist(profile_extra: dict) -> tuple[set[str], dict]:
    profile = {
        "id": None, "countries": ["CZ"], "cities": [], "remote_scope": "worldwide",
        "work_modes": list(geo.WORK_MODES), "stack": [], "label": "",
        "eligible_only": True, "role_categories": ["data_analysis"], **profile_extra,
    }
    rows, meta = store.query_shortlist_meta(profile, limit=200)
    return ({r["posting_id"][len(PREFIX):] for r in rows
             if r["posting_id"].startswith(PREFIX)}, meta)


# --------------------------------------------- the regression this file exists for ---

def test_a_us_subscriber_receives_us_onsite_roles(monkeypatch):
    """The headline. Before 2026-08-07 this row was withheld from **every** subscriber,
    including one who had explicitly selected the United States, because the allowlist was a
    constant that excluded `likely needs US work auth` — 98% of live US inventory."""
    monkeypatch.setattr(store, "SHORTLIST_FLOOR", 1)
    ids, meta = _shortlist({"countries": ["US"]})
    assert not meta["widened"], "this case is meant to exercise the narrow path"
    assert "us-onsite" in ids
    assert "us-remote" in ids


def test_the_same_us_role_is_withheld_from_a_subscriber_who_did_not_pick_the_us(monkeypatch):
    """Isolated from the location gate on purpose: `us-remote` is fully remote and the profile
    scope is `worldwide`, so the location predicate admits it. Whether it comes back therefore
    turns on eligibility alone — and for a CZ-only subscriber `likely needs US work auth` is a
    true statement about a job they cannot take."""
    monkeypatch.setattr(store, "SHORTLIST_FLOOR", 1)
    ids, _ = _shortlist({"countries": ["CZ"]})
    assert "us-remote" not in ids
    assert "cz-eligible" in ids


def test_a_clearance_requirement_is_never_unlocked_by_picking_the_country(monkeypatch):
    """A security clearance or citizenship requirement is not something a country preference
    can satisfy, so `blocked (clearance/US-only)` stays out even for a US subscriber."""
    monkeypatch.setattr(store, "SHORTLIST_FLOOR", 1)
    ids, _ = _shortlist({"countries": ["US"]})
    assert "us-blocked" not in ids


def test_unknown_always_passes(monkeypatch):
    """`unknown` is what `work_region` produces for anywhere it cannot classify — 18 023 live
    rows, and **every Canadian posting** (`work_region("Toronto","CA")` returns `"other"`).
    Canada is therefore reachable only because this value is unconditionally admitted; if a
    `CA` branch is ever added to `work_region`, this test is what fails instead of 1 946
    postings silently vanishing from every shortlist."""
    monkeypatch.setattr(store, "SHORTLIST_FLOOR", 1)
    ids, _ = _shortlist({"countries": ["CZ"]})
    assert "cz-unknown" in ids


def test_verify_uk_right_to_work_stays_unconditional(monkeypatch):
    """Advisory, not blocking — and it is what 5 557 of 5 749 live GB rows carry. Making it
    conditional on picking GB would *narrow* what existing subscribers already receive, which
    is a regression dressed as a fix."""
    monkeypatch.setattr(store, "SHORTLIST_FLOOR", 1)
    ids, _ = _shortlist({"countries": ["CZ", "GB"]})
    assert "gb-verify" in ids


def test_the_gate_survives_the_widening_pass():
    """The retrieval floor drops the *recall* predicate when a shortlist comes back too small.
    The eligibility filter sits outside that branch, so widening must not hand back a role the
    subscriber cannot take. Asking for a category none of the fixtures carry forces the widened
    path."""
    ids, meta = _shortlist({"countries": ["CZ"], "role_categories": ["design"]})
    assert meta["widened"], "expected the floor to widen — the assertions below only mean " \
                            "something on the widened path"
    assert "us-remote" not in ids
    assert "us-blocked" not in ids
    assert "cz-eligible" in ids
