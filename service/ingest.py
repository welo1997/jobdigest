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
import sys
from collections import Counter, defaultdict

# Make the repo root importable when run as `python -m service.ingest` or directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search_jobs import (  # noqa: E402
    dedup_key, eligibility, gather, is_part_time,
    seniority, work_region, work_type,
)
from service import education, geo, skills, store, taxonomy  # noqa: E402

logger = logging.getLogger("service.ingest")

# --- role_category ------------------------------------------------------------
# The taxonomy itself lives in service/taxonomy.py — it is consumed by the shortlist
# builder, the digest subject line and the CV parser too, and used to be copy-pasted into
# each of them. Re-exported under the old name so callers here read unchanged.
role_category = taxonomy.classify


def _classify(title, hint, cache: dict[str, str] | None,
              stats: Counter | None = None) -> str:
    """Patterns, then the publisher's code, then the routine's memoised answer.

    The cache is consulted **last and only on a decline**, which is what keeps it from
    overriding anything the repo can reason about: a pattern is reviewable, an occupation
    code comes from the publisher, and a model's answer is neither. It also keeps the cache
    honest as a measurement — its hit count is exactly the residue the other two could not
    read.

    `taxonomy.classify` is not the place for this. It is imported by `ingestion/`, which has
    no database, and by the CV parser and the digest, which classify a subscriber's words
    rather than a posting's. A dict passed in from the one caller that has a connection keeps
    the taxonomy a pure function of its input.
    """
    category = role_category(title, hint)
    if category != taxonomy.UNCATEGORISED:
        return category
    # Counted here rather than reconstructed afterwards: "the residue" is defined by what the
    # patterns and the code declined, and a row's stored category cannot tell you that once
    # the cache has answered for it.
    if stats is not None:
        stats["residue"] += 1
    if not cache:
        return category
    from service.categorize_exchange import normalise_title  # local: ingest has no cycle

    answer = cache.get(normalise_title(title), taxonomy.UNCATEGORISED)
    if stats is not None and answer != taxonomy.UNCATEGORISED:
        stats["cache_hit"] += 1
    return answer


def build_row(p, title_cache: dict[str, str] | None = None,
              stats: Counter | None = None) -> dict:
    # Resolve the free-text location into (country, city) before anything else: most boards
    # send no country_code at all (Greenhouse, Lever, Remotive, LinkedIn all pass None), so
    # the resolved value is what makes country- and city-level preferences possible — and it
    # sharpens `region`/`eligibility` too, which previously read "Berlin" as region 'other'.
    country_code, city = geo.resolve_location(p.location, p.country_code)
    # One classification, two columns: `remote_signal` is what exempts a posting from the
    # location gate, `work_mode` is the finer answer a subscriber can filter on and the digest
    # can show. Derived from the same call so they cannot disagree.
    mode = geo.work_mode(p.location, p.description, p.remote_signal)
    remote = mode == "remote"
    # The *other* remote question: not "is there an office" but "where may I live". Computed only
    # for fully-remote rows — the reach of an on-site Berlin job is a category error, and deriving
    # one anyway would fill the column with trivially-`country` rows and make its coverage look
    # far better than it is. `scope_raw` is stored whatever the mode, because it is the board's own
    # claim and re-deriving the verdict after a classifier change must not need a re-ingest.
    scope_raw = getattr(p, "scope_raw", None)
    # Both columns from one call, so a posting cannot be `region` on the strength of its scope field
    # and filed under the area named in its location field.
    reach, areas = (geo.classify_reach(scope_raw, p.location, p.description) if remote
                    else (None, []))
    region = work_region(p.location, country_code)
    text = f"{p.title or ''} {p.location or ''} {p.description or ''}"
    return {
        "posting_id": p.posting_id, "source": p.source, "title": p.title,
        "company": p.company, "url": p.url, "description": p.description,
        "location": p.location, "country_code": country_code, "city": city,
        "remote_signal": remote, "work_mode": mode,
        "scope_raw": scope_raw, "remote_reach": reach, "reach_areas": areas,
        # Null whenever the ad does not state a binding requirement, which is the answer for
        # ~97% of postings — and necessarily for every source that ships no description text
        # (jobs.cz, profesia, cocuma). See service/education.py before reading anything into
        # a low count here.
        "education_min": education.classify_requirement(p.description, p.title),
        "salary_raw": p.salary_raw,
        "currency": p.currency, "posted_at": p.posted_at,
        "role_category": _classify(p.title, getattr(p, "source_category", None),
                                   title_cache, stats),
        "region": region,
        "eligibility": eligibility(region, text),
        "seniority": seniority(p.title),
        "work_type": work_type(p.title, p.description),
        "is_part_time": is_part_time(p.title, p.description),
        # Canonical skills the ad names (migration 020), deterministic gazetteer match. An empty
        # list is stored as null by `upsert_postings` — null/empty passes every facet, the same
        # rule as education_min above. See service/skills.py.
        "skills": skills.extract_skills(p.title, p.description),
        "dedup_key": dedup_key(p),
    }


def _report_discarded_hints(discarded: dict[str, Counter]) -> None:
    """Name the adapters passing a `source_category` that is not a real role_category.

    `taxonomy.classify` discards a non-canonical hint rather than writing it into
    `postings.role_category`, which is the fix for 14% of the corpus sitting in a category
    nothing could match. But a guard that silently drops the value replaces one quiet failure
    with another: the adapter goes on producing junk and the row falls back to title-only
    classification — which, for the Swedish and Czech sources, usually means `uncategorised`.

    So this is a report for a human, in the shape of `store.unmet_demand_terms()`: it never
    feeds anything automatic, it just makes the vocabulary visible so a decision to write a
    curated map (or to teach the taxonomy Swedish) can be taken on measured labels rather than
    a guess. Warning level because every line here is an adapter to fix, not weather.
    """
    for source, hints in sorted(discarded.items(), key=lambda kv: -sum(kv[1].values())):
        top = ", ".join(f"{h!r} x{n}" for h, n in hints.most_common(5))
        logger.warning(
            "%s: %d rows carried a source_category that is not a role_category (%d distinct) "
            "— discarded, classified on title alone. Top: %s",
            source, sum(hints.values()), len(hints), top,
        )


def run(include_cz: bool, stale_days: int) -> None:
    postings = gather(include_cz=include_cz)
    logger.info("Fetched %d postings", len(postings))

    # One read for the whole run, and non-fatal for the same reason the embedding step is:
    # this is an enrichment, and a database hiccup in it must cost some rows their category,
    # never the day's ingest. Without the cache every posting falls back to exactly the
    # answer it had before the cache existed.
    try:
        title_cache = store.title_category_map()
    except Exception:
        logger.exception("title cache unavailable — classifying without it")
        title_cache = {}

    rows, seen = [], set()
    cache_stats: Counter = Counter()
    discarded: dict[str, Counter] = defaultdict(Counter)
    for p in postings:
        # Broad scope: keep every role at a tech company (all sources here are
        # already tech-focused boards). Only drop rows with no URL or no title.
        if not (p.url and (p.title or "").strip()):
            continue
        if p.posting_id in seen:
            continue
        seen.add(p.posting_id)
        hint = getattr(p, "source_category", None)
        if hint and hint not in taxonomy.CATEGORIES:
            discarded[p.source][hint] += 1
        rows.append(build_row(p, title_cache, cache_stats))

    n = store.upsert_postings(rows)
    stale = store.deactivate_stale(days=stale_days)
    by_cat = Counter(r["role_category"] for r in rows)
    logger.info("Upserted %d postings; deactivated %d stale; %d active total",
                n, stale, store.count_active())
    logger.info("By role_category: %s", dict(by_cat.most_common()))
    _report_title_cache(cache_stats, title_cache)
    _report_discarded_hints(discarded)


def _report_title_cache(stats: Counter, cache: dict[str, str]) -> None:
    """How much of the residue the routine's answers actually covered today.

    `PLAN.md` item 4 sets the test this reports against: **if the hit rate is not >95%, the
    key is wrong.** The number that matters is not "how many rows did the cache classify" but
    "of the rows the patterns and codes declined, how many did it have an answer for" — a
    cache keyed on something too specific (an unnormalised title, or one with the employer
    left in it) still classifies plenty of rows while missing most of what it was asked
    about, and only this ratio shows that.
    """
    residue = stats.get("residue", 0)
    if not residue:
        return
    hit = stats.get("cache_hit", 0)
    logger.info("Title cache: %d/%d of the pattern residue had a memoised answer (%.1f%%); "
                "%d titles cached in total",
                hit, residue, 100 * hit / residue, len(cache))


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
