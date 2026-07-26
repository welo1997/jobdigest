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
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from service import taxonomy

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
    posting_id, source, title, company, url, description, location, country_code,
    remote_signal, salary_raw, currency, posted_at,
    role_category, region, eligibility, seniority, work_type, is_part_time, dedup_key,
    last_seen_at, is_active
) values %s
on conflict (posting_id) do update set
    title = excluded.title,
    company = excluded.company,
    description = excluded.description,
    location = excluded.location,
    country_code = excluded.country_code,
    remote_signal = excluded.remote_signal,
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
    last_seen_at = now(),
    is_active = true;
"""


def upsert_postings(rows: Iterable[dict]) -> int:
    """Insert/refresh postings. Bumps last_seen_at on re-ingest (freshness)."""
    values = [
        (
            r["posting_id"], r["source"], r.get("title"), r.get("company"), r["url"],
            r.get("description"), r.get("location"), r.get("country_code"),
            r.get("remote_signal"), r.get("salary_raw"), r.get("currency"),
            r.get("posted_at"),
            r.get("role_category"), r.get("region"), r.get("eligibility"),
            r.get("seniority"), r.get("work_type"), r.get("is_part_time", False),
            r.get("dedup_key"),
        )
        for r in rows
    ]
    if not values:
        return 0
    template = ("(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "now(), true)")
    with cursor(commit=True) as cur:
        psycopg2.extras.execute_values(cur, _UPSERT_SQL, values, template=template,
                                       page_size=500)
    return len(values)


def deactivate_stale(days: int = 7) -> int:
    """Mark postings not re-seen within `days` as inactive (drops expired listings)."""
    with cursor(commit=True) as cur:
        cur.execute(
            "update postings set is_active = false "
            "where is_active = true and last_seen_at < now() - (%s || ' days')::interval",
            (days,),
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
    accumulates. Saved/applied rows are kept indefinitely — they're the user's own history.
    `digest_sends` is deliberately NOT pruned: it's the never-repeat-a-job ledger and must
    outlive the posting."""
    with cursor(commit=True) as cur:
        cur.execute(
            "delete from matches m using postings p "
            "where m.posting_id = p.posting_id and p.is_active = false "
            "and m.status in ('new','dismissed') "
            "and p.last_seen_at < now() - (%s || ' days')::interval",
            (int(days),),
        )
        return cur.rowcount


def query_candidates(profile: dict, limit: int = 100) -> list[dict]:
    """Cheap SQL prefilter for a profile — returns active postings before LLM scoring.

    One row per dedup_key (collapses near-duplicate roles across sources).
    """
    where = ["p.is_active"]
    params: list[Any] = []

    if profile.get("role_categories"):
        where.append("p.role_category = any(%s)")
        params.append(profile["role_categories"])
    if profile.get("regions"):
        where.append("p.region = any(%s)")
        params.append(profile["regions"])
    if profile.get("seniorities"):
        where.append("p.seniority = any(%s)")
        params.append(profile["seniorities"])
    if profile.get("work_types"):
        where.append("p.work_type = any(%s)")
        params.append(profile["work_types"])
    if profile.get("part_time_only"):
        where.append("p.is_part_time")
    if profile.get("eligible_only", True):
        where.append("p.eligibility in ('eligible','verify UK right-to-work','unknown')")

    params.append(limit)
    sql = f"""
        select distinct on (coalesce(p.dedup_key, p.posting_id))
               p.posting_id, p.source, p.title, p.company, p.url, p.location,
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


def _shortlist_terms(profile: dict) -> list[str]:
    """Keyword terms to OR-match a profile against title+company+description."""
    terms: list[str] = []
    for cat in (profile.get("role_categories") or []):
        terms += _ROLE_KEYWORDS.get(cat, [cat.replace("_", " ")])
    terms += [s for s in (profile.get("stack") or [])]
    for w in (profile.get("label") or "").split():
        wl = w.lower().strip(".,")
        if len(wl) > 2 and wl not in _GENERIC_WORDS:
            terms.append(wl)
    # de-dup preserving order, cap so the tsquery stays sane
    seen, out = set(), []
    for t in (x.strip().lower() for x in terms if x and x.strip()):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:40]


def query_shortlist(profile: dict, limit: int = 120) -> list[dict]:
    """Recall-first candidate shortlist for the AI matcher.

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

    `part_time_only` sorts rather than filters. Part-time is ~2% of inventory (200 of 11k
    active CZ postings), so a hard gate would leave such a subscriber with a near-empty
    shortlist and no digest — but leaving the ordering alone is worse in practice, because
    the freshest-first window of `limit` rows fills with full-time roles before a single
    part-time one appears. Sorting them to the front puts the postings the subscriber
    actually asked for in front of the model, which then applies the preference in scoring.
    """
    where = ["p.is_active"]
    where_params: list[Any] = []

    if profile.get("regions"):
        where.append("p.region = any(%s)")
        where_params.append(profile["regions"])
    if profile.get("eligible_only", True):
        where.append("p.eligibility in ('eligible','verify UK right-to-work','unknown')")

    # Recall predicate: role_category match OR keyword match.
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

    # Bucket = the posting's role_category when the subscriber selected it, else `__other__`.
    # Passed even when empty: an empty array matches nothing, everything lands in one bucket,
    # and the round-robin collapses to plain freshest-first.
    #
    # psycopg2 binds %s positionally by where it appears in the SQL *text*, and the partition
    # clause is written above the inner WHERE — so this parameter has to come first, ahead of
    # the where params, regardless of the order the fragments were built in.
    params: list[Any] = [profile.get("role_categories") or [], *where_params, limit]
    # Freshest-first *within* a bucket; part-time first for a part-time-only subscriber.
    order = ("d.is_part_time desc, d.last_seen_at desc" if profile.get("part_time_only")
             else "d.last_seen_at desc")
    sql = f"""
        select posting_id, source, title, company, url, location,
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
                       p.region, p.eligibility, p.seniority, p.work_type, p.is_part_time,
                       p.role_category, p.salary_raw, p.currency, p.posted_at, p.description,
                       p.last_seen_at
                from postings p
                where {' and '.join(where)}
                order by coalesce(p.dedup_key, p.posting_id), p.last_seen_at desc
            ) d
        ) r
        order by r.bucket_rank, {order.replace('d.', 'r.')}
        limit %s
    """
    with cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def matched_jobs(profile_id: str, limit: int = 50) -> list[dict]:
    """AI-picked jobs for a profile (matches join postings), best fit first.

    Read side for the digest: returns only active postings the matcher selected
    (score not null), highest score first. The digest still filters out anything in
    digest_sends so a job is never emailed twice.
    """
    with cursor() as cur:
        cur.execute(
            """
            select p.posting_id, p.source, p.title, p.company, p.url, p.location,
                   p.region, p.eligibility, p.seniority, p.work_type, p.is_part_time,
                   p.role_category, p.salary_raw, p.currency, p.posted_at,
                   m.score, m.summary
            from matches m
            join postings p on p.posting_id = m.posting_id
            where m.profile_id = %s and p.is_active and m.score is not null
            order by m.score desc, p.posted_at desc nulls last
            limit %s
            """,
            (profile_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def match_count(profile_id: str) -> int:
    """How many active matches this profile has (same filter as matched_jobs) — used to
    show 'see all N matches' in the email and the page header."""
    with cursor() as cur:
        cur.execute(
            """select count(*) as n
               from matches m join postings p on p.posting_id = m.posting_id
               where m.profile_id = %s and p.is_active and m.score is not null""",
            (profile_id,),
        )
        return int(cur.fetchone()["n"])


# --- profiles ------------------------------------------------------------------

def create_profile(user_id: str, data: dict) -> dict:
    cols = ["user_id", "label", "stack", "seniorities", "regions", "role_categories",
            "work_types", "part_time_only", "eligible_only", "sectors", "min_score"]
    vals = [user_id, data.get("label", "My search"), data.get("stack", []),
            data.get("seniorities", ["junior", "mid"]),
            data.get("regions", ["cz", "eu", "worldwide"]),
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


# --- email subscriptions (v1 digest product) -----------------------------------

_SUBSCRIBER_FIELDS = ["label", "stack", "seniorities", "regions", "role_categories",
                      "work_types", "part_time_only", "eligible_only", "sectors",
                      "min_score", "frequency"]


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
        data.get("regions", ["cz", "eu", "worldwide"]),
        data.get("role_categories", []),
        data.get("work_types", ["permanent", "freelance/contract"]),
        data.get("part_time_only", False), data.get("eligible_only", True),
        data.get("sectors", []), data.get("min_score", 6),
        data.get("frequency", "daily"),
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
    sets, params = [], []
    for f in _SUBSCRIBER_FIELDS:
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
    Frequency-based due-ness is decided by the caller (pipeline)."""
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
