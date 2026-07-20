-- Migration 002 — pivot to email-first, token-based profiles + digest delivery tables.
-- Idempotent: safe to apply to the running Phase-1 database and to re-run.
-- Apply:  psql "$DATABASE_URL" -f service/db/migration_002_email_digest.sql

-- --- profiles: become an email subscription, no login required --------------
alter table profiles alter column user_id drop not null;         -- auth optional in v1

alter table profiles add column if not exists email          text;
alter table profiles add column if not exists status         text not null default 'pending';
    -- pending -> active -> paused | unsubscribed | bounced
alter table profiles add column if not exists frequency      text not null default 'daily';
    -- daily | weekdays | weekly
alter table profiles add column if not exists confirm_token  text;
alter table profiles add column if not exists manage_token   text;
alter table profiles add column if not exists consent_at     timestamptz;
alter table profiles add column if not exists confirmed_at   timestamptz;
alter table profiles add column if not exists last_digest_at timestamptz;
alter table profiles add column if not exists paused_until   timestamptz;
alter table profiles add column if not exists unsubscribed_at timestamptz;

create index if not exists idx_profiles_email        on profiles (lower(email));
create unique index if not exists uq_profiles_confirm_token on profiles (confirm_token) where confirm_token is not null;
create unique index if not exists uq_profiles_manage_token  on profiles (manage_token)  where manage_token  is not null;
-- profiles ready to receive a digest right now
create index if not exists idx_profiles_sendable
    on profiles (status, last_digest_at) where status = 'active';

-- --- digest_sends: every job we've emailed a profile, so we never repeat -----
create table if not exists digest_sends (
    profile_id  uuid not null references profiles(id) on delete cascade,
    posting_id  text not null references postings(posting_id) on delete cascade,
    score       int,
    sent_at     timestamptz not null default now(),
    primary key (profile_id, posting_id)
);
create index if not exists idx_digest_sends_profile on digest_sends (profile_id, sent_at desc);

-- --- suppression: emails we must never contact again (unsub / bounce) --------
create table if not exists suppression (
    email       text primary key,              -- stored lowercased
    reason      text not null,                 -- unsub | bounce | complaint
    created_at  timestamptz not null default now()
);
