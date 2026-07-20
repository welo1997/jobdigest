"""Postgres store for the job-matching service.

Thin data-access layer over `postings`, `profiles`, `matches`. Uses psycopg2 with a
module-level connection pool. Connection string comes from DATABASE_URL, e.g.
    postgresql://jobmatch:jobmatch@localhost:5433/jobmatch   (local docker)
    postgresql://...supabase.co:5432/postgres                (Supabase)
"""

from __future__ import annotations

import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

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
# Keywords are bilingual (EN + CZ/SK) so localised titles still surface.
_ROLE_KEYWORDS: dict[str, list[str]] = {
    "data_engineering": ["data engineer", "analytics engineer", "datový inženýr", "etl", "dbt"],
    "data_analysis": ["data analyst", "bi analyst", "analytik", "power bi", "reporting"],
    "machine_learning": ["machine learning", "ml engineer", "data scientist", "ai engineer",
                         "strojové učení"],
    "software_engineering": ["software engineer", "developer", "vývojář", "programátor",
                            "backend", "frontend", "fullstack"],
    "devops_platform": ["devops", "sre", "platform engineer", "cloud engineer",
                       "kubernetes", "administrátor"],
    "product": ["product manager", "product owner", "produktový manažer", "produktový vlastník"],
    "design": ["designer", "designér", "ux", "ui", "grafik", "návrhář"],
    "other_tech_function": ["marketing", "marketingový", "obchod", "obchodní", "sales",
                           "finance", "účetní", "recruiter", "personalista"],
}
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
    """
    where = ["p.is_active"]
    params: list[Any] = []

    if profile.get("regions"):
        where.append("p.region = any(%s)")
        params.append(profile["regions"])
    if profile.get("eligible_only", True):
        where.append("p.eligibility in ('eligible','verify UK right-to-work','unknown')")

    # Recall predicate: role_category match OR keyword match.
    recall, terms = [], _shortlist_terms(profile)
    if profile.get("role_categories"):
        recall.append("p.role_category = any(%s)")
        params.append(profile["role_categories"])
    if terms:
        tsq = " || ".join(["plainto_tsquery('simple', %s)"] * len(terms))
        recall.append(f"p.search_tsv @@ ({tsq})")
        params.extend(terms)
    if recall:
        where.append("(" + " or ".join(recall) + ")")

    params.append(limit)
    # Inner query dedups (one row per dedup_key); outer takes the freshest `limit` so
    # the AI always sees current roles rather than an arbitrary alphabetical slice.
    sql = f"""
        select posting_id, source, title, company, url, location,
               region, eligibility, seniority, work_type, is_part_time,
               role_category, salary_raw, currency, posted_at, description
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
        order by d.last_seen_at desc
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


def create_email_subscription(email: str, data: dict) -> dict:
    """Create a pending (unconfirmed) subscription with fresh tokens. Double opt-in:
    the row is not emailed a digest until `confirm_subscription` flips it to active."""
    email = email.strip().lower()
    confirm_token = secrets.token_urlsafe(32)
    manage_token = secrets.token_urlsafe(32)
    # CV-derived signals (parse & discard): stored alongside, never the raw file.
    cv_cols = ["has_cv", "cv_summary", "years_experience"]
    cols = (["email", "status", "consent_at", "confirm_token", "manage_token"]
            + _SUBSCRIBER_FIELDS + cv_cols)
    vals = [
        email, "pending", datetime.now(timezone.utc), confirm_token, manage_token,
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
    with cursor(commit=True) as cur:
        cur.execute(f"insert into profiles ({','.join(cols)}) values ({placeholders}) "
                    "returning *", vals)
        return dict(cur.fetchone())


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


def confirm_subscription(confirm_token: str) -> Optional[dict]:
    """Flip pending -> active on confirm-link click. Idempotent for already-active rows."""
    with cursor(commit=True) as cur:
        cur.execute(
            "update profiles set status = 'active', confirmed_at = coalesce(confirmed_at, now()) "
            "where confirm_token = %s and status in ('pending','active') returning *",
            (confirm_token,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


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


def add_suppression(email: str, reason: str) -> None:
    with cursor(commit=True) as cur:
        cur.execute("insert into suppression (email, reason) values (%s, %s) "
                    "on conflict (email) do update set reason = excluded.reason",
                    (email.strip().lower(), reason))


def is_suppressed(email: str) -> bool:
    with cursor() as cur:
        cur.execute("select 1 from suppression where email = %s", (email.strip().lower(),))
        return cur.fetchone() is not None
