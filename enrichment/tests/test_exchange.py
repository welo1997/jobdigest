"""Tests for the no-API enrichment exchange.

The enrichment parts are written by a claude.ai routine and travel through cloud storage,
so these tests exist to prove the import step does not trust them. They are written to fail
if a guard is removed -- each one breaks for exactly one reason.
"""

from unittest.mock import MagicMock, patch

import json

import pytest

from enrichment.exchange import (
    _clean_skills,
    _part_files,
    export_postings,
    import_enrichment,
)


def _write(tmp_path, payload, name="enrichment.json"):
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def _conn_where_known(known_ids):
    """A fake Snowflake connection whose job_postings contains exactly `known_ids`."""
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = [(i,) for i in known_ids]
    return conn


# --- the trust boundary ----------------------------------------------------------------


def test_invented_posting_id_is_dropped(tmp_path):
    """A posting_id the DB has never seen must not reach a MERGE."""
    path = _write(tmp_path, {"enrichment": [
        {"posting_id": "real", "skills": ["dbt"]},
        {"posting_id": "invented", "skills": ["sql"]},
    ]})
    conn = _conn_where_known(["real"])

    with patch("enrichment.exchange.get_snowflake_connection", return_value=conn), \
         patch("enrichment.exchange.store_skill_tags", return_value=1) as store:
        assert import_enrichment(path) == 1

    assert [r["posting_id"] for r in store.call_args[0][1]] == ["real"]


def test_malformed_record_does_not_abort_the_batch(tmp_path):
    """One bad record costs its own row, never the whole run."""
    path = _write(tmp_path, {"enrichment": [
        "not a dict",
        {"posting_id": "a", "skills": "dbt, sql"},     # skills must be a list
        {"posting_id": "b", "skills": ["dbt"]},
    ]})
    conn = _conn_where_known(["a", "b"])

    with patch("enrichment.exchange.get_snowflake_connection", return_value=conn), \
         patch("enrichment.exchange.store_skill_tags", return_value=1) as store:
        assert import_enrichment(path) == 1

    assert [r["posting_id"] for r in store.call_args[0][1]] == ["b"]


def test_skills_are_coerced_to_short_lowercase_strings():
    """int_skill_exploded flattens this array, so junk here becomes junk in a fact table."""
    assert _clean_skills(["DBT", " SQL ", "dbt"]) == ["dbt", "sql"]      # normalised, deduped
    assert _clean_skills([{"name": "dbt"}, None, True, ["x"]]) == []      # non-strings dropped
    assert _clean_skills("dbt") == []                                    # not a list at all
    assert len(_clean_skills([f"skill{i}" for i in range(50)])) == 30     # capped
    assert len(_clean_skills(["x" * 500])[0]) == 60                       # truncated


def test_files_that_store_nothing_raise(tmp_path):
    """Parsed files that yield no rows are an outage, not a quiet no-op."""
    path = _write(tmp_path, {"enrichment": [{"posting_id": "ghost", "skills": ["dbt"]}]})
    conn = _conn_where_known([])          # the DB knows none of them

    with patch("enrichment.exchange.get_snowflake_connection", return_value=conn), \
         patch("enrichment.exchange.store_skill_tags") as store:
        with pytest.raises(RuntimeError, match="none were usable"):
            import_enrichment(path)
    store.assert_not_called()


def test_duplicate_posting_id_stores_once(tmp_path):
    path = _write(tmp_path, {"enrichment": [
        {"posting_id": "a", "skills": ["dbt"]},
        {"posting_id": "a", "skills": ["sql"]},
    ]})
    conn = _conn_where_known(["a"])

    with patch("enrichment.exchange.get_snowflake_connection", return_value=conn), \
         patch("enrichment.exchange.store_skill_tags", return_value=1) as store:
        import_enrichment(path)

    stored = store.call_args[0][1]
    assert len(stored) == 1
    assert stored[0]["skills"] == ["dbt"]      # first answer wins


# --- the 15000-byte truncation, which actually happened --------------------------------


def test_truncated_part_is_skipped_not_fatal(tmp_path):
    """A severed part must cost only its own postings.

    On 2026-07-21 the routine's Drive write cut off at exactly 15000 bytes mid-string,
    producing invalid JSON. Letting that raise would abort the whole pipeline run --
    ingestion, dbt and alerts included -- over a transport glitch in a supplementary step.
    """
    good = _write(tmp_path, {"enrichment": [{"posting_id": "b", "skills": ["dbt"]}]},
                  name="enrichment-002.json")
    bad = tmp_path / "enrichment-001.json"
    bad.write_text('{"enrichment": [{"posting_id": "a", "skills": ["dbt", "sq',
                   encoding="utf-8")          # truncated mid-string, as observed
    conn = _conn_where_known(["a", "b"])

    with patch("enrichment.exchange.get_snowflake_connection", return_value=conn), \
         patch("enrichment.exchange.store_skill_tags", return_value=1) as store:
        assert import_enrichment(str(tmp_path)) == 1

    # The intact part still landed; the truncated one was skipped, not fatal.
    assert [r["posting_id"] for r in store.call_args[0][1]] == ["b"]


def test_all_parts_truncated_raises(tmp_path):
    """If every part is unreadable, nothing was stored — that IS the outage case."""
    for n in ("enrichment-001.json", "enrichment-002.json"):
        (tmp_path / n).write_text('{"enrichment": [{"posting_id": "a", "skil',
                                  encoding="utf-8")

    with patch("enrichment.exchange.get_snowflake_connection") as conn:
        assert import_enrichment(str(tmp_path)) == 0    # no usable posting_id at all
    conn.assert_not_called()


def test_parts_are_discovered_and_merged(tmp_path):
    """The routine answers in numbered parts; all of them must be read."""
    for i, pid in enumerate(["a", "b", "c"], start=1):
        _write(tmp_path, {"enrichment": [{"posting_id": pid, "skills": ["dbt"]}]},
               name=f"enrichment-{i:03d}.json")
    assert len(_part_files(str(tmp_path))) == 3

    conn = _conn_where_known(["a", "b", "c"])
    with patch("enrichment.exchange.get_snowflake_connection", return_value=conn), \
         patch("enrichment.exchange.store_skill_tags", return_value=3) as store:
        assert import_enrichment(str(tmp_path)) == 3

    assert sorted(r["posting_id"] for r in store.call_args[0][1]) == ["a", "b", "c"]


# --- the export ------------------------------------------------------------------------


def test_export_carries_no_credentials_and_only_public_fields(tmp_path):
    """The batch file lands in cloud storage; it must hold nothing but the job ad."""
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = [
        ("p1", "Data Engineer", "Acme", "Prague", True, "Build dbt models\nwith Snowflake"),
    ]
    out = tmp_path / "postings.json"

    with patch("enrichment.exchange.get_snowflake_connection", return_value=conn):
        assert export_postings(str(out), limit=10) == 1

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert set(payload["postings"][0]) == {
        "posting_id", "title", "company", "location", "is_remote", "description",
    }
    blob = out.read_text(encoding="utf-8").lower()
    for secret in ("snowflake_", "password", "private_key", "anthropic", "api_key"):
        assert secret not in blob


def test_export_asks_only_for_postings_without_skills():
    """Personal scoring was retired 2026-07-21; the query must not join personal_scores."""
    from enrichment.exchange import FETCH_SQL
    assert "skill_tags" in FETCH_SQL
    assert "personal_scores" not in FETCH_SQL
