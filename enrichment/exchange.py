"""No-API enrichment: export postings -> claude.ai routine -> import enrichment.

Market-intelligence enrichment used to be API-only, which is why an exhausted Anthropic
credit balance took the Snowflake pipeline down on 2026-07-20 while JobDigest kept working:
the matcher already had a no-key path and this did not. This module is that missing half,
built to the same shape as `service/matcher.py`'s export/import pair.

    postings.json                                  enrichment.json
   ──────────────▶   claude.ai routine   ──────────────▶
   (export step,      (no DB, no key,                  (import step,
    has Snowflake)     reads → writes)                  has Snowflake)

Two things differ from the matcher, both deliberate:

* **One pass, not two.** The API path makes a Haiku call per posting for skills and a
  second for the personal score. A routine makes a handful of large-context calls, so the
  routine reads each posting once and returns both. Fewer calls and the two answers cannot
  disagree about the same text.
* **No personal data.** `shortlists.json` carries subscriber preferences and CV summaries,
  which is why `deploy/matcher-routine.md` has a PII section. This file carries public job
  advertisements and nothing else, so the transport folder's exposure is not a privacy
  question here. It is still a *trust* boundary — see `import_enrichment`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import snowflake.connector

# Reuse rather than add a fourth copy of the connection helper. It already carries the
# "no SYSADMIN default" decision in its comments; duplicating it is how that drifts.
from enrichment.skill_extractor import get_snowflake_connection

logger = logging.getLogger(__name__)

# Batch small enough that the whole file fits comfortably in one routine's context. At
# DESC_CHARS below this is roughly 0.5 MB per file -- the matcher's shortlist is ~120
# candidates, and this is the same order of magnitude.
BATCH_SIZE = 300
DESC_CHARS = 1500
MAX_SKILLS = 30
MAX_SKILL_LEN = 60
MAX_SUMMARY_LEN = 500

ROUTINE_INSTRUCTIONS = (
    "You are the job-market dataset's enrichment pass. For EACH posting below, do two "
    "things in one read. (1) SKILLS: extract every technical skill, tool and technology, "
    "as lowercase strings, normalising variants (\"PostgreSQL\" -> \"postgresql\", "
    "\"React.js\" -> \"react\"); max 30, use [] if none. (2) PERSONAL_SCORE: rate 0-10 how "
    "well the role fits an early-career data professional (dbt, Snowflake, Python, SQL; "
    "~1-2 years; based in Czechia/EU; wants fully-remote EU/worldwide roles, or anything "
    "in CZ). Score 0 ONLY for a genuine eligibility blocker (needs non-EU work "
    "authorisation, on-site outside CZ/EU with no remote option, security clearance, or "
    "fluency in a language other than English/Czech). Stack mismatch or too-senior is a "
    "deduction, not a zero. Add a 2-sentence summary of the fit. Postings may be Czech, "
    "Slovak or English; judge them equally. Only use posting_ids present in the file; "
    "never invent one. Write ONE JSON file `enrichment.json`: "
    '{"enrichment":[{"posting_id":"<id>","skills":["..."],"personal_score":<int 0-10>,'
    '"summary":"<2 sentences>"}]}'
)


# --- export ---------------------------------------------------------------------------

FETCH_SQL = """
    SELECT p.posting_id, p.title, p.company, p.location, p.remote_signal, p.description
    FROM raw.job_postings p
    LEFT JOIN raw.skill_tags s      ON p.posting_id = s.posting_id
    LEFT JOIN raw.personal_scores c ON p.posting_id = c.posting_id
    WHERE p.description IS NOT NULL
      AND (s.posting_id IS NULL OR c.posting_id IS NULL)
    ORDER BY p.loaded_at DESC
"""


def fetch_unenriched_postings(
    conn: snowflake.connector.SnowflakeConnection, limit: Optional[int] = None
) -> list[dict]:
    """Postings missing skills, a personal score, or both.

    Deliberately one query for both gaps rather than the API path's two: a posting missing
    only its score still needs its description sent, and sending it twice would double the
    routine's reading for nothing.
    """
    sql = FETCH_SQL + (f" LIMIT {int(limit)}" if limit else "")
    cur = conn.cursor()
    try:
        cur.execute(sql)
        rows = cur.fetchall()
    finally:
        cur.close()
    return [
        {
            "posting_id": r[0],
            "title": r[1],
            "company": r[2],
            "location": r[3],
            "is_remote": bool(r[4]),
            "description": (r[5] or "").replace("\n", " ").strip()[:DESC_CHARS],
        }
        for r in rows
    ]


def export_postings(path: str, limit: Optional[int] = BATCH_SIZE) -> int:
    """Write a batch file for the claude.ai routine to enrich. Returns #postings."""
    conn = get_snowflake_connection()
    try:
        postings = fetch_unenriched_postings(conn, limit=limit)
    finally:
        conn.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instructions": ROUTINE_INSTRUCTIONS,
        "postings": postings,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    logger.info("Exported %d posting(s) -> %s", len(postings), path)
    return len(postings)


# --- import ---------------------------------------------------------------------------


def _clean_skills(value: object) -> list[str]:
    """Coerce the routine's `skills` into the shape raw.skill_tags promises.

    `int_skill_exploded` flattens this array and the marts group by its values, so one
    dict, one 40 kB string, or one null in here becomes a bad row in a fact table rather
    than an obvious crash. Anything that is not a usable string is dropped silently; the
    caller decides what an empty list means.
    """
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, (str, int, float)) or isinstance(item, bool):
            continue
        s = str(item).lower().strip()[:MAX_SKILL_LEN]
        if s and s not in out:
            out.append(s)
        if len(out) >= MAX_SKILLS:
            break
    return out


def _clean_score(value: object) -> Optional[int]:
    """Clamp to 0-10, or None if it is not a number at all.

    None and 0 must stay distinguishable: 0 is a real verdict ("not eligible"), whereas
    None means the routine gave us nothing usable and the row should keep its NULL so a
    later run retries it.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return max(0, min(10, int(value)))
    except (ValueError, TypeError):
        return None


def _existing_posting_ids(
    conn: snowflake.connector.SnowflakeConnection, ids: list[str]
) -> set[str]:
    """Which of `ids` are real postings. One round trip, parameterised."""
    if not ids:
        return set()
    cur = conn.cursor()
    try:
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(
            f"SELECT posting_id FROM raw.job_postings WHERE posting_id IN ({placeholders})",
            tuple(ids),
        )
        return {r[0] for r in cur.fetchall()}
    finally:
        cur.close()


def store_enrichment(
    conn: snowflake.connector.SnowflakeConnection, records: list[dict]
) -> tuple[int, int]:
    """MERGE validated records into raw.skill_tags and raw.personal_scores.

    Returns (skill_rows, score_rows). Both MERGEs are INSERT-only on purpose, matching the
    API path: enrichment is idempotent, so re-running must not rewrite an answer that is
    already stored.
    """
    skills_n = scores_n = 0
    cur = conn.cursor()
    try:
        for r in records:
            pid = r["posting_id"]
            if r["skills"]:
                cur.execute(
                    """
                    MERGE INTO raw.skill_tags AS tgt
                    USING (SELECT %s AS posting_id, parse_json(%s) AS skills,
                                  %s AS raw_response) AS src
                    ON tgt.posting_id = src.posting_id
                    WHEN NOT MATCHED THEN INSERT (posting_id, skills, raw_response)
                        VALUES (src.posting_id, src.skills, src.raw_response)
                    """,
                    (pid, json.dumps(r["skills"]), r["raw_response"]),
                )
                skills_n += 1
            if r["personal_score"] is not None:
                cur.execute(
                    """
                    MERGE INTO raw.personal_scores AS tgt
                    USING (SELECT %s AS posting_id, %s AS personal_score,
                                  %s AS summary) AS src
                    ON tgt.posting_id = src.posting_id
                    WHEN NOT MATCHED THEN INSERT (posting_id, personal_score, summary)
                        VALUES (src.posting_id, src.personal_score, src.summary)
                    """,
                    (pid, r["personal_score"], r["summary"]),
                )
                scores_n += 1
    finally:
        cur.close()
    return skills_n, scores_n


def import_enrichment(path: str) -> int:
    """Read the routine's enrichment JSON, validate against the DB, store it.

    This file crosses a trust boundary: it is written by a claude.ai routine and travels
    via cloud storage, so nothing in it is taken on faith. `posting_id` is checked against
    `raw.job_postings` -- an invented or stale id is dropped, so the routine cannot conjure
    rows for postings that were never ingested. Skills are coerced to a list of short
    lowercase strings, scores clamped to 0-10, summaries truncated, and a malformed record
    is logged and skipped rather than aborting the batch (`import_picks`'s rule: one bad
    record must not cost the whole run).

    Returns the number of postings that stored something.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("enrichment", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        logger.warning(
            "enrichment file %s: expected a list of records, got %s",
            path, type(entries).__name__,
        )
        return 0

    claimed = [
        str(e.get("posting_id"))
        for e in entries
        if isinstance(e, dict) and e.get("posting_id")
    ]
    if not claimed:
        logger.warning("enrichment file %s: no usable posting_id in %d record(s)",
                       path, len(entries))
        return 0

    conn = get_snowflake_connection()
    try:
        known = _existing_posting_ids(conn, sorted(set(claimed)))
        for unknown in sorted(set(claimed) - known):
            logger.warning("enrichment file: unknown posting_id %s — record skipped", unknown)

        records: list[dict] = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            pid = str(entry.get("posting_id") or "")
            # A duplicated id would MERGE twice for no gain; the first answer wins.
            if pid not in known or pid in seen:
                continue
            skills = _clean_skills(entry.get("skills"))
            score = _clean_score(entry.get("personal_score"))
            if not skills and score is None:
                continue
            seen.add(pid)
            records.append({
                "posting_id": pid,
                "skills": skills,
                "personal_score": score,
                "summary": str(entry.get("summary") or "")[:MAX_SUMMARY_LEN],
                "raw_response": json.dumps(
                    {"skills": skills, "personal_score": score}, ensure_ascii=False
                ),
            })

        if not records:
            # Same reasoning as the API path's all-postings-failed guard: a file that was
            # produced, transported and parsed, yet yielded nothing, is an outage wearing a
            # green checkmark -- most likely the routine wrote a different shape, or the
            # batch belongs to a database that has since been rebuilt.
            raise RuntimeError(
                f"enrichment file {path} held {len(entries)} record(s) but none were "
                "usable — nothing was stored. Check the warnings above: unknown "
                "posting_ids mean the batch and the database disagree; no warnings at all "
                "usually means the routine wrote the wrong JSON shape."
            )

        skills_n, scores_n = store_enrichment(conn, records)
    finally:
        conn.close()

    logger.info(
        "Imported enrichment for %d posting(s) from %s: %d skill row(s), %d score row(s)",
        len(records), path, skills_n, scores_n,
    )
    return len(records)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "--export":
        limit = int(args[3]) if len(args) >= 4 and args[2] == "--limit" else BATCH_SIZE
        export_postings(args[1], limit=limit)
    elif len(args) >= 2 and args[0] == "--import":
        import_enrichment(args[1])
    else:
        print(
            "usage: python -m enrichment.exchange --export postings.json [--limit N]\n"
            "       python -m enrichment.exchange --import enrichment.json",
            file=sys.stderr,
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
