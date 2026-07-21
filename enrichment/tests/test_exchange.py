"""Tests for the no-API enrichment exchange.

`enrichment.json` is written by a claude.ai routine and travels through cloud storage, so
these tests exist to prove the import step does not trust it. They are written to fail if
a guard is removed -- each one breaks for exactly one reason.
"""

from unittest.mock import MagicMock, patch

import json

import pytest

from enrichment.exchange import (
    _clean_score,
    _clean_skills,
    export_postings,
    import_enrichment,
)


def _write(tmp_path, payload):
    p = tmp_path / "enrichment.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def _conn_where_known(known_ids):
    """A fake Snowflake connection whose job_postings contains exactly `known_ids`."""
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchall.return_value = [(i,) for i in known_ids]
    return conn


# --- the trust boundary ----------------------------------------------------------------


def test_invented_posting_id_is_dropped(tmp_path):
    """A posting_id the DB has never seen must not reach a MERGE."""
    path = _write(tmp_path, {"enrichment": [
        {"posting_id": "real", "skills": ["dbt"], "personal_score": 7},
        {"posting_id": "invented", "skills": ["sql"], "personal_score": 9},
    ]})
    conn = _conn_where_known(["real"])

    with patch("enrichment.exchange.get_snowflake_connection", return_value=conn), \
         patch("enrichment.exchange.store_enrichment", return_value=(1, 1)) as store:
        assert import_enrichment(path) == 1

    stored = store.call_args[0][1]
    assert [r["posting_id"] for r in stored] == ["real"]


def test_score_is_clamped_not_trusted(tmp_path):
    """A score outside 0-10 is clamped, so it cannot skew the personal mart."""
    path = _write(tmp_path, {"enrichment": [
        {"posting_id": "a", "personal_score": 99},
        {"posting_id": "b", "personal_score": -5},
    ]})
    conn = _conn_where_known(["a", "b"])

    with patch("enrichment.exchange.get_snowflake_connection", return_value=conn), \
         patch("enrichment.exchange.store_enrichment", return_value=(0, 2)) as store:
        import_enrichment(path)

    by_id = {r["posting_id"]: r for r in store.call_args[0][1]}
    assert by_id["a"]["personal_score"] == 10
    assert by_id["b"]["personal_score"] == 0


def test_malformed_record_does_not_abort_the_batch(tmp_path):
    """One bad record costs its own row, never the whole run."""
    path = _write(tmp_path, {"enrichment": [
        "not a dict",
        {"posting_id": "a", "skills": "dbt, sql", "personal_score": "seven"},
        {"posting_id": "b", "skills": ["dbt"], "personal_score": 6},
    ]})
    conn = _conn_where_known(["a", "b"])

    with patch("enrichment.exchange.get_snowflake_connection", return_value=conn), \
         patch("enrichment.exchange.store_enrichment", return_value=(1, 1)) as store:
        assert import_enrichment(path) == 1

    stored = store.call_args[0][1]
    # "a" contributed nothing usable: a bare string is not a skills array and "seven" is
    # not a score, so it is skipped rather than stored as garbage.
    assert [r["posting_id"] for r in stored] == ["b"]


def test_skills_are_coerced_to_short_lowercase_strings():
    """int_skill_exploded flattens this array, so junk here becomes junk in a fact table."""
    assert _clean_skills(["DBT", " SQL ", "dbt"]) == ["dbt", "sql"]      # normalised, deduped
    assert _clean_skills([{"name": "dbt"}, None, True, ["x"]]) == []      # non-strings dropped
    assert _clean_skills("dbt") == []                                    # not a list at all
    assert len(_clean_skills([f"skill{i}" for i in range(50)])) == 30     # capped
    assert len(_clean_skills(["x" * 500])[0]) == 60                       # truncated


def test_zero_score_is_kept_but_unusable_score_is_none():
    """0 is a real verdict; None means retry later. Collapsing them loses that."""
    assert _clean_score(0) == 0
    assert _clean_score(None) is None
    assert _clean_score("seven") is None
    assert _clean_score(True) is None      # bool is an int in Python; not a score


def test_a_file_that_stores_nothing_raises(tmp_path):
    """A parsed file that yields no rows is an outage, not a quiet no-op.

    Mirrors the all-postings-failed guard in skill_extractor: on 2026-07-20 a totally
    failed enrichment step reported success and the pipeline broke two steps later.
    """
    path = _write(tmp_path, {"enrichment": [
        {"posting_id": "ghost", "skills": ["dbt"], "personal_score": 7},
    ]})
    conn = _conn_where_known([])          # the DB knows none of them

    with patch("enrichment.exchange.get_snowflake_connection", return_value=conn), \
         patch("enrichment.exchange.store_enrichment") as store:
        with pytest.raises(RuntimeError, match="none were usable"):
            import_enrichment(path)
    store.assert_not_called()


def test_wrong_shape_returns_zero_without_touching_the_db(tmp_path):
    path = _write(tmp_path, {"enrichment": {"posting_id": "a"}})   # dict, not list
    with patch("enrichment.exchange.get_snowflake_connection") as conn:
        assert import_enrichment(path) == 0
    conn.assert_not_called()


def test_duplicate_posting_id_stores_once(tmp_path):
    path = _write(tmp_path, {"enrichment": [
        {"posting_id": "a", "skills": ["dbt"], "personal_score": 8},
        {"posting_id": "a", "skills": ["sql"], "personal_score": 2},
    ]})
    conn = _conn_where_known(["a"])

    with patch("enrichment.exchange.get_snowflake_connection", return_value=conn), \
         patch("enrichment.exchange.store_enrichment", return_value=(1, 1)) as store:
        import_enrichment(path)

    stored = store.call_args[0][1]
    assert len(stored) == 1
    assert stored[0]["skills"] == ["dbt"]      # first answer wins


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
