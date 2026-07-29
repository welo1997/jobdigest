"""Build and render a subscriber's daily digest.

Pure functions where possible so this is easy to test and reuse:

    build_digest(profile)  -> ranked, de-duplicated shortlist (list[dict])
    render_html(profile, jobs, base_url) -> str   (Direction-A email, inline styles)
    render_text(profile, jobs, base_url) -> str   (plaintext alternative part)
    subject_line(profile, jobs)          -> str

`build_digest` reads the AI matcher's picks from `matches` (best fit first), drops
anything already emailed to this profile (`digest_sends`), and returns the top N. The
inference happened earlier in `service.matcher`; this module does no scoring itself.

Run standalone to see a real email written to service/outbox/ (requires the matcher to
have run for the profile first, otherwise the digest is empty):

    DATABASE_URL=... python -m service.digest [--email you@example.com] [--limit 7]
"""

from __future__ import annotations

import argparse
import html
import os
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import links, store, taxonomy  # noqa: E402

DEFAULT_LIMIT = 5            # curated highlights in the email; the rest live on /matches
EMAIL_MIN_SCORE = 6         # a "strong" fit — a digest of these is the normal, headline case
FALLBACK_LIMIT = int(os.environ.get("DIGEST_FALLBACK_LIMIT", "3"))  # weaker picks shown on a quiet day
BASE_URL = os.environ.get("BASE_URL", "https://jobdigest.eu")

# Direction-A palette (kept in sync with design/prototype.html)
C = {
    "paper": "#FBFAF6", "surface": "#FFFFFF", "surface2": "#F4F2EC",
    "ink": "#182233", "muted": "#5B6472", "faint": "#8A93A1",
    "brand": "#0E7C6B", "brandDeep": "#0A5C50", "gold": "#C98A18", "line": "#E7E4DC",
}
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


# ---------------------------------------------------------------- build --------

def build_digest(profile: dict, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Return the top-`limit` AI-picked matches for a profile, best fit first.

    Reads the picks the matcher (`service.matcher`) wrote to `matches` — no heuristic
    scoring. Anything already emailed to this profile (`digest_sends`) is filtered out so
    a job is never sent twice. If the matcher hasn't run for this profile yet, returns []
    and the digest is skipped (nothing to send)."""
    if not profile.get("id"):
        return []
    picks = store.matched_jobs(profile["id"], limit=limit * 6)
    already = store.already_sent_ids(profile["id"])
    unsent = [j for j in picks if j["posting_id"] not in already]
    strong = [j for j in unsent if (j.get("score") or 0) >= EMAIL_MIN_SCORE]
    if strong:
        return strong[:limit]
    # Quiet day: no strong (>= EMAIL_MIN_SCORE) match. Rather than skip the send entirely,
    # surface the best of the weaker (4-5) matches so the subscriber still gets a daily
    # signal. They are all sub-threshold, which the renderer detects (see `_is_weak`) and
    # labels honestly rather than dressing them up as strong fits. Only a genuinely empty
    # pick list — the matcher found nothing new at all — still returns [] and skips.
    return unsent[:FALLBACK_LIMIT]


def _is_weak(jobs: list[dict]) -> bool:
    """True when this is a quiet-day digest: every job is below EMAIL_MIN_SCORE.

    build_digest returns either all-strong or all-weak, never a mix, so checking the
    scores is enough — no separate flag has to be threaded through the renderers."""
    return bool(jobs) and all((j.get("score") or 0) < EMAIL_MIN_SCORE for j in jobs)


# ---------------------------------------------------------------- helpers ------

def _manage_url(profile: dict, action: str = "") -> str:
    """Footer links: preferences + pause both land on the self-service page (frontend);
    unsubscribe is the API's one-click page."""
    tok = profile.get("manage_token", "")
    if action == "unsub":
        return links.unsubscribe_link(tok)
    return links.preferences_link(tok)      # "" (refine) and "pause" both -> prefs page


def _matches_url(profile: dict) -> str:
    """The 'see all matches' page (everything the matcher found, not just the emailed few)."""
    return links.matches_link(profile.get("manage_token", ""))


def safe_url(url: str | None) -> str:
    """Return `url` only if it is a plain http(s) link, else '#'.

    Posting URLs come from third-party feeds we do not control and land in an email `href`.
    `html.escape` stops attribute breakout but says nothing about the *scheme*, so a feed
    serving `javascript:` or `data:text/html,...` would ship an active link out under our
    DKIM signature. Most mail clients block those, which is a reason not to rely on them.
    """
    u = (url or "").strip()
    return u if u[:7].lower() == "http://" or u[:8].lower() == "https://" else "#"


def _tags(job: dict) -> list[str]:
    tags = []
    if job.get("region"):
        tags.append(str(job["region"]).upper())
    # "Hybrid" is checked first and is never also "Remote": before migration 012 these
    # postings carried no work tag at all, so a Prague office job with two days from home
    # looked identical in the email to one with five days in the office.
    if job.get("work_mode") == "hybrid":
        tags.append("Hybrid")
    elif job.get("remote_signal") or (job.get("region") in ("eu", "worldwide")):
        tags.append("Remote")
    if job.get("seniority"):
        tags.append(str(job["seniority"]).capitalize())
    if job.get("work_type") == "freelance/contract":
        tags.append("Freelance")
    if job.get("is_part_time"):
        tags.append("Part-time")
    # de-dup preserving order
    seen, out = set(), []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:4]


# role_category -> what a human calls it in a subject line (see service/taxonomy.py).
_ROLE_WORDS = taxonomy.SUBJECT_WORDS


def subject_line(profile: dict, jobs: list[dict]) -> str:
    """Describe what is actually in *this* email, not what the profile asks for.

    Previously this used the profile's first role_category, which is an unordered Postgres
    array — so a profile listing several categories got a subject naming whichever happened
    to sort first, even when no job in the digest matched it (observed 2026-07-20: "3 new
    product roles" for a digest of three data_analysis jobs). Now the label comes from the
    most common role_category among the jobs being sent, and is dropped entirely when the
    digest is mixed, which is honest rather than misleading."""
    n = len(jobs)
    when = datetime.now().strftime("%-d %b") if os.name != "nt" else datetime.now().strftime("%#d %b")

    # Quiet day: don't claim "N new roles for you" when nothing cleared the bar — say so.
    if _is_weak(jobs):
        thing = "role" if n == 1 else "roles"
        return f"No strong matches today — {n} {thing} to explore — {when}"

    cats = [j.get("role_category") for j in jobs if j.get("role_category")]
    role = ""
    if cats:
        top, hits = Counter(cats).most_common(1)[0]
        # Only claim a category when it genuinely characterises the digest. A 2-of-5 plurality
        # would make the subject a lie for most of the email.
        if hits / len(cats) >= 0.6:
            role = _ROLE_WORDS.get(top, top.replace("_", " "))

    noun = "role" if n == 1 else "roles"
    return f"{n} new {role} {noun} for you — {when}" if role else f"{n} new {noun} for you — {when}"


# ---------------------------------------------------------------- HTML ---------

def _job_html(job: dict) -> str:
    esc = html.escape
    tags = "".join(
        f'<span style="font:600 10px {SANS};letter-spacing:.04em;text-transform:uppercase;'
        f'color:{C["muted"]};background:{C["surface2"]};border:1px solid {C["line"]};'
        f'border-radius:999px;padding:2px 8px;margin-right:5px;white-space:nowrap;">{esc(t)}</span>'
        for t in _tags(job)
    )
    pct = int(job["score"]) * 10
    return f"""
    <tr><td style="padding:14px 0;border-top:1px solid {C['line']};">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td width="52" valign="top" style="text-align:center;">
          <div style="font:700 20px {SANS};color:{C['ink']};">{job['score']}</div>
          <div style="font:400 10px {SANS};color:{C['faint']};">/ 10</div>
          <div style="height:4px;background:{C['line']};border-radius:3px;margin-top:5px;">
            <div style="height:4px;width:{pct}%;background:{C['brand']};border-radius:3px;"></div></div>
        </td>
        <td valign="top" style="padding-left:12px;">
          <div style="font:700 15px {SANS};color:{C['ink']};">{esc(job.get('title') or 'Role')}
            <span style="font-weight:400;color:{C['muted']};">— {esc(job.get('company') or '')}</span></div>
          <div style="margin:6px 0;">{tags}</div>
          <div style="font:400 14px {SANS};color:{C['muted']};">{esc(job.get('summary') or '')}</div>
          <a href="{esc(safe_url(job.get('url')))}" style="font:700 12px {SANS};color:{C['brand']};
            text-decoration:none;display:inline-block;margin-top:5px;">View &amp; apply →</a>
        </td>
      </tr></table>
    </td></tr>"""


def _see_all_html(profile: dict, shown: int, total_matches: int | None) -> str:
    """A CTA button linking to the full matches page. Shown when the matcher found more
    than the few we emailed."""
    if not total_matches:
        return ""
    if total_matches > shown:
        label = f"See all {total_matches} matches →"
        sub = f"We emailed the {shown} strongest — browse the rest on your matches page."
    else:
        label = "Open your matches page →"
        sub = "All your matches, on one page, any time."
    return f"""
    <tr><td style="padding:2px 26px 20px;text-align:center;">
      <a href="{html.escape(_matches_url(profile))}" style="display:inline-block;font:700 13px {SANS};
        color:#ffffff;background:{C['brand']};text-decoration:none;border-radius:10px;padding:11px 22px;">{label}</a>
      <div style="font:400 11px {SANS};color:{C['faint']};margin-top:8px;">{html.escape(sub)}</div>
    </td></tr>"""


def _greeting_html(jobs: list[dict]) -> str:
    """The line under the subject. On a quiet day it is honest about *why* the picks are
    thinner, rather than calling weak matches 'fresh matches ranked for you'."""
    n = len(jobs)
    if _is_weak(jobs):
        thing = "one worth a look" if n == 1 else f"{n} worth a look"
        return (f'<p style="font:400 14px {SANS};color:{C["muted"]};margin:12px 0 4px;">'
                f'Good morning. <b style="color:{C["ink"]};">No strong matches today</b> — '
                f'but here {"is" if n == 1 else "are"} {thing}, and the full list is on your '
                f'matches page.</p>')
    return (f'<p style="font:400 14px {SANS};color:{C["muted"]};margin:12px 0 4px;">'
            f'Good morning. <b style="color:{C["ink"]};">{n} fresh '
            f'{"match" if n == 1 else "matches"}</b> today, ranked for you.</p>')


def render_html(profile: dict, jobs: list[dict], base_url: str = BASE_URL,
                total_matches: int | None = None) -> str:
    esc = html.escape
    global BASE_URL
    BASE_URL = base_url
    subject = subject_line(profile, jobs)
    when = datetime.now().strftime("%H:%M")
    rows = "".join(_job_html(j) for j in jobs)
    see_all = _see_all_html(profile, len(jobs), total_matches)
    email = esc(profile.get("email") or "you@example.com")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(subject)}</title></head>
<body style="margin:0;background:{C['paper']};font-family:{SANS};">
<div style="height:3px;background:linear-gradient(90deg,#2b2e57,{C['brand']} 45%,#E0A32E);"></div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{C['paper']};padding:24px 12px;">
<tr><td align="center">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0"
    style="max-width:600px;background:{C['surface']};border:1px solid {C['line']};border-radius:16px;overflow:hidden;">
    <!-- header -->
    <tr><td style="padding:22px 26px 6px;">
      <table role="presentation" width="100%"><tr>
        <td style="font:700 20px {SERIF};color:{C['ink']};">
          <span style="color:{C['brand']};">◐</span> Job<span style="color:{C['brand']};">Digest</span></td>
        <td style="text-align:right;font:400 12px {SANS};color:{C['faint']};">{when}</td>
      </tr></table>
    </td></tr>
    <!-- subject + greeting -->
    <tr><td style="padding:8px 26px 4px;">
      <div style="font:700 21px {SERIF};color:{C['ink']};letter-spacing:-.01em;">{esc(subject)}</div>
      {_greeting_html(jobs)}
    </td></tr>
    <!-- jobs -->
    <tr><td style="padding:6px 26px 18px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>
    </td></tr>
    {see_all}
    <!-- footer -->
    <tr><td style="padding:16px 26px;background:{C['surface2']};border-top:1px solid {C['line']};text-align:center;">
      <div style="font:600 12px {SANS};margin-bottom:7px;">
        <a href="{_manage_url(profile)}" style="color:{C['brand']};text-decoration:none;">Refine preferences</a>
        &nbsp;·&nbsp;
        <a href="{_manage_url(profile,'pause')}" style="color:{C['brand']};text-decoration:none;">Pause 2 weeks</a>
        &nbsp;·&nbsp;
        <a href="{_manage_url(profile,'unsub')}" style="color:{C['brand']};text-decoration:none;">Unsubscribe</a>
      </div>
      <div style="font:400 11px {SANS};color:{C['faint']};">
        Sent to {email} · You signed up at jobdigest.eu · One email a day.</div>
    </td></tr>
  </table>
</td></tr></table></body></html>"""


def render_text(profile: dict, jobs: list[dict], base_url: str = BASE_URL,
                total_matches: int | None = None) -> str:
    if _is_weak(jobs):
        greeting = (f"Good morning. No strong matches today — "
                    f"{len(jobs)} weaker {'one' if len(jobs)==1 else 'ones'} to explore below, "
                    f"and the full list is on your matches page.")
    else:
        greeting = f"Good morning. {len(jobs)} fresh {'match' if len(jobs)==1 else 'matches'} today."
    lines = [subject_line(profile, jobs), "", greeting, ""]
    for j in jobs:
        tags = " · ".join(_tags(j))
        lines.append(f"[{j['score']}/10] {j.get('title','Role')} — {j.get('company','')}  ({tags})")
        lines.append(f"    {j.get('summary','')}")
        lines.append(f"    {safe_url(j.get('url'))}")
        lines.append("")
    if total_matches and total_matches > len(jobs):
        lines.append(f"See all {total_matches} matches: {_matches_url(profile)}")
        lines.append("")
    lines += ["—" * 20,
              f"Preferences: {_manage_url(profile)}",
              f"Unsubscribe: {_manage_url(profile,'unsub')}",
              f"Sent to {profile.get('email','you@example.com')} · One email a day."]
    return "\n".join(lines)


# ---------------------------------------------------------------- standalone ---

def _demo_profile(email: str) -> dict:
    """A ready-made profile for local preview (not persisted)."""
    return {
        "id": None, "email": email, "manage_token": "demo",
        "stack": ["dbt", "snowflake", "python", "sql"],
        "seniorities": ["junior", "mid"], "regions": ["cz", "eu", "worldwide"],
        "role_categories": ["data_engineering"],
        "work_types": ["permanent", "freelance/contract"],
        "part_time_only": False, "eligible_only": True,
        "sectors": ["fintech", "ecommerce", "trading"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="you@example.com")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--base-url", default=BASE_URL)
    args = ap.parse_args()

    profile = _demo_profile(args.email)
    jobs = build_digest(profile, limit=args.limit)

    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outbox")
    os.makedirs(outdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(outdir, f"digest-{stamp}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(profile, jobs, base_url=args.base_url))

    print(f"{len(jobs)} matches -> {path}")
    print("Subject:", subject_line(profile, jobs))
    for j in jobs:
        print(f"  {j['score']}/10  {j.get('title','')[:40]:42} {j.get('company','')[:18]:20} {j.get('summary','')[:60]}")


if __name__ == "__main__":
    main()
