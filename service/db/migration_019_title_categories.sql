-- Migration 019: a memoised title -> role_category answer from the claude.ai routine, for
-- the residue that neither the patterns nor the publishers' occupation codes reach.
--
-- **The cache is not an optimisation, it is the design.** Classification happens at extract
-- (`service.ingest.build_row`), and ingest re-fetches every active posting every day —
-- ~101k rows, of which only ~2 400 are genuinely new (measured 2026-08-09). `upsert_postings`
-- rewrites `role_category` on conflict, which is exactly why a classifier change needs no
-- backfill; it also means asking the routine about every posting would be 101k titles a day
-- instead of 2 400. Keyed on the *normalised* title so the answer survives the posting: the
-- same title is re-advertised by the same employer for months, and by others for years.
--
-- Normalisation has ONE definition, `service.categorize_exchange.normalise_title`, and it is
-- deliberately not expressed in SQL. A `lower(btrim(regexp_replace(...)))` here would be a
-- second copy of it, and the two would drift the way `geo.py` and `geo.ts` would without the
-- drift test — except silently, as a cache that stops hitting. The queries stay trivial and
-- Python does the grouping.
--
-- **`category = 'uncategorised'` is a recorded decline, not a failure.** A title the routine
-- could not read must still be written here, or the export asks about it again every single
-- run, forever. `title_category_map()` filters those out for the ingest path, where they mean
-- exactly what no row means; `asked_title_keys()` keeps them, because "have we asked?" and
-- "do we know?" are different questions.
--
-- No personal data: a public job title and a category from a fixed vocabulary. Nothing here
-- references a subscriber, so nothing here needs erasing under the 30-day promise, and the
-- privacy policy is unchanged (security rule 4 — this adds no new *category* of stored data,
-- and the exported file carries job titles and nothing else).
--
-- Idempotent, and applied by hand before the code that reads it — the deploy runs no
-- migrations.

create table if not exists title_categories (
    title_key    text primary key,                     -- normalise_title(title)
    category     text not null,                        -- must be a value in taxonomy.CATEGORIES
    sample_title text not null,                        -- one real title, so a human can audit
    source       text not null default 'routine',      -- who answered: routine | manual
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

-- The ingest read is "every key we actually know", so the index that matters is the one that
-- lets `category <> 'uncategorised'` skip the declines rather than scan them.
create index if not exists idx_title_categories_category on title_categories (category);
