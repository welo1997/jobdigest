-- Migration 012: hybrid as a first-class work setup.
--
-- The problem: `postings.remote_signal` is a two-state boolean, so the model had exactly two
-- kinds of job — fully remote, and everything else. On 2026-07-28 that "everything else"
-- bucket was found to contain 5 218 postings that are neither: a Prague office job with two
-- days a week from home ("možnost občasné práce z domova", "hybrid work model of 3 days in
-- the office") had been *claiming* to be remote, was repaired to remote_signal=false, and now
-- sits indistinguishable from five-days-in-the-office work. The location gate treats those two
-- the same and should — a hybrid Brno role is still a commute to Brno — but a subscriber
-- cannot say "I'll take hybrid, not full-time office", and the digest cannot say which it is.
--
-- Adds one column on each side:
--
--   postings.work_mode    remote | hybrid | onsite | null. **Null means unknown and is the
--                         common case** — most postings never say. It is deliberately not
--                         folded into `onsite`: the filter keeps nulls and hands the
--                         preference to the AI matcher, exactly as an unresolved city is
--                         handled. See service/geo.work_mode.
--   profiles.work_modes   which of those a subscriber will accept. Defaults to all three, so
--                         this migration changes nobody's digest until they edit their
--                         preferences.
--
-- `remote_signal` stays, and stays authoritative for the location gate: it is now simply
-- `work_mode = 'remote'`, written by the same classifier in the same place. Keeping it means
-- the gate, the matcher export, the dbt models and every existing test keep working unchanged.
--
-- Only the remote rows can be backfilled in SQL. Deciding hybrid-vs-onsite-vs-unknown needs
-- the pattern tables in service/geo.py, which SQL cannot see — the same reason migration 010
-- could not fill `postings.city`. **Run `python -m service.backfill_geo` after applying this**,
-- or every non-remote posting stays null and the new filter has almost nothing to filter on.

alter table postings add column if not exists work_mode text;

alter table profiles add column if not exists work_modes text[] not null
    default '{onsite,hybrid,remote}';

-- Exact, not a guess: `remote_signal` is already `geo.is_fully_remote(...)`, which is
-- `work_mode(...) = 'remote'` by construction. Everything else is left null for the backfill.
update postings set work_mode = 'remote' where remote_signal and work_mode is null;
