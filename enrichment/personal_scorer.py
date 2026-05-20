"""Score job postings for personal fit using Claude Sonnet."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional

import anthropic
import snowflake.connector
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

BATCH_SIZE = 50
MODEL = "claude-haiku-4-5-20251001"

PROMPT_TEMPLATE = """Score this job posting for an early-career data professional with strong fundamentals
in dbt, Snowflake, Python, and SQL — growing into mid-level. Open to stretch roles where the
stack matches even if the seniority is one step above current level.

Candidate profile:
- Core stack: dbt, Snowflake, Python, SQL (production experience)
- Working knowledge: Docker, Metabase, Google Sheets
- Strong interest in AI/ML tooling; learns quickly
- Based in Czech Republic; remote, on-site in CZ, hybrid in CZ are all fine
- ~1-2 years professional experience; credible growth into mid-level
- Open to full-time or freelance/contract
- Languages: English (fluent), Czech (native)
- Background in ecommerce (Footshop)
- Bonus alignment: trading firms or ecommerce companies

Hard dealbreakers (score 0 only if these apply):
- Requires native-level fluency in a language other than English/Czech
- Requires relocation outside the Czech Republic / EU (e.g., on-site only in US, Asia)

Soft factors (reduce the score, do NOT zero it):
- Posted as 5+ years required: -2 to -3 from base fit
- Stack mismatch with core stack (e.g., Java/Scala/AWS-only): -2 to -4
- Lead / Manager / Principal title with little hands-on: -2 to -3
- Senior individual contributor with strong stack overlap: -0 to -2 (still scorable)

Score on actual fit — stack overlap, remote-friendliness, growth potential — not just
seniority gap. A senior IC role with perfect stack overlap can score 6-8 if the candidate
would credibly grow into it. Reserve 9-10 for near-perfect mid-level matches.

Rate 0-10. Write 2 sentences explaining fit.
Respond ONLY in JSON: {{"personal_score": <int>, "summary": "<2 sentences>"}}

Title: {title}
Company: {company}
Skills: {skills_csv}
Description: {description}"""


def get_snowflake_connection() -> snowflake.connector.SnowflakeConnection:
    kwargs = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        role=os.environ.get("SNOWFLAKE_ROLE", "SYSADMIN"),
    )
    if key_path := os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
        kwargs["private_key_file"] = os.path.expanduser(key_path)
    else:
        kwargs["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    return snowflake.connector.connect(**kwargs)


def fetch_unscored_postings(
    conn: snowflake.connector.SnowflakeConnection, limit: Optional[int] = None
) -> list[dict]:
    """Get remote data-category postings that haven't been scored yet."""
    sql = """
        SELECT
            p.posting_id,
            p.title,
            p.company,
            p.description,
            array_to_string(s.skills, ', ') as skills_csv
        FROM raw.job_postings p
        LEFT JOIN raw.skill_tags s ON p.posting_id = s.posting_id
        LEFT JOIN raw.personal_scores ps ON p.posting_id = ps.posting_id
        JOIN raw_staging.stg_job_postings stg ON p.posting_id = stg.posting_id
        WHERE ps.posting_id IS NULL
          AND stg.role_category IN ('data_engineering', 'data_analysis', 'machine_learning')
          AND p.description IS NOT NULL
        ORDER BY p.loaded_at DESC
    """
    if limit:
        sql += f" LIMIT {limit}"

    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cur.close()
    return [
        {
            "posting_id": r[0],
            "title": r[1],
            "company": r[2],
            "description": r[3],
            "skills_csv": r[4] or "",
        }
        for r in rows
    ]


def score_posting(
    client: anthropic.Anthropic, title: str, company: str,
    description: str, skills_csv: str
) -> tuple[Optional[int], Optional[str], str]:
    """Call Sonnet to score personal fit. Returns (score, summary, raw_response)."""
    truncated_desc = (description or "")[:2000]
    prompt = PROMPT_TEMPLATE.format(
        title=title or "Unknown",
        company=company or "Unknown",
        skills_csv=skills_csv or "none extracted",
        description=truncated_desc,
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = response.content[0].text.strip()

    # Strip markdown fences if present
    cleaned = raw_text
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        result = json.loads(cleaned)
        score = int(result["personal_score"])
        summary = str(result["summary"])
        if not 0 <= score <= 10:
            raise ValueError(f"Score {score} out of range 0-10")
        return score, summary, raw_text
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.warning("Failed to parse scoring response: %s - %s", exc, raw_text[:200])
        return None, None, raw_text


def store_personal_scores(
    conn: snowflake.connector.SnowflakeConnection,
    results: list[dict],
) -> int:
    """MERGE personal scores into raw.personal_scores."""
    if not results:
        return 0

    cur = conn.cursor()
    inserted = 0
    for r in results:
        if r["personal_score"] is None:
            continue
        cur.execute(
            """
            MERGE INTO raw.personal_scores AS tgt
            USING (
                SELECT
                    %s AS posting_id,
                    %s AS personal_score,
                    %s AS summary
            ) AS src
            ON tgt.posting_id = src.posting_id
            WHEN NOT MATCHED THEN INSERT (posting_id, personal_score, summary)
                VALUES (src.posting_id, src.personal_score, src.summary)
            """,
            (r["posting_id"], r["personal_score"], r["summary"]),
        )
        result = cur.fetchone()
        if result and result[0] > 0:
            inserted += 1
    cur.close()
    return inserted


def run(limit: Optional[int] = None) -> None:
    """Main entry point: fetch unscored postings, score them, store results."""
    conn = get_snowflake_connection()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    postings = fetch_unscored_postings(conn, limit=limit)
    logger.info("Found %d unscored postings", len(postings))

    if not postings:
        conn.close()
        return

    batch_results: list[dict] = []

    for i, posting in enumerate(postings):
        try:
            score, summary, raw_response = score_posting(
                client,
                posting["title"],
                posting["company"],
                posting["description"],
                posting["skills_csv"],
            )
            batch_results.append({
                "posting_id": posting["posting_id"],
                "personal_score": score,
                "summary": summary,
            })
            logger.info(
                "[%d/%d] %s @ %s -> score=%s",
                i + 1, len(postings),
                (posting["title"] or "?")[:40],
                (posting["company"] or "?")[:20],
                score,
            )
        except Exception:
            logger.exception(
                "[%d/%d] Failed to score %s, skipping.",
                i + 1, len(postings), posting["posting_id"],
            )

        if len(batch_results) >= BATCH_SIZE:
            stored = store_personal_scores(conn, batch_results)
            logger.info("Stored batch: %d new scores", stored)
            batch_results = []

    if batch_results:
        stored = store_personal_scores(conn, batch_results)
        logger.info("Stored final batch: %d new scores", stored)

    conn.close()
    logger.info("Personal scoring complete.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    limit = None
    if len(sys.argv) > 2 and sys.argv[1] == "--limit":
        limit = int(sys.argv[2])
    run(limit=limit)
