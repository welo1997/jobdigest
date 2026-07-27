-- Migration 011: per-subscriber digest outcomes, so failing one person is observable.
--
-- The problem this exists for: on 2026-07-26 a single subscriber review turned up five
-- independent matching defects, and *not one of them raised an error anywhere*. No exception,
-- no failed timer, no alert. One of them (global shortlist slot allocation) meant a subscriber
-- would have received no digest at all — the matcher correctly returned 0 picks from 112
-- candidates it should never have been shown. From the outside that run looked healthy:
-- every timer green, every log line INFO.
--
-- The pipeline already knows everything needed to catch that. It just throws it away:
-- `matcher.py` logs "profile X: N candidates -> M picks" to a file nobody reads, and
-- `pipeline.run` returns a summary aggregated across *all* profiles, where one starved
-- subscriber is invisible among the healthy ones.
--
-- One row per profile per day, written by whichever stage learns each number:
--
--   shortlist_n  candidates retrieval found        (matcher.export_shortlists)
--   widened      the retrieval floor had to fire   (store.query_shortlist_meta)
--   picks_n      matches the AI returned           (matcher.import_picks / run)
--   sendable_n   jobs that cleared EMAIL_MIN_SCORE (pipeline.run)
--   sent         a digest actually left the box    (pipeline.run)
--
-- Those separate the two failure modes that look identical from a summary count and want
-- opposite fixes: retrieval starved them (shortlist_n low, or widened) versus the matcher saw
-- a fair shortlist and correctly rejected it (shortlist_n healthy, picks_n zero). Only the
-- first is a bug in our code.
--
-- Operational, not analytics: deliberately NOT in `events`, which is the cookieless
-- product-analytics table with its own privacy rationale and a 180-day prune. This holds no
-- personal data beyond the profile reference it is keyed on, and cascades on profile delete,
-- so the 30-day erasure promise in the privacy policy needs no extra step. No new field about
-- a subscriber is stored — these are counts of our own behaviour.

create table if not exists digest_runs (
    day          date not null,
    profile_id   uuid not null references profiles(id) on delete cascade,
    shortlist_n  int  not null default 0,
    widened      boolean not null default false,
    picks_n      int  not null default 0,
    sendable_n   int  not null default 0,
    sent         boolean not null default false,
    recorded_at  timestamptz not null default now(),
    primary key (day, profile_id)
);

-- The watchdog's query: "which profiles have gone N days without a digest", so it must be
-- cheap to scan one profile's recent days.
create index if not exists idx_digest_runs_profile_day on digest_runs (profile_id, day desc);
create index if not exists idx_digest_runs_day on digest_runs (day desc);
