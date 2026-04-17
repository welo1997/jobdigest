"""Tests for the Snowflake loader (unit tests with mocked connection)."""

from datetime import date
from unittest.mock import MagicMock, patch, mock_open

from ingestion.base import JobPosting
from ingestion.load import load_postings


def _make_posting(**overrides) -> JobPosting:
    defaults = dict(
        posting_id="abc123",
        source="test",
        title="Test Job",
        company="TestCo",
        url="https://example.com/job/1",
        description="A test job.",
        location="Prague",
        country_code="CZ",
        remote_signal=True,
        salary_raw="50000 - 70000",
        currency="CZK",
        posted_at=date(2026, 4, 15),
    )
    defaults.update(overrides)
    return JobPosting(**defaults)


@patch("ingestion.load.get_connection")
def test_load_postings_executes_merge(mock_conn_fn):
    mock_cur = MagicMock()
    # MERGE returns (rows_inserted, rows_updated)
    mock_cur.fetchone.return_value = (2,)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_conn_fn.return_value = mock_conn

    postings = [_make_posting(), _make_posting(posting_id="def456")]
    inserted = load_postings(postings)

    assert inserted == 2
    assert mock_cur.execute.call_count >= 4  # stage, put, create table, copy, merge
    mock_cur.close.assert_called_once()
    mock_conn.close.assert_called_once()


@patch("ingestion.load.get_connection")
def test_load_empty_list(mock_conn_fn):
    result = load_postings([])
    assert result == 0
    mock_conn_fn.assert_not_called()
