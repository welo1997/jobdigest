"""Tests for skill extractor with mocked Anthropic client."""

from unittest.mock import MagicMock, patch

from enrichment.skill_extractor import extract_skills, store_skill_tags


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
