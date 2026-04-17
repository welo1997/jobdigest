"""Load normalised job postings into Snowflake via staged batch MERGE."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Sequence

import snowflake.connector
from dotenv import load_dotenv

from ingestion.base import JobPosting

load_dotenv()
logger = logging.getLogger(__name__)

COLUMNS = [
    "posting_id",
    "source",
    "title",
    "company",
    "url",
    "description",
    "location",
    "country_code",
    "remote_signal",
    "salary_raw",
    "currency",
    "posted_at",
]

BATCH_MERGE_SQL = f"""
MERGE INTO RAW.JOB_POSTINGS AS tgt
USING __staging_postings AS src
ON tgt.posting_id = src.posting_id
WHEN NOT MATCHED THEN INSERT (
    {', '.join(COLUMNS)}
) VALUES (
    src.posting_id, src.source, src.title, src.company, src.url,
    src.description, src.location, src.country_code, src.remote_signal,
    src.salary_raw, src.currency, src.posted_at
)
"""


def get_connection() -> snowflake.connector.SnowflakeConnection:
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        role=os.environ.get("SNOWFLAKE_ROLE", "SYSADMIN"),
    )


def _posting_to_row(p: JobPosting) -> dict:
    return {
        "posting_id": p.posting_id,
        "source": p.source,
        "title": p.title,
        "company": p.company,
        "url": p.url,
        "description": p.description,
        "location": p.location,
        "country_code": p.country_code,
        "remote_signal": p.remote_signal,
        "salary_raw": p.salary_raw,
        "currency": p.currency,
        "posted_at": p.posted_at.isoformat() if p.posted_at else None,
    }


def load_postings(postings: Sequence[JobPosting]) -> int:
    """Batch MERGE upsert postings into raw.job_postings. Returns count of new rows."""
    if not postings:
        logger.info("No postings to load.")
        return 0

    conn = get_connection()
    cur = conn.cursor()
    try:
        # Write postings to a temp NDJSON file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ndjson", delete=False, encoding="utf-8"
        ) as f:
            tmp_path = f.name
            for p in postings:
                json.dump(_posting_to_row(p), f, ensure_ascii=False)
                f.write("\n")

        # Ensure schema context is set for temp objects
        cur.execute("USE SCHEMA RAW")

        # Create temp stage & staging table, PUT file, COPY INTO, MERGE
        cur.execute("CREATE TEMPORARY STAGE IF NOT EXISTS __posting_stage")
        cur.execute(
            f"PUT 'file://{tmp_path.replace(os.sep, '/')}' @__posting_stage "
            f"AUTO_COMPRESS=TRUE OVERWRITE=TRUE"
        )
        cur.execute(f"""
            CREATE TEMPORARY TABLE __staging_postings (
                posting_id   VARCHAR,
                source       VARCHAR,
                title        VARCHAR,
                company      VARCHAR,
                url          VARCHAR,
                description  TEXT,
                location     VARCHAR,
                country_code VARCHAR(2),
                remote_signal BOOLEAN,
                salary_raw   VARCHAR,
                currency     VARCHAR,
                posted_at    DATE
            )
        """)
        cur.execute("""
            COPY INTO __staging_postings
            FROM @__posting_stage
            FILE_FORMAT = (TYPE = 'JSON' STRIP_OUTER_ARRAY = FALSE)
            MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
        """)

        cur.execute(BATCH_MERGE_SQL)
        result = cur.fetchone()
        inserted = result[0] if result else 0

        logger.info(
            "Loaded %d postings (%d new, %d existing).",
            len(postings),
            inserted,
            len(postings) - inserted,
        )
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        cur.close()
        conn.close()

    return inserted
