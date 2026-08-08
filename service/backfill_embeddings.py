"""Compute embeddings for postings and profiles that are missing or stale ones.

    DATABASE_URL=... python -m service.backfill_embeddings [--limit N] [--batch N] [--dry-run]

Runs as its own short-lived process, **sequentially after the export rather than inside it**.
That is deliberate: the box has 3814 MB and no swap, so peak RAM has to be
`max(ingest, embed)` and not their sum. Loading ~550 MiB of model weights into the ingest
process — which already holds every fetched posting in memory — is how the OOM killer gets to
choose between Postgres and the export.

Three properties, each of which is the difference between this being safe to schedule and not:

**Resumable and idempotent.** Work is selected by `embedding is null or embedding_model <>
current`, so a run killed half way simply resumes, and a completed run is a cheap no-op. There
is no cursor to persist and no "where did it get to" state to get wrong.

**A model change is a first-class case, not a manual chore.** `embedding_model` carries the
library version (see `service/embed.EMBEDDING_MODEL_ID`), so bumping fastembed makes every row
stale and this backfill re-embeds them. Without that, a pooling change would leave two
incomparable vector spaces in one column with cosine distance quietly comparing across them.

**It can fail without costing anyone a digest.** Nothing on the delivery path reads
`embedding`; a null one falls back to the existing `first_seen_at` ordering. So this exits
non-zero on a real failure and the digest still goes out.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2.extras  # noqa: E402

from service import embed, store  # noqa: E402

logger = logging.getLogger("service.backfill_embeddings")

#: Rows fetched and embedded per transaction. Bounds both the memory held at once and how much
#: work a kill loses. 200 x ~1 KB of text is trivial; the model's own batch is `embed.BATCH_SIZE`.
BATCH = int(os.environ.get("EMBED_BACKFILL_BATCH", "200"))

_SELECT_POSTINGS = """
    select posting_id, title, company, location, description
      from postings
     where is_active
       and (embedding is null or embedding_model is distinct from %s)
     order by first_seen_at desc
     limit %s
"""

# Ordered newest-first so that if this is interrupted — or the budget runs out — what got
# embedded is the inventory a subscriber is most likely to be shown, not an arbitrary slice.

_UPDATE_POSTINGS = """
    update postings as p set embedding = v.embedding::halfvec,
                             embedding_model = v.model,
                             embedded_at = now()
      from (values %s) as v(posting_id, embedding, model)
     where p.posting_id = v.posting_id
"""


def _pending_postings() -> int:
    with store.cursor() as cur:
        cur.execute("select count(*) as n from postings where is_active "
                    "and (embedding is null or embedding_model is distinct from %s)",
                    (embed.EMBEDDING_MODEL_ID,))
        return int(cur.fetchone()["n"])


def _embed_postings(limit: int | None, batch: int, dry_run: bool) -> tuple[int, float]:
    """Embed pending postings, newest first, committing each batch.

    The loop's exit condition is "the select returned nothing", which only becomes true because
    each batch *writes* — the rows it embedded no longer match the predicate. That makes the
    dry-run path structurally different rather than a flag on the same loop: writing nothing
    means the same rows are selected for ever. This was an infinite loop until a test caught it,
    which is the argument for the count-and-sample shape below rather than a `continue`.
    """
    started = time.time()
    if dry_run:
        pending = _pending_postings()
        with store.cursor() as cur:
            cur.execute(_SELECT_POSTINGS, (embed.EMBEDDING_MODEL_ID, min(batch, 3)))
            sample = [dict(r) for r in cur.fetchall()]
        planned = pending if limit is None else min(pending, limit)
        logger.info("[dry run] %d postings pending, would embed %d. No model loaded, "
                    "nothing written.", pending, planned)
        for r in sample:
            text = embed.posting_text(r)
            logger.info("[dry run]   %s -> %d chars: %.90s", r["posting_id"], len(text), text)
        return planned, time.time() - started

    done = 0
    while limit is None or done < limit:
        take = batch if limit is None else min(batch, limit - done)
        with store.cursor() as cur:
            cur.execute(_SELECT_POSTINGS, (embed.EMBEDDING_MODEL_ID, take))
            rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            break

        vectors = embed.embed_texts([embed.posting_text(r) for r in rows])
        values = [(r["posting_id"], embed.to_pgvector(v), embed.EMBEDDING_MODEL_ID)
                  for r, v in zip(rows, vectors)]
        with store.cursor(commit=True) as cur:
            psycopg2.extras.execute_values(cur, _UPDATE_POSTINGS, values,
                                           template="(%s,%s,%s)", page_size=batch)
        done += len(rows)
        rate = done / max(time.time() - started, 1e-6)
        logger.info("embedded %d postings (%.1f/s)", done, rate)

    return done, time.time() - started


def _embed_profiles(dry_run: bool) -> int:
    """Profiles are embedded here too, not in the webapp on save.

    Keeping the model out of the always-on `api` container is what stops ~550 MiB of weights
    competing with Postgres for RAM. A profile saved during the day gets its vector on the next
    nightly run, which is soon enough for a daily digest — and until then it simply has none,
    which the caller treats like any other missing vector.
    """
    with store.cursor() as cur:
        cur.execute(
            "select id::text as id, label, role_categories, stack, sectors, education_field, "
            "       cv_summary "
            "  from profiles "
            " where status = 'active' "
            "   and (embedding is null or embedding_model is distinct from %s)",
            (embed.EMBEDDING_MODEL_ID,))
        rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return 0
    if dry_run:
        logger.info("[dry run] would embed %d profiles", len(rows))
        return len(rows)

    vectors = embed.embed_texts([embed.profile_text(r) for r in rows])
    with store.cursor(commit=True) as cur:
        psycopg2.extras.execute_values(
            cur,
            "update profiles as p set embedding = v.embedding::halfvec, "
            "       embedding_model = v.model, embedded_at = now() "
            "  from (values %s) as v(id, embedding, model) where p.id = v.id::uuid",
            [(r["id"], embed.to_pgvector(v), embed.EMBEDDING_MODEL_ID)
             for r, v in zip(rows, vectors)],
            template="(%s,%s,%s)")
    logger.info("embedded %d profiles", len(rows))
    return len(rows)


def run(limit: int | None = None, batch: int = BATCH, dry_run: bool = False) -> dict:
    logger.info("model %s, desc budget %d chars, batch %d",
                embed.EMBEDDING_MODEL_ID, embed.DESC_CHARS, batch)
    n_post, elapsed = _embed_postings(limit, batch, dry_run)
    n_prof = _embed_profiles(dry_run)

    with store.cursor() as cur:
        cur.execute(
            "select count(*) as active, "
            "       count(embedding) as vectored, "
            "       count(*) filter (where embedding is null) as missing, "
            "       count(*) filter (where coalesce(description,'') = '') as no_description "
            "  from postings where is_active")
        s = dict(cur.fetchone())

    logger.info("postings embedded this run: %d in %.0fs (%.1f/s)",
                n_post, elapsed, n_post / max(elapsed, 1e-6))
    logger.info("coverage: %d of %d active postings have a vector, %d still missing "
                "(%d of those carry no description at all)",
                s["vectored"], s["active"], s["missing"], s["no_description"])
    return {"postings": n_post, "profiles": n_prof, **s}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, help="stop after N postings (measurement / smoke test)")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--dry-run", action="store_true",
                    help="select and build the text, but load no model and write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(limit=args.limit, batch=args.batch, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
