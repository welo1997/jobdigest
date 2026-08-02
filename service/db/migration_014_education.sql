-- Migration 014: education requirements as a fourth preference axis.
--
-- The problem: a subscriber whose highest qualification is a high-school diploma or an
-- apprenticeship has no way to say so, and is emailed roles that open with "Bachelor's degree
-- in Computer Science required". There is no column that knows, so the AI matcher is the only
-- thing that could ever have caught it, and it only sees what the shortlist hands it.
--
-- Adds one column on the posting side and two on the profile side:
--
--   postings.education_min     secondary | vocational | bachelor | master | doctorate | null.
--                              The *lowest* qualification the ad demands. **Null means the ad
--                              never said, and it is overwhelmingly the common case** — see
--                              the numbers below. Not folded into any level: the gate keeps
--                              nulls and hands the preference to the AI matcher, exactly as
--                              migration 012 does for `work_mode` and 010 for an unresolved
--                              city. Written by service/education.classify_requirement.
--   profiles.education_levels  which requirement levels the subscriber will accept. Defaults
--                              to all five, so this migration changes nobody's digest until
--                              they edit their preferences.
--   profiles.education_field   free-text field of study ("Economics"). **Never filtered on.**
--                              Whether a degree is "in a related field" is a judgement, not a
--                              predicate; it travels to the matcher as text and stops there.
--
-- **Know what this can and cannot do before extending it.** Measured against production on
-- 2026-08-02, across 20 763 active postings:
--
--     postings stating any degree requirement        952   (4.6%)
--     ... of which soften it ("or equivalent", ...)  286
--     binding, readable requirement                 ~666   (3.2%)
--
-- and the limit is not employer silence — it is that **70% of the corpus has no description
-- to read**. Median description length is 35 characters. `jobscz` (9 639 active postings, the
-- largest source) averages 32 and stores scraps like "70 000 – 80 000 Kč"; `profesia` (4 365)
-- stores the empty string; `cocuma` (319) averages 9. So the entire Czech and Slovak
-- inventory — 14 323 postings — will hold `education_min = null` forever, and every value this
-- column ever carries comes from the international sources. That is a real feature for
-- subscribers reading English-language ATS postings and **nothing at all** for someone
-- searching only Czech boards. The UI copy has to say so.
--
-- No SQL backfill is possible: deciding a requirement needs the pattern tables in
-- service/education.py, which SQL cannot see — the same reason migration 012 could not fill
-- `work_mode` and 010 could not fill `city`. **Run `python -m service.backfill_education`
-- after applying this**, or every posting stays null and the new filter has nothing to act on.

alter table postings add column if not exists education_min text;

alter table profiles add column if not exists education_levels text[] not null
    default '{secondary,vocational,bachelor,master,doctorate}';

alter table profiles add column if not exists education_field text;

-- Only the rows the gate can act on are worth indexing, and they are ~3% of the table.
-- A partial index keeps it small and keeps the null-majority path on the existing scans.
create index if not exists idx_postings_education
    on postings (education_min) where education_min is not null;
