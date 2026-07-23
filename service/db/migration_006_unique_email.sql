-- migration 006: one live subscription per email address.
--
-- Before this, `profiles.email` was only indexed, not unique — so re-submitting the signup
-- form with an already-subscribed address created a second `pending` row, and confirming it
-- produced TWO active rows for one address, i.e. the digest sent twice. (Observed 2026-07-23
-- while testing the instant preview with a live address.)
--
-- The index is PARTIAL: `unsubscribed` rows are excluded, so a genuine re-subscribe still
-- works once the address is cleared from the suppression list. `pending`, `active` and
-- `paused` are all "live" and must be unique per address.
--
-- Idempotent. Requires no live duplicates to exist first (verified none did before applying):
--   select lower(email), count(*) from profiles
--   where status <> 'unsubscribed' and email is not null group by 1 having count(*) > 1;

create unique index if not exists uq_profiles_live_email
  on profiles (lower(email))
  where status <> 'unsubscribed';
