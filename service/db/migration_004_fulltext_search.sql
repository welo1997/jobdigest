-- Migration 004: recall-first keyword retrieval for the AI matcher.
--
-- The matcher works retrieve → rerank: a cheap SQL shortlist per subscriber, then an
-- AI pass that reads the shortlist in context and picks the best fits. This adds the
-- full-text surface the shortlist needs.
--
-- `search_tsv` is a generated tsvector over title + company + description, built with
-- the **'simple'** dictionary on purpose: no stemming and no English stopword removal,
-- so Czech/Slovak titles ("Vývojář", "Obchodní zástupce") tokenise cleanly and match
-- keyword terms exactly. Stored + GIN-indexed so the shortlist query stays cheap at
-- 20k+ active rows.

alter table postings
    add column if not exists search_tsv tsvector
    generated always as (
        to_tsvector(
            'simple',
            coalesce(title, '') || ' ' ||
            coalesce(company, '') || ' ' ||
            coalesce(description, '')
        )
    ) stored;

create index if not exists idx_postings_search_tsv on postings using gin (search_tsv);
