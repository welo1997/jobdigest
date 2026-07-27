-- Job/Freelance matching service — Postgres schema.
-- Works on local Postgres (docker) and Supabase unchanged.
-- Supabase note: user identity lives in auth.users; `profiles.user_id` references it
-- in production. Locally we allow any uuid so the API runs without Supabase auth.

create extension if not exists "uuid-ossp";
create extension if not exists pg_trgm;      -- fuzzy title search / dedup support

-- ---------------------------------------------------------------------------
-- postings: one enriched row per job, deduped on posting_id (md5 of url).
-- Classification columns are computed at ingest time so /matches is a cheap query.
-- ---------------------------------------------------------------------------
create table if not exists postings (
    posting_id     text primary key,               -- md5(url)
    source         text not null,
    title          text,
    company        text,
    url            text not null,
    description    text,
    location       text,
    country_code   varchar(2),
    city           text,                            -- resolved slug ('prague'), null = unknown
    remote_signal  boolean,                          -- true only when FULLY remote, not hybrid
    salary_raw     text,
    currency       text,
    posted_at      date,

    -- enrichment / classification (from the search_jobs classifiers)
    role_category  text,                            -- data_engineering | data_analysis | ...
    region         text,                            -- cz | eu | uk | us | worldwide | other
    eligibility    text,                            -- eligible | verify UK... | likely US... | blocked | unknown
    seniority      text,                            -- junior | mid | senior
    work_type      text,                            -- permanent | freelance/contract
    is_part_time   boolean default false,
    skills         jsonb default '[]'::jsonb,       -- Haiku-extracted, phase 2
    dedup_key      text,                            -- normalised title+company, for near-dup collapse

    -- lifecycle / freshness (solves the stale-posting problem at service scale)
    first_seen_at  timestamptz not null default now(),
    last_seen_at   timestamptz not null default now(),
    is_active      boolean not null default true,

    -- recall-first keyword surface for the AI matcher's shortlist (migration 004).
    -- 'simple' dictionary: no stemming/stopwords so CZ/SK titles tokenise cleanly.
    search_tsv     tsvector generated always as (
        to_tsvector('simple',
            coalesce(title, '') || ' ' || coalesce(company, '') || ' ' || coalesce(description, ''))
    ) stored
);

create index if not exists idx_postings_active     on postings (is_active, last_seen_at desc);
create index if not exists idx_postings_geo         on postings (country_code, city);
create index if not exists idx_postings_filters     on postings (role_category, region, seniority, work_type);
create index if not exists idx_postings_eligibility on postings (eligibility);
create index if not exists idx_postings_dedup       on postings (dedup_key);
create index if not exists idx_postings_title_trgm  on postings using gin (title gin_trgm_ops);
create index if not exists idx_postings_search_tsv  on postings using gin (search_tsv);

-- ---------------------------------------------------------------------------
-- profiles: an email subscription + fit criteria. Email-first / token-based —
-- no login required in v1 (user_id links to auth.users once Supabase auth lands).
-- ---------------------------------------------------------------------------
create table if not exists profiles (
    id              uuid primary key default uuid_generate_v4(),
    user_id         uuid,                           -- -> auth.users.id in Supabase (optional in v1)
    email           text,                           -- lowercased subscriber address
    label           text not null default 'My search',
    stack           text[]  not null default '{}',  -- ['dbt','snowflake','python','sql']
    seniorities     text[]  not null default '{junior,mid}',
    -- Location preferences (migration 010). `countries`/`cities`/`remote_scope` are what the
    -- filter reads; `regions` is derived from them on every write and kept only so the matcher
    -- export and older clients keep working. See service/geo.py for the one definition.
    countries       text[]  not null default '{}',  -- ISO-2, e.g. {CZ,DE}
    cities          text[]  not null default '{}',  -- {cz:prague}; no entry = any city there
    remote_scope    text    not null default 'eu',  -- country | eu | worldwide (fully remote)
    regions         text[]  not null default '{cz,eu,worldwide}',   -- derived, coarse
    role_categories text[]  not null default '{}',  -- empty = all data roles
    work_types      text[]  not null default '{permanent,freelance/contract}',
    part_time_only  boolean not null default false,
    eligible_only   boolean not null default true,  -- drop blocked / US-auth-gated
    sectors         text[]  not null default '{}',  -- soft boost: fintech, ecommerce, trading
    min_score       int     not null default 6,

    -- subscription lifecycle (double opt-in, token-based management, no password)
    status          text not null default 'pending', -- pending|active|paused|unsubscribed|bounced
    frequency       text not null default 'daily',    -- daily|weekdays|weekly
    confirm_token   text,
    manage_token    text,
    consent_at      timestamptz,
    confirmed_at    timestamptz,
    last_digest_at  timestamptz,
    paused_until    timestamptz,
    unsubscribed_at timestamptz,
    manage_link_sent_at timestamptz,   -- rate-limits the "email me my settings link" path (migration 007)

    -- CV-derived signals (parse & discard: the raw CV file is never stored)
    has_cv           boolean not null default false,
    cv_summary       text,                            -- short "Detected: ..." line
    years_experience int,

    created_at      timestamptz not null default now()
);

create index if not exists idx_profiles_user  on profiles (user_id);
create index if not exists idx_profiles_email on profiles (lower(email));
-- One live subscription per address: pending/active/paused are unique per email, unsubscribed
-- rows excluded so a genuine re-subscribe still works (see migration_006_unique_email.sql).
create unique index if not exists uq_profiles_live_email on profiles (lower(email)) where status <> 'unsubscribed';
create unique index if not exists uq_profiles_confirm_token on profiles (confirm_token) where confirm_token is not null;
create unique index if not exists uq_profiles_manage_token  on profiles (manage_token)  where manage_token  is not null;
create index if not exists idx_profiles_sendable on profiles (status, last_digest_at) where status = 'active';

-- ---------------------------------------------------------------------------
-- digest_sends: every job emailed to a profile, so a job is never repeated.
-- ---------------------------------------------------------------------------
create table if not exists digest_sends (
    profile_id  uuid not null references profiles(id) on delete cascade,
    posting_id  text not null references postings(posting_id) on delete cascade,
    score       int,
    sent_at     timestamptz not null default now(),
    primary key (profile_id, posting_id)
);
create index if not exists idx_digest_sends_profile on digest_sends (profile_id, sent_at desc);

-- ---------------------------------------------------------------------------
-- suppression: emails we must never contact again (unsubscribe / bounce).
-- ---------------------------------------------------------------------------
create table if not exists suppression (
    email       text primary key,                   -- lowercased
    reason      text not null,                       -- unsub | bounce | complaint
    created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- matches: per (profile, posting) AI fit score + the user's tracking status.
-- Cached so we never re-score the same (profile, posting) pair.
-- ---------------------------------------------------------------------------
create table if not exists matches (
    profile_id   uuid not null references profiles(id) on delete cascade,
    posting_id   text not null references postings(posting_id) on delete cascade,
    score        int,                               -- 0..10 (Haiku), null until scored
    summary      text,                              -- one-line "why", from Haiku
    status       text not null default 'new',       -- new | saved | dismissed | applied
    scored_at    timestamptz,
    notified_at  timestamptz,                       -- set when included in an email digest
    updated_at   timestamptz not null default now(),
    primary key (profile_id, posting_id)
);

create index if not exists idx_matches_profile_status on matches (profile_id, status, score desc);

-- ---------------------------------------------------------------------------
-- events: first-party, cookieless product analytics (migration 005).
-- Privacy-minimal by design so the site needs no consent banner: no cookies, no IP
-- (raw or hashed), country only, coarse browser family, no user free text. session_id
-- lives in sessionStorage and dies with the tab. See migration_005_events.sql for the
-- full rationale; retention is handled by service.pipeline.
-- ---------------------------------------------------------------------------
create table if not exists events (
    id          bigserial primary key,
    name        text not null,
    occurred_at timestamptz not null default now(),
    session_id  text,
    path        text,
    country     varchar(2),
    browser     text,
    props       jsonb not null default '{}'::jsonb,
    profile_id  uuid references profiles(id) on delete set null
);

create index if not exists idx_events_name_time on events (name, occurred_at desc);
create index if not exists idx_events_time      on events (occurred_at);
create index if not exists idx_events_session   on events (session_id) where session_id is not null;

-- Daily rollup — outlives raw-row retention so long-run trends survive pruning.
create table if not exists events_daily (
    day     date not null,
    name    text not null,
    country varchar(2),
    n       int  not null,
    primary key (day, name, country)
);

-- ---------------------------------------------------------------------------
-- sessions: keep a browser logged in after it clicks a magic link once (migration 008).
-- Still passwordless — the manage_token link is the only credential typed. `id` is the
-- SHA-256 of the raw cookie token (never the raw token), so a backup carries no usable
-- session credential. Rows cascade on profile delete, so retention needs no extra step.
-- ---------------------------------------------------------------------------
create table if not exists sessions (
    id           text primary key,                    -- sha256(raw cookie token), hex
    profile_id   uuid not null references profiles(id) on delete cascade,
    created_at   timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    expires_at   timestamptz not null
);
create index if not exists idx_sessions_profile on sessions (profile_id);
create index if not exists idx_sessions_expires on sessions (expires_at);

-- ---------------------------------------------------------------------------
-- signup_intents: short-lived proof that an email was Google-verified (migration 009),
-- so "Sign in with Google" can also *sign up* a new user — create an active subscription
-- with no confirm email, since Google already proved they control the address. `id` is the
-- SHA-256 of the raw cookie token; the row holds the email for ~30 min then is pruned.
-- ---------------------------------------------------------------------------
create table if not exists signup_intents (
    id          text primary key,                    -- sha256(raw cookie token), hex
    email       text not null,                        -- the Google-verified address
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null
);
create index if not exists idx_signup_intents_expires on signup_intents (expires_at);
