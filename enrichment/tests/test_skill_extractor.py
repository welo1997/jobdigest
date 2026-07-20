"""Tests for skill extractor with mocked Anthropic client."""

from unittest.mock import MagicMock, patch

import pytest

from enrichment.skill_extractor import extract_skills, run, store_skill_tags


def _mock_anthropic_response(text: str) -> MagicMock:
    """Create a mock Anthropic response with the given text."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def test_extract_skills_valid_json():
    client = MagicMock()
    client.messages.create.return_value = _mock_anthropic_response(
        '["python", "dbt", "snowflake", "airflow"]'
    )

    skills, raw = extract_skills(client, "Data Engineer", "Build pipelines with dbt and Snowflake")

    assert skills == ["python", "dbt", "snowflake", "airflow"]
    assert raw == '["python", "dbt", "snowflake", "airflow"]'
    client.messages.create.assert_called_once()


def test_extract_skills_empty_array():
    client = MagicMock()
    client.messages.create.return_value = _mock_anthropic_response("[]")

    skills, raw = extract_skills(client, "Office Manager", "Manage office supplies")

    assert skills == []
    assert raw == "[]"


def test_extract_skills_invalid_json_returns_empty():
    client = MagicMock()
    client.messages.create.return_value = _mock_anthropic_response(
        "Here are the skills: python, dbt"
    )

    skills, raw = extract_skills(client, "Data Engineer", "Some description")

    assert skills == []
    assert "Here are the skills" in raw


def test_extract_skills_normalises_to_lowercase():
    client = MagicMock()
    client.messages.create.return_value = _mock_anthropic_response(
        '["Python", "DBT", "Snowflake", "AIRFLOW"]'
    )

    skills, _ = extract_skills(client, "Data Engineer", "desc")

    assert skills == ["python", "dbt", "snowflake", "airflow"]


def test_extract_skills_truncates_to_30():
    client = MagicMock()
    import json
    long_list = [f"skill_{i}" for i in range(50)]
    client.messages.create.return_value = _mock_anthropic_response(
        json.dumps(long_list)
    )

    skills, _ = extract_skills(client, "Title", "desc")

    assert len(skills) == 30


def test_extract_skills_strips_markdown_fences():
    client = MagicMock()
    client.messages.create.return_value = _mock_anthropic_response(
        '```json\n["python", "dbt", "snowflake"]\n```'
    )

    skills, _ = extract_skills(client, "Data Engineer", "Build pipelines")

    assert skills == ["python", "dbt", "snowflake"]


def test_store_skill_tags_calls_merge():
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = (1,)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    results = [
        {"posting_id": "abc", "skills": ["python", "dbt"], "raw_response": '["python","dbt"]'},
        {"posting_id": "def", "skills": ["react"], "raw_response": '["react"]'},
    ]

    inserted = store_skill_tags(mock_conn, results)

    assert inserted == 2
    assert mock_cur.execute.call_count == 2


def test_store_skill_tags_empty_list():
    mock_conn = MagicMock()
    inserted = store_skill_tags(mock_conn, [])
    assert inserted == 0


# --- run() must not report success when it enriched nothing -------------------
#
# On 2026-07-20 this step exited 0 after failing all 14578 postings (exhausted Anthropic
# credit balance -> every call 400'd). The per-posting skip is deliberate; reporting
# success for a run that stored zero rows is not.


def _run_with(postings, extract_side_effect):
    """Call run() with the DB, API client and extraction all mocked out."""
    with patch("enrichment.skill_extractor.get_snowflake_connection"), \
         patch("enrichment.skill_extractor.anthropic.Anthropic"), \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}), \
         patch("enrichment.skill_extractor.fetch_untagged_postings", return_value=postings), \
         patch("enrichment.skill_extractor.store_skill_tags", return_value=len(postings)), \
         patch("enrichment.skill_extractor.extract_skills", side_effect=extract_side_effect):
        run()


def _posting(pid):
    return {"posting_id": pid, "title": "Data Engineer", "description": "dbt and Snowflake"}


def test_run_raises_when_every_posting_fails():
    postings = [_posting("a"), _posting("b"), _posting("c")]

    with pytest.raises(RuntimeError, match="enriched nothing"):
        _run_with(postings, extract_side_effect=Exception("credit balance is too low"))


def test_run_succeeds_when_only_some_postings_fail():
    postings = [_posting("a"), _posting("b")]

    # First succeeds, second raises -- a partial run is still a real run.
    calls = {"n": 0}

    def mixed(client, title, description):
        calls["n"] += 1
        if calls["n"] == 2:
            raise Exception("unparseable description")
        return (["dbt"], '["dbt"]')

    _run_with(postings, extract_side_effect=mixed)  # must not raise


def test_run_does_not_raise_when_there_is_nothing_to_do():
    # No untagged postings is a no-op, not an outage.
    _run_with([], extract_side_effect=Exception("should never be called"))


def test_run_skips_cleanly_with_no_api_key():
    """No key = enrichment runs via the claude.ai routine, not a failed run.

    Must not raise and must not open a Snowflake connection -- the pipeline step has to
    stay green, and cheaply, when the API path is deliberately switched off.
    """
    with patch("enrichment.skill_extractor.get_snowflake_connection") as conn, \
         patch.dict("os.environ", {}, clear=True):
        run()  # must not raise

    conn.assert_not_called()
