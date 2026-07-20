"""Ingestion runner: fetch all sources, classify, upsert into Postgres.

Reuses the existing `ingestion/` sources and the `search_jobs` classifiers so there
is a single source of truth for how a posting is normalised and labelled. Intended to
run on a schedule (cron / GitHub Action / Supabase cron) writing into the `postings`
table the API reads from.

    DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5433/jobmatch \\
        python -m service.ingest [--cz] [--stale-days 7]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections import Counter

# Make the repo root importable when run as `python -m service.ingest` or directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search_jobs import (  # noqa: E402
    dedup_key, eligibility, gather, is_part_time,
    seniority, work_region, work_type,
)
from service import store  # noqa: E402

logger = logging.getLogger("service.ingest")

# --- role_category ------------------------------------------------------------
# Full taxonomy (mirrors CLAUDE.md and the frontend ROLE_CAT map in web/app).
# Ordered most-specific first: data / ML / devops must precede the broad
# software "engineer" catch-all, and product before design (a "Product Designer"
# should land in `design`, not `product`). Anything unmatched stays a first-class
# `uncategorised` row — kept, never dropped.
# Patterns carry both English and Czech/Slovak terms, because a large share of
# jobs.cz / profesia titles are localised ("Java vývojář", "Produktový manažer",
# "Obchodní zástupce", "Účetní"). Without the CZ/SK terms these all fall through to
# `uncategorised` and never match a subscriber's role filter.
_ROLE_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("data_engineering", re.compile(
        r"data engineer|analytics engineer|dataops|data platform|data warehouse|\betl\b|"
        r"datov[ýá] inžen|dátový inžinier|data inžinier", re.I)),
    ("machine_learning", re.compile(
        r"machine learning|\bml engineer|\bai engineer|data scientist|mlops|"
        r"deep learning|computer vision|\bnlp\b|strojové uč|"
        r"umělá inteligence|umelá inteligencia|datov[ýá] v[ěe]dec", re.I)),
    ("data_analysis", re.compile(
        r"data analyst|bi analyst|business intelligence|power bi|\btableau\b|\banalyst\b|"
        r"analytics|analytik|analytičk|analytičc", re.I)),
    ("devops_platform", re.compile(
        r"devops|platform engineer|site reliability|\bsre\b|cloud engineer|"
        r"infrastructure engineer|\bkubernetes\b|cloud architect|"
        r"správce systém|správca systémov|systémov[ýá] administr|"
        r"administrátor (?:is|it|systém|sít|server)|síťov[ýá] administr", re.I)),
    ("product", re.compile(
        r"product manager|product owner|product lead|product management|\btpm\b|program manager|"
        r"produktov\w*\s+manaž|produktov\w*\s+vlastník", re.I)),
    ("design", re.compile(
        r"designer|\bux\b|\bui\b|user experience|user interface|design lead|"
        r"designér|dizajnér|grafik|grafičk|návrhá[řr]", re.I)),
    ("software_engineering", re.compile(
        r"software engineer|software developer|back[- ]?end|front[- ]?end|full[- ]?stack|"
        r"web developer|mobile developer|\bios\b|android|\bdeveloper\b|programmer|"
        r"\bengineer(?:ing)?\b|qa engineer|\bsdet\b|"
        r"vývojá[řr]|vývojárk|programátor|programátork|softwarov|softvérov", re.I)),
    ("other_tech_function", re.compile(
        r"marketing|\bseo\b|growth|\bsales\b|account executive|business development|"
        r"finance|account(?:ant|ing)|controller|recruit|talent|people ops|"
        r"human resources|\bhr\b|operations|customer success|content|copywriter|legal|counsel|"
        r"obchodn|prodejce|predajca|marketingov|marketér|účetní|účtovník|personalist|"
        r"nábor|právník|právnik|ekonom|nákupčí|nákupca|mzdov", re.I)),
)


def role_category(title: str | None, hint: str | None = None) -> str:
    """Classify a job title into a broad role_category. Title patterns win; when
    they match nothing, fall back to the source-profession `hint` (e.g. a jobs.cz
    "Marketing" field → other_tech_function) which rescues localised CZ/SK titles
    the English+CZ regex still misses. Only `uncategorised` when neither fires."""
    t = title or ""
    for cat, pat in _ROLE_PATTERNS:
        if pat.search(t):
            return cat
    return hint or "uncategorised"


def build_row(p) -> dict:
    region = work_region(p.location, p.country_code)
    text = f"{p.title or ''} {p.location or ''} {p.description or ''}"
    return {
        "posting_id": p.posting_id, "source": p.source, "title": p.title,
        "company": p.company, "url": p.url, "description": p.description,
        "location": p.location, "country_code": p.country_code,
        "remote_signal": p.remote_signal, "salary_raw": p.salary_raw,
        "currency": p.currency, "posted_at": p.posted_at,
        "role_category": role_category(p.title, getattr(p, "source_category", None)),
        "region": region,
        "eligibility": eligibility(region, text),
        "seniority": seniority(p.title),
        "work_type": work_type(p.title, p.description),
        "is_part_time": is_part_time(p.title, p.description),
        "dedup_key": dedup_key(p),
    }


def run(include_cz: bool, stale_days: int) -> None:
    postings = gather(include_cz=include_cz)
    logger.info("Fetched %d postings", len(postings))

    rows, seen = [], set()
    for p in postings:
        # Broad scope: keep every role at a tech company (all sources here are
        # already tech-focused boards). Only drop rows with no URL or no title.
        if not (p.url and (p.title or "").strip()):
            continue
        if p.posting_id in seen:
            continue
        seen.add(p.posting_id)
        rows.append(build_row(p))

    n = store.upsert_postings(rows)
    stale = store.deactivate_stale(days=stale_days)
    by_cat = Counter(r["role_category"] for r in rows)
    logger.info("Upserted %d postings; deactivated %d stale; %d active total",
                n, stale, store.count_active())
    logger.info("By role_category: %s", dict(by_cat.most_common()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cz", action="store_true", help="also include Czech sources")
    ap.add_argument("--stale-days", type=int, default=7)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    run(include_cz=args.cz, stale_days=args.stale_days)


if __name__ == "__main__":
    main()
