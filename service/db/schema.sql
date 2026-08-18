-- Job/Freelance matching service — Postgres schema.
-- Works on local Postgres (docker) and Supabase unchanged.
-- Supabase note: user identity lives in auth.users; `profiles.user_id` references it
-- in production. Locally we allow any uuid so the API runs without Supabase auth.

create extension if not exists "uuid-ossp";
create extension if not exists pg_trgm;      -- fuzzy title search / dedup support
-- Embeddings (migration 015). This is why the `db` service is BUILT from
-- deploy/db.Dockerfile rather than pulled: stock postgres:16-alpine has no pgvector, and the
-- obvious pulled alternative (pgvector/pgvector:pg16) is Debian/glibc, which would silently
-- corrupt this cluster's musl-ordered indexes. See deploy/db.Dockerfile.
create extension if not exists vector;

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
    work_mode      text,                             -- remote | hybrid | onsite | null=unknown
    -- How far a fully-remote role reaches (migration 021), which is a different question from
    -- whether it happens in an office. `scope_raw` is the board's own scope field verbatim — a
    -- claim, like `remote_signal` (WWR's `region`, Himalayas' location+timezone restrictions,
    -- Ashby's `secondaryLocations`, Greenhouse's `offices`). `remote_reach` is the derived
    -- verdict: anywhere | region | country | null=never said. Set only for remote rows.
    -- Null is 22% of active remote rows and passes every gate — see service/geo.py
    -- (`remote_reach`) before relying on this, and never gate the digest path on it in SQL.
    scope_raw      text,
    remote_reach   text,
    -- Which named areas a *multi-country* scope reaches (migration 022): a subset of
    -- geo.REACH_AREAS ('eea', 'na'), overlapping on purpose — a "North America or Europe" role is
    -- in both, and that overlap is the only set where someone in the EEA can hold a US role.
    -- Null for `remote_reach` of `country` or unknown: a single-country scope is answered by the
    -- country filter itself. Null/empty passes every filter, exactly like `skills`.
    reach_areas    text[],
    -- Every country the posting names as a place the job can be held (migration 023), when it
    -- names two or more. `country_code` has to be a single value because `city` must agree with
    -- it, so without this column the other countries a multi-site posting names were reachable by
    -- no filter at all. Unlike the two columns above, this is **not** gated on being remote: "may
    -- I live anywhere" is a category error for an on-site job, but "is there a job for me in
    -- Poland" is not, and two offices in two countries answers it yes in both.
    -- Null below two countries — one country is what `country_code` already says.
    reach_countries text[],
    -- Lowest qualification the ad demands (migration 014): secondary | vocational | bachelor |
    -- master | doctorate | null=never said. Null for ~97% of rows and for 100% of the CZ/SK
    -- inventory, which carries no description text at all — the gate keeps nulls and defers to
    -- the AI matcher. See service/education.py before relying on this.
    education_min  text,
    -- Minimum years of experience the ad demands (migration 025): null = never said, and the
    -- gate keeps nulls and defers to the AI matcher — the same design, for the same reasons,
    -- as education_min above. Roughly a quarter of active rows carry a value (measured
    -- 2026-08-18). See service/experience.py before relying on this.
    experience_min smallint,
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
    -- Structured skills facet (migration 020): canonical tech/tool names the ad mentions,
    -- from service/skills.extract_skills (a deterministic gazetteer, not AI). Null/empty means
    -- "none named / not classified" and passes every gate — a facet only narrows on a positive
    -- selection. See service/skills.py. (Was jsonb for the decommissioned Haiku plan; never
    -- written, repurposed to text[] for array-containment filtering + unnest facet counts.)
    skills         text[],
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
    ) stored,

    -- Retrieval embedding (migration 015). `halfvec` is fp16: 74MB across 96,583 active rows
    -- against 148MB for `vector`, and the precision it drops sits far below what changes a
    -- ranking order — on a 3814MB swapless box what competes for page cache matters.
    --
    -- `embedding_model` is not bookkeeping. Two models produce vectors in unrelated spaces, so
    -- a model swap without a full re-embed leaves this column meaning two things at once and
    -- cosine distance quietly comparing nonsense. The stored identity includes the *library*
    -- version for the same reason — fastembed 0.8.0 moved this model from CLS to mean pooling
    -- while its name stayed byte-identical. Changing either means re-running
    -- `python -m service.backfill_embeddings`.
    --
    -- Null is always valid: ~5% of active postings carry no description and the embed step can
    -- be skipped, so a missing vector falls back to `first_seen_at` ordering and must never
    -- remove a job from a digest. Same rule as work_mode, education_min and an unresolved city.
    embedding       halfvec(384),
    embedding_model text,
    embedded_at     timestamptz
);

create index if not exists idx_postings_active     on postings (is_active, last_seen_at desc);
create index if not exists idx_postings_geo         on postings (country_code, city);
create index if not exists idx_postings_filters     on postings (role_category, region, seniority, work_type);
create index if not exists idx_postings_eligibility on postings (eligibility);
create index if not exists idx_postings_dedup       on postings (dedup_key);
-- Partial: only ~3% of rows carry a requirement, and the null-majority path never needs it.
create index if not exists idx_postings_education   on postings (education_min) where education_min is not null;
-- Partial (migration 025), same shape: the interesting rows are the ones with a value.
create index if not exists idx_postings_experience  on postings (experience_min) where experience_min is not null;
-- Partial (migration 021): only ever set for fully-remote rows, and every query that reads it is
-- already filtering on remote.
create index if not exists idx_postings_remote_reach on postings (remote_reach)
    where is_active and remote_reach is not null;
-- GIN over the reach areas (migration 022), so `reach_areas && array['eea']` is index-backed.
create index if not exists idx_postings_reach_areas on postings using gin (reach_areas);
-- GIN over the secondary countries (migration 023), so `reach_countries && array['pl']` is
-- index-backed alongside `idx_postings_geo`.
create index if not exists idx_postings_reach_countries
    on postings using gin (reach_countries);
-- GIN over skill array elements (migration 020): index-backs `skills && array['python']` and
-- unnest facet counts. Null/empty arrays add no entries, so it stays proportional to postings
-- that name a skill.
create index if not exists idx_postings_skills      on postings using gin (skills);
create index if not exists idx_postings_title_trgm  on postings using gin (title gin_trgm_ops);
create index if not exists idx_postings_search_tsv  on postings using gin (search_tsv);
-- The embed backfill's own work queue: active rows still missing a vector. Partial, so it
-- stays tiny once caught up — ~8,000 new postings a day rather than 96,583 for ever.
create index if not exists idx_postings_needs_embedding
    on postings (first_seen_at desc) where is_active and embedding is null;
-- Cosine, because these models are trained for it. NOTE for anyone querying this index behind
-- a WHERE clause: HNSW gathers `hnsw.ef_search` (default 40) candidates and filters
-- *afterwards*, so a selective gate starves it — measured on production 2026-08-08, a LIMIT of
-- 120 returned 4 rows for one profile and 0 for another, with nothing raised. Set
-- `hnsw.iterative_scan = relaxed_order` on any such query; see store.query_shortlist_vector.
create index if not exists idx_postings_embedding_hnsw
    on postings using hnsw (embedding halfvec_cosine_ops);

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
    -- Which work setups they'll accept (migration 012). All three = no filter; a posting whose
    -- work_mode is unknown always passes and is judged by the matcher. See service/geo.py.
    work_modes      text[]  not null default '{onsite,hybrid,remote}',
    -- Which education requirements they'll accept (migration 014). All five = no filter; a
    -- posting whose requirement is unknown always passes. `education_field` is free text for
    -- the AI matcher and is never filtered on. See service/education.py.
    education_levels text[] not null default '{secondary,vocational,bachelor,master,doctorate}',
    education_field  text,
    regions         text[]  not null default '{cz,eu,worldwide}',   -- derived, coarse
    role_categories text[]  not null default '{}',  -- empty = all data roles
    work_types      text[]  not null default '{permanent,freelance/contract}',
    part_time_only  boolean not null default false,
    eligible_only   boolean not null default true,  -- drop blocked / US-auth-gated
    sectors         text[]  not null default '{}',  -- soft boost: fintech, ecommerce, trading
    min_score       int     not null default 6,

    -- Which language to write to this person (migration 013). Set from the locale they signed
    -- up under; 'en' for every row that predates the site having more than one. Read through
    -- `service.i18n.clean_locale`, so an unknown value degrades to English instead of failing
    -- a send.
    language        text    not null default 'en',

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
    last_ondemand_at    timestamptz,   -- rate-limits the on-demand "run my digest now" path (migration 024)

    -- CV-derived signals (parse & discard: the raw CV file is never stored)
    has_cv           boolean not null default false,
    cv_summary       text,                            -- short "Detected: ..." line
    years_experience int,

    -- The query side of the posting embedding space (migration 015). Deliberately unindexed:
    -- there are three subscribers and this is fetched by primary key, never searched.
    --
    -- Written by the pipeline, NOT by the webapp on save — keeping the model out of the
    -- always-on `api` container is what stops ~500MB of resident weights competing with
    -- Postgres. A profile saved during the day gets its vector on the next nightly run, which
    -- is soon enough for a daily digest. See service/embed.py `profile_text` for what is
    -- encoded, and note it deliberately excludes anything a hard gate already decides.
    embedding       halfvec(384),
    embedding_model text,
    embedded_at     timestamptz,

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

-- ---------------------------------------------------------------------------
-- digest_runs: what the pipeline did for each subscriber each day (migration 011).
-- Operational, not analytics — see migration_011_digest_runs.sql for why it is separate
-- from `events`. Exists because five matching bugs found on 2026-07-26 were all invisible
-- from the outside: the run summary aggregates across profiles, so one subscriber being
-- starved of candidates looks exactly like a healthy day. Splitting shortlist_n / picks_n /
-- sendable_n distinguishes "retrieval failed them" from "the matcher correctly found
-- nothing", which want opposite fixes. Read by service/watchdog.py.
-- ---------------------------------------------------------------------------
create table if not exists digest_runs (
    day          date not null,
    profile_id   uuid not null references profiles(id) on delete cascade,
    shortlist_n  int  not null default 0,             -- candidates retrieval found
    widened      boolean not null default false,      -- the retrieval floor had to fire
    picks_n      int  not null default 0,             -- matches the AI returned
    sendable_n   int  not null default 0,             -- jobs clearing EMAIL_MIN_SCORE
    sent         boolean not null default false,      -- a digest actually left the box
    -- Bytes this subscriber's entry occupied in shortlists.json (migration 016). Nullable,
    -- unlike every count above: 0 is a real measurement here ("their export was empty"), so a
    -- default would make unmeasured rows indistinguishable from genuinely empty ones. This is
    -- the matcher's per-subscriber cost, and the unit the scaling plan is sized against.
    shortlist_bytes bigint,
    recorded_at  timestamptz not null default now(),
    primary key (day, profile_id)
);

create index if not exists idx_digest_runs_profile_day on digest_runs (profile_id, day desc);
create index if not exists idx_digest_runs_day on digest_runs (day desc);

-- ---------------------------------------------------------------------------
-- shortlist_shadow (migration 015): the vector ranking, recorded beside the live
-- keyword one and emailed to nobody.
-- ---------------------------------------------------------------------------
-- Switching how candidates are retrieved decides what real people are shown, so it is settled
-- by measurement rather than by argument: the vector path writes here nightly, the keyword
-- path keeps deciding digests, and the two are compared per subscriber over real days. That
-- comparison is the only thing that can show *improvement* — `scripts/measure_shadow_recall.py`
-- can only show preservation, because `matches` was itself populated from keyword shortlists
-- and a posting vectors would find but keywords missed can never appear in it.
--
-- `in_live` is the whole point and is why the row is written now: whether the vector would
-- have surfaced what the keyword path did is cheap at this moment and impossible to
-- reconstruct later, once postings age out and `is_active` flips.
--
-- Keyed by day so a window can be compared rather than one night, and cascading on profile
-- delete so the 30-day erasure promise needs no extra step (same reasoning as digest_runs).
-- Holds no personal data beyond the profile reference it is keyed on. Nothing on the delivery
-- path may read it — `test_shortlist_shadow_sql.py` fails if digest, mailer, webapp, pipeline
-- or the watchdog so much as names it.
create table if not exists shortlist_shadow (
    day         date not null,
    profile_id  uuid not null references profiles(id) on delete cascade,
    posting_id  text not null references postings(posting_id) on delete cascade,
    rank        int  not null,
    similarity  real not null,
    in_live     boolean not null default false,       -- also in that day's keyword shortlist
    recorded_at timestamptz not null default now(),
    primary key (day, profile_id, posting_id)
);

create index if not exists idx_shortlist_shadow_profile_day
    on shortlist_shadow (profile_id, day desc);

-- ---------------------------------------------------------------------------
-- categorization_daily (migration 017): how much of the corpus carries a category,
-- one row per source per day plus a synthetic `__all__` row for the corpus total.
-- ---------------------------------------------------------------------------
-- Read by `service/categorization_watchdog.py`, which compares today against a trailing
-- median — categorisation rots silently and no timer, exception or existing check moves when
-- it does. The full reasoning (and the two obvious metrics that were measured and rejected)
-- is in `migration_017_categorization_daily.sql`.
--
-- **This section was missing until 2026-08-09 and that is the bug it now fixes**, not a
-- tidy-up: 017 was applied by hand on the box and never folded in here, so every database
-- built from this file — CI's throwaway Postgres, `dev/db.ps1 reset` — lacked a table
-- production has. The symptom is not a missing table, it is a test that cannot run.
create table if not exists categorization_daily (
    day            date not null,
    source         text not null,          -- adapter name, or '__all__' for the corpus total
    total          int  not null,
    uncategorised  int  not null,
    recorded_at    timestamptz not null default now(),
    primary key (day, source)
);

create index if not exists idx_categorization_daily_source_day
    on categorization_daily (source, day desc);

-- ---------------------------------------------------------------------------
-- title_categories (migration 019): what the claude.ai routine answered about a title
-- neither the patterns nor the publishers' occupation codes could read.
-- ---------------------------------------------------------------------------
-- **The cache is not an optimisation, it is the design.** Classification happens at extract
-- (`service.ingest.build_row`), and ingest re-fetches every active posting every day —
-- ~101k rows, of which only ~2 400 are genuinely new (measured 2026-08-09). `upsert_postings`
-- rewrites `role_category` on conflict, which is exactly why a classifier change needs no
-- backfill; it also means asking the routine about every posting would be 101k titles a day
-- instead of 2 400. Keyed on the *normalised* title so the answer survives the posting: the
-- same title is re-advertised by the same employer for months, and by others for years.
--
-- Normalisation has ONE definition, `service.categorize_exchange.normalise_title`, and it is
-- deliberately not expressed in SQL. A `lower(btrim(regexp_replace(...)))` here would be a
-- second copy of it, and the two would drift the way `geo.py` and `geo.ts` would without the
-- drift test — except silently, as a cache that stops hitting. The queries stay trivial and
-- Python does the grouping.
--
-- **`category = 'uncategorised'` is a recorded decline, not a failure.** A title the routine
-- could not read must still be written here, or the export asks about it again every single
-- run, forever. `title_category_map()` filters those out for the ingest path, where they mean
-- exactly what no row means; `asked_title_keys()` keeps them, because "have we asked?" and
-- "do we know?" are different questions.
--
-- No personal data: a public job title and a category from a fixed vocabulary. Nothing here
-- references a subscriber, so nothing here needs erasing under the 30-day promise, and the
-- privacy policy is unchanged (security rule 4 — this adds no new *category* of stored data,
-- and the exported file carries job titles and nothing else).
--
-- Idempotent, and applied by hand before the code that reads it — the deploy runs no
-- migrations.

create table if not exists title_categories (
    title_key    text primary key,                     -- normalise_title(title)
    category     text not null,                        -- must be a value in taxonomy.CATEGORIES
    sample_title text not null,                        -- one real title, so a human can audit
    source       text not null default 'routine',      -- who answered: routine | manual
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

-- The ingest read is "every key we actually know", so the index that matters is the one that
-- lets `category <> 'uncategorised'` skip the declines rather than scan them.
create index if not exists idx_title_categories_category on title_categories (category);
