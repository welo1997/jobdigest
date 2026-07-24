-- migration 009 — Google-verified signup intents.
--
-- Bridges the gap between "Google confirmed this email" and "the user finished the preference
-- wizard and pressed subscribe". A short-lived server-side row proves the address was
-- Google-verified so the wizard can create an ACTIVE subscription without the usual
-- double-opt-in confirm email — Google already proved the person controls the inbox.
--
-- Security / privacy:
--   * `id` is the SHA-256 of the raw cookie token (never the token), like `sessions`.
--   * Holds the verified email for at most ~30 minutes, then it's pruned. It is the signup
--     process itself, not stored profile data; it carries no preferences.
--   * Single-use in practice: `consume_signup_intent` deletes the row as it reads it.
create table if not exists signup_intents (
    id          text primary key,                    -- sha256(raw cookie token), hex
    email       text not null,                        -- the Google-verified address
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null
);

create index if not exists idx_signup_intents_expires on signup_intents (expires_at);
