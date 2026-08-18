-- Migration 025 — the minimum years of experience a posting demands.
--
-- `postings.experience_min` is the fifth axis, and everything migration 014 said about the
-- fourth (`education_min`) applies verbatim: SQL cannot fill it (deciding whether "25 years of
-- experience" is a requirement on the applicant or the employer's anniversary needs the pattern
-- tables in `service/experience.py`), NULL means "the ad never said" and passes every gate, and
-- the classifier's only safe error is a miss.
--
-- The subscriber side already exists: `profiles.years_experience` (migration 003, typed on the
-- form or detected from a CV). The gate is `experience.experience_predicate` — a posting
-- demanding more years than the subscriber has is excluded, a null posting or a silent profile
-- filters nothing.
--
-- Measured on the live corpus 2026-08-18 (129 226 active rows): 34.7% carry a number+years
-- shape in their description; on a 400-row random sample of those, 80% state a genuine binding
-- tenure requirement — so expect roughly a quarter of active postings to get a value, an order
-- of magnitude more than education's 3.2%. The mode sits at 3 and 5 years.
--
-- Backfill: python -m service.backfill_experience (idempotent; re-run after any pattern change).

alter table postings add column if not exists experience_min smallint;

-- Partial, like every nullable-axis index here: the interesting rows are the ones with a value.
create index if not exists idx_postings_experience
    on postings (experience_min) where experience_min is not null;
