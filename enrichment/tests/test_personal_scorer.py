"""Tests for personal scorer with mocked Anthropic client."""

from unittest.mock import MagicMock, patch

import pytest

from enrichment.personal_scorer import run, score_posting, store_personal_scores


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


# --- run() must not report success when it scored nothing ---------------------
# Same guarantee as skill_extractor's: skipping one bad posting is fine, scoring
# zero of them is an outage and must fail the pipeline step.


def _run_with(postings, score_side_effect):
    with patch("enrichment.personal_scorer.get_snowflake_connection"), \
         patch("enrichment.personal_scorer.anthropic.Anthropic"), \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}), \
         patch("enrichment.personal_scorer.fetch_unscored_postings", return_value=postings), \
         patch("enrichment.personal_scorer.store_personal_scores", return_value=len(postings)), \
         patch("enrichment.personal_scorer.score_posting", side_effect=score_side_effect):
        run()


def _posting(pid):
    return {
        "posting_id": pid,
        "title": "Data Engineer",
        "company": "ACME",
        "description": "dbt and Snowflake",
        "skills_csv": "dbt, snowflake",
        "location": "Prague",
        "is_remote": True,
    }


def test_run_raises_when_every_posting_fails():
    postings = [_posting("a"), _posting("b")]

    with pytest.raises(RuntimeError, match="scored nothing"):
        _run_with(postings, score_side_effect=Exception("credit balance is too low"))


def test_run_succeeds_when_only_some_postings_fail():
    postings = [_posting("a"), _posting("b")]
    calls = {"n": 0}

    def mixed(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise Exception("unparseable description")
        return (8, "great fit", '{"score": 8}')

    _run_with(postings, score_side_effect=mixed)  # must not raise


def test_run_does_not_raise_when_there_is_nothing_to_do():
    _run_with([], score_side_effect=Exception("should never be called"))


def test_run_skips_cleanly_with_no_api_key():
    """No key = the claude.ai routine does this work; skip without touching Snowflake."""
    with patch("enrichment.personal_scorer.get_snowflake_connection") as conn, \
         patch.dict("os.environ", {}, clear=True):
        run()  # must not raise

    conn.assert_not_called()
