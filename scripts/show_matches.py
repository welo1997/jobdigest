"""Show rescored personal_scores results: deduped top matches with year requirements."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("SNOWFLAKE_PRIVATE_KEY_PATH", "~/.snowflake/rsa_key.p8")

from enrichment.personal_scorer import get_snowflake_connection  # noqa: E402


# Pull deduped top matches with description snippet + extracted year requirement.
# Dedup key: normalized lower(title) + lower(company). Keep the highest-scoring,
# most-recently-scored row per group.
QUERY = """
WITH scored AS (
    SELECT
        ps.posting_id,
        ps.personal_score,
        ps.summary,
        p.title,
        p.company,
        p.url,
        p.description,
        stg.role_category,
        stg.country_code,
        ps.scored_at
    FROM raw.personal_scores ps
    JOIN raw.job_postings p ON ps.posting_id = p.posting_id
    JOIN raw_staging.stg_job_postings stg ON p.posting_id = stg.posting_id
    WHERE stg.role_category IN ('data_engineering', 'data_analysis', 'machine_learning')
      AND ps.personal_score >= %(min_score)s
      AND (%(country)s IS NULL OR stg.country_code = %(country)s)
),
ranked AS (
    SELECT
        s.*,
        ROW_NUMBER() OVER (
            PARTITION BY LOWER(TRIM(title)), LOWER(TRIM(COALESCE(company, '')))
            ORDER BY personal_score DESC, scored_at DESC
        ) AS rn
    FROM scored s
)
SELECT
    personal_score,
    title,
    company,
    role_category,
    country_code,
    url,
    description,
    summary
FROM ranked
WHERE rn = 1
ORDER BY personal_score DESC, scored_at DESC
LIMIT %(limit)s
"""


YEAR_PATTERNS = [
    re.compile(r"(\d+)\s*[-––to]+\s*(\d+)\s*\+?\s*years?(?:\s+of)?\s+(?:experience|exp)", re.I),
    re.compile(r"(\d+)\s*\+\s*years?(?:\s+of)?\s+(?:experience|exp)", re.I),
    re.compile(r"(?:minimum|at\s+least|min\.?)\s*(\d+)\s*years?", re.I),
    re.compile(r"(\d+)\s*years?(?:\s+of)?\s+(?:experience|exp)", re.I),
    re.compile(r"(\d+)\s*\+\s*years?", re.I),
    re.compile(r"(?:junior|entry[-\s]?level|graduate|traineeship|trainee)", re.I),
    re.compile(r"(?:senior|lead|principal|staff)\b", re.I),
]


def extract_years(description: str | None, title: str | None) -> str:
    """Best-effort: pick first matching year-pattern from description, fall back to title."""
    text = f"{title or ''}\n{description or ''}"
    for pat in YEAR_PATTERNS[:5]:
        m = pat.search(text)
        if m:
            return m.group(0).strip()
    # Seniority cues
    if YEAR_PATTERNS[5].search(title or ""):
        return f"(title: {YEAR_PATTERNS[5].search(title or '').group(0).lower()})"
    if YEAR_PATTERNS[6].search(title or ""):
        return f"(title: {YEAR_PATTERNS[6].search(title or '').group(0).lower()})"
    if YEAR_PATTERNS[5].search(description or ""):
        return f"(desc: {YEAR_PATTERNS[5].search(description or '').group(0).lower()})"
    if YEAR_PATTERNS[6].search(description or ""):
        return f"(desc: {YEAR_PATTERNS[6].search(description or '').group(0).lower()})"
    return "—"


def print_section(label: str, rows: list[dict]) -> None:
    print(f"\n=== {label} ({len(rows)} rows) ===")
    for r in rows:
        years = extract_years(r["DESCRIPTION"], r["TITLE"])
        title = (r["TITLE"] or "").strip()[:70]
        company = (r["COMPANY"] or "?").strip()[:30]
        country = r.get("COUNTRY_CODE") or "—"
        print(
            f"  [{r['PERSONAL_SCORE']}] {title:<70} | "
            f"{company:<30} | {country:<3} | yrs: {years}"
        )
        print(f"        {r['URL']}")


def fetch(min_score: int, country: str | None, limit: int) -> list[dict]:
    with get_snowflake_connection() as conn, conn.cursor() as cur:
        cur.execute(QUERY, {"min_score": min_score, "country": country, "limit": limit})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


if __name__ == "__main__":
    print_section("Top deduped CZ matches (score >= 6)", fetch(6, "CZ", 30))
    print_section("Top deduped non-CZ matches (score >= 7)", fetch(7, None, 40))
