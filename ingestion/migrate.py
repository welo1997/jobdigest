"""Idempotent schema migrations for raw.job_postings.

Runs at the start of the pipeline (before ingestion) so schema changes self-apply
in CI without a manual step. Every statement must be safe to re-run.

(``setup_snowflake.py`` creates the tables for a fresh account but is gitignored /
local-only; this module carries the migrations that the daily pipeline depends on.)
"""

from __future__ import annotations

import logging
import sys

from ingestion.load import get_connection

logger = logging.getLogger(__name__)

MIGRATIONS = [
    # last_seen_at is bumped on every re-ingest (see load.py MERGE), so stale or
    # filled postings can be aged out downstream. Backfill existing rows from
    # loaded_at so nothing is null after the column is added.
    "ALTER TABLE RAW.JOB_POSTINGS ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP_NTZ",
    "UPDATE RAW.JOB_POSTINGS SET last_seen_at = loaded_at WHERE last_seen_at IS NULL",
]


def main() -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("USE SCHEMA RAW")
        for stmt in MIGRATIONS:
            logger.info(">> %s", stmt.strip().splitlines()[0][:80])
            cur.execute(stmt)
        logger.info("Migrations applied (%d statements).", len(MIGRATIONS))
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    main()
