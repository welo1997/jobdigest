-- migration 024: on-demand "run my digest now" cooldown.
--
-- Adds a per-profile timestamp used only to rate-limit how often a subscriber may trigger
-- their own metered match+send (POST /digest/run). Each on-demand run costs a real Anthropic
-- API call, so without a cooldown a subscriber holding the refresh button would run up cost
-- and hammer the mail provider. `claim_ondemand_run` claims this slot atomically (one UPDATE,
-- no check-then-act race), exactly like `manage_link_sent_at` gates POST /manage-link.
--
-- Deliberately separate from `last_digest_at`: that column tracks the daily cadence (and is
-- moved by the on-demand send too, so an on-demand run can't double-send the same day), while
-- this one bounds *how often the button works*. The two answer different questions.
--
-- Not personal data beyond the subscription it belongs to: deleted with the profile on
-- unsubscribe + 30-day prune, same as every other column here.
--
-- Idempotent.

alter table profiles
  add column if not exists last_ondemand_at timestamptz;
