"""Public digest API (v1 product surface) — token-based, no accounts.

This is the capture + self-service layer the Next.js landing (Phase B2) talks to. It is
deliberately separate from `service/api.py` (the internal `/matches` API, kept but not
exposed in v1). Everything a subscriber can do is reachable with a token from their email;
there is never a password.

    POST /subscribe          create a PENDING subscription + send the confirm email
    POST /manage-link        email an existing subscriber their private settings link
    POST /session            exchange a magic-link token for a login-session cookie
    GET  /session            who is this browser logged in as? (cookie -> profile)
    POST /logout             revoke this browser's session + clear the cookie
    GET  /auth/google/start  begin "Sign in with Google" (optional; 404 if unconfigured)
    GET  /auth/google/callback  Google OAuth return -> session (existing) or signup intent (new)
    GET  /auth/google/pending   the Google-verified email awaiting a wizard submission
    POST /subscribe/google   finish a Google-verified signup -> active subscription + session
    GET  /confirm            double opt-in — activate + send welcome (HTML page)
    GET  /preferences        current settings — magic-link token OR session cookie (JSON)
    POST /preferences        update settings
    GET  /matches            one page of this subscriber's matches (`hidden=true` = the
                             ones they hid: already applied, not interested)
    POST /matches/hide       hide jobs — off the matches page and out of the digest
    POST /matches/unhide     put them back; hiding is never a delete
    POST /pause              pause N days without unsubscribing
    POST /resume             resume a paused subscription
    GET  /unsubscribe        human one-click unsubscribe (HTML page)
    POST /unsubscribe        RFC 8058 one-click (List-Unsubscribe-Post from mail clients)
    POST /cv/parse           multipart CV -> derived signals JSON (file discarded)
    GET  /jobs               public job search — no auth, no profile, no model, writes
                             nothing (the free tier's feed; see notes/scaling/PLAN.md)
    POST /event              cookieless first-party analytics event (whitelisted names)

Bot protection: every mutating public entry point (subscribe, cv/parse) verifies a
Cloudflare Turnstile token. If TURNSTILE_SECRET is unset (local dev) verification is
skipped so the flow is testable with no account.

Run:  DATABASE_URL=... MAIL_BACKEND=file uvicorn service.webapp:app --reload
"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import os
import secrets
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

from service import (cvparse, education, geo, i18n, links, mailer, store, taxonomy,
                     transactional)
# Aliased because the /matches endpoint has a `skills` query parameter that would otherwise
# shadow the module inside that function.
from service import skills as skill_gazetteer
# Bound at import rather than read as `store.SEARCH_TERM_MAX` at call time: the lifecycle
# tests swap `webapp.store` for a fake that implements the query functions and no constants,
# and reaching through the module for a value would make this endpoint depend on that double
# carrying one. Still a single definition — `store` owns it, this only borrows the name.
from service.store import SEARCH_TERM_MAX
from service.digest import C, SANS, SERIF

# Where users land back (frontend). Used for the "homepage" links on API-served pages.
SITE_URL = links.site_url()
TURNSTILE_SECRET = os.environ.get("TURNSTILE_SECRET")
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# /cv/parse resource limits — parsing is CPU-bound and runs on user-supplied files, so it
# must never be able to stall or exhaust the box (see security review H2).
CV_PARSE_TIMEOUT = float(os.environ.get("CV_PARSE_TIMEOUT", "10"))   # seconds, hard cap
# Minutes before another confirm email may be sent to the same pending address. Blunts
# "joe-job" flooding of a victim's inbox and stops duplicate pending rows (review M1/L5).
SUBSCRIBE_COOLDOWN_MIN = int(os.environ.get("SUBSCRIBE_COOLDOWN_MIN", "10"))
# Minutes before a subscriber's manage link may be re-emailed to the same address via
# /manage-link (the passwordless "email me my settings link" recovery path). This cooldown is
# the primary abuse control there: it caps a victim's inbox at one such email per window.
MANAGE_LINK_COOLDOWN_MIN = int(os.environ.get("MANAGE_LINK_COOLDOWN_MIN", "30"))

# --- login sessions (persisted magic links) ------------------------------------
# JobDigest is still passwordless: clicking a magic link is the only way to authenticate. A
# session just lets the browser keep that identity so return visits need no token in the URL.
SESSION_COOKIE = os.environ.get("SESSION_COOKIE_NAME", "jd_session")
# Secure flag on by default; set SESSION_COOKIE_SECURE=0 for plain-http local dev only.
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") != "0"
# A cookie-authenticated *write* must carry this header. Browsers forbid a cross-site page
# from setting a custom header without a CORS preflight our allow-list refuses, so its mere
# presence proves the request came from our own origin — this is the CSRF defense for cookie
# auth. Token-in-body callers (email links, RFC 8058 clients) don't need it.
CSRF_HEADER = "x-jobdigest-auth"

# --- Google sign-in (optional) -------------------------------------------------
# "Sign in with Google" is a *login* for existing subscribers: Google confirms the user's
# email, we match it to a live subscription and mint the same session as a magic link. We
# store nothing extra from Google — not even the Google account id — so it adds no new personal
# data, only a processor during the handshake. The whole feature is dormant until
# GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET are set (same optional-by-absence pattern as
# Turnstile), so a box with no credentials simply 404s the endpoints and hides the button.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI", f"{links.api_public_url()}/auth/google/callback"
)
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# Short-lived cookie holding the OAuth `state`, checked on callback to defeat login CSRF.
OAUTH_STATE_COOKIE = "jd_oauth_state"
# Cookie carrying a Google-verified *signup* intent while the user finishes the wizard.
SIGNUP_COOKIE = os.environ.get("SIGNUP_COOKIE_NAME", "jd_signup")


def _google_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_pool()
    yield


app = FastAPI(title="JobDigest — public API", version="1.0.0", lifespan=lifespan)

# CORS — the static Next.js frontend calls this API cross-origin. Origins come from
# CORS_ORIGINS (comma-separated); defaults cover local dev. In prod set it to the site domain.
_origins = os.environ.get(
    "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["GET", "POST"],
    # X-JobDigest-Auth is the CSRF header carried on cookie-authenticated writes.
    allow_headers=["Content-Type", "X-JobDigest-Auth"],
    # Let the browser send/receive the session cookie on cross-origin dev calls. In prod the
    # frontend and API are same-origin (jobdigest.eu + /api) so this is a no-op there.
    allow_credentials=True,
)


def _lang(profile: dict | None) -> str:
    """The language to write to this subscriber in. One helper so no call site forgets."""
    return i18n.clean_locale((profile or {}).get("language"))


# --------------------------------------------------------------- turnstile -----

def _verify_turnstile(
    token: Optional[str], remote_ip: Optional[str] = None, *, required: bool = True
) -> None:
    """Raise 400 if the Turnstile token is missing/invalid. No-op when unconfigured.

    When ``required`` is False the endpoint treats Turnstile as optional: a *missing* token
    is allowed through (the caller relies on other limits — byte cap, parse timeout, edge
    rate-limiting), but a token that is *present* is still validated so a forged one fails."""
    if not TURNSTILE_SECRET:
        return                                   # local dev / not yet wired
    if not token:
        if not required:
            return                               # optional entry point (e.g. /cv/parse)
        raise HTTPException(400, "Bot check failed — please retry.")
    data = {"secret": TURNSTILE_SECRET, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        req = urllib.request.Request(
            TURNSTILE_VERIFY_URL, data=urllib.parse.urlencode(data).encode()
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = json.loads(resp.read().decode()).get("success", False)
    except Exception:
        raise HTTPException(502, "Bot check unavailable — please retry.")
    if not ok:
        raise HTTPException(400, "Bot check failed — please retry.")


# --------------------------------------------------------------- sessions ------

def _set_session_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, raw,
        max_age=store.SESSION_TTL_DAYS * 86400,
        httponly=True,                 # JS can't read it — it isn't an XSS-exfiltratable token
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",                # not sent on cross-site sub-requests; belt to the CSRF header
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _clear_signup_cookie(response: Response) -> None:
    response.delete_cookie(SIGNUP_COOKIE, path="/")


def _cookie_profile(request: Request) -> Optional[dict]:
    return store.session_profile(request.cookies.get(SESSION_COOKIE) or "")


def _resolve_subscriber(
    request: Request, token: Optional[str], *, mutating: bool
) -> Optional[dict]:
    """Identify the subscriber behind a self-service call, by either credential.

    Priority order:
      * an explicit ``manage_token`` (from a magic link or an RFC 8058 client) — always
        accepted, so email links keep working exactly as before;
      * otherwise the session cookie (a logged-in browser). A *mutating* cookie-authed
        request must also carry the CSRF header (see ``CSRF_HEADER``) or it's refused; reads
        need no header.

    Returns the full profile row — including ``manage_token``, which the caller uses
    server-side to drive the existing token-keyed store functions — or None."""
    if token:
        return store.get_by_manage_token(token)
    profile = _cookie_profile(request)
    if profile is None:
        return None
    if mutating and request.headers.get(CSRF_HEADER) is None:
        raise HTTPException(403, "Missing CSRF header for cookie-authenticated request.")
    return profile


# --------------------------------------------------------------- html pages ----

def _page(title: str, body_html: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · JobDigest</title></head>
<body style="margin:0;background:{C['paper']};font-family:{SANS};color:{C['ink']};">
<div style="height:3px;background:linear-gradient(90deg,#2b2e57,{C['brand']} 45%,#E0A32E);"></div>
<div style="max-width:560px;margin:64px auto;padding:0 24px;text-align:center;">
  <div style="font:700 22px {SERIF};margin-bottom:20px;">
    <span style="color:{C['brand']};">◐</span> Job<span style="color:{C['brand']};">Digest</span></div>
  <div style="background:{C['surface']};border:1px solid {C['line']};border-radius:16px;padding:36px 30px;">
    {body_html}
  </div>
</div></body></html>"""


# --------------------------------------------------------------- subscribe -----

def _check_role_categories(v: Optional[list[str]]) -> Optional[list[str]]:
    """Reject role_category values the taxonomy doesn't define.

    Both forms used to accept any string, and the preferences page slugified free-text role
    chips straight into this field. A value like `social_media_specialist` is not a category:
    no posting carries it, so `role_category = any(...)` never fires and the subscriber holds
    a filter that silently matches nothing. Failing the request is the only outcome the user
    can see — dropping the value server-side would reproduce the same silence one layer down.
    `uncategorised` is excluded deliberately: it is a real stored value but selecting it as a
    preference means "postings we failed to classify", which nobody wants as a search.
    """
    if v is None:
        return v
    allowed = set(taxonomy.CATEGORIES) - {taxonomy.UNCATEGORISED}
    unknown = sorted({c for c in v if c not in allowed})
    if unknown:
        raise ValueError(
            f"unknown role_categories: {', '.join(unknown)}. "
            f"Valid values: {', '.join(sorted(allowed))}"
        )
    return v


def _check_countries(v: Optional[list[str]]) -> Optional[list[str]]:
    """Reject country codes we don't know.

    Same reasoning as `_check_role_categories`: a value the filter can never match is worse
    than an error, because nothing anywhere reports it — the subscriber simply never sees a
    job from a country they believe they selected.
    """
    if v is None:
        return v
    codes = [str(c).strip().upper() for c in v]
    unknown = sorted(set(codes) - set(geo.KNOWN_COUNTRIES))
    if unknown:
        raise ValueError(
            f"unknown countries: {', '.join(unknown)}. "
            f"Valid values: {', '.join(sorted(geo.COUNTRIES))}"
        )
    return codes


def _check_cities(v: Optional[list[str]]) -> Optional[list[str]]:
    """A city preference is ``<country>:<slug>`` — e.g. ``cz:prague``.

    The slug need NOT be one we curated: the form lets people type a town we don't list, and
    such a value still reaches the AI matcher. It cannot reach the SQL gate (nothing in
    `postings.city` will ever equal it) — see `geo.location_predicate` for why that only
    costs precision. The country half must be real, or the value can never apply to anything.
    """
    if v is None:
        return v
    if len(v) > geo.MAX_CITIES:
        raise ValueError(f"too many cities (max {geo.MAX_CITIES})")
    parsed = [(str(x), geo.split_city(x)) for x in v]
    bad = [raw for raw, (country, _) in parsed if country is None]
    if bad:
        raise ValueError(
            f"malformed cities: {', '.join(bad[:5])}. "
            "Expected '<country>:<city-slug>', e.g. 'cz:prague'."
        )
    return [geo.qualify(country, slug) for _, (country, slug) in parsed if country and slug]


def _check_remote_scope(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    scope = str(v).strip().lower()
    if scope not in geo.REMOTE_SCOPES:
        raise ValueError(f"unknown remote_scope: {v}. "
                         f"Valid values: {', '.join(geo.REMOTE_SCOPES)}")
    return scope


def _check_work_modes(v: Optional[list[str]]) -> Optional[list[str]]:
    """Reject an unknown mode outright, but let an empty list through.

    The asymmetry is deliberate. A typo'd mode is a client bug and should be loud — silently
    honouring `["hybird"]` would mean `clean_work_modes` widening it back to all three and the
    subscriber never learning their choice did nothing. An *empty* list is not a bug: it is a
    subscriber who unticked every box, and `geo.clean_work_modes` reads that as "no preference"
    rather than "nothing acceptable", which is the only reading that cannot silently empty
    somebody's digest.
    """
    if v is None:
        return v
    modes = [str(m).strip().lower() for m in v]
    unknown = [m for m in modes if m and m not in geo.WORK_MODES]
    if unknown:
        raise ValueError(f"unknown work_modes: {', '.join(unknown[:5])}. "
                         f"Valid values: {', '.join(geo.WORK_MODES)}")
    return geo.clean_work_modes(modes)


def _check_education_levels(v: Optional[list[str]]) -> Optional[list[str]]:
    """Reject an unknown level outright, but let an empty list through.

    Exactly the asymmetry `_check_work_modes` documents, for exactly the same reason: a typo'd
    level is a client bug that `education.clean_levels` would otherwise widen back to all five,
    leaving the subscriber believing they set a filter that does nothing; an empty list is a
    subscriber who unticked every box, which is "no preference", not "nothing is acceptable".
    """
    if v is None:
        return v
    levels = [str(m).strip().lower() for m in v]
    unknown = [m for m in levels if m and m not in education.LEVELS]
    if unknown:
        raise ValueError(f"unknown education_levels: {', '.join(unknown[:5])}. "
                         f"Valid values: {', '.join(education.LEVELS)}")
    return education.clean_levels(levels)


def _check_education_field(v: Optional[str]) -> Optional[str]:
    """Trim and cap the free-text field of study. Never raises: it is prose, not a vocabulary."""
    return education.clean_field(v) if v is not None else v


class LocationFieldsMixin(BaseModel):
    """The location preferences, shared by every form that can set them.

    `countries`/`cities`/`remote_scope` are what the filter reads; `regions` is accepted only
    so an older client still works and is re-derived server-side (`store._location_prefs`).
    The defaults reproduce the legacy `regions` default exactly, so a caller that sends no
    location fields at all gets the behaviour it got before this existed.
    """
    countries: list[str] = Field(default_factory=lambda: ["CZ"])
    cities: list[str] = Field(default_factory=list)
    remote_scope: str = "worldwide"
    regions: list[str] = Field(default_factory=lambda: ["cz", "eu", "worldwide"])
    # Not location, but the same "where and how do you work" question and the same set of
    # forms. Defaults to all three: a client that has never heard of this field must not
    # narrow the subscriber it is creating.
    work_modes: list[str] = Field(default_factory=lambda: list(geo.DEFAULT_WORK_MODES))
    # Nor is this location — it rides here for the same reason `work_modes` does: it belongs on
    # the same three forms (signup, preview, Google signup), and this codebase's history is of
    # filters silently applied at one call site and not the other. One shared mixin cannot be
    # half-forgotten. Defaults to all five levels, so a client that has never heard of the
    # field cannot narrow the subscriber it is creating.
    education_levels: list[str] = Field(
        default_factory=lambda: list(education.DEFAULT_LEVELS))
    education_field: Optional[str] = None

    _valid_countries = field_validator("countries")(_check_countries)
    _valid_cities = field_validator("cities")(_check_cities)
    _valid_scope = field_validator("remote_scope")(_check_remote_scope)
    _valid_work_modes = field_validator("work_modes")(_check_work_modes)
    _valid_education = field_validator("education_levels")(_check_education_levels)
    _valid_education_field = field_validator("education_field")(_check_education_field)


class SubscribeIn(LocationFieldsMixin):
    email: EmailStr
    label: str = "My digest"
    stack: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=lambda: ["junior", "mid"])
    role_categories: list[str] = Field(default_factory=list)
    work_types: list[str] = Field(default_factory=lambda: ["permanent", "freelance/contract"])
    part_time_only: bool = False
    eligible_only: bool = True
    sectors: list[str] = Field(default_factory=list)
    min_score: int = 6
    frequency: str = "daily"
    # Which language to write to this person in. Sent by the signup form as the locale the
    # visitor was reading; `store` cleans it, so an unknown value becomes English rather than
    # producing mail nobody can render. Defaults to English for older clients.
    language: str = i18n.DEFAULT_LOCALE
    # optional CV signals from a prior POST /cv/parse (merged server-side)
    cv_signals: Optional[dict] = None
    cf_turnstile_token: Optional[str] = None

    _valid_roles = field_validator("role_categories")(_check_role_categories)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "active_postings": store.count_active()}


@app.post("/subscribe")
def subscribe(body: SubscribeIn) -> dict:
    """Create a pending subscription and email a confirm link (double opt-in).

    Always returns the same shape whether or not the email is new/suppressed — we never
    reveal whether an address is already subscribed."""
    _verify_turnstile(body.cf_turnstile_token)
    email = str(body.email).strip().lower()

    generic = {"ok": True, "status": "pending",
               "message": "Check your inbox to confirm your subscription."}

    # Don't re-subscribe a suppressed (unsubscribed/bounced) address silently.
    if store.is_suppressed(email):
        return generic

    # One subscription per address. If a live (pending/active/paused) row already exists, do
    # not create a duplicate or send another email — return the same generic reply, which
    # never reveals whether the address is subscribed. This is the graceful path; the partial
    # unique index (uq_profiles_live_email) is the hard backstop against a concurrent race.
    if store.live_subscription_exists(email):
        return generic

    # Per-email cooldown: if we already emailed a confirm to this address very recently,
    # don't send another (or create another pending row). Blunts inbox-flooding a victim
    # and duplicate rows on rapid retries. Same generic response either way.
    if store.recent_signup_exists(email, SUBSCRIBE_COOLDOWN_MIN):
        return generic

    data = body.model_dump(exclude={"email", "cf_turnstile_token", "cv_signals"})
    if body.cv_signals:
        cvparse.merge_into_profile(data, body.cv_signals)

    profile = store.create_email_subscription(email, data)
    if profile is None:
        # Lost a race with a concurrent signup for the same address — the unique index
        # rejected the duplicate. Same generic reply, no second confirm email.
        return generic
    confirm_url = links.confirm_link(profile["confirm_token"])
    # The language they signed up in, read back off the stored row rather than off the request
    # body — whatever the digest will use tomorrow is what the confirm email must use today.
    lang = _lang(profile)
    subject, html_body, text = transactional.render_confirm(email, confirm_url, lang)
    try:
        mailer.send(email, subject, html_body, text)
    except Exception:
        # Don't leave an unconfirmable orphan row if the mail provider hiccups.
        logging.exception("confirm email send failed for %s", email)
        store.delete_profile(profile["id"])
        raise HTTPException(
            502, "We couldn't send the confirmation email just now. Please try again in a moment."
        )
    return generic


# --------------------------------------------------------------- manage link ---

class ManageLinkIn(BaseModel):
    email: EmailStr
    cf_turnstile_token: Optional[str] = None


@app.post("/manage-link")
def manage_link(body: ManageLinkIn) -> dict:
    """Email a subscriber the private link to manage their existing subscription — the
    passwordless "log me back in" path for someone who lost their link.

    Why this cannot be abused (the reason it's safe to expose an email box publicly):

    * **No enumeration.** The response is identical whether or not the address is subscribed,
      so it can't be used to learn who has an account.
    * **No hijack.** The link only ever goes to the address's *own* inbox and the token in it
      is the credential — knowing an email but not controlling the inbox grants nothing, exactly
      like a password reset. There is no endpoint that turns an email into a token in a response.
    * **No inbox flooding.** `store.request_manage_link` atomically enforces a per-address
      cooldown, so repeated requests for one address send at most one email per window.
    * **Confirmed subs only.** Only `active`/`paused` subscriptions qualify (a `pending` row
      re-confirms via /subscribe; an `unsubscribed` address is never re-contacted).

    Turnstile is optional here (this is a standalone one-field form with no widget minted): a
    token, if present, is still validated; a missing one is allowed because the per-address
    cooldown and the generic response — not the bot check — are what actually bound abuse."""
    _verify_turnstile(body.cf_turnstile_token, required=False)
    email = str(body.email).strip().lower()

    generic = {"ok": True,
               "message": "If that address has a subscription, we've emailed its settings link."}

    profile = store.request_manage_link(email, MANAGE_LINK_COOLDOWN_MIN)
    if profile is None:
        # No live subscription, or one already emailed within the cooldown window. Say exactly
        # the same thing either way so the response reveals nothing about the address.
        return generic

    lang = _lang(profile)
    manage_url = links.preferences_link(profile["manage_token"], lang)
    subject, html_body, text = transactional.render_manage_link(email, manage_url, lang)
    try:
        mailer.send(email, subject, html_body, text)
    except Exception:
        # Log, but still return the generic reply: a distinct error response would only appear
        # for an address that *is* subscribed (a non-subscriber never reaches the send), which
        # would turn a mail hiccup into a weak enumeration signal. The user can retry after the
        # cooldown window.
        logging.exception("manage-link email send failed for %s", email)
    return generic


# --------------------------------------------------------------- preview -------
# Instant, no-AI keyword preview shown right after signup. It runs the SAME cheap prefilter
# the daily matcher uses (store.query_shortlist), applies the seniority hard-filter locally
# (the AI normally enforces that), and ranks by how many of the person's skills each posting
# mentions. It is read-only: no email, no DB writes, no `matches` rows, no score. The daily
# digest still delivers the real AI-ranked version — this is only a "here are live jobs now"
# first impression. Deliberately key-less, so it never touches the billing decision.

class PreviewIn(LocationFieldsMixin):
    label: str = "My digest"
    stack: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=lambda: ["junior", "mid"])
    role_categories: list[str] = Field(default_factory=list)
    work_types: list[str] = Field(default_factory=lambda: ["permanent", "freelance/contract"])
    part_time_only: bool = False
    eligible_only: bool = True
    sectors: list[str] = Field(default_factory=list)
    cv_signals: Optional[dict] = None
    cf_turnstile_token: Optional[str] = None

    _valid_roles = field_validator("role_categories")(_check_role_categories)


PREVIEW_SHORTLIST = 60      # candidates pulled from the prefilter before ranking
PREVIEW_LIMIT = 8           # cards returned to the browser


def _preview_view(j: dict, terms: list[str]) -> dict:
    """Public card for the instant preview — same non-sensitive fields as a match, plus a
    plain 'why' from the keyword overlap. No score: this is keyword relevance, not judged fit,
    and dressing an overlap count up as a 0-10 would be dishonest."""
    hay = f"{j.get('title') or ''} {j.get('description') or ''}".lower()
    matched = [t for t in terms if t and t in hay]
    tags: list[str] = []
    if j.get("region"):
        tags.append(str(j["region"]).upper())
    # Same precedence as the digest's `_tags`: hybrid is never also "Remote".
    if j.get("work_mode") == "hybrid":
        tags.append("Hybrid")
    elif j.get("remote_signal") or j.get("region") in ("eu", "worldwide"):
        tags.append("Remote")
    if j.get("seniority"):
        tags.append(str(j["seniority"]).capitalize())
    if j.get("work_type") == "freelance/contract":
        tags.append("Freelance")
    if j.get("salary_raw"):
        tags.append(str(j["salary_raw"]))
    # de-dup, preserve order
    seen, dedup = set(), []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            dedup.append(t)
    why = ("Mentions " + ", ".join(list(dict.fromkeys(matched))[:4])) if matched \
        else "Recent role matching your search"
    return {
        "posting_id": j.get("posting_id"),
        "title": j.get("title"),
        "company": j.get("company"),
        "url": j.get("url"),
        "location": j.get("location"),
        "region": j.get("region"),
        "seniority": j.get("seniority"),
        "work_type": j.get("work_type"),
        "tags": dedup[:5],
        # Extracted from the description on the fly here (the preview query returns it) rather
        # than from stored `skills` — avoids touching the shared matcher shortlist query. Same
        # gazetteer, so the result matches the stored value on /matches.
        "skills": skill_gazetteer.extract_skills(j.get("title"), j.get("description")),
        "why": why,
    }


@app.post("/preview")
def preview(body: PreviewIn) -> dict:
    """Instant keyword matches for a just-completed signup. See the section note above.

    Turnstile is optional here (the token minted for /subscribe is single-use and consumed
    there, so the browser deliberately doesn't send it to /preview). The endpoint is bounded
    by one indexed prefilter query and edge rate-limiting; it writes nothing."""
    _verify_turnstile(body.cf_turnstile_token, required=False)
    data = body.model_dump(exclude={"cf_turnstile_token", "cv_signals"})
    if body.cv_signals:
        cvparse.merge_into_profile(data, body.cv_signals)

    candidates = store.query_shortlist(data, limit=PREVIEW_SHORTLIST)

    # Seniority hard-filter, applied here because query_shortlist doesn't (the AI normally
    # does). Keep a posting when its level is one the person wants, OR when it's unknown —
    # never drop an ambiguous role, matching the "soft/AI" choice for the daily path.
    levels = {str(x).lower() for x in (data.get("seniorities") or [])}
    if levels:
        candidates = [c for c in candidates
                      if not c.get("seniority") or str(c["seniority"]).lower() in levels]

    # Rank by how many of the person's skills each posting mentions; stable sort preserves
    # the prefilter's recency order within an equal-overlap group.
    terms = [str(s).strip().lower() for s in (data.get("stack") or []) if str(s).strip()]
    def _overlap(j: dict) -> int:
        hay = f"{j.get('title') or ''} {j.get('description') or ''}".lower()
        return sum(1 for t in terms if t in hay)
    candidates.sort(key=_overlap, reverse=True)

    return {"count": len(candidates),
            "jobs": [_preview_view(c, terms) for c in candidates[:PREVIEW_LIMIT]]}


# ------------------------------------------------------------- public search ----
#
# The free tier's job feed (`notes/scaling/PLAN.md`): unauthenticated, no profile, no model,
# no tokens. It is the only endpoint that hands the corpus to somebody who is not a
# subscriber, so every bound is load-bearing rather than defensive habit.

JOBS_WINDOW_SEC = int(os.environ.get("JOBS_WINDOW_SEC", "60"))
JOBS_MAX_GLOBAL = int(os.environ.get("JOBS_MAX_GLOBAL", "600"))

_jobs_window_start = 0.0
_jobs_total = 0


def _jobs_allowed() -> bool:
    """Fixed-window global cap on search queries. Process-local, like `_event_allowed`.

    There is no session id to key on — a search page is browsed before anyone identifies
    themselves — so this is a global ceiling, not a per-caller one. It exists to stop a
    scraper turning an indexed query into a full corpus dump at wire speed, and it is the
    *inner* bound; Cloudflare's edge limiting is the outer one. Deliberately not a 429 with
    a Retry-After: telling a scraper exactly when to come back is a scheduling hint.
    """
    global _jobs_window_start, _jobs_total
    now = time.monotonic()
    if now - _jobs_window_start >= JOBS_WINDOW_SEC:
        _jobs_window_start = now
        _jobs_total = 0
    if _jobs_total >= JOBS_MAX_GLOBAL:
        return False
    _jobs_total += 1
    return True


#: Selectable in the UI, and the same rule `_check_role_categories` applies to preferences:
#: `uncategorised` is a real stored value but not something anyone means to search for.
#: Postings carrying it are still returned whenever no category is ticked, which is the half
#: that matters — a filter nobody applied must never narrow anything.
_SEARCH_CATEGORIES = frozenset(taxonomy.CATEGORIES) - {taxonomy.UNCATEGORISED}
_SEARCH_SENIORITIES = frozenset({"junior", "mid", "senior"})


def _is_offered_city(value: str) -> bool:
    """True for a `cz:prague` pair naming a city the location picker actually offers.

    `geo.CITIES` is keyed by uppercase country and the facet emits the stored lowercase form,
    so this goes through `split_city` rather than comparing strings — one definition of what a
    city pair is, and the same one `_search_where` filters on.
    """
    country, slug = geo.split_city(value)
    return bool(country and slug and slug in geo.CITIES.get(country, {}))


def _drop_unknown(raw: Optional[str], allowed: frozenset[str] | set[str],
                  *, lower: bool = True) -> list[str]:
    """Parse a comma-separated `/matches` filter, keeping only known values.

    The forgiving twin of `_pick`, and the difference is deliberate rather than an
    inconsistency. On `/jobs` the filters *are* the page, so a silently-ignored one renders
    an unfiltered corpus under a heading claiming otherwise and must 400. Here the
    subscriber's own matches are the page and a dropped filter merely shows more of them —
    breaking someone's match list over a hand-edited URL would be the worse answer.

    The kept values are echoed back in the response, so the UI lights up what was applied
    rather than what was asked for.
    """
    out: list[str] = []
    for token in (raw or "").split(","):
        v = token.strip()
        if not v:
            continue
        v = v.lower() if lower else v.upper()
        if v in allowed and v not in out:
            out.append(v)
    return out


def _pick(values: list[str], allowed: frozenset[str] | set[str], field: str,
          *, lower: bool = True) -> list[str]:
    """Validate one repeatable query parameter against its canonical vocabulary.

    **Rejects rather than drops.** These arrive in a shareable URL that a person can hand-edit,
    and silently ignoring `?category=softwar` would render a full unfiltered page under a
    heading claiming a filter — the same silence `_check_role_categories` refuses for
    preferences. A 400 is the only version of this the visitor can see.
    """
    out: list[str] = []
    unknown: list[str] = []
    for raw in values or []:
        v = str(raw).strip()
        v = v.lower() if lower else v.upper()
        if not v:
            continue
        if v not in allowed:
            unknown.append(v)
        elif v not in out:
            out.append(v)
    if unknown:
        raise HTTPException(400, f"unknown {field}: {', '.join(sorted(set(unknown)))}")
    return out


def _job_view(j: dict) -> dict:
    """Public card for one posting in the search feed.

    The same non-sensitive field set as `_match_view`, minus `score` and `summary` — those are
    the AI's per-job judgement, which is the paid tier's difference and is not withheld by
    accident. `source` is carried because attribution is a terms obligation for at least one
    feed (Remote OK), and the card cannot honour it without knowing where a row came from.
    """
    posted = j.get("posted_at")
    return {
        "posting_id": j.get("posting_id"),
        "title": j.get("title"),
        "company": j.get("company"),
        "url": j.get("url"),
        "location": j.get("location"),
        "country_code": j.get("country_code"),
        "city": j.get("city"),
        "region": j.get("region"),
        "seniority": j.get("seniority"),
        "work_type": j.get("work_type"),
        "work_mode": j.get("work_mode"),
        "remote_signal": j.get("remote_signal"),
        "role_category": j.get("role_category"),
        "salary": j.get("salary_raw"),
        "source": j.get("source"),
        "skills": j.get("skills") or [],
        "posted_at": posted.isoformat() if hasattr(posted, "isoformat") else posted,
    }


@app.get("/jobs")
def search_jobs_public(
    q: Optional[str] = None,
    country: list[str] = Query(default_factory=list),
    city: list[str] = Query(default_factory=list),
    category: list[str] = Query(default_factory=list),
    work_mode: list[str] = Query(default_factory=list),
    seniority: list[str] = Query(default_factory=list),
    remote: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """One page of the public job feed. Reads only; writes nothing, ever.

    **This is a GET and it must stay side-effect free** — not merely "does no harm", but
    records nothing about who searched for what. A search term is a statement about
    somebody's job hunt, and `web/components/legal/PrivacyEn.tsx` makes no promise covering
    it. Logging queries here would need that page to change in the same commit (security
    rule 4); the cheaper and better answer is not to.

    **Facets are computed on the first page only.** They do not change as you page, so
    recomputing them for `offset=20` would be two extra aggregates per scroll for a value the
    client already holds. `facets` is absent, not empty, on later pages — an empty list would
    read as "nothing to filter by" and blank the menu the visitor is using.
    """
    if not _jobs_allowed():
        raise HTTPException(503, "busy")

    countries = _pick(country, frozenset(geo.COUNTRIES), "country", lower=False)
    categories = _pick(category, _SEARCH_CATEGORIES, "category")
    work_modes = _pick(work_mode, frozenset(geo.WORK_MODES), "work_mode")
    seniorities = _pick(seniority, _SEARCH_SENIORITIES, "seniority")
    # Cities are validated by `geo.clean_cities`, which drops a malformed pair and — when
    # countries are also given — one whose country is not among them. That is the same rule
    # the subscription form applies, so a city filter cannot outlive the country it belongs to.
    cities = geo.clean_cities(city, countries or None)

    rows, total, capped = store.search_postings(
        q=q, countries=countries, cities=cities, categories=categories,
        work_modes=work_modes, seniorities=seniorities, remote_only=remote,
        limit=limit, offset=offset)

    out: dict = {
        "count": total,
        "count_capped": capped,
        "jobs": [_job_view(r) for r in rows],
    }
    if offset == 0:
        facets = store.search_facets(
            q=q, countries=countries, cities=cities, categories=categories,
            work_modes=work_modes, seniorities=seniorities, remote_only=remote)
        # The category facet is filtered to the canonical vocabulary *here*, which is the one
        # place that can import it. Production still holds ~1 058 rows whose `role_category`
        # is a raw Swedish SSYK label ("Butikssäljare, fackhandel") from the 2026-08-08 hint
        # bug; without this they would be offered as filter options on a public page, and
        # `uncategorised` would be offered as a search anyone would regret running.
        out["facets"] = {
            "categories": [f for f in facets["categories"]
                           if f["value"] in _SEARCH_CATEGORIES],
            "countries": [f for f in facets["countries"]
                          if f["value"] in geo.COUNTRIES],
        }
        if "cities" in facets:
            # Filtered to the curated table for the same reason the categories are, though
            # this one is belt-and-braces rather than a fix: `geo.resolve_location` can only
            # return a slug that is already in `CITIES`, so the column holds nothing else
            # today. It held nothing else for `role_category` either, until a source hint
            # started writing raw SSYK labels straight past the classifier — so the public
            # dropdown checks the vocabulary rather than trusting the column.
            out["facets"]["cities"] = [
                f for f in facets["cities"] if _is_offered_city(f["value"])]
    return out


# --------------------------------------------------------------- confirm -------

@app.get("/confirm", response_class=HTMLResponse)
def confirm(token: str) -> HTMLResponse:
    profile, newly_confirmed = store.confirm_subscription(token)
    if not profile:
        # A confirm link is single-use and TTL-bounded, so `None` means used, expired, or
        # never valid — indistinguishable by design (don't reveal whether a token existed).
        # Reassure a just-confirmed user rather than only pushing them to sign up again.
        return HTMLResponse(_page("Link no longer active", f"""
          <div style="font:700 20px {SERIF};margin-bottom:10px;">This confirmation link is no longer active</div>
          <p style="font:400 15px {SANS};color:{C['muted']};line-height:1.55;">
          A confirm link works once and is good for {store.CONFIRM_TOKEN_TTL_DAYS} days, so this one has
          already been used or has expired. If you've already confirmed, you're all set — manage your
          subscription from the link in your welcome email. Otherwise
          <a href="{SITE_URL}" style="color:{C['brand']};">sign up again at jobdigest.eu</a>.</p>"""),
          status_code=404)

    manage_url = links.preferences_link(profile["manage_token"], _lang(profile))

    # Only on the click that actually confirmed. Sending on every click made a confirm link
    # an unlimited "email this person" primitive for anyone who obtained one.
    if newly_confirmed:
        subject, html_body, text = transactional.render_welcome(
            profile["email"], manage_url, _lang(profile))
        mailer.send(profile["email"], subject, html_body, text)
    else:
        return HTMLResponse(_page("Already confirmed", f"""
          <div style="font:700 22px {SERIF};margin-bottom:10px;">You're already subscribed</div>
          <p style="font:400 15px {SANS};color:{C['muted']};line-height:1.55;">
            This link has already been used — nothing more to do.</p>
          <p style="margin-top:20px;"><a href="{manage_url}" style="color:{C['brand']};font-weight:700;">
            Manage your preferences →</a></p>"""))

    return HTMLResponse(_page("Confirmed", f"""
      <div style="width:52px;height:52px;border-radius:50%;margin:0 auto 16px;display:grid;place-items:center;
        background:{C['brand']};color:#fff;font-size:1.6rem;">✓</div>
      <div style="font:700 22px {SERIF};margin-bottom:10px;">You're all set</div>
      <p style="font:400 15px {SANS};color:{C['muted']};line-height:1.55;">
        Your subscription is confirmed. Your first curated digest lands at 7:00 tomorrow morning.</p>
      <p style="margin-top:20px;"><a href="{manage_url}" style="color:{C['brand']};font-weight:700;">
        Fine-tune your preferences →</a></p>"""))


# --------------------------------------------------------------- preferences ---

_PUBLIC_FIELDS = ["email", "status", "label", "stack", "seniorities",
                  "countries", "cities", "remote_scope", "regions", "work_modes",
                  "education_levels", "education_field",
                  "role_categories", "work_types", "part_time_only", "eligible_only",
                  "sectors", "min_score", "frequency", "has_cv", "cv_summary",
                  "years_experience", "paused_until"]


def _public_view(profile: dict) -> dict:
    """Subscriber-safe projection — never leak tokens."""
    return {k: profile.get(k) for k in _PUBLIC_FIELDS}


class PreferencesIn(BaseModel):
    # Optional now: a logged-in browser authenticates by session cookie and sends no token.
    # A magic-link visit still passes its manage_token, which always wins (see _resolve_subscriber).
    token: Optional[str] = None
    label: Optional[str] = None
    stack: Optional[list[str]] = None
    seniorities: Optional[list[str]] = None
    # Location: all four are optional here because `update_preferences` sends only what
    # changed. `store._location_prefs` merges them against the stored row, so sending just
    # `cities` still validates against the countries on file.
    countries: Optional[list[str]] = None
    cities: Optional[list[str]] = None
    remote_scope: Optional[str] = None
    regions: Optional[list[str]] = None
    work_modes: Optional[list[str]] = None
    education_levels: Optional[list[str]] = None
    education_field: Optional[str] = None
    role_categories: Optional[list[str]] = None
    work_types: Optional[list[str]] = None
    part_time_only: Optional[bool] = None
    eligible_only: Optional[bool] = None
    sectors: Optional[list[str]] = None
    min_score: Optional[int] = None
    frequency: Optional[str] = None
    # Changing the site language updates which language the emails arrive in too — the two
    # being different is the thing this whole change exists to stop.
    language: Optional[str] = None

    _valid_roles = field_validator("role_categories")(_check_role_categories)
    _valid_countries = field_validator("countries")(_check_countries)
    _valid_cities = field_validator("cities")(_check_cities)
    _valid_scope = field_validator("remote_scope")(_check_remote_scope)
    _valid_work_modes = field_validator("work_modes")(_check_work_modes)
    _valid_education = field_validator("education_levels")(_check_education_levels)
    _valid_education_field = field_validator("education_field")(_check_education_field)


class SessionIn(BaseModel):
    token: str


@app.post("/session")
def session_login(body: SessionIn, response: Response) -> dict:
    """Exchange a valid manage token (from a magic link) for a session cookie.

    The frontend calls this once when it lands on a page carrying ``?token=``, then drops the
    token from the URL and rides the cookie. The token is still the credential — a session is
    only minted for one that resolves to a real subscriber."""
    profile = store.get_by_manage_token(body.token)
    if not profile:
        raise HTTPException(404, "Unknown or expired link.")
    raw = store.create_session(profile["id"])
    _set_session_cookie(response, raw)
    return _public_view(profile)


@app.get("/session")
def session_whoami(request: Request) -> dict:
    """Who is this browser logged in as? Resolves the session cookie; 401 if none/expired."""
    profile = _cookie_profile(request)
    if not profile:
        raise HTTPException(401, "Not logged in.")
    return _public_view(profile)


@app.post("/logout")
def logout(request: Request, response: Response) -> dict:
    """Revoke this browser's session and clear the cookie. No CSRF header required: a forged
    logout only logs you out, which is a nuisance rather than a vulnerability."""
    store.revoke_session(request.cookies.get(SESSION_COOKIE) or "")
    _clear_session_cookie(response)
    return {"ok": True}


# ----------------------------------------------------------- google sign-in ----

def _decode_jwt_claims(token: str) -> Optional[dict]:
    """Base64url-decode a JWT's payload segment. No signature check here — see below."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)      # restore base64 padding
        return json.loads(base64.urlsafe_b64decode(payload_b64).decode())
    except Exception:
        return None


def _google_verify_code(code: str) -> Optional[dict]:
    """Exchange an auth code with Google; return {'email', 'email_verified'} or None.

    The id_token is read straight from Google's HTTPS token endpoint in a server-to-server
    call authenticated by our client secret, so TLS guarantees its provenance and we validate
    its claims (aud/iss/exp) without a separate signature check — exactly the case Google's
    docs say local signature verification is unnecessary. We still check aud/iss/exp so a token
    minted for a different app or an expired one is rejected."""
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    try:
        req = urllib.request.Request(GOOGLE_TOKEN_ENDPOINT, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
    except Exception:
        return None
    claims = _decode_jwt_claims(payload.get("id_token") or "")
    if not claims:
        return None
    if claims.get("aud") != GOOGLE_CLIENT_ID:
        return None
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        return None
    if int(claims.get("exp", 0)) < int(time.time()):
        return None
    email = (claims.get("email") or "").strip().lower()
    if not email:
        return None
    return {"email": email, "email_verified": claims.get("email_verified") in (True, "true")}


@app.get("/auth/google/start")
def google_start():
    """Kick off the OAuth dance: set a state cookie and redirect to Google's consent screen.

    Scope is the minimum — `openid email` — because all we want is a verified address to match
    to an existing subscription. `prompt=select_account` lets a user pick which Google account;
    `access_type=online` because we never need offline/refresh access."""
    if not _google_configured():
        raise HTTPException(404, "Google sign-in is not enabled.")
    state = secrets.token_urlsafe(24)
    params = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    redirect = RedirectResponse(f"{GOOGLE_AUTH_ENDPOINT}?{params}", status_code=302)
    redirect.set_cookie(
        OAUTH_STATE_COOKIE, state, max_age=600, httponly=True,
        secure=SESSION_COOKIE_SECURE, samesite="lax", path="/",
    )
    return redirect


@app.get("/auth/google/callback")
def google_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Google sends the user back here. Three outcomes:

    * an existing subscriber -> mint a session, land on /preferences logged in;
    * a new, verified address -> start a signup intent and send them into the wizard
      (`/?google=signup`) to pick preferences — the confirm email is skipped since Google
      proved the address;
    * a previously-unsubscribed address -> `/manage?google=suppressed` (a Google login must
      not silently re-subscribe someone who left).

    Any failure lands on `google=error`, never a stack trace."""
    if not _google_configured():
        raise HTTPException(404, "Google sign-in is not enabled.")

    def bounce(flag: str) -> RedirectResponse:
        r = RedirectResponse(f"{links.site_page('manage')}?google={flag}", status_code=302)
        r.delete_cookie(OAUTH_STATE_COOKIE, path="/")
        return r

    # Login CSRF: the state Google echoes back must match the one we stashed in the cookie.
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if error or not code or not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        return bounce("error")

    info = _google_verify_code(code)
    if not info or not info["email_verified"]:
        return bounce("error")

    profile = store.get_live_profile_by_email(info["email"])
    if profile:
        # Existing subscriber -> log straight in.
        raw = store.create_session(profile["id"])
        r = RedirectResponse(links.site_page("preferences"), status_code=302)
        r.delete_cookie(OAUTH_STATE_COOKIE, path="/")
        _set_session_cookie(r, raw)
        return r

    # Not subscribed. An unsubscribed/bounced address must not be re-signed-up silently — that
    # would defeat the never-contact list. Send them to the login page with a clear note.
    if store.is_suppressed(info["email"]):
        return bounce("suppressed")

    # New user: begin a Google-verified signup. Stash a short-lived intent and send them into
    # the wizard to pick preferences; the confirm email is skipped because Google verified them.
    raw = store.create_signup_intent(info["email"])
    # Straight to the language-prefixed wizard, NOT to "/". The bare root is the language
    # negotiator, and a client-side redirect there would drop `?google=signup` — the wizard
    # would never enter Google mode and the picks stashed before the OAuth hop would strand.
    r = RedirectResponse(f"{links.site_page()}?google=signup", status_code=302)
    r.delete_cookie(OAUTH_STATE_COOKIE, path="/")
    r.set_cookie(
        SIGNUP_COOKIE, raw, max_age=store.SIGNUP_INTENT_TTL_MIN * 60, httponly=True,
        secure=SESSION_COOKIE_SECURE, samesite="lax", path="/",
    )
    return r


@app.get("/auth/google/pending")
def google_pending(request: Request) -> dict:
    """The Google-verified email awaiting a wizard submission, for the signup page to display.
    401 if there is no live intent (nothing to sign up)."""
    email = store.signup_intent_email(request.cookies.get(SIGNUP_COOKIE) or "")
    if not email:
        raise HTTPException(401, "No pending Google signup.")
    return {"email": email}


class GoogleSubscribeIn(LocationFieldsMixin):
    label: str = "My digest"
    stack: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=lambda: ["junior", "mid"])
    role_categories: list[str] = Field(default_factory=list)
    work_types: list[str] = Field(default_factory=lambda: ["permanent", "freelance/contract"])
    part_time_only: bool = False
    eligible_only: bool = True
    sectors: list[str] = Field(default_factory=list)
    min_score: int = 6
    frequency: str = "daily"
    language: str = i18n.DEFAULT_LOCALE
    cv_signals: Optional[dict] = None

    _valid_roles = field_validator("role_categories")(_check_role_categories)


@app.post("/subscribe/google")
def subscribe_google(body: GoogleSubscribeIn, request: Request, response: Response) -> dict:
    """Finish a Google-verified signup: create an ACTIVE subscription (no confirm email) and
    log the user in.

    The email is read from the server-side signup intent keyed by the ``jd_signup`` cookie,
    never from the request body — so a caller can only ever subscribe the address they actually
    verified with Google. The intent is consumed here (single-use)."""
    email = store.consume_signup_intent(request.cookies.get(SIGNUP_COOKIE) or "")
    if not email:
        raise HTTPException(400, "Your Google sign-in expired — please start again.")
    _clear_signup_cookie(response)

    # Respect the never-contact list even here: a Google login can't override an unsubscribe.
    if store.is_suppressed(email):
        raise HTTPException(409, "This address can't be subscribed. Contact us to re-subscribe.")

    def _login(profile_id: str) -> dict:
        _set_session_cookie(response, store.create_session(profile_id))
        return {"ok": True, "status": "active"}

    # Already subscribed (e.g. signed up in another tab meanwhile) -> just log in.
    existing = store.get_live_profile_by_email(email)
    if existing:
        return _login(existing["id"])

    data = body.model_dump(exclude={"cv_signals"})
    if body.cv_signals:
        cvparse.merge_into_profile(data, body.cv_signals)

    profile = store.create_email_subscription(email, data, confirmed=True)
    if profile is None:
        # Lost a concurrent-signup race; the row now exists — log into it.
        existing = store.get_live_profile_by_email(email)
        if existing:
            return _login(existing["id"])
        raise HTTPException(409, "Couldn't complete signup. Please try again.")

    # Welcome email carries their manage link as a backup credential — best-effort, never fatal
    # (they're already logged in via the session).
    try:
        manage_url = links.preferences_link(profile["manage_token"], _lang(profile))
        subject, html_body, text = transactional.render_welcome(email, manage_url, _lang(profile))
        mailer.send(email, subject, html_body, text)
    except Exception:
        logging.exception("welcome email failed for google signup %s", email)

    return _login(profile["id"])


@app.get("/preferences")
def get_preferences(request: Request, token: Optional[str] = None) -> dict:
    profile = _resolve_subscriber(request, token, mutating=False)
    if not profile:
        raise HTTPException(404, "Unknown or expired link.")
    return _public_view(profile)


MATCHES_PAGE_LIMIT = 25

#: The top rung of the score ladder, and the only one that is purely presentational:
#: `matcher.MATCH_FLOOR` (4) decides what is stored at all, `digest.EMAIL_MIN_SCORE` (6)
#: decides what is worth an email, and this decides what the "great fits only" toggle on
#: /matches leaves on screen. Env-overridable because it is a threshold (the no-hardcoded-
#: thresholds rule), and deliberately **not** `profiles.min_score` — that is a stored
#: preference gating what gets matched, while this filters what is already matched. Reading
#: one off the other would let a display toggle quietly change what the matcher considers.
GREAT_FIT_MIN_SCORE = int(os.environ.get("GREAT_FIT_MIN_SCORE", "8"))


def _match_view(j: dict) -> dict:
    """Public shape for one match on the /matches page (no internal fields)."""
    posted = j.get("posted_at")
    return {
        "posting_id": j.get("posting_id"),
        "title": j.get("title"),
        "company": j.get("company"),
        "url": j.get("url"),
        "location": j.get("location"),
        "region": j.get("region"),
        # Carried since the page gained Country and City filters: a card the visitor narrowed
        # to Brno should be able to say so, and `_job_view` on the public feed already does.
        "country_code": j.get("country_code"),
        "city": j.get("city"),
        "seniority": j.get("seniority"),
        "work_type": j.get("work_type"),
        "work_mode": j.get("work_mode"),
        "role_category": j.get("role_category"),
        "salary": j.get("salary_raw"),
        "score": j.get("score"),
        "summary": j.get("summary"),
        # Extracted tech/tool facet (migration 020), read-only chips. Null/empty -> [] so the
        # web can always `.map` it. Canonical names are proper nouns, rendered untranslated.
        "skills": j.get("skills") or [],
        "posted_at": posted.isoformat() if hasattr(posted, "isoformat") else posted,
    }


@app.get("/matches")
def get_matches(request: Request, token: Optional[str] = None, offset: int = 0,
                hidden: bool = False, skills: Optional[str] = None,
                work_modes: Optional[str] = None, great_fits: bool = False,
                q: Optional[str] = None, categories: Optional[str] = None,
                countries: Optional[str] = None, cities: Optional[str] = None,
                seniorities: Optional[str] = None) -> dict:
    """One page of everything the matcher found for this subscriber (not just the emailed
    few), ranked best-first. Authenticated by the private magic-link token or the session
    cookie.

    `count` is the unbounded total and `jobs` is a page of at most MATCHES_PAGE_LIMIT, so the
    caller must page with `offset` until it has `count` of them. Returning the two without
    the caller doing that is what made the page claim "127 matches … this is the full list"
    while rendering 25: every subscriber was over the limit, and the ~100 rows below the cut
    are exactly the sub-EMAIL_MIN_SCORE picks this page exists to show. /matches is the
    complete record that makes never-email-twice suppression safe — it has to be complete.

    `hidden=true` returns the other half of the same record: the jobs the subscriber hid
    (already applied, not interested). Both counts come back either way, so the visible page
    can link to "Hidden (n)" without a second round trip and the hidden page can link back.

    Three optional display filters narrow both the rows and `count` — they must agree, which
    `store._match_filters` now guarantees by construction rather than by both call sites
    remembering:

      `skills`      comma-separated skill ids; array-overlap, so ANY selected skill matches.
      `work_modes`  comma-separated `remote` / `hybrid` / `onsite`. A posting whose ad never
                    said is excluded — this is an explicit request for a named setup, not the
                    matcher's keep-unknown rule, and the facet never offers null as an option.
      `great_fits`  keeps only scores >= GREAT_FIT_MIN_SCORE.

    **Values outside the controlled vocabulary are dropped, not 422'd.** A hand-edited param
    should show fewer results, never break the page (the same forgiving rule as `offset`).
    That is deliberately the opposite of `/jobs`, which 400s on an unknown value: there the
    filters are the whole page and a silently-ignored one would render an unfiltered corpus
    under a heading claiming otherwise, whereas here the subscriber's own matches are the
    page and a dropped filter merely shows more of them. Both rules are right where they are.

    `facets` carries each menu's options computed with the *other* filters applied but never
    its own, so no single tick can empty the menu it came from. `skill_facets` remains as a
    top-level alias of `facets.skills` because it is the shipped shape the deployed frontend
    reads — removing it would blank the filter row for anyone on a cached bundle.
    """
    profile = _resolve_subscriber(request, token, mutating=False)
    if not profile:
        raise HTTPException(404, "Unknown or expired link.")
    # Clamp rather than 422: a hand-edited offset should show an empty last page, not break
    # someone's match list.
    offset = max(0, offset)
    # Validate each filter against its controlled vocabulary; drop anything else silently.
    valid = set(skill_gazetteer.canonical_skills())
    picked = [t for t in (s.strip().lower() for s in (skills or "").split(",")) if t in valid]
    skills_filter = picked or None
    picked_modes = [m for m in (w.strip().lower() for w in (work_modes or "").split(","))
                    if m in geo.WORK_MODES]
    modes_filter = picked_modes or None
    min_score = GREAT_FIT_MIN_SCORE if great_fits else None

    # The four filters this page gained on 2026-08-12, so it offers the same axes as the
    # public feed. Dropped rather than 400'd when unknown, like every other filter here and
    # unlike `/jobs` — see the docstring: there the filters are the whole page, here they
    # narrow a record the subscriber already owns.
    picked_cats = _drop_unknown(categories, _SEARCH_CATEGORIES)
    picked_countries = _drop_unknown(countries, frozenset(geo.COUNTRIES), lower=False)
    picked_levels = _drop_unknown(seniorities, _SEARCH_SENIORITIES)
    # `clean_cities` drops a city whose country is not also selected, so a city filter cannot
    # outlive the country it belongs to — the same guarantee `/jobs` relies on.
    picked_cities = geo.clean_cities(
        [c for c in (cities or "").split(",") if c.strip()], picked_countries or None)
    term = (q or "").strip()[:SEARCH_TERM_MAX] or None

    narrowing = dict(skills_filter=skills_filter, work_modes=modes_filter,
                     min_score=min_score, q=term, categories=picked_cats or None,
                     countries=picked_countries or None, cities=picked_cities or None,
                     seniorities=picked_levels or None)

    jobs = store.matched_jobs(profile["id"], limit=MATCHES_PAGE_LIMIT, offset=offset,
                              hidden=hidden, **narrowing)
    facets = store.match_facets(profile["id"], hidden=hidden,
                                great_fit_score=GREAT_FIT_MIN_SCORE, **narrowing)
    # The category menu is filtered to the canonical vocabulary here for the same reason the
    # public one is: production still holds rows whose `role_category` is a raw SSYK label,
    # and a matched posting carrying one would offer it as a filter. Cities likewise.
    facets["categories"] = [f for f in facets.get("categories", [])
                            if f["value"] in _SEARCH_CATEGORIES]
    facets["countries"] = [f for f in facets.get("countries", [])
                           if f["value"] in geo.COUNTRIES]
    if "cities" in facets:
        facets["cities"] = [f for f in facets["cities"] if _is_offered_city(f["value"])]
    return {
        "email": profile.get("email"),
        "label": profile.get("label"),
        "count": store.match_count(profile["id"], hidden=hidden, **narrowing),
        # The other half's total is deliberately unfiltered: it labels a link to a different
        # view, and "Hidden (3)" that changes as you narrow *this* page would be describing
        # a list the subscriber has not opened.
        "hidden_count": store.match_count(profile["id"], hidden=True),
        "offset": offset,
        "limit": MATCHES_PAGE_LIMIT,
        "hidden": hidden,
        "skills": picked,
        "work_modes": picked_modes,
        # Echoed back so the client renders the filters that were actually applied rather
        # than the ones it asked for — a city dropped for naming an unselected country must
        # not stay lit in the UI, which is the same rule `/jobs` follows by pruning the URL.
        "q": term or "",
        "categories": picked_cats,
        "countries": picked_countries,
        "cities": picked_cities,
        "seniorities": picked_levels,
        "great_fits": great_fits,
        "great_fit_score": GREAT_FIT_MIN_SCORE,
        "facets": facets,
        "skill_facets": facets["skills"],   # legacy alias — see the docstring
        "jobs": [_match_view(j) for j in jobs],
    }


# How many jobs one hide/unhide call may name. A page shows MATCHES_PAGE_LIMIT rows and the
# UI only ever submits what is on screen, so this is far above any honest request — it is
# here so a scripted caller cannot make one request that rewrites an unbounded number of
# rows. Refused loudly rather than truncated: a silent partial write would leave the page
# and the database disagreeing about what is hidden.
MAX_HIDE_IDS = 200


class MatchHideIn(BaseModel):
    token: Optional[str] = None
    posting_ids: list[str] = []


def _set_hidden(body: MatchHideIn, request: Request, *, hidden: bool) -> dict:
    profile = _resolve_subscriber(request, body.token, mutating=True)
    if not profile:
        raise HTTPException(404, "Unknown or expired link.")
    if len(body.posting_ids) > MAX_HIDE_IDS:
        raise HTTPException(400, f"Too many jobs in one request (max {MAX_HIDE_IDS}).")
    changed = store.set_matches_hidden(profile["id"], body.posting_ids, hidden)
    # The fresh totals: hiding shrinks one list and grows the other, and the caller renders
    # both numbers. Returning them here is what lets the page drop the rows it just hid
    # instead of refetching a list whose offsets have all moved.
    return {
        "ok": True,
        "changed": changed,
        "visible_count": store.match_count(profile["id"]),
        "hidden_count": store.match_count(profile["id"], hidden=True),
    }


@app.post("/matches/hide")
def hide_matches(body: MatchHideIn, request: Request) -> dict:
    """Hide jobs from this subscriber's own matches page — and from their digest.

    A POST, not a GET, for the reason `/unsubscribe` is: link scanners fetch URLs found in
    email, and this changes state. Nothing here trusts the ids beyond the subscriber they
    are scoped to (see `store.set_matches_hidden`); an id that isn't theirs changes nothing
    and is reported as changed=0 rather than as an error, because a stale page re-submitting
    a posting that has since gone inactive is ordinary traffic, not an attack.
    """
    return _set_hidden(body, request, hidden=True)


@app.post("/matches/unhide")
def unhide_matches(body: MatchHideIn, request: Request) -> dict:
    """Put hidden jobs back on the matches page. Hiding is never a delete — this is why."""
    return _set_hidden(body, request, hidden=False)


@app.post("/preferences")
def update_preferences(body: PreferencesIn, request: Request) -> dict:
    profile = _resolve_subscriber(request, body.token, mutating=True)
    if not profile:
        raise HTTPException(404, "Unknown or expired link.")
    changes = body.model_dump(exclude={"token"}, exclude_none=True)
    updated = store.update_subscription(profile["manage_token"], changes)
    if not updated:
        raise HTTPException(404, "Unknown or expired link.")
    return _public_view(updated)


class PauseIn(BaseModel):
    token: Optional[str] = None
    days: int = 14


@app.post("/pause")
def pause(body: PauseIn, request: Request) -> dict:
    profile = _resolve_subscriber(request, body.token, mutating=True)
    if not profile:
        raise HTTPException(404, "Unknown or expired link.")
    until = datetime.now(timezone.utc) + timedelta(days=max(1, body.days))
    store.pause_subscription(profile["manage_token"], until)
    return {"ok": True, "status": "paused", "paused_until": until.isoformat()}


class TokenIn(BaseModel):
    token: Optional[str] = None


@app.post("/resume")
def resume(body: TokenIn, request: Request) -> dict:
    profile = _resolve_subscriber(request, body.token, mutating=True)
    if not profile:
        raise HTTPException(404, "Unknown or expired link.")
    store.resume_subscription(profile["manage_token"])
    return {"ok": True, "status": "active"}


# --------------------------------------------------------------- unsubscribe ---

def _do_unsubscribe(token: str) -> Optional[str]:
    return store.unsubscribe(token)     # returns email or None; also adds suppression


def _unsubscribed_page() -> str:
    return _page("Unsubscribed", f"""
      <div style="font:700 22px {SERIF};margin-bottom:10px;">You've unsubscribed</div>
      <p style="font:400 15px {SANS};color:{C['muted']};line-height:1.55;">
        No more emails — effective immediately. Changed your mind?
        <a href="{SITE_URL}" style="color:{C['brand']};">Re-subscribe anytime</a>.</p>""")


def _bad_link_page() -> str:
    return _page("Not found", f"""
      <div style="font:700 20px {SERIF};margin-bottom:10px;">Nothing to unsubscribe</div>
      <p style="font:400 15px {SANS};color:{C['muted']};">This link isn't valid or you've already unsubscribed.</p>""")


@app.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(token: str) -> HTMLResponse:
    """Ask for confirmation — do NOT unsubscribe on the GET itself.

    A GET must not change state, and here that is not pedantry: mail clients, link
    previewers and corporate security scanners fetch URLs found in messages to check them,
    which silently unsubscribed people who never clicked. Now the click lands on a button.

    RFC 8058 one-click is unaffected — mail clients POST directly from
    List-Unsubscribe-Post and never issue this GET.
    """
    profile = store.get_by_manage_token(token)
    if not profile:
        return HTMLResponse(_bad_link_page(), status_code=404)

    action = f"{links.api_public_url()}/unsubscribe"
    return HTMLResponse(_page("Unsubscribe", f"""
      <div style="font:700 22px {SERIF};margin-bottom:10px;">Unsubscribe from JobDigest?</div>
      <p style="font:400 15px {SANS};color:{C['muted']};line-height:1.55;">
        This stops the daily digest to <b>{html.escape(profile.get('email') or 'your address')}</b>
        immediately.</p>
      <form method="post" action="{html.escape(action)}" style="margin-top:22px;">
        <input type="hidden" name="token" value="{html.escape(token)}">
        <input type="hidden" name="confirm" value="1">
        <button type="submit" style="font:700 14px {SANS};color:#fff;background:{C['brand']};
          border:0;border-radius:10px;padding:12px 26px;cursor:pointer;">Yes, unsubscribe me</button>
      </form>
      <p style="margin-top:18px;font:400 13px {SANS};">
        <a href="{links.preferences_link(token)}" style="color:{C['brand']};">
          Or just change how often you hear from us →</a></p>"""))


@app.post("/unsubscribe")
def unsubscribe_oneclick(
    request: Request,
    response: Response,
    token: Optional[str] = Form(None),
    confirm: Optional[str] = Form(None),
):
    """Perform the unsubscribe. Three callers, two response shapes.

    * Email link / RFC 8058: `token` in the form body. `confirm` is set only by our own HTML
      form, so a person gets a page; RFC 8058 one-click posts without it and gets JSON.
      Always 200 for a token so mail clients never retry a one-click that already succeeded.
    * Logged-in browser: no token — the session cookie identifies the subscriber, and (being
      a mutating cookie-authed request) it must carry the CSRF header. Its sessions are then
      revoked and the cookie cleared, because the subscription it authenticated is gone."""
    if token:
        email = _do_unsubscribe(token)      # idempotent
        if confirm:
            if email is None:
                return HTMLResponse(_bad_link_page(), status_code=404)
            return HTMLResponse(_unsubscribed_page())
        return {"ok": True}

    # Cookie path.
    profile = _cookie_profile(request)
    if profile is None:
        raise HTTPException(404, "Unknown or expired link.")
    if request.headers.get(CSRF_HEADER) is None:
        raise HTTPException(403, "Missing CSRF header for cookie-authenticated request.")
    _do_unsubscribe(profile["manage_token"])
    store.revoke_profile_sessions(profile["id"])
    _clear_session_cookie(response)
    return {"ok": True}


# --------------------------------------------------------------- cv/parse ------

async def _read_capped(file: UploadFile, cap: int) -> bytes:
    """Read an upload in bounded chunks, rejecting as soon as it exceeds `cap` — so a
    multi-gigabyte body can never be buffered into memory/disk before we notice."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise HTTPException(413, "That file is too large (max 8 MB).")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_cv(data: bytes, filename: str) -> dict:
    """Sync, CPU-bound parse — always called via a thread so it can't block the event loop."""
    text = cvparse.extract_text(data, filename)
    return cvparse.extract_signals(text)


@app.post("/cv/parse")
async def cv_parse(file: UploadFile, cf_turnstile_token: Optional[str] = Form(None)) -> dict:
    """Parse an uploaded CV into derived signals. The file is read in memory and discarded;
    only the returned signals ever leave this function (parse & discard).

    Bot protection: Turnstile here is OPTIONAL (the widget lives on the final signup step, so a
    token isn't available yet when the CV is dropped on step 1). A token, if sent, is still
    validated; a missing one is allowed through because this endpoint is already bounded by the
    hard byte cap, the wall-clock parse timeout, and edge rate-limiting. The actual signup at
    /subscribe remains strict Turnstile. In local dev (no secret) it is a full no-op.
    Resource safety: the body is read under a hard byte cap, and the CPU-bound parse runs in a
    worker thread under a wall-clock timeout so a pathological file can neither exhaust memory
    nor stall the API (security review H2)."""
    _verify_turnstile(cf_turnstile_token, required=False)
    data = await _read_capped(file, cvparse.MAX_BYTES)
    try:
        signals = await asyncio.wait_for(
            asyncio.to_thread(_parse_cv, data, file.filename or ""),
            timeout=CV_PARSE_TIMEOUT,
        )
    except cvparse.CVParseError as e:
        raise HTTPException(422, str(e))
    except asyncio.TimeoutError:
        logging.warning("cv/parse timed out on %r (%d bytes)", file.filename, len(data))
        raise HTTPException(503, "That file took too long to process. Please try a simpler PDF/DOCX.")
    finally:
        del data                        # drop the raw bytes promptly
    return {"ok": True, "signals": signals}


# --------------------------------------------------------------- analytics -----
# First-party, cookieless product analytics. Exists to answer "where does the signup flow
# lose people", nothing more. See migration_005_events.sql for the privacy rationale and
# web/lib/analytics.ts for the client half.

# Strict whitelist: an unknown name is dropped, so a hostile client can't invent event
# types or use this table as free storage.
EVENT_NAMES = {
    "landing_view",         # someone loaded the landing page
    "form_started",         # first interaction with the signup form
    "cv_upload_attempted",  # a file was chosen
    "cv_parse_ok",          # parse succeeded (props: {"skills": <int count>})
    "cv_parse_failed",      # parse failed  (props: {"reason": "<enum>"}) — the key UX signal
    "turnstile_failed",     # bot check rejected a (probably real) person
    "subscribe_submitted",  # signup POST issued
    "subscribe_ok",         # signup accepted
    "subscribe_error",      # signup rejected (props: {"status": <int>})
    "confirm_clicked",      # double opt-in link followed
    "preferences_viewed",
    "preferences_saved",
    "matches_viewed",
    "match_clicked",
    # The public feed. `job_search` carries how many results came back and *never the query*
    # — the whitelist below could not admit it anyway, and `/event` strips the query string
    # off `path`, so what somebody searched for cannot reach this table by any route. That is
    # the property the privacy policy depends on; do not add a "term" key to make a funnel
    # easier to read.
    "feed_viewed",
    "job_search",           # props: {"count": <int results>}
    "feed_job_clicked",
}

# Only these keys may appear in props, and values are coerced to short scalars — this is
# what keeps CV text, emails and search terms out of the table by construction.
EVENT_PROP_KEYS = {"reason", "status", "skills", "count", "variant", "step"}

# Abuse bounds. The whitelist above limits *what* can be written; nothing limited *how
# much*, so anyone could grow this table indefinitely. Two in-memory fixed-window counters
# fix that without a dependency or a round trip.
#
# Deliberately NOT keyed on IP: the privacy policy promises we never keep your IP, raw or
# hashed, and a rate-limit map keyed on IP is still keeping it. Keyed on the client-supplied
# session id instead — which is trivially rotated, so the per-session cap only stops honest
# runaway loops. The GLOBAL cap is the one that actually bounds table growth against someone
# inventing new ids, and it is why both exist.
EVENT_WINDOW_SEC = int(os.environ.get("EVENT_WINDOW_SEC", "60"))
EVENT_MAX_PER_SESSION = int(os.environ.get("EVENT_MAX_PER_SESSION", "60"))
EVENT_MAX_GLOBAL = int(os.environ.get("EVENT_MAX_GLOBAL", "2000"))

_event_window_start = 0.0
_event_counts: dict[str, int] = {}
_event_total = 0


def _event_allowed(session_id: Optional[str]) -> bool:
    """False when this event should be dropped. Fixed window, process-local.

    Clearing the dict each window also bounds the limiter's own memory, so the thing
    protecting the box cannot itself become the leak. Single uvicorn process, so this
    state is coherent; if the API is ever scaled to workers this becomes per-worker and
    the global cap should move to the edge or to Postgres.
    """
    global _event_window_start, _event_total
    now = time.monotonic()
    if now - _event_window_start >= EVENT_WINDOW_SEC:
        _event_window_start = now
        _event_counts.clear()
        _event_total = 0
    if _event_total >= EVENT_MAX_GLOBAL:
        return False
    _event_total += 1
    key = session_id or "-"
    seen = _event_counts.get(key, 0) + 1
    _event_counts[key] = seen
    return seen <= EVENT_MAX_PER_SESSION


_BROWSERS = (("edg", "edge"), ("chrome", "chrome"), ("safari", "safari"),
             ("firefox", "firefox"))


def _browser_family(ua: str) -> str:
    """Coarse family only — we never store the raw user-agent string."""
    ua = (ua or "").lower()
    for needle, family in _BROWSERS:
        if needle in ua:
            # Chrome's UA contains "safari"; edge's contains "chrome". Order above handles it.
            return family
    return "other"


def _clean_props(props: Optional[dict]) -> dict:
    out: dict = {}
    for k, v in (props or {}).items():
        if k not in EVENT_PROP_KEYS:
            continue
        if isinstance(v, bool) or isinstance(v, int):
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:64]                      # short enum-ish strings only
    return out


class EventIn(BaseModel):
    name: str
    session_id: Optional[str] = Field(None, max_length=64)
    path: Optional[str] = Field(None, max_length=200)
    props: Optional[dict] = None
    token: Optional[str] = None      # manage token, only sent from authenticated pages


@app.post("/event")
async def event(body: EventIn, request: Request) -> dict:
    """Record one analytics event. Always returns {"ok": true} — analytics must never
    surface an error into a user flow, and a silent response gives a hostile client no
    signal about what was accepted.

    Country comes from Cloudflare's edge header; the IP itself is never read or stored.
    """
    if body.name not in EVENT_NAMES:
        return {"ok": True}                       # unknown event: silently ignored
    # Before the token lookup below, which is a DB round trip — otherwise the rate limit
    # would still let a flood cost us one query per request.
    if not _event_allowed(body.session_id):
        return {"ok": True}                       # dropped; the client is told nothing

    # A manage token identifies a subscriber on their own pages. Resolve it to a profile_id
    # so engagement can be analysed; the token itself is never stored on the event.
    profile_id = None
    if body.token:
        prof = store.get_by_manage_token(body.token)
        profile_id = str(prof["id"]) if prof else None

    path = (body.path or "").split("?")[0][:200] or None   # drop any query string
    country = (request.headers.get("cf-ipcountry") or "")[:2].upper() or None
    if country in {"XX", "T1"}:                    # CF's unknown / Tor placeholders
        country = None

    store.record_event(
        body.name,
        session_id=body.session_id,
        path=path,
        country=country,
        browser=_browser_family(request.headers.get("user-agent", "")),
        props=_clean_props(body.props),
        profile_id=profile_id,
    )
    return {"ok": True}
