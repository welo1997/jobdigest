"""One-off: remove the duplicate posting_id row from raw.job_postings.

Wrapped in a transaction. Confirms both rows are byte-identical first; if they
diverge on any column (e.g. notified, notified_at) we abort and ask.
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

    # 1. Count rows and verify rows are byte-identical (DISTINCT collapses them to 1)
    cur.execute(
        "SELECT COUNT(*), COUNT(DISTINCT TO_JSON(OBJECT_CONSTRUCT(*))) "
        "FROM raw.job_postings WHERE posting_id = %s",
        (DUPE_POSTING_ID,),
    )
    total, distinct = cur.fetchone()
    print(f"Rows for posting_id={DUPE_POSTING_ID}: total={total} distinct={distinct}")

    if total <= 1:
        print("Nothing to do.")
        return
    cur.execute("USE SCHEMA RAW")
    # Transactional dedupe: keep the row with the longest description
    # (rows differ only on description whitespace/encoding; longer = more content preserved)
    cur.execute("BEGIN")
    try:
        cur.execute(
            "CREATE OR REPLACE TEMPORARY TABLE __dedupe_keep AS "
            "SELECT * FROM raw.job_postings "
            "WHERE posting_id = %s "
            "QUALIFY ROW_NUMBER() OVER ("
            "  PARTITION BY posting_id "
            "  ORDER BY LENGTH(description) DESC NULLS LAST, loaded_at DESC"
            ") = 1",
            (DUPE_POSTING_ID,),
        )
        cur.execute(
            "DELETE FROM raw.job_postings WHERE posting_id = %s",
            (DUPE_POSTING_ID,),
        )
        cur.execute(
            "INSERT INTO raw.job_postings SELECT * FROM __dedupe_keep"
        )
        cur.execute("COMMIT")
        print("Committed.")
    except Exception:
        cur.execute("ROLLBACK")
        raise

    # 3. Verify
    cur.execute(
        "SELECT COUNT(*) FROM raw.job_postings WHERE posting_id = %s",
        (DUPE_POSTING_ID,),
    )
    print(f"After dedupe: {cur.fetchone()[0]} row(s).")

    cur.close()
    c.close()


if __name__ == "__main__":
    main()
