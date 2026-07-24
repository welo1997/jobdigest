-- migration 008 — session-persisted magic links.
--
-- JobDigest stays passwordless: the manage_token magic link is still the only credential a
-- subscriber ever types. This table lets a browser *stay logged in* after clicking that link
-- once, so returning visits need no token in the URL and the site can show a real
-- "logged in / log out" affordance.
--
-- Security notes:
--   * `id` is the SHA-256 of the raw cookie token, never the token itself. A DB dump or
--     backup therefore contains no usable session credential (same reasoning as a password
--     hash) — which matters because backups already carry manage_token bearer credentials.
--   * Rows cascade on profile delete, so `prune_unsubscribed` erasing a subscription also
--     erases its sessions — the 30-day retention promise stays true without extra work.
--   * `expires_at` is an idle timeout, slid forward on each use (see store.session_profile).
create table if not exists sessions (
    id           text primary key,                    -- sha256(raw cookie token), hex
    profile_id   uuid not null references profiles(id) on delete cascade,
    created_at   timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    expires_at   timestamptz not null
);

create index if not exists idx_sessions_profile on sessions (profile_id);
create index if not exists idx_sessions_expires on sessions (expires_at);
