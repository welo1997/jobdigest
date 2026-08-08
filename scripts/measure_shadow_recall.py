"""Does vector retrieval surface the postings the AI actually picked?

    DATABASE_URL=... python scripts/measure_shadow_recall.py [--k 25 50 120]

**This is the gate for narrowing the shortlist, and it is deliberately falsifiable.** The plan
is to stop handing the matcher ~120 keyword-retrieved candidates and hand it ~25
vector-retrieved ones instead, which is what makes per-subscriber cost survive growth. Whether
that is safe is not a design argument — it is a number: of the postings the AI has actually
chosen for a subscriber, how many would have been inside the vector top-K?

  recall@25 high  -> narrowing to 25 keeps what we already find. Safe to switch.
  recall@25 low   -> do NOT narrow. The vector work still earns its place by replacing the
                     English-only recall predicate and the physical-scan-order ranking, but
                     the candidate count stays where it is.

**What this measurement cannot tell you, stated plainly.** Every row in `matches` was drawn from
a *keyword* shortlist, so the ground truth is biased toward what keyword retrieval already
surfaced. A posting that vector retrieval would find and keywords missed cannot appear in
`matches` at all, and therefore cannot be credited here. So recall@K is a **necessary condition**
(does the new path preserve what the old one found?) and not evidence of improvement. Measuring
improvement needs the shadow table accumulating over real days, which is what
`shortlist_shadow` is for.

Uses the *real* gates from `service.store` / `service.geo` rather than reimplementing them, so
the comparison is apples-to-apples: same location, eligibility, education and work-mode
filtering, only the ranking and the candidate count differ.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import education, embed, geo, store  # noqa: E402

logger = logging.getLogger("measure_shadow_recall")

#: Only picks at or above this score were ever emailed, so they are the ones whose loss would be
#: felt. Reported separately from the >= MATCH_FLOOR set, which only ever reached /matches.
EMAIL_MIN_SCORE = 6


def _gate_sql(profile: dict) -> tuple[str, list]:
    """The hard filters, taken from the production helpers so they cannot drift from the real
    shortlist. Ranking is the only thing this measurement changes."""
    where, params = ["p.is_active", "p.embedding is not null"], []

    loc_sql, loc_params = geo.location_predicate(profile)
    if loc_sql != "true":
        where.append(loc_sql)
        params.extend(loc_params)

    edu_sql, edu_params = education.education_predicate(profile)
    if edu_sql != "true":
        where.append(edu_sql)
        params.extend(edu_params)

    if profile.get("eligible_only", True):
        where.append("p.eligibility = any(%s)")
        params.append(store.eligibility_allowlist(profile))

    return " and ".join(where), params


def vector_top_k(profile: dict, k: int) -> list[str]:
    """The vector top-K, with the HNSW post-filter defeated.

    **Without the `iterative_scan` line this function measures the index setting, not the
    embeddings, and it does so silently.** pgvector's HNSW scan collects `hnsw.ef_search`
    (default 40) candidates from the index and applies the WHERE clause *afterwards*, so a
    selective gate starves it: measured on production 2026-08-08, this query returned **4 rows
    for a LIMIT of 120** on one profile and **0 rows at every K** on another, while the pool it
    was filtering held 15 393 and 18 543 postings. Recall therefore came out near-zero *and
    flat across K* — the flatness is the tell, because a genuinely bad ranking still improves
    as K grows. `relaxed_order` keeps scanning until K rows survive the filter; measured
    against an exact scan it recovers 14 of 15 true hits at K=120, where the default recovered
    2.

    This is not only a measurement concern. Any production vector shortlist runs the same
    shape of query behind the same gates, so it inherits the same starvation — an empty
    shortlist with no error, no log and no failed timer, which is this repo's recurring
    failure mode. Whatever wires the vector path into `query_shortlist_meta` must set this too.
    """
    gate, params = _gate_sql(profile)
    with store.cursor() as cur:
        # pgvector registers its GUCs in _PG_init, which has not run on a freshly pooled
        # connection; `set hnsw.*` errors with "unrecognized configuration parameter" until
        # some vector expression forces the library to load.
        cur.execute("select '[1,0]'::vector <=> '[0,1]'::vector")
        cur.execute("set local hnsw.iterative_scan = relaxed_order")
        cur.execute(
            f"select p.posting_id from postings p where {gate} "
            f"order by p.embedding <=> %s::halfvec limit %s",
            (*params, embed.to_pgvector(profile["embedding_vec"]), k))
        rows = [r["posting_id"] for r in cur.fetchall()]
    if len(rows) < k:
        # Fewer rows than asked for means the scan, not the corpus, decided the answer.
        logger.warning("top-%d returned only %d rows — the index is still truncating; every "
                       "recall figure below it is a floor, not a measurement", k, len(rows))
    return rows


def run(ks: list[int]) -> None:
    with store.cursor() as cur:
        cur.execute("select * from profiles where status = 'active' and embedding is not null")
        profiles = [dict(r) for r in cur.fetchall()]
    if not profiles:
        logger.error("no active profile has an embedding yet — run "
                     "`python -m service.backfill_embeddings` first")
        return

    with store.cursor() as cur:
        cur.execute("select count(embedding) as v, count(*) as n from postings where is_active")
        cov = dict(cur.fetchone())
    logger.info("posting coverage: %d of %d active postings have a vector (%.1f%%)",
                cov["v"], cov["n"], 100.0 * cov["v"] / max(cov["n"], 1))
    if cov["v"] < cov["n"]:
        # Stated rather than silently depressing every number below: a posting with no vector
        # cannot be retrieved, so an incomplete backfill understates recall.
        logger.warning("backfill is INCOMPLETE — %d postings have no vector and therefore "
                       "cannot be retrieved. Recall below is a LOWER BOUND.",
                       cov["n"] - cov["v"])

    for p in profiles:
        # psycopg2 returns halfvec as its text literal; parse back to floats for the query.
        p["embedding_vec"] = [float(x) for x in str(p["embedding"]).strip("[]").split(",")]

        with store.cursor() as cur:
            cur.execute(
                "select m.posting_id, m.score from matches m join postings p "
                "  on p.posting_id = m.posting_id "
                " where m.profile_id = %s and p.is_active and m.score is not null",
                (p["id"],))
            picks = [dict(r) for r in cur.fetchall()]

        emailed = {r["posting_id"] for r in picks if (r["score"] or 0) >= EMAIL_MIN_SCORE}
        allpicks = {r["posting_id"] for r in picks}
        if not allpicks:
            logger.info("profile %s: no scored picks on active postings — nothing to measure",
                        str(p["id"])[:8])
            continue

        logger.info("profile %s (%s): %d picks on active postings, %d of them email-grade",
                    str(p["id"])[:8], p.get("label") or "-", len(allpicks), len(emailed))
        for k in ks:
            top = set(vector_top_k(p, k))
            r_all = len(top & allpicks) / len(allpicks)
            line = f"  recall@{k:<4} all picks {r_all:5.1%} ({len(top & allpicks)}/{len(allpicks)})"
            if emailed:
                r_em = len(top & emailed) / len(emailed)
                line += f"   email-grade {r_em:5.1%} ({len(top & emailed)}/{len(emailed)})"
            logger.info(line)

    logger.info("Reminder: `matches` was populated from KEYWORD shortlists, so this is a "
                "necessary-condition check (does vector retrieval preserve what we already "
                "find?), not evidence of improvement.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--k", type=int, nargs="+", default=[25, 50, 120],
                    help="candidate counts to measure recall at")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    run(args.k)


if __name__ == "__main__":
    main()
