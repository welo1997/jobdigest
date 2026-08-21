"""`prune_gated_matches`, executed against a real Postgres.

The gap it closes: a hard gate added *after* a match was scored never removes it — the matcher
only ever filters new retrieval, so `matches` (and therefore `/matches`) keeps every job scored
before the gate that the gate now blocks. Measured on the owner's own feed 2026-08-21: 14 matches
failing the language axis (shipped that day) and 60 failing the reach gate (shipped 2026-08-17)
were still visible. `prune_gated_matches` re-applies the *same* `_hard_gate` object to a profile's
own matches so it self-heals on the next match.

This proves the pruning *mechanism* — the status/active/null polarity and the per-profile scope —
using the language axis as the discriminator, because it needs only one column (`postings.language`)
and an unconstrained profile isolates it (no countries/regions/work_modes ⇒ `location_predicate`
is "true"). The gates themselves are proven in `test_geo_sql`/`test_language`; here the question is
only whether the prune applies `_hard_gate` faithfully and touches nothing it shouldn't.

Same harness and reason as `test_hidden_sql.py`/`test_geo_sql.py`. Skipped when TEST_DATABASE_URL
is unset.
"""

from __future__ import annotations

import os

import pytest

from service import store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "gateprune-"
# posting_id -> (language, is_active). One English (passes), one Ukrainian (fails the language
# gate), one null-language (unknown always passes), plus a Ukrainian pair used for the
# status/active carve-outs below.
POSTINGS = {
    f"{PREFIX}en": ("en", True),
    f"{PREFIX}uk": ("uk", True),
    f"{PREFIX}null": (None, True),
    f"{PREFIX}uk-saved": ("uk", True),      # fails the gate but the user acted on it -> kept
    f"{PREFIX}uk-inactive": ("uk", False),  # fails the gate but inactive -> prune leaves it
}


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    with store.cursor(commit=True) as cur:
        for pid, (lang, active) in POSTINGS.items():
            cur.execute(
                "insert into postings (posting_id, source, url, title, company, region, "
                "  eligibility, seniority, work_type, role_category, dedup_key, language, "
                "  is_active) "
                "values (%s,'test',%s,'Data Analyst','Test','cz','eligible','mid',"
                "  'permanent','data_analysis',%s,%s,%s) "
                "on conflict (posting_id) do nothing",
                (pid, f"https://x.test/{pid}", pid, lang, active),
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


def _profile(email: str) -> dict:
    """A subscriber who reads only English (so the language gate is live), with a 'new' match on
    every fixture posting. No countries/work_modes, so nothing but the language gate applies.
    `uk-saved`'s match is promoted to a user-acted status the prune must never touch."""
    with store.cursor(commit=True) as cur:
        cur.execute("delete from profiles where email = %s", (email,))
        cur.execute(
            "insert into profiles (email, label, understood_languages, eligible_only) "
            "values (%s, 'gate prune test', %s, false) returning *",
            (email, ["en"]),
        )
        prof = dict(cur.fetchone())
    for pid in POSTINGS:
        store.upsert_match(prof["id"], pid, 8, "why")
    with store.cursor(commit=True) as cur:
        cur.execute("update matches set status = 'saved' where profile_id = %s "
                    "and posting_id = %s", (prof["id"], f"{PREFIX}uk-saved"))
    return prof


@pytest.fixture
def profile():
    return _profile(f"{PREFIX}a@example.test")


def _match_ids(profile_id: str) -> set[str]:
    """Every posting_id in `matches` for this profile, ignoring status and is_active — the prune
    is what we are testing, so we must read the table directly, not through `matched_jobs`."""
    with store.cursor() as cur:
        cur.execute("select posting_id from matches where profile_id = %s", (profile_id,))
        return {r["posting_id"] for r in cur.fetchall()}


def test_prune_drops_only_the_active_new_gate_failure(profile):
    """The Ukrainian, active, un-acted match is the one thing that should go — and exactly it."""
    assert _match_ids(profile["id"]) == set(POSTINGS)          # all five present to begin with
    dropped = store.prune_gated_matches(profile)
    assert dropped == 1
    assert _match_ids(profile["id"]) == set(POSTINGS) - {f"{PREFIX}uk"}


def test_prune_keeps_passing_and_unknown_language(profile):
    """English passes, a null language passes (unknown is kept) — neither is ever pruned."""
    store.prune_gated_matches(profile)
    remaining = _match_ids(profile["id"])
    assert f"{PREFIX}en" in remaining and f"{PREFIX}null" in remaining


def test_prune_never_touches_a_user_acted_match(profile):
    """A `saved`/`applied`/`dismissed` match is the user's own history — kept even when it now
    fails the gate, exactly as `prune_matches` keeps it."""
    store.prune_gated_matches(profile)
    assert f"{PREFIX}uk-saved" in _match_ids(profile["id"])


def test_prune_leaves_inactive_postings_to_retention(profile):
    """Inactive postings never display and are `prune_matches`' job — this prune ignores them
    even when they fail the gate, so it can't race the retention window."""
    store.prune_gated_matches(profile)
    assert f"{PREFIX}uk-inactive" in _match_ids(profile["id"])


def test_prune_is_idempotent(profile):
    """Once the residue is gone, a second run finds nothing to do."""
    assert store.prune_gated_matches(profile) == 1
    assert store.prune_gated_matches(profile) == 0


def test_prune_is_scoped_to_the_one_profile(profile):
    """`profile_id` in the WHERE clause is the scope — one subscriber's prune cannot reach
    another's identical gate-failing match."""
    other = _profile(f"{PREFIX}b@example.test")
    store.prune_gated_matches(profile)
    assert f"{PREFIX}uk" in _match_ids(other["id"])            # untouched


def test_a_profile_with_no_languages_prunes_nothing(profile):
    """Empty `understood_languages` is 'no filter' — with the only active gate switched off,
    the prune must be a no-op even on the Ukrainian rows (the keep-the-unknown polarity)."""
    with store.cursor(commit=True) as cur:
        cur.execute("update profiles set understood_languages = '{}' where id = %s",
                    (profile["id"],))
    prof = {**profile, "understood_languages": []}
    assert store.prune_gated_matches(prof) == 0
    assert _match_ids(profile["id"]) == set(POSTINGS)
