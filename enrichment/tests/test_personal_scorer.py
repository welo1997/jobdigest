"""Tests for personal scorer with mocked Anthropic client."""

from unittest.mock import MagicMock

from enrichment.personal_scorer import score_posting, store_personal_scores


def _mock_anthropic_response(text: str) -> MagicMock:
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def test_score_posting_valid_json():
    client = MagicMock()
    client.messages.create.return_value = _mock_anthropic_response(
        '{"personal_score": 8, "summary": "Great fit. Uses dbt and Snowflake."}'
    )

    score, summary, raw = score_posting(
        client, "Data Engineer", "Acme", "Build pipelines with dbt", "dbt, snowflake"
    )

    assert score == 8
    assert "Great fit" in summary
    client.messages.create.assert_called_once()


def test_score_posting_with_markdown_fences():
    client = MagicMock()
    client.messages.create.return_value = _mock_anthropic_response(
        '```json\n{"personal_score": 6, "summary": "Decent match."}\n```'
    )

    score, summary, _ = score_posting(
        client, "Analyst", "Corp", "SQL needed", "sql"
    )

    assert score == 6
    assert summary == "Decent match."


def test_score_posting_dealbreaker_zero():
    client = MagicMock()
    client.messages.create.return_value = _mock_anthropic_response(
        '{"personal_score": 0, "summary": "Requires relocation to NYC. Not remote."}'
    )

    score, summary, _ = score_posting(
        client, "Data Engineer", "BigCo", "Must relocate to NYC", "python"
    )

    assert score == 0
    assert "relocation" in summary.lower()


def test_score_posting_invalid_json():
    client = MagicMock()
    client.messages.create.return_value = _mock_anthropic_response(
        "This job is a 7 out of 10 for you."
    )

    score, summary, raw = score_posting(
        client, "Analyst", "Corp", "desc", "sql"
    )

    assert score is None
    assert summary is None


def test_score_posting_out_of_range():
    client = MagicMock()
    client.messages.create.return_value = _mock_anthropic_response(
        '{"personal_score": 15, "summary": "Amazing fit."}'
    )

    score, summary, _ = score_posting(
        client, "Engineer", "Corp", "desc", "python"
    )

    assert score is None


def test_store_personal_scores_calls_merge():
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = (1,)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    results = [
        {"posting_id": "abc", "personal_score": 8, "summary": "Good fit."},
        {"posting_id": "def", "personal_score": 3, "summary": "Weak fit."},
    ]

    inserted = store_personal_scores(mock_conn, results)
    assert inserted == 2
    assert mock_cur.execute.call_count == 2


def test_store_personal_scores_skips_none():
    mock_cur = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    results = [
        {"posting_id": "abc", "personal_score": None, "summary": None},
    ]

    inserted = store_personal_scores(mock_conn, results)
    assert inserted == 0
    mock_cur.execute.assert_not_called()
