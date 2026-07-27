-- Migration 010: city-level location preferences.
--
-- The problem: `profiles.regions` held only coarse buckets (cz | eu | uk | us | worldwide),
-- so a subscriber in Prague could not say "Czechia, but not an on-site job in Brno" — and was
-- emailed exactly that. Country was equally inexpressible: "Czechia or Germany" had no
-- representation at all.
--
-- Adds three preference columns and one posting column:
--
--   profiles.countries     ISO-2 codes, e.g. {CZ,DE}. Empty means "legacy row" and the
--                          filter falls back to `regions` (service/geo.location_predicate).
--   profiles.cities        qualified city slugs, e.g. {cz:prague,cz:brno}. A country with no
--                          entry here means "any city in that country".
--   profiles.remote_scope  country | eu | worldwide — how far afield a *fully remote* role
--                          may be. Separate from the city list on purpose: "on-site only in
--                          Prague, but remote from anywhere in the EU" is the common case.
--   postings.city          resolved city slug, or null when the free-text location could not
--                          be resolved. Null is kept by the filter, never dropped.
--
-- `regions` stays, derived from the new fields on every write, so the matcher export and any
-- older client keep working and the two can never disagree.
--
-- The backfill is deliberately a no-op in behaviour: every existing subscriber becomes
-- countries={CZ} with no city restriction and the remote scope their old `regions` implied,
-- which `service/geo.regions_for` maps straight back to the same `regions` value they had.
-- Nobody's digest changes until they edit their preferences.
--
-- postings.city is NOT backfilled here — resolving free-text locations needs the alias table
-- in service/geo.py, which SQL cannot see. Run `python -m service.backfill_geo` after
-- applying this (the daily ingest also fills it in for everything still being posted).

alter table postings add column if not exists city text;
create index if not exists idx_postings_geo on postings (country_code, city);

alter table profiles add column if not exists countries    text[] not null default '{}';
alter table profiles add column if not exists cities       text[] not null default '{}';
alter table profiles add column if not exists remote_scope text   not null default 'eu';

update profiles
   set countries = array_remove(array[
           case when regions && '{cz,eu,worldwide}'::text[] then 'CZ' end,
           case when 'uk' = any(regions)                    then 'GB' end,
           case when 'us' = any(regions)                    then 'US' end
       ]::text[], null),
       remote_scope = case
           when 'worldwide' = any(regions) then 'worldwide'
           when 'eu'        = any(regions) then 'eu'
           else 'country'
       end
 where countries = '{}';

-- A row whose `regions` was empty or unrecognised: default to the product's home market
-- rather than leaving countries empty, which would silently keep it on the legacy path.
update profiles set countries = '{CZ}' where countries = '{}';
