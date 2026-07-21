"""No-API enrichment: export postings -> claude.ai routine -> import skill tags.

Market-intelligence enrichment used to be API-only, which is why an exhausted Anthropic
credit balance took the Snowflake pipeline down on 2026-07-20 while JobDigest kept working:
the matcher already had a no-key path and this did not. This module is that missing half,
built to the same shape as `service/matcher.py`'s export/import pair.

    postings.json                          enrichment-NNN.json
   ──────────────▶   claude.ai routine   ──────────────▶
   (export step,      (no DB, no key,                  (import step,
    has Snowflake)     reads → writes)                  has Snowflake)

**Skills only.** Personal scoring was retired on 2026-07-21: its consumer was a Telegram
alert feed that has been replaced by using JobDigest itself as a subscriber. Skill tags
remain the point of this pass — they feed `int_skill_exploded` and the skill-demand
analysis that the whole dataset exists to support.

Two things differ from the matcher, both deliberate:

* **The output arrives in parts.** The routine's Drive write truncates at 15 000 bytes,
  silently and mid-string — observed on 2026-07-21, when a 300-posting reply was severed
  after exactly 50 records and arrived as invalid JSON. The routine therefore writes
  numbered part files and `import_enrichment` merges them. Do not "simplify" this back to
  a single file.
* **No personal data.** `shortlists.json` carries subscriber preferences and CV summaries,
  which is why `deploy/matcher-routine.md` has a PII section. This file carries public job
  advertisements and nothing else. It is still a *trust* boundary — see `import_enrichment`.
"""

from __future__ import annotations

import glob as globmod
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

BATCH_SIZE = 300
DESC_CHARS = 1500
MAX_SKILLS = 30
MAX_SKILL_LEN = 60

# What the routine writes. A directory or bare filename given to --import is expanded with
# this, so a single legacy enrichment.json still imports.
PART_GLOB = "enrichment*.json"

# Kept in sync with the prompt in enrichment/ROUTINE.md. Also embedded in postings.json so
# the file is self-describing if the two ever drift.
ROUTINE_INSTRUCTIONS = (
    "You are the job-market dataset's skill-extraction pass. For EACH posting below, "
    "extract every technical skill, tool and technology mentioned, as lowercase strings, "
    "normalising variants (\"PostgreSQL\" -> \"postgresql\", \"React.js\" -> \"react\"); "
    "max 30 per posting, [] if none. Postings may be Czech, Slovak or English; treat them "
    "equally. Only use posting_ids present in this file; never invent one. "
    "IMPORTANT: write your answer as SEVERAL numbered files -- enrichment-001.json, "
    "enrichment-002.json, ... -- each holding AT MOST 120 postings and staying under "
    "12000 bytes. A single larger file is silently truncated in transit and arrives as "
    "invalid JSON. Each file has the same shape: "
    '{"enrichment":[{"posting_id":"<id>","skills":["..."]}]}'
)


# --- export ---------------------------------------------------------------------------

FETCH_SQL = """
    SELECT p.posting_id, p.title, p.company, p.location, p.remote_signal, p.description
    FROM raw.job_postings p
    LEFT JOIN raw.skill_tags s ON p.posting_id = s.posting_id
    WHERE s.posting_id IS NULL
      AND p.description IS NOT NULL
    ORDER BY p.loaded_at DESC
"""


def fetch_untagged_postings(
    conn: snowflake.connector.SnowflakeConnection, limit: Optional[int] = None
) -> list[dict]:
    """Postings that have no skill tags yet."""
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
    """Write a batch file for the claude.ai routine to tag. Returns #postings."""
    conn = get_snowflake_connection()
    try:
        postings = fetch_untagged_postings(conn, limit=limit)
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
    than an obvious crash. Anything that is not a usable string is dropped silently.
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


def _existing_posting_ids(
    conn: snowflake.connector.SnowflakeConnection, ids: list[str]
) -> set[str]:
    """Which of `ids` are real postings. Parameterised, chunked to stay under bind limits."""
    known: set[str] = set()
    cur = conn.cursor()
    try:
        for i in range(0, len(ids), 1000):
            chunk = ids[i:i + 1000]
            placeholders = ",".join(["%s"] * len(chunk))
            cur.execute(
                f"SELECT posting_id FROM raw.job_postings "
                f"WHERE posting_id IN ({placeholders})",
                tuple(chunk),
            )
            known |= {r[0] for r in cur.fetchall()}
    finally:
        cur.close()
    return known


def store_skill_tags(
    conn: snowflake.connector.SnowflakeConnection, records: list[dict]
) -> int:
    """MERGE validated records into raw.skill_tags. Returns #rows.

    INSERT-only on purpose, matching the API path: enrichment is idempotent, so re-running
    must not rewrite an answer that is already stored.
    """
    n = 0
    cur = conn.cursor()
    try:
        for r in records:
            cur.execute(
                """
                MERGE INTO raw.skill_tags AS tgt
                USING (SELECT %s AS posting_id, parse_json(%s) AS skills,
                              %s AS raw_response) AS src
                ON tgt.posting_id = src.posting_id
                WHEN NOT MATCHED THEN INSERT (posting_id, skills, raw_response)
                    VALUES (src.posting_id, src.skills, src.raw_response)
                """,
                (r["posting_id"], json.dumps(r["skills"]), r["raw_response"]),
            )
            n += 1
    finally:
        cur.close()
    return n


def _part_files(path: str) -> list[str]:
    """Resolve --import's argument to the list of part files to read."""
    if os.path.isdir(path):
        return sorted(globmod.glob(os.path.join(path, PART_GLOB)))
    if any(ch in path for ch in "*?["):
        return sorted(globmod.glob(path))
    return [path]


def _read_entries(paths: list[str]) -> list[dict]:
    """Parse each part file, skipping any that is unreadable rather than aborting.

    A truncated part is the expected transport failure here, not a hypothetical: the
    routine's Drive write cuts off at 15 000 bytes mid-string, producing invalid JSON. One
    severed part must cost only its own postings -- the rest of the batch, and the rest of
    the pipeline run, still have work to do.
    """
    entries: list[dict] = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error(
                "enrichment part %s is unreadable (%s) — skipped. A truncated part usually "
                "means the routine exceeded the 15000-byte write limit; it must split its "
                "answer into more files.", p, exc,
            )
            continue
        part = data.get("enrichment", data) if isinstance(data, dict) else data
        if not isinstance(part, list):
            logger.warning("enrichment part %s: expected a list, got %s — skipped",
                           p, type(part).__name__)
            continue
        entries.extend(e for e in part if isinstance(e, dict))
    return entries


def import_enrichment(path: str) -> int:
    """Read the routine's enrichment part files, validate against the DB, store them.

    `path` may be a single file, a glob, or a directory containing `enrichment*.json`.

    These files cross a trust boundary: they are written by a claude.ai routine and travel
    via cloud storage, so nothing in them is taken on faith. `posting_id` is checked against
    `raw.job_postings` -- an invented or stale id is dropped, so the routine cannot conjure
    rows for postings that were never ingested. Skills are coerced to a list of short
    lowercase strings, and a malformed record is skipped rather than aborting the batch
    (`import_picks`'s rule: one bad record must not cost the whole run).

    Returns the number of postings stored.
    """
    paths = _part_files(path)
    if not paths:
        logger.warning("no enrichment part files matched %s", path)
        return 0
    logger.info("Reading %d enrichment part(s): %s", len(paths), ", ".join(paths))

    entries = _read_entries(paths)
    claimed = [str(e.get("posting_id")) for e in entries if e.get("posting_id")]
    if not claimed:
        logger.warning("enrichment parts held no usable posting_id in %d record(s)",
                       len(entries))
        return 0

    conn = get_snowflake_connection()
    try:
        known = _existing_posting_ids(conn, sorted(set(claimed)))
        for unknown in sorted(set(claimed) - known):
            logger.warning("enrichment: unknown posting_id %s — record skipped", unknown)

        records: list[dict] = []
        seen: set[str] = set()
        for entry in entries:
            pid = str(entry.get("posting_id") or "")
            # A duplicated id would MERGE twice for no gain; the first answer wins.
            if pid not in known or pid in seen:
                continue
            skills = _clean_skills(entry.get("skills"))
            if not skills:
                continue
            seen.add(pid)
            records.append({
                "posting_id": pid,
                "skills": skills,
                "raw_response": json.dumps({"skills": skills}, ensure_ascii=False),
            })

        if not records:
            # Same reasoning as the API path's all-postings-failed guard: files that were
            # produced, transported and parsed, yet yielded nothing, are an outage wearing a
            # green checkmark -- most likely every part was truncated, or the batch belongs
            # to a database that has since been rebuilt.
            raise RuntimeError(
                f"enrichment parts held {len(entries)} record(s) but none were usable — "
                "nothing was stored. Check the warnings above: unknown posting_ids mean "
                "the batch and the database disagree; parse errors mean the routine's "
                "files were truncated in transit."
            )

        stored = store_skill_tags(conn, records)
    finally:
        conn.close()

    logger.info("Imported skills for %d posting(s) from %d part(s)", stored, len(paths))
    return stored


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
            "       python -m enrichment.exchange --import <file|glob|dir>",
            file=sys.stderr,
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
