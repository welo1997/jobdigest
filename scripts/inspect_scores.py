"""One-off: inspect current personal_scores state to decide rescore scope."""

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
        role=os.environ.get("SNOWFLAKE_ROLE", "SYSADMIN"),
    )


queries = {
    "Raw job_postings (last 30 days)": """
        SELECT COUNT(*) AS n,
               MIN(loaded_at) AS first_load,
               MAX(loaded_at) AS last_load
        FROM raw.job_postings
        WHERE loaded_at >= CURRENT_DATE - 30
    """,
    "Personal scores: total + distribution": """
        SELECT personal_score, COUNT(*) AS n
        FROM raw.personal_scores
        GROUP BY personal_score
        ORDER BY personal_score DESC
    """,
    "Data-category postings in scope vs scored": """
        SELECT
            COUNT(DISTINCT p.posting_id) AS data_role_postings,
            COUNT(DISTINCT ps.posting_id) AS already_scored,
            COUNT(DISTINCT CASE WHEN ps.posting_id IS NULL THEN p.posting_id END) AS unscored
        FROM raw.job_postings p
        JOIN raw_staging.stg_job_postings stg ON p.posting_id = stg.posting_id
        LEFT JOIN raw.personal_scores ps ON p.posting_id = ps.posting_id
        WHERE stg.role_category IN ('data_engineering', 'data_analysis', 'machine_learning')
          AND p.description IS NOT NULL
    """,
    "Top 10 current scores": """
        SELECT ps.personal_score, p.title, p.company, stg.role_category, stg.is_remote
        FROM raw.personal_scores ps
        JOIN raw.job_postings p ON ps.posting_id = p.posting_id
        JOIN raw_staging.stg_job_postings stg ON p.posting_id = stg.posting_id
        ORDER BY ps.personal_score DESC, ps.scored_at DESC
        LIMIT 10
    """,
}

with conn() as c, c.cursor() as cur:
    for label, sql in queries.items():
        print(f"\n=== {label} ===")
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            print(dict(zip(cols, row)))
