-- Migration 022 — which named areas a multi-country remote scope reaches.
--
-- `remote_reach` (migration 021) measures the *breadth* of a scope: anywhere / region / country.
-- It cannot answer the question `/jobs` actually asks — "could I, here, hold this job" — because
-- `region` covers "Europe", "APAC" and "US or Canada" alike and only one of those is any use to
-- someone in Prague. Measured over the 961 multi-country remote postings on 2026-08-14:
--
--     EEA-inclusive        568   ("Europe" 119, "EMEA" 60, "CET +/-3", "Anywhere in FR/BE/ES")
--     US/CA-inclusive      403   ("Remote, Canada; Remote, US", "North America", "AMER")
--       of which both      150   <- the only set where someone in the EEA can hold a US role
--     EEA only, closed to NA    418
--     NA only, closed to Europeans  253
--     neither (APAC / LATAM / Middle East only)  114
--
-- So the two areas **overlap by design** and the overlap is the interesting part. `reach_areas` is
-- a `text[]` for that reason — a single-valued column would have to pick one and lose the 150.
-- Same shape as `skills` (migration 020): GIN-indexed, array-containment filtered, and
-- null/empty passes every filter rather than being hidden.
--
-- Only populated for `remote_reach in ('anywhere','region')`. A single-country scope is answered
-- by the country filter itself — a Germany-remote job belongs under Germany, not under an
-- international heading — so `country` and unknown reaches store null.
--
-- One definition: `geo.classify_reach` writes both columns from one pass over the same text, so a
-- posting whose scope field says "EMEA" and whose location says "APAC" cannot end up `region` from
-- one and filed under the other continent from the other.
--
-- `eea` means `geo.EEA_COUNTRIES`, **not** `geo.COUNTRIES`. GB is selectable and is not in the EEA,
-- so "Australia, Canada, New Zealand, United Kingdom, United States" is `na` alone. Merging those
-- two sets here would quietly tell a Czech visitor a UK-only role is open to them.
--
-- No backfill of source data is needed — `geo.classify_reach` reads the stored `scope_raw` /
-- `location` / `description`, so `python -m service.backfill_remote_reach` fills both columns.

alter table postings add column if not exists reach_areas text[];

-- GIN over the array elements, so `reach_areas && array['eea']` is index-backed and the facet
-- counts can unnest. Null/empty arrays add no entries, so it stays proportional to the ~1 000
-- postings that actually carry a multi-country scope rather than to the whole table.
create index if not exists idx_postings_reach_areas on postings using gin (reach_areas);
