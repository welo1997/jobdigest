"""Show currently-live deduped top matches, bypassing the dbt mart.

Reuses the liveness probe from notify.py so the verdict matches what
Friday's pipeline will see.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("SNOWFLAKE_PRIVATE_KEY_PATH", "~/.snowflake/rsa_key.p8")

from enrichment.personal_scorer import get_snowflake_connection  # noqa: E402
from notify import check_url_liveness  # noqa: E402


QUERY = """
WITH scored AS (
    SELECT
        ps.posting_id, ps.personal_score, ps.summary, ps.scored_at,
        p.title, p.company, p.url, p.location,
        stg.role_category, stg.country_code, stg.is_remote
    FROM raw.personal_scores ps
    JOIN raw.job_postings p ON ps.posting_id = p.posting_id
    JOIN raw_staging.stg_job_postings stg ON p.posting_id = stg.posting_id
    WHERE stg.role_category IN ('data_engineering', 'data_analysis', 'machine_learning')
      AND ps.personal_score >= %(min_score)s
      -- Freshness: only postings re-seen in a source feed recently, matching the
      -- fct_personal_matches mart. Without this the tool surfaces months-old,
      -- long-filled postings (many sites serve HTTP 200 for expired listings).
      AND coalesce(p.last_seen_at, p.loaded_at)
          >= dateadd(day, -7, current_timestamp())
)
SELECT *
FROM (
    SELECT
        s.*,
        ROW_NUMBER() OVER (
            PARTITION BY LOWER(TRIM(title)), LOWER(TRIM(COALESCE(company, '')))
            ORDER BY personal_score DESC, scored_at DESC
        ) AS rn
    FROM scored s
)
WHERE rn = 1
ORDER BY personal_score DESC, scored_at DESC
"""


def fetch_top(min_score: int = 6) -> list[dict]:
    with get_snowflake_connection() as conn, conn.cursor() as cur:
        cur.execute(QUERY, {"min_score": min_score})
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> None:
    rows = fetch_top(min_score=6)
    print(f"Probing {len(rows)} deduped matches (score >= 6)...\n")

    live, dead, unknown = [], [], []
    for r in rows:
        verdict, note = check_url_liveness(r["URL"])
        if verdict == "live":
            live.append(r)
        elif verdict == "dead":
            dead.append((r, note))
        else:
            unknown.append((r, note))
        print(
            f"  {verdict:<7} [{r['PERSONAL_SCORE']}] "
            f"{(r['TITLE'] or '')[:60]:<60} | {(r['COMPANY'] or '?')[:25]:<25} | "
            f"{r.get('COUNTRY_CODE') or '-':<2} | {note}"
        )

    print(f"\n=== LIVE matches: {len(live)} (dead {len(dead)} / unknown {len(unknown)}) ===\n")
    # Group by CZ vs other
    cz = [r for r in live if (r.get("COUNTRY_CODE") == "CZ"
          or any(w in (r.get("LOCATION") or "").lower() for w in ("prague", "praha", "brno", "czech")))]
    other = [r for r in live if r not in cz]

    def show_block(label: str, items: list[dict]) -> None:
        print(f"--- {label} ({len(items)}) ---")
        for r in items:
            print(f"  [{r['PERSONAL_SCORE']}] {r['TITLE']} — {r['COMPANY']}")
            print(f"        {r['URL']}")
            summary = (r.get("SUMMARY") or "").strip().replace("\n", " ")
            print(f"        > {summary[:200]}")
            print()

    show_block("CZ-located LIVE matches", sorted(cz, key=lambda r: -r["PERSONAL_SCORE"]))
    show_block("Other LIVE matches", sorted(other, key=lambda r: -r["PERSONAL_SCORE"]))


if __name__ == "__main__":
    main()
