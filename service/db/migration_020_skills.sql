-- Migration 020: skills as a structured, filterable facet.
--
-- The problem: skills are already *searchable* — `search_tsv` covers the description and both
-- `store.query_shortlist` and `/preview` match a subscriber's `stack` that way — but they are
-- not *facetable*. There is no column to build a "Python" filter chip from, to count how many
-- postings name a tool, or to show extracted skills on a card. This adds one.
--
-- **This repurposes the dead `skills jsonb` column, it does not add a new field.** That column
-- was declared for the enrichment plan's Haiku extraction ("phase 2") on the Snowflake side,
-- which is decommissioned (see CLAUDE.md). Nothing in the live path ever wrote or read it:
-- `store._UPSERT_SQL` never listed it, and no SELECT reads it — so every row holds the empty
-- default and dropping it loses nothing. We swap its type jsonb -> text[] because a facet wants
-- array containment (`skills && array['python']`) and `unnest`-based counts, which a GIN index
-- on text[] serves directly; jsonb would need a different operator class and awkward casts.
--
--   postings.skills   text[] | null. The canonical skills the ad names, written by
--                     service/skills.extract_skills (a deterministic gazetteer match, NOT AI).
--                     **Null means "no skill named / not classified", and it passes every
--                     gate** — the same null-passes rule as work_mode (012), education_min
--                     (014) and an unresolved city (010). A skills facet only ever *narrows*
--                     when a subscriber positively selects a skill; an empty/null list simply
--                     does not match that positive filter, it is never excluded by one. The
--                     matcher is deliberately untouched — it still matches skills via full-text.
--
-- No SQL backfill is possible: extracting skills needs the gazetteer + ambiguous-token guards
-- in service/skills.py, which SQL cannot see (same reason 012/014 could not backfill in SQL).
-- **Run `python -m service.backfill_skills` after applying this**, or existing rows stay null
-- and the facet has nothing to act on until each posting is next re-ingested.

-- Repurpose in place. The column is empty everywhere, so there is no data to migrate; a plain
-- type swap is the honest description of what happens. `if exists` keeps this safe to re-run
-- and safe on a fresh database where schema.sql already created it as text[].
alter table postings drop column if exists skills;
alter table postings add  column if not exists skills text[];

-- GIN over the array elements: what makes `skills && array['python']` and `array['python'] <@
-- skills` index-backed. Null and empty arrays produce no index entries, so the index stays
-- proportional to postings that actually name a skill — no partial predicate needed.
create index if not exists idx_postings_skills on postings using gin (skills);
