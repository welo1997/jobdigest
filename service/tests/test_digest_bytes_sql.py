"""The export must record what it actually cost, and recording it must never cost anything.

`digest_runs.shortlist_bytes` (migration 016) is how production measures its own matcher
budget. Without it the per-subscriber payload — the number every item on
`notes/scaling/PLAN.md` is sized against — is only knowable by someone with a shell on the
box running `scripts/scaling_budget.py` by hand, which means in practice it is knowable once
and then goes stale. With it, every export writes the figure for every subscriber, and the
ladder can be re-judged against real numbers instead of a projection.

Three properties, and each is a way this could be quietly wrong:

  - **It measures what ships, not an estimate of it.** The figure is the serialised size of
    the exact profile entry written into `shortlists.json`. An approximation would drift from
    the file the routine actually reads, and nothing downstream could tell.
  - **It is written before the `continue`, like every other field on this row.** The
    subscriber the export found nothing for is the one worth alerting on, so that path must
    not be the one that leaves no trace — migration 011's whole rationale.
  - **A missing column costs the telemetry and nothing else.** Migrations here are manual
    `.sql` files that no deploy step applies, so a deploy that lands before its migration is a
    real ordering people hit. `record_digest_run` already swallows its own errors, but a
    failed INSERT takes the *whole row* with it — including `shortlist_n`, which
    `service/watchdog.py` reads to tell RETRIEVAL failures from MATCHER ones. So the write
    degrades per-column: the new field drops out and the row still lands.

Same harness and reason as `test_geo_sql.py` — see its docstring for the throwaway database
recipe. Skipped when TEST_DATABASE_URL is unset; CI fails if these go back to skipping.
"""

from __future__ import annotations

import json
import os

import pytest

from service import store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "bytestest-"


@pytest.fixture(autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None
    _cleanup()
    try:
        yield
    finally:
        _cleanup()
        store._POOL = None
        if previous_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_dsn


def _cleanup() -> None:
    with store.cursor(commit=True) as cur:
        cur.execute("delete from profiles where email like %s", (PREFIX + "%",))


def _profile(name: str) -> str:
    with store.cursor(commit=True) as cur:
        cur.execute(
            "insert into profiles (email, status, confirmed_at) "
            "values (%s, 'active', now()) returning id",
            (PREFIX + name + "@example.com",),
        )
        return str(cur.fetchone()["id"])


def _row(profile_id: str) -> dict:
    with store.cursor() as cur:
        cur.execute(
            "select * from digest_runs where profile_id = %s and day = current_date",
            (profile_id,),
        )
        return cur.fetchone()


# --- the column exists and carries the figure ---------------------------------------------


def test_the_column_exists_and_is_nullable():
    """Nullable on purpose: a row written before migration 016 has no answer, and 0 would be
    a lie — it would read as "this subscriber cost nothing" rather than "not measured"."""
    with store.cursor() as cur:
        cur.execute(
            "select is_nullable, data_type from information_schema.columns "
            "where table_name = 'digest_runs' and column_name = 'shortlist_bytes'"
        )
        col = cur.fetchone()
    assert col is not None, "migration 016 has not been applied to the test database"
    assert col["is_nullable"] == "YES"


def test_shortlist_bytes_round_trips():
    pid = _profile("roundtrip")
    store.record_digest_run(pid, shortlist_n=120, shortlist_bytes=77_000)
    row = _row(pid)
    assert row["shortlist_n"] == 120
    assert row["shortlist_bytes"] == 77_000


def test_it_merges_like_every_other_field():
    """Three stages write this row; a later write must not blank an earlier one."""
    pid = _profile("merge")
    store.record_digest_run(pid, shortlist_n=120, shortlist_bytes=77_000)
    store.record_digest_run(pid, picks_n=8)
    row = _row(pid)
    assert (row["shortlist_n"], row["shortlist_bytes"], row["picks_n"]) == (120, 77_000, 8)


def test_an_empty_shortlist_still_records_zero_bytes():
    """The subscriber the export found nothing for is the one worth alerting on."""
    pid = _profile("empty")
    store.record_digest_run(pid, shortlist_n=0, widened=True, shortlist_bytes=0)
    row = _row(pid)
    assert row is not None and row["shortlist_n"] == 0 and row["shortlist_bytes"] == 0


# --- the figure is the real serialised payload --------------------------------------------


def test_the_export_records_the_size_of_what_it_actually_writes(monkeypatch, tmp_path):
    """End to end: the recorded figure equals the entry's size in the file on disk.

    This is the property that makes the number worth having. An estimate, or a measurement
    taken before `already_sent_ids` filtering, would drift from the file the routine reads.
    """
    from service import matcher

    pid = _profile("endtoend")
    candidates = [
        {
            "posting_id": f"{i:032x}", "title": "Datový inženýr", "company": "Přiklad s.r.o.",
            "location": "Praha", "region": "cz", "city": "cz:prague", "remote_signal": False,
            "work_mode": "hybrid", "education_min": None, "seniority": "senior",
            "work_type": "fulltime", "is_part_time": False, "salary_raw": "80 000 Kč",
            "description": "x" * matcher.DESC_CHARS,
        }
        for i in range(5)
    ]
    with store.cursor() as cur:
        cur.execute("select * from profiles where id = %s", (pid,))
        profile = dict(cur.fetchone())

    monkeypatch.setattr(matcher, "_profiles_for", lambda *a, **k: [profile])
    monkeypatch.setattr(store, "query_shortlist_meta",
                        lambda *a, **k: (candidates, {"n": len(candidates), "widened": False}))
    monkeypatch.setattr(store, "already_sent_ids", lambda *a, **k: set())
    monkeypatch.setattr(matcher, "_record_shadow", lambda *a, **k: None)

    out = tmp_path / "shortlists.json"
    matcher.export_shortlists(str(out))

    written = json.loads(out.read_text(encoding="utf-8"))["profiles"][0]
    on_disk = len(json.dumps(written, ensure_ascii=False).encode("utf-8"))
    assert _row(pid)["shortlist_bytes"] == on_disk


def test_the_figure_reflects_the_shortlist_after_already_sent_filtering(monkeypatch, tmp_path):
    """Measured after `already_sent_ids`, not before.

    The candidate window is filtered by what the subscriber has already been emailed, and that
    filter bites hardest on the longest-tenured subscribers — on 2026-08-07 it took 21 of 30
    slots for one of them. A figure taken before the filter would over-report exactly the
    people whose real cost is lowest, and the error would grow with tenure, so the budget
    would drift in the most misleading direction available.
    """
    from service import matcher

    pid = _profile("filtered")
    candidates = [
        {
            "posting_id": f"{i:032x}", "title": "Data Engineer", "company": "Example",
            "location": "Praha", "region": "cz", "city": "cz:prague", "remote_signal": False,
            "work_mode": None, "education_min": None, "seniority": "senior",
            "work_type": "fulltime", "is_part_time": False, "salary_raw": None,
            "description": "x" * matcher.DESC_CHARS,
        }
        for i in range(6)
    ]
    with store.cursor() as cur:
        cur.execute("select * from profiles where id = %s", (pid,))
        profile = dict(cur.fetchone())

    already = {candidates[0]["posting_id"], candidates[1]["posting_id"]}
    monkeypatch.setattr(matcher, "_profiles_for", lambda *a, **k: [profile])
    monkeypatch.setattr(store, "query_shortlist_meta",
                        lambda *a, **k: (list(candidates), {"n": 6, "widened": False}))
    monkeypatch.setattr(store, "already_sent_ids", lambda *a, **k: already)
    monkeypatch.setattr(matcher, "_record_shadow", lambda *a, **k: None)

    out = tmp_path / "shortlists.json"
    matcher.export_shortlists(str(out))

    written = json.loads(out.read_text(encoding="utf-8"))["profiles"][0]
    assert len(written["candidates"]) == 4, "the filter did not apply — fixture is wrong"
    row = _row(pid)
    assert row["shortlist_n"] == 4
    assert row["shortlist_bytes"] == len(json.dumps(written, ensure_ascii=False).encode("utf-8"))


# --- a missing column costs the telemetry and nothing else --------------------------------


def test_a_missing_column_does_not_take_the_rest_of_the_row_with_it():
    """Deploy-before-migrate must not blind the watchdog.

    `record_digest_run` never raises, so the digest is safe either way. But without the
    per-column fallback the whole INSERT fails and `shortlist_n` is never written — and
    `watchdog.diagnose` reads that column to tell a retrieval failure (ours) from the matcher
    correctly rejecting a fair shortlist (not a bug). Losing it turns a precise alert into
    three days of silence followed by a starvation report nobody can act on.
    """
    pid = _profile("degrade")
    with store.cursor(commit=True) as cur:
        cur.execute("alter table digest_runs drop column shortlist_bytes")
    try:
        store.record_digest_run(pid, shortlist_n=120, widened=True, shortlist_bytes=77_000)
        row = _row(pid)
        assert row is not None, "the row was lost entirely — the fallback did not fire"
        assert row["shortlist_n"] == 120 and row["widened"] is True
    finally:
        with store.cursor(commit=True) as cur:
            cur.execute("alter table digest_runs add column shortlist_bytes bigint")
