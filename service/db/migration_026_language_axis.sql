-- Migration 026 — the language a posting is written in, and the languages a subscriber reads.
--
-- The sixth axis. Triggered 2026-08-20: a Russian-language Data Analyst ad reached the owner's
-- /matches, and nothing anywhere read the language of a posting's text. Everything migration 025
-- (experience) and 014 (education) said about the *shape* of a null-defers axis applies here too
-- — SQL cannot fill the posting column (it needs the detector in service/language.py), NULL means
-- "we could not read it" and passes every gate, and the classifier's only safe error is a miss.
--
--   postings.language              ISO-639-1 code detected from the description, or NULL. NULL for
--                                  short/ambiguous text — most of the CZ/SK title-only corpus and
--                                  every genuinely bilingual ad — and NULL always passes the gate.
--                                  Distinct from profiles.language below: this is the *content*
--                                  language of the ad, that is the subscriber's *UI/email* locale.
--                                  Two different questions on two different tables — do not conflate.
--
--   profiles.understood_languages  the languages the subscriber can read, as ISO-639-1 codes.
--                                  Empty = no preference = NO FILTER (widen). Deliberately empty by
--                                  default and NOT seeded from anything: every existing subscriber
--                                  is migrated into the no-filter state, so nobody's digest narrows
--                                  without them choosing it — the same rule as education_levels and
--                                  work_modes. Subscribers opt in via the profile form.
--
-- The gate (service/language.py:language_predicate) refuses exactly one thing: a posting
-- CONFIDENTLY detected in a NON-English language the subscriber did NOT declare. English always
-- passes and a NULL language always passes — see the module docstring for why English is never a
-- reason to refuse anything.
--
-- Backfill: python -m service.backfill_language (idempotent; re-run after any threshold change).
-- Idempotent, like every migration here: safe to re-run.

alter table postings add column if not exists language text;

-- Partial, like every nullable-axis index here: the interesting rows are the ones with a value,
-- and the null-majority path never needs it.
create index if not exists idx_postings_language
    on postings (language) where language is not null;

alter table profiles
    add column if not exists understood_languages text[] not null default '{}';
