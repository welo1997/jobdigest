"""Postgres store for the job-matching service.

Thin data-access layer over `postings`, `profiles`, `matches`. Uses psycopg2 with a
module-level connection pool. Connection string comes from DATABASE_URL, e.g.
    postgresql://jobmatch:jobmatch@localhost:5433/jobmatch   (local docker)
    postgresql://...supabase.co:5432/postgres                (Supabase)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

# `embed` is safe to import here even though the webapp imports this module: it pulls no ML
# dependency at import time — fastembed and numpy load inside `embed._load()` / `embed_texts`,
# neither of which the API ever calls. Only `to_pgvector` is used below, and it stays the one
# definition of how a vector is rendered for Postgres.
from service import education, embed, experience, geo, i18n, language, taxonomy

_POOL: Optional[ThreadedConnectionPool] = None


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    return dsn


def init_pool(minconn: int = 1, maxconn: int = 8) -> None:
    global _POOL
    if _POOL is None:
        _POOL = ThreadedConnectionPool(minconn, maxconn, dsn=_dsn())


@contextmanager
def cursor(commit: bool = False):
    """Borrow a pooled connection + RealDict cursor."""
    init_pool()
    assert _POOL is not None
    conn = _POOL.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _POOL.putconn(conn)


# --- postings ------------------------------------------------------------------

_UPSERT_SQL = """
insert into postings (
    posting_id, source, title, company, url, description, location, country_code, city,
    remote_signal, work_mode, scope_raw, remote_reach, reach_areas, reach_countries,
    education_min, experience_min, language,
    salary_raw, currency, posted_at,
    role_category, region, eligibility, seniority, work_type, is_part_time, dedup_key, skills,
    last_seen_at, is_active
) values %s
on conflict (posting_id) do update set
    -- `url` is refreshed, which matters only where an adapter decouples `posting_id` from
    -- the link (startupjobs hashes the immutable offer id so an employer's retitle cannot
    -- mint a duplicate row). Everywhere else `posting_id = md5(url)`, so `excluded.url` is
    -- byte-identical to what is stored and this is a no-op. Without it a link stored wrong
    -- stays wrong for the life of the row: the 2026-08-06 startupjobs rewrite emitted a
    -- 404-ing URL and there was no path to repair it in place short of changing the id and
    -- duplicating all 450 postings.
    url = excluded.url,
    title = excluded.title,
    company = excluded.company,
    description = excluded.description,
    location = excluded.location,
    country_code = excluded.country_code,
    city = excluded.city,
    remote_signal = excluded.remote_signal,
    work_mode = excluded.work_mode,
    scope_raw = excluded.scope_raw,
    remote_reach = excluded.remote_reach,
    reach_areas = excluded.reach_areas,
    reach_countries = excluded.reach_countries,
    education_min = excluded.education_min,
    experience_min = excluded.experience_min,
    language = excluded.language,
    salary_raw = excluded.salary_raw,
    currency = excluded.currency,
    posted_at = excluded.posted_at,
    role_category = excluded.role_category,
    region = excluded.region,
    eligibility = excluded.eligibility,
    seniority = excluded.seniority,
    work_type = excluded.work_type,
    is_part_time = excluded.is_part_time,
    dedup_key = excluded.dedup_key,
    skills = excluded.skills,
    last_seen_at = now(),
    is_active = true;
"""


def upsert_postings(rows: Iterable[dict]) -> int:
    """Insert/refresh postings. Bumps last_seen_at on re-ingest (freshness)."""
    values = [
        (
            r["posting_id"], r["source"], r.get("title"), r.get("company"), r["url"],
            r.get("description"), r.get("location"), r.get("country_code"), r.get("city"),
            r.get("remote_signal"), r.get("work_mode"),
            r.get("scope_raw"), r.get("remote_reach"),
            # Empty list -> NULL for the same reason as `skills` below: psycopg2 renders `[]` as an
            # untyped empty array Postgres cannot coerce, and null/empty are equivalent here.
            r.get("reach_areas") or None, r.get("reach_countries") or None,
            r.get("education_min"), r.get("experience_min"), r.get("language"),
            r.get("salary_raw"), r.get("currency"),
            r.get("posted_at"),
            r.get("role_category"), r.get("region"), r.get("eligibility"),
            r.get("seniority"), r.get("work_type"), r.get("is_part_time", False),
            r.get("dedup_key"),
            # Empty skills list -> NULL, not ARRAY[]: psycopg2 renders `[]` as an untyped empty
            # array Postgres cannot coerce, and null/empty are equivalent for the facet anyway.
            r.get("skills") or None,
        )
        for r in rows
    ]
    if not values:
        return 0
    # 29 placeholders for the 29 columns above `last_seen_at`. Counted, not eyeballed: these
    # bind by position, so one missing %s shifts every column after it by one and psycopg2
    # cannot tell — it would write `skills` into `dedup_key` and fail on the type, or
    # worse, not fail at all. The assert below is cheap and turns that into a loud error —
    # it earned its keep on 2026-08-15, when `reach_countries` reached the column list and
    # the values tuple but not this string, again on 2026-08-18 when `experience_min` did
    # exactly the same thing, and again on 2026-08-21 for `language` — each caught in the
    # first local test run.
    template = ("(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s,%s,now(), true)")
    assert template.count("%s") == len(values[0]), (
        f"upsert template has {template.count('%s')} placeholders "
        f"for {len(values[0])} values")
    with cursor(commit=True) as cur:
        psycopg2.extras.execute_values(cur, _UPSERT_SQL, values, template=template,
                                       page_size=500)
    return len(values)


def source_freshness(baseline_days: int = 7, min_rows: int = 50) -> list[dict]:
    """Per-source: active rows, how many first appeared today, and how stale the newest is.

    Read side for `service.source_watchdog`. `age_days` is measured from `max(last_seen_at)`,
    which is what a silent zero moves and nothing else does — a source that answers with an
    empty list leaves every other column looking exactly as it did yesterday. Sources with
    no active rows are included (with `age_days` null) so a source that vanished entirely is
    a row here rather than an absence the caller has to notice.

    `repeat_today` / `keyed_today` / `baseline_share` are what detect an id re-mint, and each
    of the two simpler measures was tried against production first and failed:

    * **"how much of the run was new"** — `arbeitnow` and `himalayas` are rolling "newest N"
      feeds whose older jobs stop being served, so they are ~100% new *every day, for ever*.
    * **"how much of today is a job we already had"** — right shape, wrong on its own:
      `adzuna` sits at 69–86% every single day, because it is an aggregator whose corpus
      genuinely carries the same job under many ad ids (one role had 122). A permanent alert
      is a muted alert.

    So `baseline_share` is the same measure over the source's own prior days, and the caller
    compares today against it. A re-mint is a **jump**: startupjobs went 0.0 → 3.8 → *96.7*,
    while adzuna went 68.8 → 81.1 → 85.5 and is not a fault. Note the key deliberately omits
    `city`: adding it drops the real incident from 96.7% to 3.3%, because the old adapter
    hardcoded `country_code='CZ'` (leaving `city` null) and the new one resolves `prague`.
    """
    with cursor() as cur:
        cur.execute(
            """
            with keyed as (
                select source, is_active, last_seen_at,
                       first_seen_at::date as d,
                       case when company is not null and title is not null
                            then lower(company) || '|' || lower(title) end as k
                from postings
            ),
            -- The day each (source, job) was first seen. An aggregate, deliberately: the
            -- first version asked "does an earlier row exist" as a correlated subquery per
            -- row, which is O(n^2) and did not return on 100k rows. This runs in ~1.1 s.
            firsts as (
                select source, k, min(d) as first_d
                from keyed where k is not null group by source, k
            ),
            daily as (
                select k.source, k.d,
                       count(*)                                    as new_rows,
                       count(*) filter (where f.first_d < k.d)     as repeats
                from keyed k
                join firsts f on f.source = k.source and f.k = k.k
                where k.d > current_date - %s
                group by k.source, k.d
            ),
            baseline as (
                select source, max(repeats::numeric / new_rows) as share
                from daily where d < current_date and new_rows >= %s
                group by source
            ),
            totals as (
                select source,
                       count(*) filter (where is_active)                     as active,
                       count(*) filter (where last_seen_at::date =
                                              current_date)                  as seen_today,
                       count(*) filter (where d = current_date)              as new_today,
                       extract(epoch from (now() - max(last_seen_at)))
                         / 86400.0                                           as age_days
                from keyed group by source
            )
            select t.source, t.active, t.seen_today, t.new_today, t.age_days,
                   coalesce(dd.new_rows, 0) as keyed_today,
                   coalesce(dd.repeats, 0)  as repeat_today,
                   coalesce(b.share, 0)::float8 as baseline_share
            from totals t
            left join daily dd on dd.source = t.source and dd.d = current_date
            left join baseline b on b.source = t.source
            order by t.source
            """,
            (baseline_days, min_rows),
        )
        return [dict(r) for r in cur.fetchall()]


def deactivate_stale(days: int = 7) -> int:
    """Mark postings not re-seen within `days` as inactive (drops expired listings)."""
    with cursor(commit=True) as cur:
        cur.execute(
            "update postings set is_active = false "
            "where is_active = true and last_seen_at < now() - (%s || ' days')::interval",
            (days,),
        )
        return cur.rowcount


def deactivate_old(days: int = 365) -> int:
    """Deactivate active postings whose OWN date (`posted_at`) is older than `days`.

    The second staleness axis. `deactivate_stale` asks whether the *source's feed* still lists a
    job; this asks whether the job's *own posted date* is ancient. A national register
    (mpsv / úřad práce) and several ATS boards (ashby, lever, adzuna, teamtailor, recruitee,
    workable) re-list roles for months or years after they were filled — measured 2026-08-20,
    ~1 300 active rows carry a `posted_at` older than a year, the oldest from **2016** — so the
    feed test never catches them, `last_seen_at` bumps daily, and a subscriber is emailed a
    14-month-old listing (the úřad-práce "Datum poslední změny 1.7.2025" case).

    Deactivate, never delete — same contract as `deactivate_stale`, so analytics keep the row and
    `/matches` (which filters `is_active`) simply stops showing it. It self-heals across ingests:
    `upsert_postings` reactivates a re-listed row, and this re-deactivates it the same run while
    its date stays old.

    **A NULL `posted_at` is left active.** Three sources (startupjobs, cocuma, goldencareers)
    never provide a date, and an age we cannot read is not an age we act on — the same "abstain
    on unknown" polarity the geo and liveness paths use."""
    with cursor(commit=True) as cur:
        cur.execute(
            "update postings set is_active = false "
            "where is_active = true and posted_at is not null "
            "and posted_at < (now() - (%s || ' days')::interval)::date",
            (int(days),),
        )
        return cur.rowcount


def matched_active_posting_ids() -> set[str]:
    """Active postings that some subscriber has a scored match for.

    The read side of the liveness sweep's "matched" half — exactly the set of postings a
    subscriber can currently see on `/matches` (same `is_active` + `score is not null` filter
    as `matched_jobs`). Probing these is what lets a job that has since died vanish from the
    page instead of lingering as a dead link nobody re-checks. See `service/liveness.py`."""
    with cursor() as cur:
        cur.execute(
            "select distinct p.posting_id from postings p "
            "where p.is_active and exists ("
            "  select 1 from matches m where m.posting_id = p.posting_id and m.score is not null)"
        )
        return {r["posting_id"] for r in cur.fetchall()}


def posting_targets(ids: Iterable[str]) -> list[dict]:
    """`{posting_id, url, title, company, source}` for the given **active** postings.

    Used by the liveness sweep to turn a set of posting ids (matched rows + today's shortlists)
    into the URLs it has to probe. Inactive rows are dropped: a posting already deactivated
    needs no probing, and re-probing it could only wrongly revive it."""
    ids = [i for i in ids if i]
    if not ids:
        return []
    with cursor() as cur:
        cur.execute(
            "select posting_id, url, title, company, source from postings "
            "where is_active and posting_id = any(%s)",
            (ids,),
        )
        return [dict(r) for r in cur.fetchall()]


def deactivate_postings(ids: Iterable[str]) -> int:
    """Flip `is_active = false` on the given postings. Returns how many rows changed.

    The liveness sweep's one write. Same semantics as `deactivate_stale` (a dead listing is
    *deactivated*, never deleted, so analytics keep the row and `/matches` — which filters on
    `is_active` — simply stops showing it). The `and is_active` guard makes it idempotent and
    keeps the rowcount honest about what actually changed."""
    ids = [i for i in ids if i]
    if not ids:
        return 0
    with cursor(commit=True) as cur:
        cur.execute(
            "update postings set is_active = false "
            "where is_active = true and posting_id = any(%s)",
            (ids,),
        )
        return cur.rowcount


def prune_descriptions(days: int = 90) -> int:
    """Blank the description of postings inactive for `days`, keeping the row.

    Descriptions are ~81% of the table's on-disk size (they TOAST out at ~7.2 KB of the
    ~9.1 KB per posting), so this is the one lever that meaningfully bounds growth. The
    row itself stays for analytics — title, company, salary, dates and classification are
    all preserved; only the free text goes.

    Safe by construction: the matcher only ever reads `is_active` postings, and the
    generated `search_tsv` shrinks with the description, reclaiming GIN index space too.
    Idempotent — rows already pruned are skipped, so daily runs touch only new arrivals.
    """
    with cursor(commit=True) as cur:
        cur.execute(
            "update postings set description = null "
            "where description is not null and is_active = false "
            "and last_seen_at < now() - (%s || ' days')::interval",
            (int(days),),
        )
        return cur.rowcount


def record_event(name: str, *, session_id: str | None = None, path: str | None = None,
                 country: str | None = None, browser: str | None = None,
                 props: dict | None = None, profile_id: str | None = None) -> None:
    """Append one analytics event. Never raises — analytics must not break a user flow.

    Callers are responsible for validating `name` against a whitelist and for keeping
    `props` free of personal data; this function only enforces column-level limits.
    """
    try:
        with cursor(commit=True) as cur:
            cur.execute(
                "insert into events (name, session_id, path, country, browser, props, profile_id) "
                "values (%s,%s,%s,%s,%s,%s,%s)",
                (name[:64], (session_id or None) and session_id[:64],
                 (path or None) and path[:200], (country or None) and country[:2],
                 (browser or None) and browser[:16], json.dumps(props or {})[:2000],
                 profile_id),
            )
    except Exception:                                  # pragma: no cover - defensive
        logging.getLogger("service.store").warning("record_event failed", exc_info=True)


def rollup_events(day_offset: int = 1) -> int:
    """Fold one day's raw events into `events_daily` so trends survive retention pruning.

    Idempotent: re-running for the same day replaces that day's counts."""
    with cursor(commit=True) as cur:
        cur.execute(
            "insert into events_daily (day, name, country, n) "
            "select (occurred_at at time zone 'utc')::date, name, coalesce(country, '??'), count(*) "
            "from events "
            "where (occurred_at at time zone 'utc')::date = (current_date - %s::int) "
            "group by 1, 2, 3 "
            "on conflict (day, name, country) do update set n = excluded.n",
            (day_offset,),
        )
        return cur.rowcount


def prune_events(days: int = 180) -> int:
    """Delete raw events older than `days`. Daily rollups are kept indefinitely."""
    with cursor(commit=True) as cur:
        cur.execute("delete from events where occurred_at < now() - (%s || ' days')::interval",
                    (int(days),))
        return cur.rowcount


def prune_matches(days: int = 180) -> int:
    """Delete match rows for postings long gone, except ones the user acted on.

    `matches` growth is bounded (matcher.MAX_PICKS per profile per run), but it still
    accumulates. Rows the user acted on are kept indefinitely — they're the user's own
    history. That now includes `dismissed`, which since the /hidden page is a deliberate
    subscriber action rather than the dead internal value it used to be: pruning one would
    delete a row off the page they can see it on, and if the posting were ever reactivated
    and re-scored it would come back unhidden, which is the one outcome hiding promises
    against. `digest_sends` is deliberately NOT pruned: it's the never-repeat-a-job ledger
    and must outlive the posting."""
    with cursor(commit=True) as cur:
        cur.execute(
            "delete from matches m using postings p "
            "where m.posting_id = p.posting_id and p.is_active = false "
            "and m.status = 'new' "
            "and p.last_seen_at < now() - (%s || ' days')::interval",
            (int(days),),
        )
        return cur.rowcount


#: Eligibility values a subscriber may be shown, given which countries they picked.
#:
#: `postings.eligibility` is written by `search_jobs.eligibility`, whose own docstring still
#: reads *"Coarse EU-eligibility flag for a Czech-based candidate"* — it was written when this
#: repo served one person. It encodes a **subscriber-specific judgement in a posting-level
#: column**, so the allowlist that reads it cannot be a constant.
#:
#: Measured 2026-08-07, and this is why it matters: `profiles.eligible_only` defaults **true**
#: (`schema.sql`) and has **no UI control** — it exists only as a type in `web/lib/api.ts`, and
#: no form writes it. So the constant allowlist that used to sit here silently withheld
#: **21 291 of 21 677 active US postings (98%)** from every subscriber, including one who had
#: explicitly picked the United States, with no way to turn it off. The US half of the
#: 2026-08-05 "US is now selectable" change had therefore never worked. It bit nobody only
#: because all three subscribers were CZ-only — the invisibility this repo keeps rediscovering.
#:
#: Three rules encoded below:
#:   - **`likely needs US work auth` is admitted only if the subscriber picked `US`.** For an
#:     EEA-only subscriber it is a true statement about a job they cannot take; for a US
#:     subscriber it describes ordinary US employment.
#:   - **`verify UK right-to-work` stays unconditional.** It is advisory ("verify"), it is
#:     already what 5 557 of 5 749 GB rows carry, and making it conditional would *narrow*
#:     what existing subscribers see — a regression dressed as a fix.
#:   - **`blocked (clearance/US-only)` is never admitted.** A security clearance or a
#:     citizenship requirement is not something a country preference can unlock.
#:
#: Canada needs no branch: `work_region("Toronto", "CA")` returns `"other"`, so CA rows carry
#: `unknown` (1 808 of 1 946) and always passed. That is luck rather than design — if
#: `work_region` ever grows a `CA` branch those rows vanish from every shortlist with no error,
#: which is what `test_geo_sql.py` now pins.
_ELIGIBILITY_ALWAYS = ("eligible", "unknown", "verify UK right-to-work")


def eligibility_allowlist(profile: dict) -> list[str]:
    """The `postings.eligibility` values this subscriber may be shown.

    Shared by `query_candidates` and `query_shortlist_meta` deliberately: they are two queries
    behind one product promise, and a filter changed in one and not the other is the
    `hidden`/`match_count` failure in a new place.
    """
    allowed = list(_ELIGIBILITY_ALWAYS)
    if "US" in (profile.get("countries") or []):
        allowed.append("likely needs US work auth")
    return allowed


def query_candidates(profile: dict, limit: int = 100) -> list[dict]:
    """Cheap SQL prefilter for a profile — returns active postings before LLM scoring.

    One row per dedup_key (collapses near-duplicate roles across sources).
    """
    where = ["p.is_active"]
    params: list[Any] = []

    if profile.get("role_categories"):
        where.append("p.role_category = any(%s)")
        params.append(profile["role_categories"])
    loc_sql, loc_params = geo.location_predicate(profile)
    if loc_sql != "true":
        where.append(loc_sql)
        params.extend(loc_params)
    edu_sql, edu_params = education.education_predicate(profile)
    if edu_sql != "true":
        where.append(edu_sql)
        params.extend(edu_params)
    exp_sql, exp_params = experience.experience_predicate(profile)
    if exp_sql != "true":
        where.append(exp_sql)
        params.extend(exp_params)
    lang_sql, lang_params = language.language_predicate(profile)
    if lang_sql != "true":
        where.append(lang_sql)
        params.extend(lang_params)
    if profile.get("seniorities"):
        where.append("p.seniority = any(%s)")
        params.append(profile["seniorities"])
    if profile.get("work_types"):
        where.append("p.work_type = any(%s)")
        params.append(profile["work_types"])
    if profile.get("part_time_only"):
        where.append("p.is_part_time")
    if profile.get("eligible_only", True):
        where.append("p.eligibility = any(%s)")
        params.append(eligibility_allowlist(profile))

    params.append(limit)
    sql = f"""
        select distinct on (coalesce(p.dedup_key, p.posting_id))
               p.posting_id, p.source, p.title, p.company, p.url, p.location,
               p.city, p.country_code, p.remote_signal, p.work_mode, p.education_min,
               p.experience_min, p.language, p.remote_reach, p.reach_countries,
               p.region, p.eligibility, p.seniority, p.work_type, p.is_part_time,
               p.role_category, p.salary_raw, p.posted_at, p.description
        from postings p
        where {' and '.join(where)}
        order by coalesce(p.dedup_key, p.posting_id), p.last_seen_at desc
        limit %s
    """
    with cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def count_active() -> int:
    with cursor() as cur:
        cur.execute("select count(*) as n from postings where is_active")
        return cur.fetchone()["n"]


# --- retrieval for the AI matcher (retrieve -> rerank) --------------------------
# Recall-first: cast a wide keyword+role net, let the AI matcher do the precision.
# Keywords are bilingual (EN + CZ/SK) so localised titles still surface. They live in
# service/taxonomy.py alongside the classifier they have to stay consistent with.
_ROLE_KEYWORDS = taxonomy.SHORTLIST_KEYWORDS
_GENERIC_WORDS = {"my", "search", "digest", "job", "jobs", "the", "and", "a"}

# Label words that name something a *filter* already decides. Harvesting them into keyword
# recall does not widen the net towards what the subscriber wants — it widens it towards
# every posting that happens to use the word, and those postings then compete for the same
# 120 slots. Found on 2026-07-28 across the fifty-persona cohort: the label "Junior designer"
# put Junior HR Specialist, Junior IT Admin, Junior Payroll Admin and Junior Key Account
# Manager into a designer's shortlist — about 30 of 120 rows were "Junior <something else>",
# while `seniorities` was already filtering on exactly that word. Place names are handled the
# same way one step below, via `geo.is_place_term`.
_FILTER_WORDS = {
    # seniority — `profile["seniorities"]` already gates this
    "junior", "medior", "mid", "middle", "senior", "entry", "graduate", "absolvent",
    "juniorni", "juniorní", "seniorni", "seniorní",
    # …and the rungs added on 2026-08-12. Deliberately NOT here: "lead", "head", "director"
    # and "manager". Those are filter words on the posting side but ordinary *subject-matter*
    # words on the subscriber's side — someone whose label says "lead generation" or "head of
    # content" means the work, not the level, and dropping the word would cost them the
    # retrieval it was carrying. The rule that makes dropping safe ("the filter still applies")
    # does not hold when the word means two different things.
    # "stage" is excluded for the same reason: it is an internship in French and a theatre or
    # a pipeline step everywhere else.
    "intern", "internship", "stáž", "staz", "praktikant", "praktyka", "praktykant",
    "werkstudent", "trainee", "absolwent", "stagiaire", "tirocinio",
    # work arrangement — `remote_scope` / `geo.is_fully_remote` / `part_time_only` gate these
    "remote", "hybrid", "onsite", "on-site", "office", "homeoffice", "home-office",
    "fulltime", "full-time", "parttime", "part-time", "uvazek", "úvazek",
    # filler that survives the >2-char rule
    "only", "for", "with", "new", "role", "roles", "position", "positions",
    "work", "prace", "práce", "pozice", "hledam", "hledám",
}


def _shortlist_terms(profile: dict) -> list[str]:
    """Keyword terms to OR-match a profile against title+company+description.

    Categories and `stack` are taken as given — the subscriber typed them as subject matter.
    The **label** is different: it is a free-text name for the search, so it is mined for
    words, and a word that merely restates a filter (a place, a seniority, "remote") is
    dropped rather than searched for. Dropping is safe in a way that keeping is not: the
    corresponding filter still applies, so nothing the subscriber asked for can be lost,
    whereas keeping the word lets an unrelated posting match on it.
    """
    terms: list[str] = []
    for cat in (profile.get("role_categories") or []):
        terms += _ROLE_KEYWORDS.get(cat, [cat.replace("_", " ")])
    terms += [s for s in (profile.get("stack") or [])]
    for w in (profile.get("label") or "").split():
        wl = w.lower().strip(".,")
        if len(wl) > 2 and wl not in _GENERIC_WORDS and wl not in _FILTER_WORDS \
                and not geo.is_place_term(wl):
            terms.append(wl)
    # de-dup preserving order, cap so the tsquery stays sane
    seen, out = set(), []
    for t in (x.strip().lower() for x in terms if x and x.strip()):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:40]


# Below this many candidates a shortlist is treated as retrieval failure rather than as a
# true "nothing fits here" — see the widening step in `query_shortlist_meta`.
SHORTLIST_FLOOR = int(os.environ.get("SHORTLIST_FLOOR", "20"))


def _hard_gate(profile: dict) -> tuple[list[str], list[Any]]:
    """The filters that decide *admissibility* — location, education, eligibility.

    Split out because the vector shadow path (`query_shortlist_vector`) has to apply exactly
    these and nothing else. A copy would drift, and the drift would be invisible: the shadow
    is compared against the live shortlist to decide whether to switch ranking, so a shadow
    gated differently would make the comparison meaningless while still producing a plausible
    number. Same reasoning as `source_watchdog` reading `search_jobs.source_classes` and the
    CI skip-check globbing rather than listing.

    What is *not* here is the recall predicate (`role_category` OR keyword). That is the part
    the vector is meant to replace, so it stays in the caller.
    """
    where = ["p.is_active"]
    params: list[Any] = []

    loc_sql, loc_params = geo.location_predicate(profile)
    if loc_sql != "true":
        where.append(loc_sql)
        params.extend(loc_params)
    # Deliberately a hard gate rather than part of recall: widening retrieval must not
    # quietly re-admit roles demanding a qualification the subscriber said they do not have.
    # A posting whose requirement is unknown passes either way (~97% of them).
    edu_sql, edu_params = education.education_predicate(profile)
    if edu_sql != "true":
        where.append(edu_sql)
        params.extend(edu_params)
    # Fifth axis, same argument arm for arm: a posting demanding more years than the
    # subscriber has is inadmissible however wide retrieval goes; an unknown requirement or a
    # profile that never stated its years filters nothing (see experience.experience_predicate).
    exp_sql, exp_params = experience.experience_predicate(profile)
    if exp_sql != "true":
        where.append(exp_sql)
        params.extend(exp_params)
    # Sixth axis, and a hard gate for the same reason as education/experience: widening
    # retrieval must not re-admit a posting written in a language the subscriber cannot read.
    # A null language (unknown), an English posting, or a profile that declared no languages
    # all pass — see language.language_predicate.
    lang_sql, lang_params = language.language_predicate(profile)
    if lang_sql != "true":
        where.append(lang_sql)
        params.extend(lang_params)
    if profile.get("eligible_only", True):
        where.append("p.eligibility = any(%s)")
        params.append(eligibility_allowlist(profile))
    return where, params


def query_shortlist(profile: dict, limit: int = 120) -> list[dict]:
    """Recall-first candidate shortlist for the AI matcher. See `query_shortlist_meta`."""
    rows, _ = query_shortlist_meta(profile, limit=limit)
    return rows


def query_shortlist_meta(profile: dict, limit: int = 120) -> tuple[list[dict], dict]:
    """`query_shortlist` plus what happened building it: `{n, widened, n_narrow}`.

    The metadata exists so the caller can record *why* a subscriber got what they got
    (service/watchdog.py, `digest_runs`). A shortlist that came back near-empty and a
    shortlist the matcher rejected look identical downstream, and they want opposite fixes.

    A posting is a candidate if it matches the profile's role_categories OR any of its
    keyword terms (full-text over title+company+description). Region/eligibility still
    scope the net so a CZ-only user isn't flooded with US-onsite roles, but the
    role_category filter is *not* a hard gate — `uncategorised` and cross-language hits
    are included, and the AI pass decides what actually fits. One row per dedup_key.

    Slots are allocated round-robin across the profile's selected role_categories rather
    than handed to whichever category happens to be freshest. The categories differ in size
    by orders of magnitude — `other_tech_function` spans marketing, sales, finance, HR and
    legal (~7k active CZ rows) while `design` has ~120 — so a global freshest-first window
    gave a subscriber who picked product + design + social_media + marketing a shortlist of
    117 marketing/admin rows, 2 design and 1 social (observed 2026-07-26, and the matcher
    correctly returned zero picks from it). Ranking within each category and then taking
    rank 1 of every category, rank 2 of every category, and so on spreads the window evenly
    and self-balances: a category with only 4 postings contributes 4 and stops, its unused
    slots going to the categories that still have rows. Postings that matched on keywords
    rather than a selected category share one `__other__` bucket, so they cannot crowd out
    the categories the subscriber actually chose. With no role_categories set there is a
    single bucket and this degenerates to the old freshest-first behaviour.

    **Ordering: `first_seen_at`, then a daily rotation — not `last_seen_at`.** The window
    used to be "freshest first" by `last_seen_at`, which ordered nothing at all:
    `last_seen_at` is stamped once per *ingest run*, not per posting, so on 2026-07-28 the
    19 439 active postings held **10 distinct values and 12 390 shared one**. A 120-row
    window over a 12 390-row tie is resolved by physical scan order, which is why one
    subscriber's shortlist read alphabetically by title — it was the scraper's listing order,
    surfacing. Two things followed, both invisible: genuinely new postings got no preference,
    and because an arbitrary order is nonetheless a *stable* one, a subscriber saw
    substantially the same 120 candidates every day forever, with only the ~5 emailed ones
    ever leaving. Most of the inventory was unreachable by anyone.

    `posted_at` cannot carry this alone — it is null for jobs.cz (9 451), profesia (3 994),
    startupjobs and cocuma, 14 291 of 19 439 rows. `first_seen_at` is populated for every row
    and is genuinely informative (1 414 rows first seen today, 428 the day before, and so on).
    The `md5(posting_id || current_date)` tiebreak then shuffles what remains — deterministic
    within a day, so a re-export reproduces the same shortlist, but a different sample of the
    backlog each day instead of the same slice in perpetuity.

    `part_time_only` sorts rather than filters. Part-time is ~2% of inventory (200 of 11k
    active CZ postings), so a hard gate would leave such a subscriber with a near-empty
    shortlist and no digest — but leaving the ordering alone is worse in practice, because
    the freshest-first window of `limit` rows fills with full-time roles before a single
    part-time one appears. Sorting them to the front puts the postings the subscriber
    actually asked for in front of the model, which then applies the preference in scoring.

    **The retrieval floor.** When the recall predicate yields fewer than `SHORTLIST_FLOOR`
    candidates the query is re-run with the recall predicate dropped entirely — location and
    eligibility only, freshest first. This is the graceful-degradation path for a subscriber
    whose stated interests fall outside what the taxonomy models: someone in sales,
    cybersecurity or IT support today has no category of their own, and if their typed
    keywords also miss, category-OR-keyword retrieves almost nothing and they are emailed
    nothing, forever, silently. Widening hands the AI matcher a broad slice of live jobs in
    their location and lets it do the precision — which is the whole point of
    retrieve-then-rerank, and the one part of the system that needs no vocabulary. Note the
    direction: dropping the predicate widens, it does not narrow, because recall is an OR of
    category and keywords rather than a conjunction. The subscriber may still legitimately
    get nothing — but then it is the model's judgement on a fair shortlist, not a filter that
    could never have matched. `widened` is recorded so the difference stays visible.
    """
    def build(recall_on: bool) -> tuple[str, list[Any]]:
        # The hard gates are shared with the vector shadow path so the two cannot drift; note
        # they sit outside the `recall_on` branch, so they survive the widening pass.
        where, where_params = _hard_gate(profile)

        # Recall predicate: role_category match OR keyword match.
        if recall_on:
            recall, terms = [], _shortlist_terms(profile)
            if profile.get("role_categories"):
                recall.append("p.role_category = any(%s)")
                where_params.append(profile["role_categories"])
            if terms:
                tsq = " || ".join(["plainto_tsquery('simple', %s)"] * len(terms))
                recall.append(f"p.search_tsv @@ ({tsq})")
                where_params.extend(terms)
            if recall:
                where.append("(" + " or ".join(recall) + ")")

        # Bucket = the posting's role_category when the subscriber selected it, else
        # `__other__`. Passed even when empty: an empty array matches nothing, everything
        # lands in one bucket, and the round-robin collapses to plain freshest-first. Kept on
        # the widened pass too, so a widened shortlist still spreads across categories
        # instead of handing every slot to the biggest bucket.
        #
        # psycopg2 binds %s positionally by where it appears in the SQL *text*, and the
        # partition clause is written above the inner WHERE — so this parameter has to come
        # first, ahead of the where params, regardless of the order the fragments were built
        # in. Getting this wrong shifts every later parameter by one; see test_shortlist.py.
        params: list[Any] = [profile.get("role_categories") or [], *where_params, limit]
        # Newest-first *within* a bucket, ties rotated daily; part-time first for a
        # part-time-only subscriber. See the docstring for why it is not `last_seen_at`.
        order = ("d.is_part_time desc, d.first_seen_at desc, d.rotation"
                 if profile.get("part_time_only")
                 else "d.first_seen_at desc, d.rotation")
        sql = f"""
            select posting_id, source, title, company, url, location, city, country_code,
                   remote_signal, work_mode, remote_reach, reach_countries, education_min,
                   experience_min, language,
                   region, eligibility, seniority, work_type, is_part_time,
                   role_category, salary_raw, currency, posted_at, description
            from (
                select d.*,
                       row_number() over (
                           partition by case when d.role_category = any(%s)
                                             then d.role_category else '__other__' end
                           order by {order}
                       ) as bucket_rank
                from (
                    select distinct on (coalesce(p.dedup_key, p.posting_id))
                           p.posting_id, p.source, p.title, p.company, p.url, p.location,
                           p.city, p.country_code, p.remote_signal, p.work_mode,
                           -- Read by `matcher._reach_for_model`, not by this WHERE (the gate
                           -- using them is `geo.reach_predicate`, via `_hard_gate`). Selecting
                           -- them is what lets the model see *why* a remote row is admissible;
                           -- without them it read `remote=yes` and had no field that could say
                           -- "remote from within Poland only".
                           p.remote_reach, p.reach_countries,
                           p.education_min, p.experience_min, p.language,
                           p.region, p.eligibility, p.seniority, p.work_type, p.is_part_time,
                           p.role_category, p.salary_raw, p.currency, p.posted_at,
                           p.description, p.last_seen_at, p.first_seen_at,
                           md5(p.posting_id || current_date::text) as rotation
                    from postings p
                    where {' and '.join(where)}
                    order by coalesce(p.dedup_key, p.posting_id), p.last_seen_at desc
                ) d
            ) r
            order by r.bucket_rank, {order.replace('d.', 'r.')}
            limit %s
        """
        return sql, params

    def fetch(recall_on: bool) -> list[dict]:
        sql, params = build(recall_on)
        with cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    rows = fetch(recall_on=True)
    meta = {"n": len(rows), "n_narrow": len(rows), "widened": False}
    # Only worth widening if a recall predicate was actually applied — with neither
    # categories nor keywords the two queries are identical and the second is pure cost.
    has_recall = bool(profile.get("role_categories") or _shortlist_terms(profile))
    if len(rows) < SHORTLIST_FLOOR and has_recall:
        rows = fetch(recall_on=False)
        meta.update(n=len(rows), widened=True)
        logging.getLogger("service.store").info(
            "profile %s: shortlist of %d below floor %d — widened to %d (location only)",
            profile.get("id"), meta["n_narrow"], SHORTLIST_FLOOR, len(rows))
    return rows, meta


def query_shortlist_vector(profile: dict, limit: int = 120) -> list[dict]:
    """The same admissible set as `query_shortlist_meta`, ranked by embedding similarity.

    Written for `shortlist_shadow`: it decides nothing, is read by no delivery path, and
    exists so the vector ranking can be compared against the live one over real days before
    anyone considers switching. It applies `_hard_gate` — the *same* object the live query
    uses, not a copy — and drops only the recall predicate, which is the half the vector is
    meant to replace.

    **`hnsw.iterative_scan` is load-bearing and its absence is silent.** pgvector's HNSW scan
    gathers `hnsw.ef_search` (default 40) candidates from the index and applies the WHERE
    clause *afterwards*, so a selective gate starves it. Measured on production 2026-08-08,
    this shape of query returned **4 rows for a LIMIT of 120** on one live profile and **0 rows
    at every K** on another, against gated pools of 15 393 and 18 543 postings — no error, no
    log, just an absence, which is this repo's recurring failure shape. Recall then came out
    near-zero *and flat across K*, and flatness is the tell: a merely bad ranking still
    improves as K grows. `relaxed_order` keeps scanning until K rows survive the filter;
    measured against an exact scan it recovers 14 of 15 true hits at K=120 where the default
    recovered 2. Anything that later puts this ranking on the delivery path inherits the same
    trap.

    Cross-source duplicates are collapsed on `dedup_key` in Python rather than with
    `distinct on`, because `distinct on` requires a sort over the whole gated set and would
    throw away the index scan this is supposed to be measuring. Over-fetching then collapsing
    keeps the query production-shaped.
    """
    vec = profile.get("embedding")
    if vec is None:
        return []
    # psycopg2 hands a halfvec back as its own text literal, which is exactly the form the
    # cast wants — no float round-trip, so nothing is lost or reformatted on the way.
    lit = vec if isinstance(vec, str) else embed.to_pgvector(vec)

    where, params = _hard_gate(profile)
    where.append("p.embedding is not null")

    sql = f"""
        select p.posting_id,
               coalesce(p.dedup_key, p.posting_id) as dkey,
               p.embedding <=> %s::halfvec as dist
        from postings p
        where {' and '.join(where)}
        order by dist
        limit %s
    """
    # psycopg2 binds positionally by position in the SQL *text*: the select-list vector comes
    # first, then the gate's params, then the limit. `order by dist` refers to the select-list
    # alias so the vector literal appears once — ordering by the expression a second time
    # would shift every later parameter by one. See the same note in `query_shortlist_meta`.
    with cursor() as cur:
        # pgvector registers its GUCs in _PG_init, which has not run on a freshly pooled
        # connection; `set hnsw.*` raises "unrecognized configuration parameter" until some
        # vector expression forces the library to load. `set local` is scoped to this
        # transaction, which the pool rolls back on return, so no other query inherits it.
        cur.execute("select '[1,0]'::vector <=> '[0,1]'::vector")
        cur.execute("set local hnsw.iterative_scan = relaxed_order")
        cur.execute(sql, [lit, *params, limit * 2])
        rows = [dict(r) for r in cur.fetchall()]

    out, seen = [], set()
    for r in rows:
        if r["dkey"] in seen:
            continue
        seen.add(r["dkey"])
        out.append({"posting_id": r["posting_id"],
                    "rank": len(out) + 1,
                    # cosine distance -> similarity, so a bigger number is a better match and
                    # the stored column reads the way a reader expects.
                    "similarity": 1.0 - float(r["dist"])})
        if len(out) >= limit:
            break
    return out


def record_shortlist_shadow(profile_id: str, rows: list[dict],
                            live_ids: Iterable[str]) -> int:
    """Record today's vector ranking beside the live one. Emailed to nobody.

    Rewrites the day's rows rather than upserting them, so the table always holds what the
    most recent export actually computed instead of a merge of several attempts. `in_live` is
    the whole point of writing it now: whether the vector would have surfaced what the keyword
    path did is cheap to compute at this moment and impossible to reconstruct afterwards, once
    postings age out and `is_active` flips.
    """
    if not profile_id or not rows:
        return 0
    live = {str(x) for x in (live_ids or ())}
    values = [(str(profile_id), r["posting_id"], r["rank"], r["similarity"],
               r["posting_id"] in live) for r in rows]
    with cursor(commit=True) as cur:
        cur.execute("delete from shortlist_shadow where day = current_date "
                    "and profile_id = %s", (profile_id,))
        psycopg2.extras.execute_values(
            cur,
            "insert into shortlist_shadow "
            "  (day, profile_id, posting_id, rank, similarity, in_live) values %s",
            values, template="(current_date, %s::uuid, %s, %s, %s, %s)")
    return len(values)


HIDDEN_STATUS = "dismissed"
"""`matches.status` value for a job the subscriber hid from their own list.

One status, not two: "I already applied" and "I don't want this" want the identical
outcome — the row leaves the matches page and stops being eligible for an email — and a
distinction nothing acts on is a distinction that only invites the two to drift. The column
and its index (`idx_matches_profile_status`) predate the feature, so hiding needs no
migration; `upsert_match` never writes `status`, so a re-score by the matcher cannot
resurrect something the subscriber hid."""


def _match_filters(skills_filter: list[str] | None = None,
                   work_modes: list[str] | None = None,
                   min_score: Optional[int] = None,
                   q: Optional[str] = None,
                   categories: list[str] | None = None,
                   countries: list[str] | None = None,
                   cities: list[str] | None = None,
                   seniorities: list[str] | None = None,
                   max_experience: Optional[int] = None,
                   *, skip: str = "") -> tuple[str, list]:
    """The /matches display filters, as one SQL fragment and its params.

    **One builder because the lockstep rule is structural, not a matter of discipline.**
    `matched_jobs` renders the rows and `match_count` renders the header above them, so a
    filter added to one and forgotten in the other reads as "127 matches" over a list that
    can only ever reach 124 — the drift `test_hidden_sql.py` already pins for `hidden`. Two
    hand-built clause strings is exactly how that happens; a single builder cannot.

    These are **display** filters over an existing match set, not matching criteria:
    `min_score` here is what the subscriber is looking at right now (the "great fits only"
    toggle), and is unrelated to `profiles.min_score`, which is the subscriber's stored email
    bar — it decides which matches *headline their digest* (see `store.profile_min_score` and
    `digest.build_digest`), not what the matcher stores. Neither one is the other: narrowing
    this page never changes what the matcher picks or what tomorrow's email contains, and the
    email bar never changes what this page shows.

    **`skip` names one filter to leave out, and every arm must honour it.** `match_facets`
    computes each menu with the other filters applied but never its own, which used to be
    expressed by passing `None` in that argument's position. That worked for three filters
    and does not scale to eight: the caller had to remember which positional argument to
    blank, and blanking the wrong one is a silently wrong count. Now every facet passes the
    *whole* filter set and names what to drop. A new filter therefore participates in every
    facet automatically — the failure this replaces was `_search_where`'s missing `cities`
    arm on 2026-08-12, which let the city menu narrow to the city already ticked.

    Params are positional, so the order returned here is the order the caller must splice
    them in — after `profile_id` and the status param, before `limit`/`offset`.
    """
    clauses: list[str] = []
    params: list = []
    if q and skip != "q":
        # `simple`, matching the generated column, and `plainto_tsquery` because it ANDs the
        # words and cannot raise on punctuation — the same reasoning as the public search,
        # and the same length cap, because this box is just as free-form.
        clauses.append("and p.search_tsv @@ plainto_tsquery('simple', %s)")
        params.append(str(q).strip()[:SEARCH_TERM_MAX])
    if skills_filter and skip != "skills":
        # Array overlap: a job matches if it names ANY selected skill (OR, the settled
        # multi-select behaviour).
        clauses.append("and p.skills && %s")
        params.append(list(skills_filter))
    if work_modes and skip != "work_modes":
        # A null `work_mode` (the ad never said) is excluded by design here: this is an
        # explicit request for a named setup, not the matcher's keep-unknown rule. The facet
        # never offers null as an option, so the menu cannot promise rows this drops.
        clauses.append("and p.work_mode = any(%s)")
        params.append(list(work_modes))
    if categories and skip != "categories":
        clauses.append("and p.role_category = any(%s)")
        params.append(list(categories))
    if countries and skip != "countries":
        clauses.append("and p.country_code = any(%s)")
        params.append(list(countries))
    if cities and skip != "cities":
        # Explicit (country, city) pairs, never a bare slug — the same rule and the same
        # reason as `_search_where`: a slug alone matches the same-named city in another
        # country's table.
        good = [(cc, slug) for cc, slug in (geo.split_city(c) for c in cities) if cc and slug]
        if good:
            ors = []
            for cc, slug in good:
                ors.append("(p.country_code = %s and p.city = %s)")
                params.extend([cc, slug])
            clauses.append("and (" + " or ".join(ors) + ")")
    if seniorities and skip != "seniorities":
        clauses.append("and p.seniority = any(%s)")
        params.append(list(seniorities))
    if max_experience is not None and skip != "max_experience":
        # Exclusion semantics, the opposite polarity of the equality filters above: the ask
        # is "drop roles demanding more than N years", so an unknown requirement is KEPT —
        # excluding it would hide ~75% of matches behind a filter that says nothing about
        # them. Same null rule as the digest gate (experience.experience_predicate).
        clauses.append("and (p.experience_min is null or p.experience_min <= %s)")
        params.append(int(max_experience))
    if min_score is not None and skip != "min_score":
        clauses.append("and m.score >= %s")
        params.append(int(min_score))
    return "\n              ".join(clauses), params


def matched_jobs(profile_id: str, limit: int = 50, offset: int = 0,
                 hidden: bool = False, exclude_sent: bool = False,
                 skills_filter: list[str] | None = None,
                 work_modes: list[str] | None = None,
                 min_score: Optional[int] = None,
                 q: Optional[str] = None,
                 categories: list[str] | None = None,
                 countries: list[str] | None = None,
                 cities: list[str] | None = None,
                 seniorities: list[str] | None = None,
                 max_experience: Optional[int] = None,
                 holdable_from: list[str] | None = None) -> list[dict]:
    """AI-picked jobs for a profile (matches join postings), best fit first.

    Read side for the digest: returns only active postings the matcher selected
    (score not null), highest score first. The digest still filters out anything in
    digest_sends so a job is never emailed twice.

    **`exclude_sent` is for the digest only, and it exists because `limit` is a window
    over a growing history.** `build_digest` asks for `limit * 6` rows and then drops the
    already-emailed ones *in Python*, so before this flag every job a subscriber had ever
    been sent still occupied a candidate slot — and the postings stay `is_active`, so those
    slots are never released. Measured in production on 2026-08-07: a subscriber 15 days
    into a daily digest had **21 of 30 slots** filled with jobs already in their inbox, the
    remaining 9 were all cross-id duplicates the dedupe step then collapsed, and the email
    went out with **1 job instead of 5 while 25 unsent matches scoring >= 6 sat outside the
    window**. Nothing errored, `sendable_n` recorded 1, and the outcome is indistinguishable
    from a quiet day — this repo's recurring failure shape. The ceiling tightens the longer
    someone subscribes, so it reads as the product going quiet on its most engaged users.

    It is **opt-in and must stay that way.** `/matches` is deliberately the complete record
    — an emailed job stays on the page — and `match_count` carries this function's filters
    *separately*, so defaulting it to True (or letting the web path pass it) would render
    "42 matches" above a list that stops at 30. That is the bug `test_hidden_sql.py` already
    guards for `hidden`; the same rule applies here.

    `offset` pages the /matches web page. The ordering must be total for that to be safe —
    `score desc, posted_at desc` is not, because posted_at is null for whole sources
    (jobs.cz, profesia), so ties are broken arbitrarily and a row could appear on two pages
    or on none. `posting_id` last makes it deterministic.

    **Hidden jobs are excluded by default, and that is deliberately also true of the
    digest** — `digest.build_digest` reads through here, so a job the subscriber hid because
    they already applied to it stops being emailed as well as stops being listed. Pass
    `hidden=True` for the /hidden page, which is the only place they remain visible: hiding
    must never be a delete, or unhiding could not exist. That view is ordered by when it was
    hidden (`updated_at`), because "what did I just hide" is the question it answers; a bulk
    hide stamps one `now()` across the batch, so `posting_id` breaks the tie and keeps the
    ordering total for paging here too.

    **`holdable_from` is the reach rule applied at the point of use, opt-in for exactly the same
    reasons `exclude_sent` is.** `geo.reach_predicate` gates *retrieval*, so from 2026-08-17 no
    new pick can be a remote job bound to a country the subscriber did not choose — but `matches`
    is a historical table and `build_digest` reads out of it. On the day the gate shipped one
    profile held **49 unsent, still-active, score-6-to-8 picks that the new rule refuses**, led by
    the same Poland-only "Data Engineer pharma; 100% remote" that had scored 8 that morning.
    Tomorrow's email would have been almost identical to the one that started the investigation,
    with the gate working perfectly upstream of it.

    It also covers a case retrieval cannot: `python -m service.backfill_remote_reach` can
    *reclassify* a posting after it was matched, so admissibility at match time is not
    admissibility at send time. Re-checking on read is the only place that holds.

    **In Python instead of SQL it would be worse than useless.** `build_digest` cuts a `limit * 6`
    window and filters afterwards; dropping 49 rows below the window is precisely the failure the
    2026-08-07 measurement above is about, with a different predicate.

    **Deliberately NOT added to `match_count`**, which the lockstep rule above would otherwise
    demand. `/matches` stays the complete record — an unusable job is still a job the matcher
    picked, and the page is the audit trail — so the count heading it is the count of that page.
    The digest is a different surface with a stricter promise: the same split `exclude_sent`
    already makes.
    """
    # The web-path display filters (skill chips, work-setup menu, "great fits only"), built
    # once in `_match_filters` and applied identically by `match_count` — see that docstring
    # for why they cannot be two hand-built strings.
    filt, filt_params = _match_filters(skills_filter, work_modes, min_score, q,
                                       categories, countries, cities, seniorities,
                                       max_experience)
    # A non-remote match is out of scope: it was admitted on its country and city, and asking
    # where its holder may *live* is the category error `reach_predicate` refuses to make. Written
    # after `filt` in the SQL text below, so its params sit between `filt_params` and the
    # limit/offset pair — positional binding, the same rule as everywhere else in this module.
    reach_sql, reach_params = "", []
    if holdable_from:
        predicate, reach_params = geo.reach_predicate(holdable_from, alias="p")
        reach_sql = f"and (coalesce(p.remote_signal, false) = false or {predicate})"
    params: list = ([profile_id, HIDDEN_STATUS] + filt_params + reach_params
                    + [limit, offset])
    with cursor() as cur:
        cur.execute(
            f"""
            select p.posting_id, p.source, p.title, p.company, p.url, p.location,
                   p.region, p.city, p.country_code,
                   p.eligibility, p.seniority, p.work_type, p.is_part_time,
                   p.remote_signal, p.work_mode, p.experience_min,
                   p.role_category, p.salary_raw, p.currency, p.posted_at, p.skills,
                   m.score, m.summary, m.scored_at
            from matches m
            join postings p on p.posting_id = m.posting_id
            where m.profile_id = %s and p.is_active and m.score is not null
              and m.status {'=' if hidden else '<>'} %s
              {filt}
              {'''and not exists (select 1 from digest_sends d
                                  where d.profile_id = m.profile_id
                                    and d.posting_id = m.posting_id)'''
               if exclude_sent else ''}
              {reach_sql}
            order by {'m.updated_at desc, p.posting_id'
                      if hidden else
                      'm.score desc, p.posted_at desc nulls last, p.posting_id'}
            limit %s offset %s
            """,
            params,
        )
        return [dict(r) for r in cur.fetchall()]


def match_count(profile_id: str, hidden: bool = False,
                skills_filter: list[str] | None = None,
                work_modes: list[str] | None = None,
                min_score: Optional[int] = None,
                q: Optional[str] = None,
                categories: list[str] | None = None,
                countries: list[str] | None = None,
                cities: list[str] | None = None,
                seniorities: list[str] | None = None,
                max_experience: Optional[int] = None) -> int:
    """How many active matches this profile has (same filter as matched_jobs) — used to
    show 'see all N matches' in the email and the page header.

    Must stay in lockstep with `matched_jobs`: the header is built from this and the rows
    from that, so a filter added to one and not the other reads as "127 matches" above a
    list that can only ever reach 124. Both now route through `_match_filters`, so the two
    cannot disagree by construction rather than by remembering to edit both."""
    filt, filt_params = _match_filters(skills_filter, work_modes, min_score, q,
                                       categories, countries, cities, seniorities,
                                       max_experience)
    params: list = [profile_id, HIDDEN_STATUS] + filt_params
    with cursor() as cur:
        cur.execute(
            f"""select count(*) as n
               from matches m join postings p on p.posting_id = m.posting_id
               where m.profile_id = %s and p.is_active and m.score is not null
                 and m.status {'=' if hidden else '<>'} %s
                 {filt}""",
            params,
        )
        return int(cur.fetchone()["n"])


def match_facets(profile_id: str, hidden: bool = False,
                 skills_filter: list[str] | None = None,
                 work_modes: list[str] | None = None,
                 min_score: Optional[int] = None,
                 great_fit_score: Optional[int] = None,
                 q: Optional[str] = None,
                 categories: list[str] | None = None,
                 countries: list[str] | None = None,
                 cities: list[str] | None = None,
                 seniorities: list[str] | None = None,
                 max_experience: Optional[int] = None) -> dict:
    """The options each /matches filter menu offers, with counts.

    **Every facet is computed with the other filters applied but never its own.** That is
    the rule the single-filter version stated as "unfiltered", and it means the same thing
    for one filter while staying true for three: a facet's job is to show what you *could*
    narrow to, so hiding its own siblings would let one tick empty the menu it came from.
    Applying the *other* filters is what keeps a count honest — with a work setup ticked,
    "Excel 12" must mean twelve remote Excel jobs, or the menu promises rows the list will
    not contain. A skill already ticked can still fall out of this set; the page renders the
    active filters as their own chips, so it stays removable either way.

    `work_modes` comes back in the canonical `geo.WORK_MODES` order rather than by count: it
    is a fixed three-value vocabulary, and a menu that reorders itself under the cursor as
    counts shift is worse than one whose options sit still. Skills stay most-common-first —
    with ~80 of them, that ordering *is* the affordance. A null `work_mode` is never offered,
    so the menu cannot advertise a filter that would drop those rows.

    `great_fit_score` asks "how many would the 'great fits only' toggle leave?" and is the
    one facet computed against a threshold the caller owns — the score ladder is policy
    (`MATCH_FLOOR` → `EMAIL_MIN_SCORE` → this), and `store` deliberately holds none of it.
    Returned as None when no threshold is passed, so the caller can hide a toggle that has
    nothing to offer rather than render "Great fits (0)".
    """
    status_op = '=' if hidden else '<>'
    # The whole active filter set, passed to every facet below with one name skipped. Built
    # once so a filter cannot be left out of a menu by being forgotten at a call site — see
    # `_match_filters`' `skip` note for the bug this shape replaces.
    active = dict(skills_filter=skills_filter, work_modes=work_modes, min_score=min_score,
                  q=q, categories=categories, countries=countries, cities=cities,
                  seniorities=seniorities, max_experience=max_experience)

    def where(skip: str) -> tuple[str, list]:
        return _match_filters(**active, skip=skip)

    out: dict = {}
    with cursor() as cur:
        # --- skills: every filter except the skill one -----------------------------------
        filt, filt_params = where("skills")
        cur.execute(
            f"""select s as skill, count(*) as n
               from matches m
               join postings p on p.posting_id = m.posting_id,
                    unnest(p.skills) as s
               where m.profile_id = %s and p.is_active and m.score is not null
                 and m.status {status_op} %s
                 {filt}
               group by s order by n desc, s""",
            [profile_id, HIDDEN_STATUS] + filt_params,
        )
        out["skills"] = [{"skill": r["skill"], "count": int(r["n"])} for r in cur.fetchall()]

        # --- work setup: every filter except the work-mode one ----------------------------
        filt, filt_params = where("work_modes")
        cur.execute(
            f"""select p.work_mode as value, count(*) as n
               from matches m join postings p on p.posting_id = m.posting_id
               where m.profile_id = %s and p.is_active and m.score is not null
                 and m.status {status_op} %s
                 and p.work_mode is not null
                 {filt}
               group by p.work_mode""",
            [profile_id, HIDDEN_STATUS] + filt_params,
        )
        by_mode = {r["value"]: int(r["n"]) for r in cur.fetchall()}
        out["work_modes"] = [{"work_mode": m, "count": by_mode[m]}
                             for m in geo.WORK_MODES if m in by_mode]

        # --- field / country / city / level ----------------------------------------------
        #
        # Same shape as the public feed's facets and the same rule: each computed with the
        # other filters applied but never its own. `cities` is offered only once a country is
        # chosen — a city list spanning every country a subscriber selected is a menu nobody
        # can read, and it is absent rather than empty so "you have not picked a country" and
        # "this country has no cities" stay different statements.
        simple = [("categories", "p.role_category", "categories"),
                  ("countries", "p.country_code", "countries"),
                  ("seniorities", "p.seniority", "seniorities")]
        if countries:
            simple.append(("cities", "lower(p.country_code) || ':' || p.city", "cities"))
        for key, expr, skip in simple:
            filt, filt_params = where(skip)
            cur.execute(
                f"""select {expr} as value, count(*) as n
                   from matches m join postings p on p.posting_id = m.posting_id
                   where m.profile_id = %s and p.is_active and m.score is not null
                     and m.status {status_op} %s
                     and {expr} is not null
                     {filt}
                   group by 1 order by n desc, 1""",
                [profile_id, HIDDEN_STATUS] + filt_params,
            )
            out[key] = [{"value": r["value"], "count": int(r["n"])} for r in cur.fetchall()]

        # --- great fits: every filter except the score one --------------------------------
        if great_fit_score is None:
            out["great_fit_count"] = None
        else:
            filt, filt_params = _match_filters(**{**active, "min_score": great_fit_score})
            cur.execute(
                f"""select count(*) as n
                   from matches m join postings p on p.posting_id = m.posting_id
                   where m.profile_id = %s and p.is_active and m.score is not null
                     and m.status {status_op} %s
                     {filt}""",
                [profile_id, HIDDEN_STATUS] + filt_params,
            )
            out["great_fit_count"] = int(cur.fetchone()["n"])
    return out


# --- profiles ------------------------------------------------------------------

def create_profile(user_id: str, data: dict) -> dict:
    data = {**data, **_location_prefs(data, ensure=True)}
    cols = ["user_id", "label", "stack", "seniorities", "countries", "cities",
            "remote_scope", "work_modes", "education_levels", "education_field",
            "understood_languages",
            "regions", "role_categories",
            "work_types", "part_time_only", "eligible_only", "sectors", "min_score"]
    vals = [user_id, data.get("label", "My search"), data.get("stack", []),
            data.get("seniorities", ["junior", "mid"]),
            data["countries"], data["cities"], data["remote_scope"],
            geo.clean_work_modes(data.get("work_modes")),
            education.clean_levels(data.get("education_levels")),
            education.clean_field(data.get("education_field")),
            language.clean_languages(data.get("understood_languages")),
            data["regions"],
            data.get("role_categories", []),
            data.get("work_types", ["permanent", "freelance/contract"]),
            data.get("part_time_only", False), data.get("eligible_only", True),
            data.get("sectors", []), data.get("min_score", 6)]
    placeholders = ",".join(["%s"] * len(cols))
    with cursor(commit=True) as cur:
        cur.execute(f"insert into profiles ({','.join(cols)}) values ({placeholders}) "
                    "returning *", vals)
        return dict(cur.fetchone())


def get_profile(profile_id: str) -> Optional[dict]:
    with cursor() as cur:
        cur.execute("select * from profiles where id = %s", (profile_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_profiles(user_id: str) -> list[dict]:
    with cursor() as cur:
        cur.execute("select * from profiles where user_id = %s order by created_at", (user_id,))
        return [dict(r) for r in cur.fetchall()]


def existing_profile_ids(ids: Iterable[str]) -> set[str]:
    """The subset of `ids` that are real profiles, as text.

    For validating input that crosses a trust boundary (the matcher's picks.json) before it
    reaches a foreign-key insert. Compares `id::text` rather than casting the input to uuid,
    so a malformed id is simply absent from the result instead of raising — the caller's
    input comes from outside, where garbage is expected traffic rather than an error.
    Casting the column defeats the PK index, which is fine: `profiles` is subscriber-sized.
    """
    wanted = [str(i) for i in ids if i]
    if not wanted:
        return set()
    with cursor() as cur:
        cur.execute("select id::text as id from profiles where id::text = any(%s)", (wanted,))
        return {r["id"] for r in cur.fetchall()}


# --- public job search ---------------------------------------------------------
#
# The free tier's feed: one SQL query over the corpus, no model, no tokens, no profile.
# `notes/scaling/PLAN.md` is why this exists — 128k postings from sources nobody can search
# in one place is the asset, and reading it costs nothing per user.
#
# Three things separate this from `query_shortlist`, and none of them are style:
#
#   - **There is no profile**, so there is no `_hard_gate`. Eligibility, education and the
#     location predicate are all *subscriber-specific judgements* and applying them to an
#     anonymous visitor would be wrong for everybody (the `postings.eligibility` rule). A
#     searcher gets what they asked for and nothing is silently withheld.
#   - **It is unauthenticated**, so every bound here is a real bound. `limit` is capped,
#     the total is capped, and the text term is length-capped before it reaches
#     `plainto_tsquery`.
#   - **Nothing is written.** No query is stored, no event is recorded from here. Storing
#     what a visitor searched for would be a new personal-data category and the privacy
#     policy does not cover it (security rule 4). If that ever changes, the policy changes
#     in the same commit.

SEARCH_LIMIT_MAX = 50
SEARCH_TERM_MAX = 80
#: Counting past this is a full scan that no UI reads — "500+" says the same thing as an
#: exact 11 843 and costs a bounded subquery instead of a sort over the corpus. The flag
#: comes back with it so the caller can render the "+" rather than quoting a cap as a fact.
SEARCH_TOTAL_CAP = 500

#: How long the **unfiltered** facet counts may be served from memory.
#:
#: Those two aggregates are the landing page's whole cost: every visitor who opens `/jobs`
#: asks the identical question, and answering it means a `distinct on` over the entire active
#: corpus, twice. Measured against production on 2026-08-12 that was ~3 s — long enough that
#: the filter row visibly arrived after the rest of the page. The answer changes once a day,
#: when the 03:00 ingest lands, so serving a few minutes of staleness costs a count that is
#: slightly behind and buys a menu that is there on first paint.
#:
#: Only the no-filter case is cached, deliberately. A narrower search is a different question
#: per visitor, so a cache keyed on the filter set would hold far more entries for far fewer
#: hits, and a stale count under a filter is harder to reason about than one under none.
SEARCH_FACET_TTL_SECONDS = int(os.getenv("SEARCH_FACET_TTL_SECONDS", "600"))

#: `(expires_at, value)` for the unfiltered facets, or None. A single slot rather than a dict
#: because exactly one query is cacheable — there is no key to get wrong.
_facet_cache: Optional[tuple[float, dict[str, list[dict]]]] = None


def clear_facet_cache() -> None:
    """Drop the cached unfiltered facets. For tests, which build a corpus and then ask about
    it inside one process — and for anything that needs the next read to hit the database."""
    global _facet_cache
    _facet_cache = None


def _facet_copy(value: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """A copy deep enough that a caller cannot mutate what the next caller will be handed.
    `webapp.search_jobs_public` filters these lists; nothing today mutates the dicts, and
    this is what keeps that from mattering."""
    return {k: [dict(f) for f in facets] for k, facets in value.items()}


#: Fully remote as the posting's own words describe it — no office attendance. This is a
#: *work-arrangement* claim, not a *geographic-eligibility* one: a "remote within Germany"
#: or "US-remote" posting is still `remote_signal is true`. Hoisted to a single definition so
#: every count and filter over it agree. Hybrid is deliberately excluded — hybrid is a commute,
#: not remote.
#:
#: It is no longer ORed into the location filter. Until 2026-08-14 the Country menu led with a
#: synthetic "Remote" row that did exactly that, which meant the menu answered two questions at
#: once: a Prague visitor ticking it was shown US-only roles they cannot legally take. Country now
#: means *where* and the Work-setup menu means *how* — see `_REACH_AREA_SQL`.
#:
#: Public, and it takes a `p` alias, because `service.backfill_remote_reach` reports over exactly
#: this set. It held the same predicate as a copied literal until 2026-08-14, which is the shape of
#: drift this file spends most of its comments on: two definitions of "fully remote" would let the
#: backfill's coverage report describe a different population than the one the site filters.
REMOTE_SQL = "(p.remote_signal is true or p.work_mode = 'remote')"

#: The international rows that lead the Country menu: postings whose scope reaches a named area
#: rather than a single country. `&&` is array overlap, index-backed by `idx_postings_reach_areas`.
#:
#: These are **places, not modes** — the same shape the old remote row had, and the same shape as
#: the digest's `location_predicate`: ORed into the country/city clause, so ticking Czechia and
#: EU-International means "Czech jobs, and also internationally-open remote ones". `reach_areas` is
#: null for a single-country scope, so a Germany-remote job is reached through Germany and never
#: through here, which is the whole point of the split.
_REACH_AREA_SQL = "(p.reach_areas && %s)"


def _search_where(q: Optional[str] = None,
                  countries: Optional[Iterable[str]] = None,
                  cities: Optional[Iterable[str]] = None,
                  categories: Optional[Iterable[str]] = None,
                  work_modes: Optional[Iterable[str]] = None,
                  seniorities: Optional[Iterable[str]] = None,
                  reach_areas: Optional[Iterable[str]] = None,
                  skip: str = "") -> tuple[list[str], list[Any]]:
    """The filter set, as SQL fragments and params. One definition, four callers.

    `skip` names a facet to leave out ("categories" | "countries" | "cities"), which is what
    makes the facet counts mean "how many would you get if you ticked this" rather than "how
    many you have already". Without it a facet's own filter zeroes every other option in its
    group and the menu collapses to the one thing already selected.

    **Every faceted filter needs its own `skip` arm here.** `cities` was added to the facet
    list on 2026-08-12 and read this function's `skip` argument without it being wired up —
    so the city menu narrowed to the city already ticked and could not be widened again.
    Silent, and invisible to every test that did not open the menu twice.

    **`reach_areas` are more selected places, not a mode filter.** They join the country/city
    clause by OR: "Czechia, and also internationally-open remote jobs" — the same shape as the
    digest's `location_predicate` (`onsite-in-countries OR remote`). With no country picked, the
    union has one arm and the selection means "internationally-open jobs"; the two readings are one
    rule. Because they belong to the location group, they are left out whenever a location facet is
    being computed (`skip` of "countries" or "cities") — otherwise every country would be counted
    over the international rows only, and one of their home cities would leak into another
    country's city menu.

    This replaced an `include_remote` boolean on 2026-08-14. That flag ORed in *every* fully-remote
    posting, so the Country menu's "Remote" row answered a work-arrangement question inside a
    location control and showed a Prague visitor US-only roles. Two lessons are preserved from it:
    the OR (it was ANDed until 2026-08-12, which *narrowed* a country search to
    remote-jobs-registered-in-that-country, a set nobody asked for) and the `skip` exclusion. The
    visitor who wants only remote work in one place still has the Work-setup filter, which is now
    the only place that asks about arrangement.
    """
    where = ["p.is_active"]
    params: list[Any] = []

    term = (q or "").strip()[:SEARCH_TERM_MAX]
    if term:
        # `simple`, matching the generated column: no stemming and no stopword list, so
        # Czech, Swedish and Norwegian titles tokenise the same way the index stored them.
        # `plainto_tsquery` ANDs the words and — unlike `to_tsquery` — cannot raise on
        # whatever punctuation a visitor types, which is the only reason it is safe to put
        # in front of an anonymous input box.
        where.append("p.search_tsv @@ plainto_tsquery('simple', %s)")
        params.append(term)

    place: list[str] = []
    place_params: list[Any] = []
    if countries and skip != "countries":
        # `reach_countries` (migration 023) is ORed in, never ANDed: a posting whose stored
        # `country_code` is Spain but which names Poland among its locations *is* a job in Poland,
        # and before this column the Poland filter answered it with silence. `country_code` alone
        # can only ever name one of them, because `city` has to agree with it.
        place.append("(p.country_code = any(%s) or p.reach_countries && %s)")
        place_params.extend([list(countries), list(countries)])

    if cities and skip != "cities":
        # Stored as `cz:prague` and validated by `split_city`, so a malformed one is dropped
        # rather than widening the search. Written as explicit (country, city) pairs rather
        # than a bare `city = any(...)`: the slug alone would match the same-named city in
        # another country's table, and the pair is exactly what `idx_postings_geo` indexes.
        good = [(cc, slug) for cc, slug in (geo.split_city(c) for c in cities) if cc and slug]
        if good:
            clauses = []
            for cc, slug in good:
                clauses.append("(p.country_code = %s and p.city = %s)")
                place_params.extend([cc, slug])
            place.append("(" + " or ".join(clauses) + ")")

    # Validated here rather than trusted: these arrive from a public URL parameter, and an
    # unrecognised id must be dropped before it reaches SQL. Dropping can only widen, because an
    # empty list means the international rows were not ticked at all.
    areas = geo.clean_reach_areas(reach_areas)
    area_arm = bool(areas) and skip not in ("countries", "cities")
    if place:
        place_sql = " and ".join(place)
        where.append(f"(({place_sql}) or {_REACH_AREA_SQL})" if area_arm
                     else f"({place_sql})")
        params.extend(place_params)
        if area_arm:
            params.append(areas)
    elif area_arm:
        where.append(_REACH_AREA_SQL)
        params.append(areas)

    if categories and skip != "categories":
        where.append("p.role_category = any(%s)")
        params.append(list(categories))

    if work_modes:
        where.append("p.work_mode = any(%s)")
        params.append(list(work_modes))

    if seniorities:
        where.append("p.seniority = any(%s)")
        params.append(list(seniorities))

    return where, params


#: The columns a public card may show. Deliberately not `select *`: `description`,
#: `eligibility`, `embedding` and the lifecycle timestamps have no business leaving the box,
#: and a `*` here would ship the next column somebody adds without anyone deciding to.
_SEARCH_COLS = ("posting_id", "title", "company", "url", "location", "city", "country_code",
                "remote_signal", "work_mode", "region", "seniority", "work_type",
                "is_part_time", "role_category", "salary_raw", "currency", "posted_at",
                "skills", "source")


def search_postings(q: Optional[str] = None,
                    countries: Optional[Iterable[str]] = None,
                    cities: Optional[Iterable[str]] = None,
                    categories: Optional[Iterable[str]] = None,
                    work_modes: Optional[Iterable[str]] = None,
                    seniorities: Optional[Iterable[str]] = None,
                    reach_areas: Optional[Iterable[str]] = None,
                    limit: int = 20,
                    offset: int = 0) -> tuple[list[dict], int, bool]:
    """One page of the public feed, plus `(total, total_is_capped)`.

    **Collapsed on `dedup_key`, unlike `/matches`.** The two pages are different promises:
    `/matches` is a subscriber's complete record and shows every row it holds, while this is
    a stranger browsing inventory, where the same job carried by a national register *and* the
    employer's own ATS board should read as one job. Same `distinct on (coalesce(dedup_key,
    posting_id))` the shortlist uses, so the two agree about what a duplicate is.

    **`posting_id` is the last sort key on every path, and that is not cosmetic.** Offset
    paging over a non-unique order is how a row appears on page 1 and again on page 2 while
    another is never shown at all — the ordering is resolved by physical scan order, which
    changes under you as the ingest rewrites rows. Ties on `first_seen_at` are the normal
    case here (an ingest stamps thousands of rows within the same second), not the rare one.
    """
    limit = max(1, min(int(limit or 20), SEARCH_LIMIT_MAX))
    offset = max(0, int(offset or 0))
    where, params = _search_where(q, countries, cities, categories, work_modes,
                                  seniorities, reach_areas)

    term = (q or "").strip()[:SEARCH_TERM_MAX]
    if term:
        # Rank only when there is something to rank by. `ts_rank_cd` over the same tsvector
        # the WHERE already matched, so the sort costs no second lookup.
        rank_sql = "ts_rank_cd(p.search_tsv, plainto_tsquery('simple', %s))"
        order = "d.rank desc, d.first_seen_at desc, d.posting_id"
    else:
        rank_sql = "0::real"
        order = "d.first_seen_at desc, d.posting_id"

    cols = ", ".join(f"p.{c}" for c in _SEARCH_COLS)
    out_cols = ", ".join(f"d.{c}" for c in _SEARCH_COLS)
    inner = f"""
        select distinct on (coalesce(p.dedup_key, p.posting_id))
               {cols}, p.first_seen_at, {rank_sql} as rank
        from postings p
        where {' and '.join(where)}
        order by coalesce(p.dedup_key, p.posting_id), p.last_seen_at desc
    """
    # The rank param sits in the SELECT list, which psycopg2 binds positionally by where it
    # appears in the SQL *text* — so it goes ahead of the WHERE params, whatever order the
    # fragments were built in. Same trap as the shortlist's bucket parameter.
    inner_params: list[Any] = ([term] if term else []) + params

    with cursor() as cur:
        cur.execute(f"select {out_cols} from ({inner}) d order by {order} limit %s offset %s",
                    inner_params + [limit, offset])
        rows = [dict(r) for r in cur.fetchall()]

        # Bounded count: stop at the cap rather than sorting the whole corpus to learn a
        # number the UI renders as "500+" anyway.
        cur.execute(f"select count(*) as n from (select 1 from ({inner}) d limit %s) c",
                    inner_params + [SEARCH_TOTAL_CAP + 1])
        n = int(cur.fetchone()["n"])

    return rows, min(n, SEARCH_TOTAL_CAP), n > SEARCH_TOTAL_CAP


def search_facets(q: Optional[str] = None,
                  countries: Optional[Iterable[str]] = None,
                  cities: Optional[Iterable[str]] = None,
                  categories: Optional[Iterable[str]] = None,
                  work_modes: Optional[Iterable[str]] = None,
                  seniorities: Optional[Iterable[str]] = None,
                  reach_areas: Optional[Iterable[str]] = None) -> dict[str, list[dict]]:
    """Category, country, city and international-reach counts for the current search, each computed
    with every filter *except its own* — so ticking one option never empties the menu it came from.

    **`reach_areas` is one row per area and the rows overlap**, so they do not sum to the number of
    postings behind them: a scope reading "North America or Europe" is counted under both, and that
    intersection is the only set in which someone in the EEA can hold a US-facing role. They lead
    the Country menu as location choices — see `_REACH_AREA_SQL`.

    **`cities` is present only when a country is selected**, and is absent (not empty) the
    rest of the time — the same distinction the API's `facets` key already makes on later
    pages, and for the same reason: an empty list reads as "this country has no cities".

    **The rows are not filtered to `taxonomy.CATEGORIES` here and must be by the caller.**
    Two reasons to leave it to the API layer rather than doing it in SQL: the canonical list
    lives in `service/taxonomy.py` and re-expressing it as a SQL literal is the copy that
    drifts, and a count that vanished silently in the database would be indistinguishable
    from a category with no inventory. As of 2026-08-12 production still holds ~1 058 rows
    whose `role_category` is a raw Swedish SSYK label — residue of the 2026-08-08 hint bug,
    draining over the staleness window — and a `select distinct role_category` straight into
    a public dropdown would offer `Butikssäljare, fackhandel` as a filter.

    **The unfiltered case is served from memory for `SEARCH_FACET_TTL_SECONDS`.** It is the
    landing page's question, identical for every visitor and ~3 s to answer; see the constant.
    Anything with a term or a filter goes to the database every time.
    """
    global _facet_cache

    unfiltered = not (q or "").strip() and not (
        countries or cities or categories or work_modes or seniorities or reach_areas)
    if unfiltered and _facet_cache is not None:
        expires_at, cached = _facet_cache
        if time.monotonic() < expires_at:
            return _facet_copy(cached)

    # Each expression yields an **array** of facet values for one posting, because one of them
    # genuinely has several: since migration 023 a posting can belong to more than one country.
    # The country facet has to count exactly what ticking that country would return, or the menu
    # and the results disagree — the failure this function's `skip` argument already exists to
    # prevent, arriving through a different door. `array_agg(distinct ...)` because `country_code`
    # is usually *also* in `reach_countries`, and counting it twice would inflate every count that
    # this change is meant to correct.
    _countries_expr = ("(select array_agg(distinct c) from unnest("
                       "array_append(coalesce(p.reach_countries, '{}'::text[]), p.country_code)"
                       ") c where c is not null)")
    wanted = [("categories", "array[p.role_category]", "categories"),
              ("countries", _countries_expr, "countries")]
    if countries:
        # **The city menu is only computed once a country is chosen**, which is also the only
        # time the UI offers it. Two reasons, and the second is the load-bearing one: a city
        # list spanning every country is a menu nobody can read, and this is a third scan of
        # the corpus — on the landing page, the one path that must stay fast.
        #
        # Emitted as `cz:prague`, the same `geo.qualify` pair `_search_where` takes back and
        # `profiles.cities` stores. A bare slug could not round-trip: several countries have a
        # city of the same name, which is why the filter matches on the pair.
        wanted.append(("cities", "array[lower(p.country_code) || ':' || p.city]", "cities"))

    out: dict[str, list[dict]] = {}
    with cursor() as cur:
        for key, expr, skip in wanted:
            where, params = _search_where(q, countries, cities, categories, work_modes,
                                          seniorities, reach_areas, skip=skip)
            # Counted over the same dedup'd set the list shows, or the facet totals and the
            # result count disagree and neither is wrong-looking enough to notice.
            #
            # `||` yields NULL if either side is, so the `value is not null` test covers a
            # posting with a country but no resolved city without a second predicate.
            cur.execute(f"""
                select v.value, count(*) as count
                from (
                    select distinct on (coalesce(p.dedup_key, p.posting_id))
                           {expr} as vals
                    from postings p
                    where {' and '.join(where)}
                    order by coalesce(p.dedup_key, p.posting_id), p.last_seen_at desc
                ) d, unnest(d.vals) as v(value)
                where v.value is not null
                group by 1
                order by 2 desc, 1
            """, params)
            out[key] = [{"value": r["value"], "count": int(r["count"])}
                        for r in cur.fetchall()]

        # The international rows' counts, in the same shape as a country row so the frontend
        # renders them identically. Computed with every *non-location* filter applied but no
        # country, city or area of their own — they widen across all places, so a count must not
        # be narrowed by which country is ticked, exactly as a country facet leaves out the
        # country filter. Deduped over the same set the list shows.
        #
        # **One row per area, and they overlap**: a posting reaching both is counted under both, so
        # these two numbers deliberately do not sum to the number of postings behind them. Summing
        # them would double-count the 150 roles that are the most interesting ones in the set.
        #
        # `unnest` rather than one query per area: the areas are a closed two-value vocabulary
        # today, but a third would otherwise silently need a third round trip nobody would add.
        # `geo.REACH_AREAS` is the source of the order, so a new area appears here for free —
        # the same reason `source_watchdog` reads `search_jobs.source_classes` and not a copy.
        awhere, aparams = _search_where(q, None, None, categories, work_modes, seniorities)
        cur.execute(f"""
            select area, count(*) as count from (
                select distinct on (coalesce(p.dedup_key, p.posting_id))
                       coalesce(p.dedup_key, p.posting_id) as k, p.reach_areas
                from postings p
                where {' and '.join(awhere)} and p.reach_areas is not null
                order by coalesce(p.dedup_key, p.posting_id), p.last_seen_at desc
            ) d, unnest(d.reach_areas) as area
            where area = any(%s)
            group by 1
        """, aparams + [list(geo.REACH_AREAS)])
        counts = {r["area"]: int(r["count"]) for r in cur.fetchall()}
        out["reach_areas"] = [{"value": a, "count": counts.get(a, 0)}
                              for a in geo.REACH_AREAS]

    if unfiltered:
        # Two concurrent misses both compute and the second overwrites the first — harmless
        # duplicate work, and cheaper than holding a lock across a 3 s query.
        _facet_cache = (time.monotonic() + SEARCH_FACET_TTL_SECONDS, _facet_copy(out))
    return out


# --- matches -------------------------------------------------------------------

def upsert_match(profile_id: str, posting_id: str, score: Optional[int],
                 summary: Optional[str]) -> None:
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into matches (profile_id, posting_id, score, summary, scored_at, updated_at)
               values (%s, %s, %s, %s, now(), now())
               on conflict (profile_id, posting_id) do update set
                   score = excluded.score, summary = excluded.summary,
                   scored_at = now(), updated_at = now()""",
            (profile_id, posting_id, score, summary),
        )


def get_cached_scores(profile_id: str, posting_ids: list[str]) -> dict[str, dict]:
    if not posting_ids:
        return {}
    with cursor() as cur:
        cur.execute("select posting_id, score, summary from matches "
                    "where profile_id = %s and posting_id = any(%s)",
                    (profile_id, posting_ids))
        return {r["posting_id"]: dict(r) for r in cur.fetchall()}


def set_match_status(profile_id: str, posting_id: str, status: str) -> None:
    with cursor(commit=True) as cur:
        cur.execute("update matches set status = %s, updated_at = now() "
                    "where profile_id = %s and posting_id = %s",
                    (status, profile_id, posting_id))


def set_matches_hidden(profile_id: str, posting_ids: list[str], hidden: bool) -> int:
    """Hide (or unhide) several of this profile's matches at once. Returns rows changed.

    `profile_id` in the WHERE clause is the authorisation, not a convenience: the ids come
    from a browser and are trusted only as far as "some string a subscriber sent us". One
    belonging to another subscriber matches no row of *this* profile's, so the update is a
    no-op rather than a cross-account write, and nothing has to look up who owns what first.
    Unknown ids are silently ignored for the same reason.

    Unhiding restores `new` rather than whatever the row said before. Nothing in the live
    product writes `saved` or `applied` (`service/api.py` is the unshipped internal API), so
    there is no prior value to lose — and inventing a way to remember one would be storing a
    subscriber's history that no page can show them.
    """
    ids = [i for i in {str(p) for p in posting_ids} if i]
    if not ids:
        return 0
    with cursor(commit=True) as cur:
        cur.execute(
            "update matches set status = %s, updated_at = now() "
            "where profile_id = %s and posting_id = any(%s) and status <> %s",
            (HIDDEN_STATUS if hidden else "new", profile_id, ids,
             HIDDEN_STATUS if hidden else "new"),
        )
        return cur.rowcount


# --- email subscriptions (v1 digest product) -----------------------------------

_SUBSCRIBER_FIELDS = ["label", "stack", "seniorities", "countries", "cities",
                      "remote_scope", "work_modes", "regions", "role_categories",
                      "work_types", "part_time_only", "eligible_only", "sectors",
                      "min_score", "frequency", "language",
                      # migration 014. `education_levels` gates; `education_field` is free text
                      # for the matcher and is never filtered on. See service/education.py.
                      "education_levels", "education_field",
                      # migration 026. Languages the subscriber reads; empty = no filter.
                      "understood_languages"]

_LOCATION_KEYS = ("countries", "cities", "remote_scope", "regions")

# --- the email "strong fit" bar (profiles.min_score) ---------------------------
# One definition, because it is written here and read in two other places — `digest`
# (the strong/weak split of the actual email) and `webapp` (the `email_min_score` the
# /matches page renders its "strong" highlight from). Defining it in `store` keeps both
# importers on one function and avoids a circular import (digest already imports store).
#
# The default equals the schema default *and* `digest.EMAIL_MIN_SCORE` (6) — three copies
# of the same six that must not drift, so `digest` binds its constant to this one. The bounds
# are structural, not tunable: below `matcher.MATCH_FLOOR` (4) nothing is stored at all, so a
# lower bar is inert, and 10 is the top of the score scale.
DEFAULT_MIN_SCORE = 6
MIN_SCORE_FLOOR = 4
MIN_SCORE_CEIL = 10


def clean_min_score(value: object) -> int:
    """Clamp a caller-supplied min_score to the meaningful range; junk -> the default.

    Runs on every write (create and update), for the same reason `clean_work_modes` and
    `clean_levels` do: the value comes off a public request body and then decides which
    matches headline someone's email. Out of range is clamped rather than rejected — a
    hand-edited 99 should mean "the strictest bar", never a 500."""
    try:
        v = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_MIN_SCORE
    return max(MIN_SCORE_FLOOR, min(MIN_SCORE_CEIL, v))


def profile_min_score(profile: dict) -> int:
    """The subscriber's email 'strong fit' bar, clamped. A NULL/absent column reads as the
    default. Kept beside `clean_min_score` so the read and the write share one clamp."""
    raw = profile.get("min_score")
    return DEFAULT_MIN_SCORE if raw is None else clean_min_score(raw)


def _location_prefs(data: dict, current: Optional[dict] = None, *,
                    ensure: bool = False) -> dict:
    """The location columns to write, normalised and mutually consistent.

    One rule, applied on every write: `countries` / `cities` / `remote_scope` are the truth —
    they are what `geo.location_predicate` filters on — and `regions` is *derived* from them.

    Two paths have to be kept honest:

      * A caller that sends only `regions` (an older client, or the pre-v1 API) is translated
        the other way first. Writing its `regions` verbatim would leave the row's country list
        contradicting it, i.e. the SQL filter and the matcher prompt disagreeing about where
        the person wants to work — the exact class of silent mismatch this feature exists to
        remove.
      * A partial update that sends only `cities` is merged against the stored row, so a city
        list is always validated against the countries actually selected.

    Returns {} when the caller changed no location field, so a plain "change my frequency"
    update does not rewrite four columns. `ensure=True` (subscription creation) always
    produces a full set.
    """
    if not any(k in data for k in _LOCATION_KEYS):
        if not ensure:
            return {}
        data = {"regions": ["cz", "eu", "worldwide"]}

    base = dict(current or {})
    merged = {k: (data[k] if k in data else base.get(k)) for k in _LOCATION_KEYS}
    if "regions" in data and not any(k in data for k in _LOCATION_KEYS[:3]):
        merged["countries"], merged["remote_scope"] = \
            geo.countries_and_scope_from_regions(data["regions"])
        merged["cities"] = []

    countries = geo.clean_countries(merged.get("countries"))
    if not countries:
        countries, implied = geo.countries_and_scope_from_regions(merged.get("regions") or [])
        if merged.get("remote_scope") is None:
            merged["remote_scope"] = implied
    scope = geo.clean_remote_scope(merged.get("remote_scope"))
    return {
        "countries": countries,
        "cities": geo.clean_cities(merged.get("cities"), countries),
        "remote_scope": scope,
        "regions": geo.regions_for(countries, scope),
    }


def create_email_subscription(email: str, data: dict, *, confirmed: bool = False) -> Optional[dict]:
    """Create a subscription with fresh tokens.

    Default (double opt-in): a `pending` row with a `confirm_token`, not emailed a digest until
    `confirm_subscription` flips it to active. With ``confirmed=True`` (Google-verified signup):
    an `active` row from the start, `confirmed_at` set and no `confirm_token` — because Google
    has already proved the user controls the address, so the confirm email is redundant.

    Returns None if a live subscription for this address already exists — the partial unique
    index `uq_profiles_live_email` rejects the duplicate insert. The caller normally checks
    `live_subscription_exists` first; this handles the rare concurrent-signup race, so one
    address can never fan out into two rows (which would mean two digests)."""
    email = email.strip().lower()
    data = {**data, **_location_prefs(data, ensure=True)}
    now = datetime.now(timezone.utc)
    confirm_token = None if confirmed else secrets.token_urlsafe(32)
    manage_token = secrets.token_urlsafe(32)
    status = "active" if confirmed else "pending"
    confirmed_at = now if confirmed else None
    # CV-derived signals (parse & discard): stored alongside, never the raw file.
    cv_cols = ["has_cv", "cv_summary", "years_experience"]
    cols = (["email", "status", "consent_at", "confirmed_at", "confirm_token", "manage_token"]
            + _SUBSCRIBER_FIELDS + cv_cols)
    vals = [
        email, status, now, confirmed_at, confirm_token, manage_token,
        data.get("label", "My digest"), data.get("stack", []),
        data.get("seniorities", ["junior", "mid"]),
        data["countries"], data["cities"], data["remote_scope"],
        geo.clean_work_modes(data.get("work_modes")), data["regions"],
        data.get("role_categories", []),
        data.get("work_types", ["permanent", "freelance/contract"]),
        data.get("part_time_only", False), data.get("eligible_only", True),
        data.get("sectors", []), clean_min_score(data.get("min_score")),
        data.get("frequency", "daily"),
        # Cleaned rather than trusted: this value comes off a public request body and then
        # decides which language every future email to this person is written in.
        i18n.clean_locale(data.get("language")),
        # Same rule as `work_modes`: normalised on write, so an empty or junk selection widens
        # to every level rather than narrowing to none and silently emptying the digest.
        education.clean_levels(data.get("education_levels")),
        education.clean_field(data.get("education_field")),
        # Normalised on write like the others; empty (the default) is "no preference" and the
        # gate reads it as no filter. Must sit last, matching `understood_languages`' position at
        # the end of `_SUBSCRIBER_FIELDS` — this insert binds by position.
        language.clean_languages(data.get("understood_languages")),
        data.get("has_cv", False), data.get("cv_summary"), data.get("years_experience"),
    ]
    placeholders = ",".join(["%s"] * len(cols))
    try:
        with cursor(commit=True) as cur:
            cur.execute(f"insert into profiles ({','.join(cols)}) values ({placeholders}) "
                        "returning *", vals)
            return dict(cur.fetchone())
    except psycopg2.errors.UniqueViolation:
        # The partial unique index `uq_profiles_live_email` rejected a duplicate live row for
        # this address (a concurrent second signup). cursor() has already rolled back; report
        # "no row created" so the caller returns the same generic reply.
        return None


def live_subscription_exists(email: str) -> bool:
    """True if this address already has a non-unsubscribed subscription (pending, active or
    paused). Guards /subscribe against creating a second row for an already-subscribed
    address — re-submitting the form must not fan out into multiple digests. Unsubscribed
    rows don't count, so a genuine re-subscribe (once suppression is cleared) still works."""
    with cursor() as cur:
        cur.execute(
            "select 1 from profiles where lower(email) = %s "
            "and status <> 'unsubscribed' limit 1",
            (email.strip().lower(),),
        )
        return cur.fetchone() is not None


def get_live_profile_by_email(email: str) -> Optional[dict]:
    """The single active/paused subscription for an address, or None.

    Used by Google sign-in to log an existing subscriber in by their verified email. Only a
    *live* row counts: a `pending` (unconfirmed consent) or `unsubscribed` address is not a
    login. `uq_profiles_live_email` guarantees at most one such row, so `limit 1` is exact."""
    with cursor() as cur:
        cur.execute(
            "select * from profiles where lower(email) = %s "
            "and status in ('active', 'paused') limit 1",
            (email.strip().lower(),),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def recent_signup_exists(email: str, within_minutes: int) -> bool:
    """True if a pending/active row for this email was created within the window.

    Used to rate-limit confirm emails per target address (anti joe-job) and to avoid
    piling up duplicate pending rows on repeated /subscribe. `within_minutes` is a
    server-controlled int, never user input."""
    with cursor() as cur:
        cur.execute(
            "select 1 from profiles where lower(email) = %s "
            "and status in ('pending','active') "
            "and created_at > now() - make_interval(mins => %s) limit 1",
            (email.strip().lower(), int(within_minutes)),
        )
        return cur.fetchone() is not None


def request_manage_link(email: str, cooldown_min: int) -> Optional[dict]:
    """Atomically claim the right to email a subscriber their own manage link.

    Returns the profile (carrying its `manage_token`) only when a link *should* be sent, else
    None. Two conditions are enforced together in one UPDATE — so there is no check-then-act
    race — and both are load-bearing for the endpoint's safety:

    * The address must have a live, confirmed subscription: status `active` or `paused`. A
      `pending` row hasn't completed double opt-in (it re-confirms via /subscribe), and an
      `unsubscribed` address must never be re-contacted.
    * No manage link may have been emailed to it within the last `cooldown_min` minutes. This
      is what stops /manage-link being an inbox-flooding primitive: knowing an address yields
      at most one email per window, and only ever to that address's own inbox.

    `uq_profiles_live_email` guarantees at most one live row per address, so the UPDATE touches
    at most one profile. `cooldown_min` is a server-controlled int, never user input."""
    with cursor(commit=True) as cur:
        cur.execute(
            """
            update profiles
               set manage_link_sent_at = now()
             where lower(email) = %s
               and status in ('active', 'paused')
               and (manage_link_sent_at is null
                    or manage_link_sent_at < now() - make_interval(mins => %s))
            returning *
            """,
            (email.strip().lower(), int(cooldown_min)),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def claim_ondemand_run(profile_id: str, cooldown_min: int) -> bool:
    """Atomically claim the right to run one on-demand match+send for this profile.

    Returns True only when the run *should* proceed, else False (still within the cooldown
    window, or not a live subscription). Same one-UPDATE, no-check-then-act shape as
    `request_manage_link`: two clients racing the button can never both claim the slot, so an
    on-demand run costs at most one metered API call per `cooldown_min` window regardless of
    how fast a browser re-posts.

    `profile_id` is the *resolved* subscriber (the endpoint has already authenticated the
    caller to this profile), and `cooldown_min` is a server-controlled int, never user input.
    Restricted to `active`/`paused` so an unsubscribed row can never be re-activated by the
    button."""
    with cursor(commit=True) as cur:
        cur.execute(
            """
            update profiles
               set last_ondemand_at = now()
             where id = %s
               and status in ('active', 'paused')
               and (last_ondemand_at is null
                    or last_ondemand_at < now() - make_interval(mins => %s))
            returning id
            """,
            (profile_id, int(cooldown_min)),
        )
        return cur.fetchone() is not None


def _get_by(field: str, token: str) -> Optional[dict]:
    with cursor() as cur:
        cur.execute(f"select * from profiles where {field} = %s", (token,))
        row = cur.fetchone()
        return dict(row) if row else None


def delete_profile(profile_id: str) -> None:
    """Hard-delete a subscription row (used to roll back a signup whose confirm email failed)."""
    with cursor(commit=True) as cur:
        cur.execute("delete from profiles where id = %s", (profile_id,))


def get_by_confirm_token(token: str) -> Optional[dict]:
    return _get_by("confirm_token", token)


def get_by_manage_token(token: str) -> Optional[dict]:
    return _get_by("manage_token", token)


# --- sessions (persisted magic-link login) -------------------------------------
#
# JobDigest stays passwordless; a session just lets a browser keep the identity it already
# proved by clicking a magic link, so returning visits need no token in the URL. The raw
# cookie token is a bearer credential, so we store only its SHA-256 — a DB/backup leak then
# yields no usable session (the same reason we hash passwords, applied to a product that has
# none). See migration_008_sessions.sql.

#: Idle timeout for a login session, slid forward on each use. Also the ceiling on how long
#: a logged-in browser stays authenticated without touching a magic link again.
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))


def _hash_token(raw: str) -> str:
    """SHA-256 of a raw cookie token. We store only this — for sessions and signup intents
    alike — so a DB/backup leak never yields a usable credential."""
    return hashlib.sha256(raw.encode()).hexdigest()


def create_session(profile_id: str, ttl_days: int = SESSION_TTL_DAYS) -> str:
    """Mint a session for a profile and return the RAW cookie token.

    Only the hash is stored, so the returned value exists nowhere in the DB. Callers must put
    it in a cookie and never persist it server-side."""
    raw = secrets.token_urlsafe(32)
    with cursor(commit=True) as cur:
        cur.execute(
            "insert into sessions (id, profile_id, expires_at) "
            "values (%s, %s, now() + make_interval(days => %s))",
            (_hash_token(raw), profile_id, int(ttl_days)),
        )
    return raw


def session_profile(raw: str, ttl_days: int = SESSION_TTL_DAYS) -> Optional[dict]:
    """Resolve a raw cookie token to its profile, sliding the expiry forward.

    Returns the full profile row, or None if the token is unknown or expired. Lookup and
    slide happen in one UPDATE so they can't race, and an expired row is never revived
    (`expires_at > now()` in the WHERE clause)."""
    if not raw:
        return None
    with cursor(commit=True) as cur:
        cur.execute(
            """
            update sessions
               set last_seen_at = now(),
                   expires_at   = now() + make_interval(days => %s)
             where id = %s and expires_at > now()
            returning profile_id
            """,
            (int(ttl_days), _hash_token(raw)),
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("select * from profiles where id = %s", (row["profile_id"],))
        prof = cur.fetchone()
        return dict(prof) if prof else None


def revoke_session(raw: str) -> None:
    """Delete a single session (log out this browser)."""
    if not raw:
        return
    with cursor(commit=True) as cur:
        cur.execute("delete from sessions where id = %s", (_hash_token(raw),))


def revoke_profile_sessions(profile_id: str) -> int:
    """Delete every session for a profile — used when a subscriber unsubscribes."""
    with cursor(commit=True) as cur:
        cur.execute("delete from sessions where profile_id = %s", (profile_id,))
        return cur.rowcount


def prune_expired_sessions() -> int:
    """Delete sessions past their idle timeout. Returns rows removed."""
    with cursor(commit=True) as cur:
        cur.execute("delete from sessions where expires_at < now()")
        return cur.rowcount


# --- Google-verified signup intents --------------------------------------------
#
# A short-lived server-side proof that Google confirmed an email, so the signup wizard can
# create an ACTIVE subscription without the double-opt-in confirm email. The raw token lives in
# a cookie; only its hash is stored. See migration_009_signup_intents.sql.

#: How long a Google-verified signup stays claimable — long enough to finish the wizard, short
#: enough that a stray cookie is quickly worthless.
SIGNUP_INTENT_TTL_MIN = int(os.environ.get("SIGNUP_INTENT_TTL_MIN", "30"))


def create_signup_intent(email: str, ttl_min: int = SIGNUP_INTENT_TTL_MIN) -> str:
    """Record that `email` was just Google-verified; return the RAW cookie token."""
    raw = secrets.token_urlsafe(32)
    with cursor(commit=True) as cur:
        cur.execute(
            "insert into signup_intents (id, email, expires_at) "
            "values (%s, %s, now() + make_interval(mins => %s))",
            (_hash_token(raw), email.strip().lower(), int(ttl_min)),
        )
    return raw


def signup_intent_email(raw: str) -> Optional[str]:
    """The verified email for a live (unexpired) intent, without consuming it — used to show
    the user which address they're signing up. None if unknown or expired."""
    if not raw:
        return None
    with cursor() as cur:
        cur.execute(
            "select email from signup_intents where id = %s and expires_at > now()",
            (_hash_token(raw),),
        )
        row = cur.fetchone()
        return row["email"] if row else None


def consume_signup_intent(raw: str) -> Optional[str]:
    """Atomically claim-and-delete an intent, returning its verified email or None. Single-use:
    the row is gone after this, so a replayed cookie can't create a second subscription."""
    if not raw:
        return None
    with cursor(commit=True) as cur:
        cur.execute(
            "delete from signup_intents where id = %s and expires_at > now() returning email",
            (_hash_token(raw),),
        )
        row = cur.fetchone()
        return row["email"] if row else None


def prune_expired_signup_intents() -> int:
    """Delete signup intents past their TTL. Returns rows removed."""
    with cursor(commit=True) as cur:
        cur.execute("delete from signup_intents where expires_at < now()")
        return cur.rowcount


#: How long a confirm link stays valid. Consent that is a year stale is not consent, and an
#: unbounded token is a permanent credential sitting in an inbox.
CONFIRM_TOKEN_TTL_DAYS = int(os.environ.get("CONFIRM_TOKEN_TTL_DAYS", "7"))


def confirm_subscription(confirm_token: str) -> tuple[Optional[dict], bool]:
    """Flip pending -> active on confirm-link click, consuming the token (single-use).

    Returns ``(profile, newly_confirmed)``. `newly_confirmed` is False only for a
    transitional row confirmed by the pre-single-use code that still carries a token; the
    caller must not re-send the welcome email in that case. Previously every click re-sent
    it, which made a confirm link an unlimited send-an-email primitive for anyone holding it.

    **Single-use:** the confirming update nulls `confirm_token`. A confirm link is not just
    a "please activate" signal — `/confirm` answers it with the subscriber's `manage_token`
    (a full-control bearer credential) in the page. Leaving the token live for the whole TTL
    meant a leaked or link-scanned confirm URL could be exchanged for that manage token for
    days after the real user confirmed. Consuming it closes that window and keeps a spent
    credential out of the DB and its backups. The trade-off is that a *repeat* click can no
    longer be tied back to a profile, so the page shows a generic "link no longer active"
    message instead of a personalised one — accepted, and arguably more private.

    Expires `CONFIRM_TOKEN_TTL_DAYS` after signup. The window is measured from
    `created_at` because that is when the token was issued and mailed.

    The CTE captures `confirmed_at` *before* the update; RETURNING alone would report the
    post-update value and every click would look new. The partial unique index on
    `confirm_token` is `where confirm_token is not null`, so nulling never collides.
    """
    with cursor(commit=True) as cur:
        cur.execute(
            """
            with before as (
                select id, confirmed_at from profiles where confirm_token = %s
            )
            update profiles p
               set status = 'active',
                   confirmed_at = coalesce(p.confirmed_at, now()),
                   confirm_token = null
              from before b
             where p.id = b.id
               and p.status in ('pending', 'active')
               and p.created_at > now() - (%s || ' days')::interval
            returning p.*, (b.confirmed_at is null) as newly_confirmed
            """,
            (confirm_token, int(CONFIRM_TOKEN_TTL_DAYS)),
        )
        row = cur.fetchone()
        if not row:
            return None, False
        profile = dict(row)
        return profile, bool(profile.pop("newly_confirmed", False))


def update_subscription(manage_token: str, data: dict) -> Optional[dict]:
    # Location fields are recomputed as a set, merged against the stored row: an update that
    # touches only `cities` still has to be validated against the countries on file, and
    # `regions` must be re-derived or it goes stale against them (see `_location_prefs`).
    location = _location_prefs(data, current=get_by_manage_token(manage_token) or {})
    if location:
        data = {**data, **location}
    # Normalised here rather than trusted from the caller: an empty selection means "no
    # preference" and must widen to all three, never narrow to none. The webapp validates too,
    # but this is the only path every client shares (see `geo.clean_work_modes`).
    if "work_modes" in data:
        data = {**data, "work_modes": geo.clean_work_modes(data["work_modes"])}
    # Same reasoning for education: an empty `education_levels` is "no preference" and widens.
    if "education_levels" in data:
        data = {**data, "education_levels": education.clean_levels(data["education_levels"])}
    if "education_field" in data:
        data = {**data, "education_field": education.clean_field(data["education_field"])}
    # Empty is "no preference" (no filter); anything unknown is dropped rather than narrowing to
    # nothing — the shared write path, same reason as work_modes/education above.
    if "understood_languages" in data:
        data = {**data, "understood_languages":
                language.clean_languages(data["understood_languages"])}
    if "language" in data:
        data = {**data, "language": i18n.clean_locale(data["language"])}
    # Clamped for the same reason the others are: it comes off a public body and then decides
    # which matches headline this person's email (see `clean_min_score`).
    if "min_score" in data:
        data = {**data, "min_score": clean_min_score(data["min_score"])}
    # `""` is how a client clears it (the webapp's exclude_none drops a literal null);
    # clean_years turns it into NULL, which is "no preference" and widens the gate.
    if "years_experience" in data:
        data = {**data, "years_experience": experience.clean_years(data["years_experience"])}
    sets, params = [], []
    # `years_experience` is updatable but deliberately NOT in _SUBSCRIBER_FIELDS: the create
    # path inserts it via `cv_cols`, and listing it in both would duplicate the column in
    # that INSERT. Update-only extension is the narrow fix.
    for f in [*_SUBSCRIBER_FIELDS, "years_experience"]:
        if f in data:
            sets.append(f"{f} = %s")
            params.append(data[f])
    if not sets:
        return get_by_manage_token(manage_token)
    params.append(manage_token)
    with cursor(commit=True) as cur:
        cur.execute(f"update profiles set {', '.join(sets)} where manage_token = %s returning *",
                    params)
        row = cur.fetchone()
        return dict(row) if row else None


def pause_subscription(manage_token: str, until: datetime) -> None:
    with cursor(commit=True) as cur:
        cur.execute("update profiles set status = 'paused', paused_until = %s "
                    "where manage_token = %s", (until, manage_token))


def resume_subscription(manage_token: str) -> None:
    with cursor(commit=True) as cur:
        cur.execute("update profiles set status = 'active', paused_until = null "
                    "where manage_token = %s", (manage_token,))


def unsubscribe(manage_token: str) -> Optional[str]:
    """Mark unsubscribed + add to the suppression list. Returns the email, or None."""
    with cursor(commit=True) as cur:
        cur.execute("update profiles set status = 'unsubscribed', unsubscribed_at = now() "
                    "where manage_token = %s returning email", (manage_token,))
        row = cur.fetchone()
        if not row:
            return None
        email = (row["email"] or "").lower()
        if email:
            cur.execute("insert into suppression (email, reason) values (%s, 'unsub') "
                        "on conflict (email) do nothing", (email,))
        return email


def sendable_profiles() -> list[dict]:
    """Active, confirmed, non-paused profiles with a deliverable (non-suppressed) email.
    Frequency-based due-ness is decided by the caller (pipeline).

    **`nulls first` is a priority order, not a tidy-up, and reversing it changes who gets
    sacrificed when a run is cut short.** This list is what `export_shortlists` iterates, so
    its order is the order profiles appear in `shortlists.json` — and the matcher reads that
    file with a finite budget. Whatever the limit is (a context window, a subscription's usage
    cap, a batch that fails half way), the profiles at the *end* are the ones that lose their
    digest. `last_digest_at nulls first` puts never-yet-sent subscribers at the front, so the
    people a truncated run starves are long-tenured ones who have had a digest recently,
    never someone waiting on their first. Among the rest, oldest-sent-first, for the same
    reason.

    That matters more as the list grows: at three subscribers nothing truncates, and the
    ordering looks arbitrary enough to "clean up" into `order by created_at` or drop entirely.
    `test_export_coverage_sql.py` fails if it does.
    """
    with cursor() as cur:
        cur.execute("""
            select p.* from profiles p
            left join suppression s on s.email = lower(p.email)
            where p.status = 'active' and p.email is not null and s.email is null
              and (p.paused_until is null or p.paused_until < now())
            order by p.last_digest_at nulls first
        """)
        return [dict(r) for r in cur.fetchall()]


# --- digest delivery bookkeeping -----------------------------------------------

def already_sent_ids(profile_id: str) -> set[str]:
    with cursor() as cur:
        cur.execute("select posting_id from digest_sends where profile_id = %s", (profile_id,))
        return {r["posting_id"] for r in cur.fetchall()}


def sent_job_keys(profile_id: str, days: int = 90) -> list[tuple[str, str, str | None]]:
    """(company, title, city) of jobs emailed to this profile within `days` — repeat guard.

    Deliberately not filtered on `is_active`: the case this exists for is a listing that
    expired and was re-posted under a new id, so the row we need to recognise is precisely
    the inactive one. Postings are never deleted (only deactivated, and `prune_descriptions`
    blanks the text while keeping company and title), so this history stays readable
    indefinitely. Normalisation lives in `digest.dedupe_key` — one definition, applied to
    both sides of the comparison."""
    with cursor() as cur:
        cur.execute(
            """
            select distinct p.company, p.title, p.city
            from digest_sends d
            join postings p on p.posting_id = d.posting_id
            where d.profile_id = %s
              and d.sent_at >= now() - (%s || ' days')::interval
            """,
            (profile_id, int(days)),
        )
        return [(r["company"], r["title"], r["city"]) for r in cur.fetchall()]


def record_sends(profile_id: str, items: list[tuple[str, int]]) -> None:
    """Persist (posting_id, score) pairs just emailed, so they're never re-sent."""
    if not items:
        return
    values = [(profile_id, pid, score) for pid, score in items]
    with cursor(commit=True) as cur:
        psycopg2.extras.execute_values(
            cur,
            "insert into digest_sends (profile_id, posting_id, score) values %s "
            "on conflict (profile_id, posting_id) do nothing",
            values, template="(%s,%s,%s)",
        )


def mark_digest_sent(profile_id: str) -> None:
    with cursor(commit=True) as cur:
        cur.execute("update profiles set last_digest_at = now() where id = %s", (profile_id,))


# --- per-subscriber outcomes (digest_runs) --------------------------------------
# Why this exists at all: every one of the five matching bugs found on 2026-07-26 was
# invisible from outside — no exception, no failed timer — because the only per-profile
# numbers the pipeline produced went to a log nobody reads, and the run summary aggregates
# across profiles so one starved subscriber hides among the healthy ones. See
# migration_011_digest_runs.sql.

#: The fields a stage may report. Anything else is ignored rather than trusted into SQL.
_DIGEST_RUN_FIELDS = ("shortlist_n", "widened", "picks_n", "sendable_n", "sent",
                      "shortlist_bytes")

#: The columns that predate migration 016, and the fallback set if the insert above fails.
#: Migrations here are manual `.sql` files that no deploy step applies, so "code deployed
#: before its migration" is an ordering people actually hit. Without this the whole INSERT
#: fails on the unknown column and the row is lost — including `shortlist_n`, which
#: `watchdog.diagnose` reads to tell a retrieval failure (ours) from the matcher correctly
#: rejecting a fair shortlist (not a bug). A measurement must never cost the diagnostics that
#: were already working; same rule as `matcher._record_shadow`.
_DIGEST_RUN_FIELDS_PRE_016 = ("shortlist_n", "widened", "picks_n", "sendable_n", "sent")


def record_digest_run(profile_id: str, **fields: Any) -> None:
    """Upsert today's outcome row for one profile. Never raises.

    Written by three stages that each learn different numbers (export → shortlist_n/widened,
    import → picks_n, pipeline → sendable_n/sent), so it merges rather than replaces: only
    the keys passed are touched. Deliberately swallows its own errors — this is diagnostics,
    and a failure to *record* that a subscriber got their digest must never be the reason a
    subscriber doesn't get their digest.
    """
    cols = [k for k in fields if k in _DIGEST_RUN_FIELDS]
    if not profile_id or not cols:
        return
    try:
        _write_digest_run(profile_id, fields, cols)
        return
    except Exception:                                  # pragma: no cover - defensive
        pass

    # Retry without anything newer than migration 011. If the column genuinely does not exist
    # yet, the row still lands and the watchdog keeps working; if the failure was something
    # else, this fails too and we log once below.
    legacy = [c for c in cols if c in _DIGEST_RUN_FIELDS_PRE_016]
    log = logging.getLogger("service.store")
    if legacy and len(legacy) < len(cols):
        try:
            _write_digest_run(profile_id, fields, legacy)
            log.warning("record_digest_run: dropped %s (missing column? apply pending "
                        "migrations); the rest of the row was written",
                        sorted(set(cols) - set(legacy)))
            return
        except Exception:                              # pragma: no cover - defensive
            pass
    log.warning("record_digest_run failed", exc_info=True)


def _write_digest_run(profile_id: str, fields: dict, cols: list[str]) -> None:
    with cursor(commit=True) as cur:
        cur.execute(
            f"""insert into digest_runs (day, profile_id, {', '.join(cols)})
                values (current_date, %s, {', '.join(['%s'] * len(cols))})
                on conflict (day, profile_id) do update set
                {', '.join(f'{c} = excluded.{c}' for c in cols)},
                recorded_at = now()""",
            (profile_id, *(fields[c] for c in cols)),
        )


def exported_profile_ids() -> set[str]:
    """Profiles today's export actually put candidates in front of the matcher for.

    This is the denominator for the coverage check in `matcher.coverage_gap`: a profile in
    this set that `picks.json` never mentions was silently dropped somewhere between the
    export and the file, and nothing else in the pipeline can see that. `picks_n` cannot
    answer it — the column is `not null default 0` (migration 011), so "absent from the
    file" and "the model returned nothing for them" are the same stored value, and the
    watchdog reads the second meaning. The question is only answerable against the file.

    `shortlist_n > 0` is the filter because `export_shortlists` records the run and *then*
    `continue`s on an empty shortlist — those profiles are legitimately not in the file, and
    they are already the watchdog's RETRIEVAL case rather than a coverage gap.

    Scoped to `current_date`, which both writers use: the export runs 03:00 UTC and the
    import 07:00 UTC, so they agree unless a run straddles midnight, and neither does.
    """
    with cursor() as cur:
        cur.execute("select profile_id::text as id from digest_runs "
                    "where day = current_date and shortlist_n > 0")
        return {r["id"] for r in cur.fetchall()}


def starved_profiles(days: int = 3) -> list[dict]:
    """Active subscribers who have had no digest sent in the last `days` days.

    Returns one row per profile with the diagnosis attached — the most recent shortlist size,
    whether the retrieval floor had to fire, and how many picks/sendable jobs there were — so
    the alert says *which* failure it is. The two look identical in a summary count and want
    opposite fixes: `shortlist_n` at or near zero (or `widened` true) means retrieval failed
    them and the bug is ours; a healthy `shortlist_n` with `picks_n` zero means the matcher
    saw a fair shortlist and correctly rejected it, which is not a bug at all.

    Profiles too new to have had a chance are excluded — a subscriber who signed up an hour
    ago has legitimately never been sent anything, and alerting on that would train whoever
    reads these to ignore them.
    """
    with cursor() as cur:
        cur.execute(
            """
            select p.id, p.email, p.created_at, p.last_digest_at,
                   r.day as last_run_day, r.shortlist_n, r.widened, r.picks_n, r.sendable_n
            from profiles p
            left join lateral (
                select * from digest_runs d
                where d.profile_id = p.id order by d.day desc limit 1
            ) r on true
            where p.status = 'active'
              and p.created_at < now() - (%s || ' days')::interval
              and (p.paused_until is null or p.paused_until < now())
              and (p.last_digest_at is null
                   or p.last_digest_at < now() - (%s || ' days')::interval)
            order by p.created_at
            """,
            (int(days), int(days)),
        )
        return [dict(r) for r in cur.fetchall()]


def unmet_demand_terms(min_profiles: int = 1) -> list[dict]:
    """Words subscribers asked for that no part of the taxonomy models.

    A free-text role chip that maps to no category is stored as a search keyword in
    `profiles.stack` (it still steers full-text retrieval, which is the point) — which also
    makes it the demand signal for what the taxonomy is missing, with no new data collected:
    this is derived entirely from what subscribers already told us.

    A heuristic and a report, never an input to anything automatic. Auto-creating a category
    from user input is how `social_media_specialist` — a value no posting could ever carry —
    ended up as a live filter matching nothing. Pattern order in `taxonomy.PATTERNS` is
    load-bearing (social_media must sit after design and before other_tech_function), and
    each new category needs a live backfill, so promotion stays a human decision informed by
    this list plus actual inventory.
    """
    from service import cvparse           # local: keeps store's import surface small

    known = {t.lower() for terms in taxonomy.SHORTLIST_KEYWORDS.values() for t in terms}
    known |= {c.replace("_", " ") for c in taxonomy.CATEGORIES}
    # `stack` also holds genuine skills, which are not unmet demand — a subscriber typing
    # "Figma" told us nothing about a missing category. Excluding the tools we already know
    # keeps the report to words the system has no representation for at all.
    known |= set(cvparse.SKILL_NAMES)
    with cursor() as cur:
        cur.execute(
            "select lower(unnest(stack)) as term, count(*) as n "
            "from profiles where status in ('active','pending') "
            "group by 1 having count(*) >= %s order by 2 desc, 1",
            (int(min_profiles),),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return [r for r in rows if r["term"] not in known]


# ---------------------------------------------------------------- title_categories --
# The memoised answers from the claude.ai routine (migration 019). Every query here is
# deliberately trivial: the *normalisation* that decides what a key is has one definition, in
# `service.categorize_exchange.normalise_title`, and expressing it a second time in SQL is how
# the cache would quietly stop hitting. Python groups; SQL stores.


def title_category_map() -> dict[str, str]:
    """Every title key the routine actually answered — declines excluded.

    Read once per ingest run and consulted only where `taxonomy.classify` declines, so it is
    a dict lookup per posting rather than a query per posting. A row whose category is
    `uncategorised` means "asked, no answer", which is what an absent key already means to
    the caller; carrying tens of thousands of them into the ingest would be memory spent to
    say nothing. `asked_title_keys` is the query that does need them.
    """
    with cursor() as cur:
        cur.execute("select title_key, category from title_categories "
                    "where category <> %s", (taxonomy.UNCATEGORISED,))
        return {r["title_key"]: r["category"] for r in cur.fetchall()}


def asked_title_keys() -> set[str]:
    """Every title key the routine has already been asked about, answered or not.

    "Have we asked?" and "do we know?" are different questions and the export needs the first
    one: without it, a title the model could not read is re-exported every run, forever, and
    the residue never shrinks no matter how many runs happen.
    """
    with cursor() as cur:
        cur.execute("select title_key from title_categories")
        return {r["title_key"] for r in cur.fetchall()}


def uncategorised_titles() -> list[dict]:
    """Active postings no classifier could read: `[{title, n}]`, commonest first.

    Raw titles, grouped by the exact string — the normalised grouping happens in Python for
    the reason at the top of this section. `n` is what makes a truncated export honest: the
    caller states how many titles it dropped and how many postings they stood for, rather
    than reporting the residue as covered.
    """
    with cursor() as cur:
        cur.execute("select title, count(*) as n from postings "
                    "where is_active and role_category = %s and title is not null "
                    "group by title order by n desc, title",
                    (taxonomy.UNCATEGORISED,))
        return [dict(r) for r in cur.fetchall()]


def upsert_title_categories(rows: Iterable[tuple[str, str, str]],
                            source: str = "routine") -> int:
    """Store `(title_key, category, sample_title)` answers. Returns rows written.

    Last answer wins, and `updated_at` moves with it, so re-asking a title after a taxonomy
    change is a re-import rather than a manual delete. The category is NOT validated here:
    `categorize_exchange.import_categories` is the trust boundary and drops anything that is
    not in `taxonomy.CATEGORIES` before it reaches this call — the same shape as
    `import_picks`, where validation lives at the boundary rather than in the writer.
    """
    values = [(k, c, (t or "")[:300], source) for k, c, t in rows if k and c]
    if not values:
        return 0
    with cursor(commit=True) as cur:
        psycopg2.extras.execute_values(
            cur,
            "insert into title_categories (title_key, category, sample_title, source) values %s "
            "on conflict (title_key) do update set category = excluded.category, "
            "sample_title = excluded.sample_title, source = excluded.source, "
            "updated_at = now()",
            values, page_size=500)
    return len(values)


def prune_unsubscribed(days: int = 30) -> int:
    """Delete profiles unsubscribed more than `days` ago. Returns rows removed.

    This is what makes the privacy policy's "your profile is deleted within 30 days" true —
    `unsubscribe()` only stops the sending, it deliberately leaves the row in place for a
    window. `matches` and `digest_sends` cascade from the foreign key; `events.profile_id`
    is set null, so analytics keep their shape without pointing at a person.

    `suppression` is keyed on email in its own table and is NOT pruned: it is the
    never-contact-again list and has to outlive the profile, otherwise deleting the profile
    would quietly make the address eligible for signup emails again.

    The delay is a safety property, not laziness. GET /unsubscribe acts on the request, so a
    mail client or corporate link scanner that pre-fetches links can unsubscribe someone who
    never clicked; a window keeps that recoverable instead of turning it into irreversible
    data loss.
    """
    with cursor(commit=True) as cur:
        cur.execute(
            "delete from profiles where status = 'unsubscribed' "
            "and unsubscribed_at is not null "
            "and unsubscribed_at < now() - (%s || ' days')::interval",
            (int(days),),
        )
        return cur.rowcount


def add_suppression(email: str, reason: str) -> None:
    with cursor(commit=True) as cur:
        cur.execute("insert into suppression (email, reason) values (%s, %s) "
                    "on conflict (email) do update set reason = excluded.reason",
                    (email.strip().lower(), reason))


def is_suppressed(email: str) -> bool:
    with cursor() as cur:
        cur.execute("select 1 from suppression where email = %s", (email.strip().lower(),))
        return cur.fetchone() is not None
