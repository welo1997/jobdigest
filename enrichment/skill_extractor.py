"""Extract skills from job postings using Claude Haiku."""

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

PROMPT_TEMPLATE = """Extract all technical skills, tools, and technologies from this job posting.
Return ONLY a JSON array of lowercase strings. Normalise variants
(e.g. "PostgreSQL" -> "postgresql", "React.js" -> "react"). Max 30 items.
If none found, return [].

Title: {title}
Description: {description}"""


def get_snowflake_connection() -> snowflake.connector.SnowflakeConnection:
    kwargs = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        # No default: SYSADMIN silently used to be the fallback here, contradicting
        # CLAUDE.md's own "no SYSADMIN" rule the moment this env var was unset. The safe
        # default belongs in exactly one place (the CI workflow / .env), not duplicated as
        # a magic string in every connection function that might forget to update it.
        role=os.environ["SNOWFLAKE_ROLE"],
    )
    # The account requires MFA for password auth (account-wide policy), which a headless
    # credential can never satisfy -- keypair is the only viable CI/service auth here.
    # Defaults to the project-standard local key path (matches dbt/profiles.yml's
    # dev_local target) so this is usable interactively without CI's explicit env var.
    if key_path := os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "~/.snowflake/rsa_key.p8"):
        kwargs["private_key_file"] = os.path.expanduser(key_path)
    else:
        kwargs["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    return snowflake.connector.connect(**kwargs)


def fetch_untagged_postings(
    conn: snowflake.connector.SnowflakeConnection, limit: Optional[int] = None
) -> list[dict]:
    """Get postings that don't have skill_tags yet."""
    sql = """
        SELECT p.posting_id, p.title, p.description
        FROM raw.job_postings p
        LEFT JOIN raw.skill_tags s ON p.posting_id = s.posting_id
        WHERE s.posting_id IS NULL
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
        {"posting_id": r[0], "title": r[1], "description": r[2]}
        for r in rows
    ]


def extract_skills(
    client: anthropic.Anthropic, title: str, description: str
) -> tuple[list[str], str]:
    """Call Haiku to extract skills. Returns (skills_list, raw_response)."""
    truncated_desc = (description or "")[:1500]
    prompt = PROMPT_TEMPLATE.format(title=title or "Unknown", description=truncated_desc)

    response = client.messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = response.content[0].text.strip()

    # Strip markdown fences if present (```json ... ```)
    cleaned = raw_text
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Drop first line (```json) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        skills = json.loads(cleaned)
        if not isinstance(skills, list):
            raise ValueError(f"Expected list, got {type(skills)}")
        skills = [str(s).lower().strip() for s in skills[:30]]
        return skills, raw_text
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse skills response: %s - %s", exc, raw_text[:200])
        return [], raw_text


def store_skill_tags(
    conn: snowflake.connector.SnowflakeConnection,
    results: list[dict],
) -> int:
    """MERGE skill extraction results into raw.skill_tags."""
    if not results:
        return 0

    cur = conn.cursor()
    inserted = 0
    for r in results:
        cur.execute(
            """
            MERGE INTO raw.skill_tags AS tgt
            USING (
                SELECT
                    %s AS posting_id,
                    parse_json(%s) AS skills,
                    %s AS raw_response
            ) AS src
            ON tgt.posting_id = src.posting_id
            WHEN NOT MATCHED THEN INSERT (posting_id, skills, raw_response)
                VALUES (src.posting_id, src.skills, src.raw_response)
            """,
            (r["posting_id"], json.dumps(r["skills"]), r["raw_response"]),
        )
        result = cur.fetchone()
        if result and result[0] > 0:
            inserted += 1
    cur.close()
    return inserted


def run(limit: Optional[int] = None) -> None:
    """Main entry point: fetch untagged postings, extract skills, store results."""
    # No key configured is a deliberate no-op, not a failure: this project is moving
    # enrichment off metered API credits and onto subscription compute via a claude.ai
    # routine (the pattern deploy/matcher-routine.md already uses for JobDigest), so the
    # pipeline must stay green with no key present. This is distinct from the
    # all-postings-failed guard at the end of this function: that one fires only when work
    # was attempted and none of it landed.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.info(
            "No ANTHROPIC_API_KEY set — skipping skill extraction. "
            "Enrichment is expected to run via the claude.ai routine instead."
        )
        return

    conn = get_snowflake_connection()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    postings = fetch_untagged_postings(conn, limit=limit)
    logger.info("Found %d untagged postings", len(postings))

    if not postings:
        conn.close()
        return

    batch_results: list[dict] = []
    succeeded = 0
    failed = 0

    for i, posting in enumerate(postings):
        try:
            skills, raw_response = extract_skills(
                client, posting["title"], posting["description"]
            )
            batch_results.append({
                "posting_id": posting["posting_id"],
                "skills": skills,
                "raw_response": raw_response,
            })
            succeeded += 1
            logger.info(
                "[%d/%d] %s -> %d skills",
                i + 1, len(postings), posting["title"][:50], len(skills),
            )
        except Exception:
            failed += 1
            logger.exception(
                "[%d/%d] Failed to extract skills for %s, skipping.",
                i + 1, len(postings), posting["posting_id"],
            )

        # Store in batches
        if len(batch_results) >= BATCH_SIZE:
            stored = store_skill_tags(conn, batch_results)
            logger.info("Stored batch: %d new rows", stored)
            batch_results = []

    # Store remaining
    if batch_results:
        stored = store_skill_tags(conn, batch_results)
        logger.info("Stored final batch: %d new rows", stored)

    conn.close()

    logger.info(
        "Skill extraction: %d succeeded, %d failed, of %d postings",
        succeeded, failed, len(postings),
    )

    # Skipping an individual bad posting is deliberate (a single unparseable description
    # must not abort the run). Skipping *every* posting is not a run that partially worked
    # -- it is an outage wearing a green checkmark. On 2026-07-20 this step reported success
    # after failing all 14578 postings, because the Anthropic credit balance was exhausted
    # and every call 400'd identically; the pipeline only broke two steps later, far from
    # the cause. A run that enriched nothing must fail loudly.
    if succeeded == 0:
        raise RuntimeError(
            f"Skill extraction failed for all {failed} postings and enriched nothing. "
            "This is an outage, not a run with skipped rows -- check the error above "
            "(a repeated identical error usually means credentials, quota, or billing)."
        )
    logger.info("Skill extraction complete.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # Optional: pass --limit N to cap the run
    limit = None
    if len(sys.argv) > 2 and sys.argv[1] == "--limit":
        limit = int(sys.argv[2])
    run(limit=limit)
