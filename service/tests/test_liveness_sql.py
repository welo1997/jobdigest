"""The liveness sweep's data layer, executed against a real Postgres.

`test_liveness.py` proves the sweep's *decision* logic (only CLOSED deactivates, non-fatal) against
fakes. The three store functions it drives are where a `WHERE` typo would be invisible to that
file, so they get a real database here:

  - `matched_active_posting_ids` must return the postings a subscriber can see on `/matches` and
    only those: active, with a scored match. An inactive posting, or one nobody matched, must not
    appear — otherwise the sweep wastes probes, or worse, could re-touch a row it should leave.
  - `posting_targets` must drop inactive rows: a posting already deactivated needs no probe, and
    re-probing one could only wrongly revive it.
  - `deactivate_postings` must *deactivate, never delete* (the same contract as `deactivate_stale`,
    so analytics keep the row and `/matches`'s `is_active` filter simply stops showing it), be
    idempotent, and touch only the ids it was given.

Same harness and recipe as `test_hidden_sql.py`. Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "livetest-"
# 0,1,2,4 active; 3 inactive. Matches are attached to 0,1,3 below (2 and 4 stay unmatched).
POSTINGS = {f"{PREFIX}{i}": (i != 3) for i in range(5)}


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    with store.cursor(commit=True) as cur:
        for pid, active in POSTINGS.items():
            cur.execute(
                "insert into postings (posting_id, source, url, title, company, region, "
                "  eligibility, seniority, work_type, role_category, dedup_key, is_active) "
                "values (%s,'startupjobs',%s,'Data Engineer','Acme','cz','eligible','mid',"
                "  'permanent','data_engineering',%s,%s) "
                "on conflict (posting_id) do nothing",
                (pid, f"https://x.test/{pid}", pid, active),
            )
        cur.execute("delete from profiles where email = %s", (PREFIX + "sub",))
        cur.execute("insert into profiles (email, label) values (%s, 'liveness test') "
                    "returning id::text as id", (PREFIX + "sub",))
        pid = cur.fetchone()["id"]
    # Matches on 0 and 1 (active) and 3 (inactive). 2 and 4 stay unmatched.
    for i in (0, 1, 3):
        store.upsert_match(pid, f"{PREFIX}{i}", 8, "why")

    try:
        yield pid
    finally:
        with store.cursor(commit=True) as cur:
            cur.execute("delete from matches m using postings p where m.posting_id = p.posting_id "
                        "and p.posting_id like %s", (PREFIX + "%",))
            cur.execute("delete from postings where posting_id like %s", (PREFIX + "%",))
            cur.execute("delete from profiles where email like %s", (PREFIX + "%",))
        store._POOL = None
        if previous_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_dsn


def _mine(ids) -> set[str]:
    return {i for i in ids if i.startswith(PREFIX)}


def test_matched_active_posting_ids_are_active_and_matched_only():
    got = _mine(store.matched_active_posting_ids())
    # 0,1 are active+matched. 3 is matched but inactive. 2,4 are active but unmatched.
    assert got == {f"{PREFIX}0", f"{PREFIX}1"}


def test_posting_targets_drops_inactive_and_carries_the_url():
    rows = store.posting_targets([f"{PREFIX}0", f"{PREFIX}3", f"{PREFIX}4", "does-not-exist"])
    by_id = {r["posting_id"]: r for r in rows}
    assert set(by_id) == {f"{PREFIX}0", f"{PREFIX}4"}      # 3 is inactive, unknown id is dropped
    assert by_id[f"{PREFIX}0"]["url"] == f"https://x.test/{PREFIX}0"
    assert by_id[f"{PREFIX}0"]["source"] == "startupjobs"


def test_deactivate_postings_deactivates_without_deleting_and_is_scoped_and_idempotent():
    target = f"{PREFIX}4"
    other = f"{PREFIX}2"

    changed = store.deactivate_postings([target])
    assert changed == 1
    with store.cursor() as cur:
        cur.execute("select posting_id, is_active from postings where posting_id in (%s, %s)",
                    (target, other))
        state = {r["posting_id"]: r["is_active"] for r in cur.fetchall()}
    assert state[target] is False        # deactivated
    assert state[other] is True          # a sibling id was not touched
    assert target in state               # the row still exists — deactivated, never deleted

    # Idempotent: it is already inactive, so a second call changes nothing.
    assert store.deactivate_postings([target]) == 0


def test_deactivate_postings_ignores_empty_input():
    assert store.deactivate_postings([]) == 0
    assert store.deactivate_postings([None]) == 0
