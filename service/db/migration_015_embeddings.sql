-- Migration 015: vector embeddings, so ranking stops being a keyword problem.
--
-- WHAT THIS IS FOR. Today `query_shortlist_meta` ranks by `first_seen_at desc` and admits rows
-- via `role_category = any(...) OR search_tsv @@ plainto_tsquery(...)`. That has two costs the
-- taxonomy cannot fix. It reads English: measured 2026-08-07, `Systemutvikler`, `Dataingeniør`,
-- `Produktsjef`, `IT-arkitekt` and `Testleder` all classify `uncategorised`, and
-- `Sikkerhetsanalytiker` classifies as `data_analysis` -- wrong, it is a security analyst
-- matched on "analytiker". And it serves nobody whose field has no category: sales,
-- cybersecurity and IT support have none, which is what the `widened` retrieval floor exists to
-- paper over. Cosine similarity over multilingual embeddings answers both without teaching
-- `taxonomy.py` three more languages.
--
-- The second reason is cost, and it is the one that decides whether this product can grow.
-- Per-subscriber LLM matching is O(subscribers x candidates) -- ~263M tokens/day at 10k
-- subscribers, which no subscription plan covers. Ranking in SQL is O(1) LLM tokens, leaving
-- the budget for the part a subscriber actually reads: the reason line on the jobs they are
-- sent. See notes/ for the arithmetic.
--
-- WHY halfvec(384) AND NOT vector(768). 384 dims is the multilingual-E5-small class, which is
-- what fits: the box has 3814MB and NO SWAP, so a 2.2GB model (BGE-M3) is not a tradeoff, it is
-- an OOM with Postgres as the likely victim. `halfvec` is fp16 -- 74MB of vectors across 96,583
-- active postings against 148MB for `vector`, and the precision it drops is far below what
-- affects a ranking order. On a box this size, what competes for page cache matters.
--
-- WHY THE MODEL NAME IS STORED. `embedding_model` and `embedded_at` are not bookkeeping. Two
-- models produce vectors in unrelated spaces, so a model swap without a full re-embed leaves
-- the column meaning two things at once and cosine distance quietly comparing nonsense -- the
-- exact failure `backfill_geo` and `backfill_education` exist to prevent for their columns.
-- Changing the model means re-running the backfill, and this column is what makes a partial
-- re-embed visible instead of silent.
--
-- NULL IS ALWAYS VALID. ~5% of active postings carry no description, embedding runs on
-- subscription compute that can be exhausted, and the daily run can be skipped. So a null
-- embedding must never remove a posting from anyone's digest: the caller falls back to the
-- existing `first_seen_at` ordering for those rows. Same rule as `work_mode`, `education_min`
-- and an unresolved city -- unknown is kept and handed onward, never dropped.

create extension if not exists vector;

-- ---------------------------------------------------------------------------
-- postings: one embedding per posting, computed once when it is first seen.
-- ---------------------------------------------------------------------------
alter table postings
    add column if not exists embedding       halfvec(384),
    add column if not exists embedding_model text,
    add column if not exists embedded_at     timestamptz;

-- The backfill's own work queue: active rows still missing a vector. Partial, so it stays tiny
-- once the backfill has caught up -- ~8,000 new postings a day rather than 96,583 forever.
create index if not exists idx_postings_needs_embedding
    on postings (first_seen_at desc)
    where is_active and embedding is null;

-- Cosine, because the embedding models here are trained for cosine similarity and their output
-- is not unit-normalised in a way that makes L2 equivalent.
--
-- Built here rather than after the backfill on purpose: pgvector builds an HNSW index faster
-- over existing data, but a migration that leaves the index to a follow-up step is a migration
-- whose follow-up step gets forgotten, and then every shadow query is a sequential scan over
-- 96,583 rows and reads as "vectors are slow". Incremental maintenance across the backfill
-- costs a few minutes; a missing index costs the comparison its credibility.
--
-- Watch `maintenance_work_mem` if this is ever rebuilt from scratch: HNSW builds in memory, and
-- on a swapless 3814MB box a generous setting is how the OOM killer gets invited.
create index if not exists idx_postings_embedding_hnsw
    on postings using hnsw (embedding halfvec_cosine_ops);

-- ---------------------------------------------------------------------------
-- profiles: the query side of the same space.
-- ---------------------------------------------------------------------------
-- Deliberately no index: there are three subscribers today and this is looked up by primary
-- key, never searched. An HNSW index over a handful of rows is pure maintenance cost.
--
-- Computed by the pipeline, NOT by the webapp on save. Keeping the model out of the always-on
-- `api` container is what stops ~500MB of resident weights competing with Postgres; a profile
-- saved during the day gets its vector on the next nightly run, which is soon enough for a
-- daily digest.
alter table profiles
    add column if not exists embedding       halfvec(384),
    add column if not exists embedding_model text,
    add column if not exists embedded_at     timestamptz;

-- ---------------------------------------------------------------------------
-- shortlist_shadow: the new ranking, recorded beside the live one and emailed to nobody.
-- ---------------------------------------------------------------------------
-- The switch from keyword-and-recency to vector ranking is not a judgement call to make from a
-- design argument -- it decides what real people are shown. So the new path runs for a while
-- writing here, the old path keeps deciding digests, and the two get compared per subscriber.
-- `test_export_coverage_sql.py`'s sibling for this will pin that nothing reads this table on
-- the delivery path.
--
-- Keyed by day so a comparison can be run over a window rather than a single night, and
-- cascading on profile delete so the 30-day erasure promise needs no extra step (same
-- reasoning as digest_runs in migration 011). Holds no personal data beyond the profile
-- reference it is keyed on.
create table if not exists shortlist_shadow (
    day         date not null,
    profile_id  uuid not null references profiles(id) on delete cascade,
    posting_id  text not null references postings(posting_id) on delete cascade,
    rank        int  not null,
    similarity  real not null,
    -- Was this posting also in the live keyword shortlist that day? This is the whole point of
    -- the table: agreement is cheap to compute now and impossible to reconstruct later.
    in_live     boolean not null default false,
    recorded_at timestamptz not null default now(),
    primary key (day, profile_id, posting_id)
);

create index if not exists idx_shortlist_shadow_profile_day
    on shortlist_shadow (profile_id, day desc);
