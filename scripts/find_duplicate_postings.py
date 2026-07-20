"""One-off: diagnose 2026-05-22 pipeline failure (duplicate posting_id + skill extraction silent regression)."""

from __future__ import annotations

import os

import snowflake.connector
from dotenv import load_dotenv

load_dotenv()


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

    print("=== Duplicate posting_ids in raw.job_postings ===")
    cur.execute("""
        SELECT posting_id, COUNT(*) AS n
        FROM raw.job_postings
        GROUP BY posting_id
        HAVING COUNT(*) > 1
    """)
    for row in cur.fetchall():
        print(row)

    print("\n=== Skill extraction health (last 30 days) ===")
    cur.execute("""
        SELECT
            COUNT(*) AS total_rows,
            COUNT_IF(ARRAY_SIZE(skills) = 0) AS empty_skills,
            COUNT_IF(ARRAY_SIZE(skills) > 0) AS nonempty_skills,
            MIN(extracted_at)::DATE AS first_extract,
            MAX(extracted_at)::DATE AS last_extract
        FROM raw.skill_tags
        WHERE extracted_at >= DATEADD('day', -30, CURRENT_DATE())
    """)
    print(cur.fetchone())

    print("\n=== Skill extraction by week (last 60 days) ===")
    cur.execute("""
        SELECT
            DATE_TRUNC('week', extracted_at)::DATE AS week,
            COUNT(*) AS rows,
            COUNT_IF(ARRAY_SIZE(skills) = 0) AS empty,
            COUNT_IF(ARRAY_SIZE(skills) > 0) AS nonempty
        FROM raw.skill_tags
        WHERE extracted_at >= DATEADD('day', -60, CURRENT_DATE())
        GROUP BY 1
        ORDER BY 1
    """)
    for row in cur.fetchall():
        print(row)

    print("\n=== Sample of recent raw_response values ===")
    cur.execute("""
        SELECT posting_id, LEFT(raw_response, 250) AS preview
        FROM raw.skill_tags
        WHERE extracted_at >= DATEADD('day', -7, CURRENT_DATE())
          AND ARRAY_SIZE(skills) = 0
        ORDER BY extracted_at DESC
        LIMIT 3
    """)
    for row in cur.fetchall():
        print(row)
        print("---")

    cur.close()
    c.close()


if __name__ == "__main__":
    main()
