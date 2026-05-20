"""One-off: wipe personal_scores for data-category postings and rescore under new prompt."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("SNOWFLAKE_PRIVATE_KEY_PATH", "~/.snowflake/rsa_key.p8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

from enrichment.personal_scorer import get_snowflake_connection, run  # noqa: E402


def wipe_existing_scores() -> int:
    """Delete personal_scores for any data-category posting so they get re-scored."""
    with get_snowflake_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM raw.personal_scores
            WHERE posting_id IN (
                SELECT p.posting_id
                FROM raw.job_postings p
                JOIN raw_staging.stg_job_postings stg ON p.posting_id = stg.posting_id
                WHERE stg.role_category IN ('data_engineering', 'data_analysis', 'machine_learning')
                  AND p.description IS NOT NULL
            )
            """
        )
        return cur.rowcount


if __name__ == "__main__":
    deleted = wipe_existing_scores()
    print(f"Deleted {deleted} existing personal_scores rows. Starting rescore...")
    run()
    print("Rescore complete.")
