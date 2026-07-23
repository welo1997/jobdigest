-- migration 007: passwordless "email me my settings link" recovery path.
--
-- Adds a per-profile timestamp used only to rate-limit how often a subscriber's own manage
-- link may be re-emailed to them (POST /manage-link). Without it the endpoint could be used
-- to flood a real subscriber's inbox: knowing an address, an attacker could ask for its
-- settings link over and over. With it, at most one such email goes out per cooldown window,
-- and it always goes to the address's own inbox (the link is the credential).
--
-- Not personal data beyond the subscription it belongs to: it is deleted with the profile on
-- unsubscribe + 30-day prune, same as every other column here.
--
-- Idempotent.

alter table profiles
  add column if not exists manage_link_sent_at timestamptz;
