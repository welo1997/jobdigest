"""Public digest API (v1 product surface) — token-based, no accounts.

This is the capture + self-service layer the Next.js landing (Phase B2) talks to. It is
deliberately separate from `service/api.py` (the internal `/matches` API, kept but not
exposed in v1). Everything a subscriber can do is reachable with a token from their email;
there is never a password.

    POST /subscribe          create a PENDING subscription + send the confirm email
    GET  /confirm            double opt-in — activate + send welcome (HTML page)
    GET  /preferences        current settings for a manage token (JSON)
    POST /preferences        update settings
    POST /pause              pause N days without unsubscribing
    POST /resume             resume a paused subscription
    GET  /unsubscribe        human one-click unsubscribe (HTML page)
    POST /unsubscribe        RFC 8058 one-click (List-Unsubscribe-Post from mail clients)
    POST /cv/parse           multipart CV -> derived signals JSON (file discarded)
    POST /event              cookieless first-party analytics event (whitelisted names)

Bot protection: every mutating public entry point (subscribe, cv/parse) verifies a
Cloudflare Turnstile token. If TURNSTILE_SECRET is unset (local dev) verification is
skipped so the flow is testable with no account.

Run:  DATABASE_URL=... MAIL_BACKEND=file uvicorn service.webapp:app --reload
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field

from service import cvparse, links, mailer, store, transactional
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
    allow_headers=["Content-Type"],
)


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

class SubscribeIn(BaseModel):
    email: EmailStr
    label: str = "My digest"
    stack: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=lambda: ["junior", "mid"])
    regions: list[str] = Field(default_factory=lambda: ["cz", "eu", "worldwide"])
    role_categories: list[str] = Field(default_factory=list)
    work_types: list[str] = Field(default_factory=lambda: ["permanent", "freelance/contract"])
    part_time_only: bool = False
    eligible_only: bool = True
    sectors: list[str] = Field(default_factory=list)
    min_score: int = 6
    frequency: str = "daily"
    # optional CV signals from a prior POST /cv/parse (merged server-side)
    cv_signals: Optional[dict] = None
    cf_turnstile_token: Optional[str] = None


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

    # Per-email cooldown: if we already emailed a confirm to this address very recently,
    # don't send another (or create another pending row). Blunts inbox-flooding a victim
    # and duplicate rows on rapid retries. Same generic response either way.
    if store.recent_signup_exists(email, SUBSCRIBE_COOLDOWN_MIN):
        return generic

    data = body.model_dump(exclude={"email", "cf_turnstile_token", "cv_signals"})
    if body.cv_signals:
        cvparse.merge_into_profile(data, body.cv_signals)

    profile = store.create_email_subscription(email, data)
    confirm_url = links.confirm_link(profile["confirm_token"])
    subject, html_body, text = transactional.render_confirm(email, confirm_url)
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


# --------------------------------------------------------------- preview -------
# Instant, no-AI keyword preview shown right after signup. It runs the SAME cheap prefilter
# the daily matcher uses (store.query_shortlist), applies the seniority hard-filter locally
# (the AI normally enforces that), and ranks by how many of the person's skills each posting
# mentions. It is read-only: no email, no DB writes, no `matches` rows, no score. The daily
# digest still delivers the real AI-ranked version — this is only a "here are live jobs now"
# first impression. Deliberately key-less, so it never touches the billing decision.

class PreviewIn(BaseModel):
    label: str = "My digest"
    stack: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=lambda: ["junior", "mid"])
    regions: list[str] = Field(default_factory=lambda: ["cz", "eu", "worldwide"])
    role_categories: list[str] = Field(default_factory=list)
    work_types: list[str] = Field(default_factory=lambda: ["permanent", "freelance/contract"])
    part_time_only: bool = False
    eligible_only: bool = True
    sectors: list[str] = Field(default_factory=list)
    cv_signals: Optional[dict] = None
    cf_turnstile_token: Optional[str] = None

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
    if j.get("region") in ("eu", "worldwide"):
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

    manage_url = links.preferences_link(profile["manage_token"])

    # Only on the click that actually confirmed. Sending on every click made a confirm link
    # an unlimited "email this person" primitive for anyone who obtained one.
    if newly_confirmed:
        subject, html_body, text = transactional.render_welcome(profile["email"], manage_url)
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

_PUBLIC_FIELDS = ["email", "status", "label", "stack", "seniorities", "regions",
                  "role_categories", "work_types", "part_time_only", "eligible_only",
                  "sectors", "min_score", "frequency", "has_cv", "cv_summary",
                  "years_experience", "paused_until"]


def _public_view(profile: dict) -> dict:
    """Subscriber-safe projection — never leak tokens."""
    return {k: profile.get(k) for k in _PUBLIC_FIELDS}


class PreferencesIn(BaseModel):
    token: str
    label: Optional[str] = None
    stack: Optional[list[str]] = None
    seniorities: Optional[list[str]] = None
    regions: Optional[list[str]] = None
    role_categories: Optional[list[str]] = None
    work_types: Optional[list[str]] = None
    part_time_only: Optional[bool] = None
    eligible_only: Optional[bool] = None
    sectors: Optional[list[str]] = None
    min_score: Optional[int] = None
    frequency: Optional[str] = None


@app.get("/preferences")
def get_preferences(token: str) -> dict:
    profile = store.get_by_manage_token(token)
    if not profile:
        raise HTTPException(404, "Unknown or expired link.")
    return _public_view(profile)


MATCHES_PAGE_LIMIT = 25


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
        "seniority": j.get("seniority"),
        "work_type": j.get("work_type"),
        "role_category": j.get("role_category"),
        "salary": j.get("salary_raw"),
        "score": j.get("score"),
        "summary": j.get("summary"),
        "posted_at": posted.isoformat() if hasattr(posted, "isoformat") else posted,
    }


@app.get("/matches")
def get_matches(token: str) -> dict:
    """Everything the matcher found for this subscriber (not just the emailed few),
    ranked best-first. Token-based, same private link as /preferences — no login."""
    profile = store.get_by_manage_token(token)
    if not profile:
        raise HTTPException(404, "Unknown or expired link.")
    jobs = store.matched_jobs(profile["id"], limit=MATCHES_PAGE_LIMIT)
    return {
        "email": profile.get("email"),
        "label": profile.get("label"),
        "count": store.match_count(profile["id"]),
        "jobs": [_match_view(j) for j in jobs],
    }


@app.post("/preferences")
def update_preferences(body: PreferencesIn) -> dict:
    changes = body.model_dump(exclude={"token"}, exclude_none=True)
    profile = store.update_subscription(body.token, changes)
    if not profile:
        raise HTTPException(404, "Unknown or expired link.")
    return _public_view(profile)


class PauseIn(BaseModel):
    token: str
    days: int = 14


@app.post("/pause")
def pause(body: PauseIn) -> dict:
    if not store.get_by_manage_token(body.token):
        raise HTTPException(404, "Unknown or expired link.")
    until = datetime.now(timezone.utc) + timedelta(days=max(1, body.days))
    store.pause_subscription(body.token, until)
    return {"ok": True, "status": "paused", "paused_until": until.isoformat()}


class TokenIn(BaseModel):
    token: str


@app.post("/resume")
def resume(body: TokenIn) -> dict:
    if not store.get_by_manage_token(body.token):
        raise HTTPException(404, "Unknown or expired link.")
    store.resume_subscription(body.token)
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
def unsubscribe_oneclick(token: str = Form(...), confirm: Optional[str] = Form(None)):
    """Perform the unsubscribe. Two callers, two response shapes.

    `confirm` is set only by our own HTML form above, so a person gets a page; RFC 8058
    one-click posts without it and gets JSON. Always 200, even for an unknown token, so
    mail clients never retry a one-click that already succeeded."""
    email = _do_unsubscribe(token)      # idempotent
    if confirm:
        if email is None:
            return HTMLResponse(_bad_link_page(), status_code=404)
        return HTMLResponse(_unsubscribed_page())
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
