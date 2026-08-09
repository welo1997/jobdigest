-- Migration 017: a daily record of how much of the corpus is classified, so the watchdog
-- has something to compare today against.
--
-- Categorisation rots silently. Sources change their title formats, new adapters arrive with
-- vocabulary no pattern covers, and a regex edit can quietly stop matching — none of which
-- raises, fails a timer, or shows up in any existing check. The symptom is a subscriber's
-- shortlist getting thinner, which is indistinguishable from a quiet week.
--
-- **The obvious metrics were measured first and both are unusable**, which is why this table
-- exists rather than a query against `postings`:
--
--   Per-source, day over day.  Natural 8-day swing reaches 47 points (recruitee 21→68 on
--                              57–114 rows/day), with remoteok at 29, smartrecruiters 28,
--                              lever 23. A threshold under ~50 fires on healthy sources —
--                              exactly the day-one false alarms `source_watchdog.py`'s
--                              docstring records — and one over 50 catches nothing.
--
--   Corpus-wide, but over *new rows only*.  Swings 14.9% → 59.7% across eight days, because
--                              it measures whichever sources happened to churn. On 2026-08-05
--                              a single source rebuild produced 40 507 of the day's rows and
--                              dragged the number 27 points on its own.
--
-- What is stable is the **whole active corpus**: ~100k rows, independent of which sources
-- churned today, and it moves only when the classifier's behaviour changes — which is the
-- thing being watched. `upsert_postings` rewrites `role_category` on conflict and every
-- active posting is re-seen daily, so a regression reaches the whole corpus within one
-- ingest cycle and shows up here the next morning.
--
-- One row per source per day, plus a synthetic `__all__` row for the corpus total, so a
-- source that goes unmapped is visible next to a corpus that is fine.
--
-- Operational, like `digest_runs`: no personal data, no reference to a subscriber, nothing
-- to erase. Deliberately not in `events`, which is product analytics with its own retention.
--
-- **It starts silent.** The watchdog needs a few days of history before a trailing median
-- means anything, and that is stated rather than worked around — an alert tuned on one day of
-- data is a guess wearing a threshold.

create table if not exists categorization_daily (
    day            date not null,
    source         text not null,          -- adapter name, or '__all__' for the corpus total
    total          int  not null,
    uncategorised  int  not null,
    recorded_at    timestamptz not null default now(),
    primary key (day, source)
);

-- The watchdog's read: "this source's last N days", newest first.
create index if not exists idx_categorization_daily_source_day
    on categorization_daily (source, day desc);
