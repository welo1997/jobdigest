-- Migration 023 — the other countries a posting names.
--
-- `postings.country_code` is one country. A posting is routinely open in several, and
-- `geo.resolve_location` has to pick one of them to be *the* country because `city` has to agree
-- with it. Every other country the posting named was then unreachable: not by the Country filter,
-- not by the digest's `location_predicate`, not by anything. The Country menu is the control that
-- answers "is there a job for me in X", and for a multi-country posting it was answering wrongly
-- for all but one X.
--
-- Measured against live boards on 2026-08-15, after wiring the four ATS adapters' `locations[]`
-- arrays into `scope_raw` (they had been reading only the first entry):
--
--     teamtailor   32 of 174 postings name >1 location, 19 of them >1 country
--     lever        32 of 349 name >1 location (mostly office pairs), 4 remote and >1 country
--     recruitee    12 of 106 name >1 location, 3 remote and >1 country
--     workable      1 of 427 — but that one is open in ES, CZ, GB, IE, PT and NL, stored as ES
--
-- One Printful posting lists twelve countries and was stored as Barcelona alone, so eleven
-- country filters answered it with silence.
--
-- **Not gated on the posting being remote**, and that is the difference between this column and
-- the two before it. `remote_reach` (021) and `reach_areas` (022) answer "where may I live while
-- holding this", which is a category error for an on-site job. This one answers "can I hold this
-- job in country X", and two offices in two countries is an ordinary way for that to be yes in
-- both. Same question as the Country filter, same control.
--
-- Null unless the posting names **two or more** countries: one country is what `country_code`
-- already says, and a column repeating another one only invites the two to disagree. Same rule,
-- and same reason, as `reach_areas` being null for a single-country scope.
--
-- Fields only — `geo.reach_countries` reads `scope_raw` and `location`, never the description.
-- Reach classification reads prose through a keyhole because it is unreliable there (`anywhere`
-- from body copy was right once in three); minting a *country filter entry* from prose would be a
-- stronger claim on the same weak evidence — an employer's head office read as a hiring country.
--
-- Backfill: `python -m service.backfill_remote_reach` fills this column too. It derives from the
-- stored `scope_raw` / `location`, so no re-ingest is needed for rows that already carry a scope —
-- but the four ATS adapters only began sending `scope_raw` today, so their share of this column
-- fills in over the staleness window, not at backfill time. Their by-source count in the
-- backfill's own report is the check.

alter table postings add column if not exists reach_countries text[];

-- GIN over the array elements, so `reach_countries && array['pl']` is index-backed alongside
-- `idx_postings_geo`. Null arrays add no entries, so this stays proportional to the postings that
-- actually name more than one country rather than to the whole table.
create index if not exists idx_postings_reach_countries
    on postings using gin (reach_countries);
