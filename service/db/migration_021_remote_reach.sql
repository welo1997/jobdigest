-- Migration 021 — how far a fully-remote role reaches.
--
-- `remote_signal` / `work_mode` answer "does this job happen in an office". They have never
-- answered the question subscribers actually read into the word remote: **and where am I allowed
-- to live while doing it.** Most work-from-home roles are bound to one country because that is
-- where the employer runs payroll; a minority are genuinely location-independent. The /jobs
-- "Remote" filter meant the first and was read as the second.
--
-- Two columns, and the split matters:
--
--   scope_raw     the board's own scope field, verbatim. A *claim*, exactly like `remote_signal`:
--                 WeWorkRemotely's `region` ("Anywhere in the World"), Himalayas'
--                 `locationRestrictions` + `timezoneRestrictions` ("United States | UTC-10..UTC-5"),
--                 Ashby's `secondaryLocations`, Greenhouse's `offices`. Three adapters were
--                 fetching theirs and discarding it. This column is what lets
--                 `backfill_remote_reach` re-derive the verdict after a classifier change without
--                 re-ingesting every source — the same reason `location` is stored rather than
--                 only its resolved country.
--
--   remote_reach  the derived verdict: anywhere | region | country | NULL.
--                 One definition, `geo.remote_reach`, mirrored in web/lib/geo.ts and drift-tested.
--
-- **NULL is 22% of active remote rows and always passes every gate.** Same rule as `work_mode`,
-- `education_min` and an unresolved city: the posting never said, a guess is a promise nobody
-- made, and the AI matcher reads the whole description far better than a pattern list can. A
-- display filter is equality so NULL is excluded there; the digest path must never gate on this
-- column in SQL.
--
-- Deliberately **no backfill of `scope_raw`** — it can only come from a source payload, so it
-- fills in over the staleness window as each adapter re-ingests (the same shape as
-- `source_category` in migration 018). `remote_reach` *is* backfillable from the stored
-- `location` and `description` today, which recovers 77% of the corpus immediately; the rest
-- arrives with the next ingest.
--
-- Measured on the live corpus 2026-08-14 (stratified sample, 3 593 active fully-remote rows):
--   country   71.6%   region 5.3%   anywhere 0.75%   NULL 22.3%
-- The headline is not the coverage, it is the distribution: "remote" almost always means work
-- from home in one named country, and genuinely location-independent work is under 1% of it.

alter table postings add column if not exists scope_raw    text;
alter table postings add column if not exists remote_reach text;

-- Partial: the column is only ever set for fully-remote rows (reach of an on-site Berlin job is a
-- category error), and every query that uses it is already filtering on remote.
create index if not exists idx_postings_remote_reach
    on postings (remote_reach)
    where is_active and remote_reach is not null;
