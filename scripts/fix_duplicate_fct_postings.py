"""One-off: remove the duplicate posting_id row from marts_analysis.fct_postings.

The 2026-05-22 ingestion bug landed two byte-identical rows in fct_postings via
the incremental MERGE (a new posting_id with two source rows is inserted twice
by Snowflake's WHEN NOT MATCHED clause). The companion fix_duplicate_posting.py
cleaned raw.job_postings but the fct_postings copy was missed, and the row is
now stuck (loaded_at older than the incremental high-water mark).

Wrapped in a transaction; aborts if the two rows are not byte-identical.
"""

from __future__ import annotations

import os

import snowflake.connector
from dotenv import load_dotenv

load_dotenv()

DUPE_POSTING_ID = "14063f10ddfc445d3786d88fa7d54465"


def conn():
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "~/.snowflake/rsa_key.p8")
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key_file=os.path.expanduser(key_path),
        database=os.environ["SNOWFLAKE_DATABASE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        # No default: matches the pipeline connection functions -- the safe default
        # belongs in .env, not duplicated as a magic string here.
        role=os.environ["SNOWFLAKE_ROLE"],
    )


def main():
    c = conn()
    cur = c.cursor()

    cur.execute(
        "SELECT COUNT(*), COUNT(DISTINCT TO_JSON(OBJECT_CONSTRUCT(*))) "
        "FROM raw_marts_analysis.fct_postings WHERE posting_id = %s",
        (DUPE_POSTING_ID,),
    )
    total, distinct = cur.fetchone()
    print(f"Rows for posting_id={DUPE_POSTING_ID}: total={total} distinct={distinct}")

    if total <= 1:
        print("Nothing to do.")
        return

    if distinct != 1:
        raise SystemExit(
            f"Refusing to dedupe: rows differ ({distinct} distinct values). "
            "Inspect manually before deleting."
        )

    cur.execute("USE SCHEMA RAW_MARTS_ANALYSIS")
    cur.execute("BEGIN")
    try:
        cur.execute(
            "CREATE OR REPLACE TEMPORARY TABLE __fct_dedupe_keep AS "
            "SELECT * FROM raw_marts_analysis.fct_postings "
            "WHERE posting_id = %s "
            "QUALIFY ROW_NUMBER() OVER ("
            "  PARTITION BY posting_id ORDER BY loaded_at DESC"
            ") = 1",
            (DUPE_POSTING_ID,),
        )
        cur.execute(
            "DELETE FROM raw_marts_analysis.fct_postings WHERE posting_id = %s",
            (DUPE_POSTING_ID,),
        )
        cur.execute(
            "INSERT INTO raw_marts_analysis.fct_postings SELECT * FROM __fct_dedupe_keep"
        )
        cur.execute("COMMIT")
        print("Committed.")
    except Exception:
        cur.execute("ROLLBACK")
        raise

    cur.execute(
        "SELECT COUNT(*) FROM raw_marts_analysis.fct_postings WHERE posting_id = %s",
        (DUPE_POSTING_ID,),
    )
    print(f"After dedupe: {cur.fetchone()[0]} row(s).")

    cur.close()
    c.close()


if __name__ == "__main__":
    main()
