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

Bot protection: every mutating public entry point (subscribe, cv/parse) verifies a
Cloudflare Turnstile token. If TURNSTILE_SECRET is unset (local dev) verification is
skipped so the flow is testable with no account.

Run:  DATABASE_URL=... MAIL_BACKEND=file uvicorn service.webapp:app --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, UploadFile
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


# --------------------------------------------------------------- confirm -------

@app.get("/confirm", response_class=HTMLResponse)
def confirm(token: str) -> HTMLResponse:
    profile = store.confirm_subscription(token)
    if not profile:
        return HTMLResponse(_page("Link expired", f"""
          <div style="font:700 20px {SERIF};margin-bottom:10px;">This link isn't valid</div>
          <p style="font:400 15px {SANS};color:{C['muted']};">It may have already been used or expired.
          Try signing up again at <a href="{SITE_URL}" style="color:{C['brand']};">jobdigest.eu</a>.</p>"""),
          status_code=404)

    # Welcome email (idempotent-ish: confirm is safe to click twice; we send on each confirm).
    manage_url = links.preferences_link(profile["manage_token"])
    subject, html_body, text = transactional.render_welcome(profile["email"], manage_url)
    mailer.send(profile["email"], subject, html_body, text)

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


@app.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(token: str) -> HTMLResponse:
    email = _do_unsubscribe(token)
    if email is None:
        return HTMLResponse(_page("Not found", f"""
          <div style="font:700 20px {SERIF};margin-bottom:10px;">Nothing to unsubscribe</div>
          <p style="font:400 15px {SANS};color:{C['muted']};">This link isn't valid or you've already unsubscribed.</p>"""),
          status_code=404)
    return HTMLResponse(_page("Unsubscribed", f"""
      <div style="font:700 22px {SERIF};margin-bottom:10px;">You've unsubscribed</div>
      <p style="font:400 15px {SANS};color:{C['muted']};line-height:1.55;">
        No more emails — effective immediately. Changed your mind?
        <a href="{SITE_URL}" style="color:{C['brand']};">Re-subscribe anytime</a>.</p>"""))


@app.post("/unsubscribe")
def unsubscribe_oneclick(token: str = Form(...)) -> dict:
    """RFC 8058 one-click: mail clients POST here from the List-Unsubscribe-Post header."""
    _do_unsubscribe(token)              # idempotent; always 200 so clients don't retry
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
