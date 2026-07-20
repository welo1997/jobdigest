-- Migration 005 — first-party, cookieless product analytics.
--
-- Purpose: understand where people struggle in the signup flow so the UI can be improved.
-- Deliberately privacy-minimal so the site stays consent-banner-free under GDPR
-- (lawful basis: legitimate interest in improving our own service):
--
--   * No cookies. `session_id` is a random value held in sessionStorage that dies when the
--     tab closes. It is not a persistent identifier and cannot follow a user across visits.
--   * No IP address is ever stored — not raw, not hashed. Country comes from Cloudflare's
--     CF-IPCountry header and is the only location signal kept.
--   * No user-agent string. Only a coarse browser family (chrome/firefox/safari/other).
--   * No free text from users. `props` carries small enum-ish detail (e.g. a parse-failure
--     reason), never CV content, email addresses, or search terms.
--   * profile_id is set only on token-authenticated pages, and is NULLed (not deleted) if
--     the profile goes away — the event survives as an anonymous aggregate data point,
--     which is what a GDPR erasure request should leave behind.
--
-- Retention: raw rows are pruned by service.pipeline (RETENTION_EVENT_DAYS, default 180).

create table if not exists events (
    id          bigserial primary key,
    name        text not null,                  -- whitelisted event name, see webapp.EVENT_NAMES
    occurred_at timestamptz not null default now(),
    session_id  text,                           -- random, sessionStorage-scoped; NOT a cookie
    path        text,                           -- page path only, query string stripped
    country     varchar(2),                     -- CF-IPCountry, country granularity only
    browser     text,                           -- family only: chrome | firefox | safari | edge | other
    props       jsonb not null default '{}'::jsonb,
    profile_id  uuid references profiles(id) on delete set null
);

-- Funnel queries are "count of event X per day", so (name, occurred_at) is the workhorse.
create index if not exists idx_events_name_time on events (name, occurred_at desc);
-- Retention pruning and time-range scans.
create index if not exists idx_events_time      on events (occurred_at);
-- Per-session funnel reconstruction (did this session start the form then drop off?).
create index if not exists idx_events_session   on events (session_id) where session_id is not null;

-- Daily rollup: survives retention pruning so long-run trends outlive the raw rows.
create table if not exists events_daily (
    day        date not null,
    name       text not null,
    country    varchar(2),
    n          int  not null,
    primary key (day, name, country)
);
