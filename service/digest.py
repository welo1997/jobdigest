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
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import geo, i18n, links, store, taxonomy  # noqa: E402

DEFAULT_LIMIT = 5            # curated highlights in the email; the rest live on /matches
EMAIL_MIN_SCORE = 6         # a "strong" fit — a digest of these is the normal, headline case
FALLBACK_LIMIT = int(os.environ.get("DIGEST_FALLBACK_LIMIT", "3"))  # weaker picks shown on a quiet day
# How far back a previously emailed (company, title) suppresses a repeat. Bounded rather
# than forever: a role genuinely re-opened months later is a new opportunity, not a repeat.
REPEAT_WINDOW_DAYS = int(os.environ.get("DIGEST_REPEAT_WINDOW_DAYS", "90"))
BASE_URL = os.environ.get("BASE_URL", "https://jobdigest.eu")

# Direction-A palette (kept in sync with design/prototype.html)
C = {
    "paper": "#FBFAF6", "surface": "#FFFFFF", "surface2": "#F4F2EC",
    "ink": "#182233", "muted": "#5B6472", "faint": "#8A93A1",
    "brand": "#0E7C6B", "brandDeep": "#0A5C50", "gold": "#C98A18", "line": "#E7E4DC",
}
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


# ------------------------------------------------------------- dedupe ---------
#
# `digest_sends` guarantees a *posting_id* is never emailed twice. It cannot see the case
# where one job exists under two ids, which happens two ways and was measured on
# 2026-07-28 at 3 occurrences in 77 sends:
#
#   cross-source — the same opening carried by jobs.cz and by cocuma, ingested separately;
#   relisting    — jobs.cz re-posts an expiring ad under a fresh id, so the "new" posting
#                  is a different row with identical company and title.
#
# Both are one job to the subscriber, so both are deduplicated on a normalised
# (company, title) key. Nothing is lost by this: /matches is deliberately NOT deduplicated
# and remains the complete record of every pick. Only the email is collapsed.
#
# City is part of the identity, because (company, title) alone is not one job: 1 438 active
# postings sit in 641 same-company-same-title groups, and they are overwhelmingly one role
# advertised across many towns — ČSOB's client-care role in 10 of them, Biedronka's store
# manager in 7. Collapsing those would delete real openings from the email and, worse,
# suppress them for the whole repeat window.
#
# **An unknown city is a wildcard, not a value.** Two *known* cities that differ mean two
# jobs; if either side is unknown the pair falls back to (company, title) and is treated as
# the same job. This is the same reading `geo` gives a null everywhere else, and it is not
# academic: of the two real repeats in production, SOFTEC was prague/prague but Publicis
# Groupe was null/prague — a strict city component would have missed half of what this fixes.

# Legal forms are matched at the END of the name only. Unanchored, a legal-form word that
# happens to sit mid-name would be deleted from it — "Nord SE Consulting" would become
# "Nord Consulting" and collide with the unrelated company actually called that.
_LEGAL_SUFFIX = re.compile(
    r"[\s,]+(?:"
    r"spol\.?\s*s\s*r\.?\s*o|s\.?\s*r\.?\s*o|a\.?\s*s|k\.?\s*s|o\.?\s*p\.?\s*s|z\.?\s*s|"
    r"gmbh(?:\s*&\s*co\.?\s*kg)?|mbh|ag|se|ug|kg|ohg|b\.?\s*v|n\.?\s*v|"
    r"ltd|limited|llc|inc|incorporated|corp|corporation|plc|"
    r"s\.?\s*a|s\.?\s*p\.?\s*a|oy|ab|a/s|aps|sp\.?\s*z\.?\s*o\.?\s*o"
    r")\.?\s*$",
    re.IGNORECASE,
)
# "(m/f/d)", "[Remote]", "- 100% remote" style trailers carry no identity.
_BRACKETED = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_NON_WORD = re.compile(r"[^a-z0-9]+")


def _fold(s: str) -> str:
    """Lowercase and strip diacritics — 'Datový analytik' and 'Datovy analytik' are one job.

    Boards disagree about Czech diacritics for the same posting, so folding them is what
    makes the cross-source case actually match."""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def dedupe_key(company: str | None, title: str | None) -> str:
    """A stable identity for 'the same job', or "" when there isn't enough to judge.

    Returning "" for a missing company or title is load-bearing: an empty key must never
    collapse two rows together, so callers treat it as always-unique. Silently merging
    everything with a blank company would drop real jobs from the email."""
    company = _NON_WORD.sub(" ", _fold(_LEGAL_SUFFIX.sub("", (company or "").strip()))).strip()
    title = _NON_WORD.sub(" ", _BRACKETED.sub(" ", _fold(title or ""))).strip()
    if not company or not title:
        return ""
    return f"{company}|{title}"


def _is_repeat(seen: dict[str, set[str | None]], key: str, city: str | None) -> bool:
    """Has this (key, city) already been seen, treating an unknown city as matching any?

    `seen` maps a (company, title) key to the cities it has appeared in, where None means
    "the ad did not say". A None on either side collapses the pair, so the wildcard works
    in both directions regardless of which arrived first."""
    cities = seen.get(key)
    if cities is None:
        return False
    return city is None or None in cities or city in cities


def _drop_repeats(jobs: list[dict], seen: dict[str, set[str | None]]) -> list[dict]:
    """Keep the first job for each identity; `seen` is pre-seeded with what was emailed.

    `jobs` arrives ordered by score desc (matched_jobs), so "first" is "highest scoring" —
    that is what makes this keep the better of two copies without sorting again."""
    out = []
    for j in jobs:
        key = dedupe_key(j.get("company"), j.get("title"))
        if not key:                       # not enough to judge — always unique, never merged
            out.append(j)
            continue
        city = j.get("city") or None
        if _is_repeat(seen, key, city):
            continue
        seen.setdefault(key, set()).add(city)
        out.append(j)
    return out


# ---------------------------------------------------------------- build --------

def build_digest(profile: dict, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Return the top-`limit` AI-picked matches for a profile, best fit first.

    Reads the picks the matcher (`service.matcher`) wrote to `matches` — no heuristic
    scoring. Anything already emailed to this profile (`digest_sends`) is filtered out so
    a job is never sent twice. If the matcher hasn't run for this profile yet, returns []
    and the digest is skipped (nothing to send).

    Two jobs that are the same opening under different posting_ids are collapsed to one —
    see the dedupe section above. This happens BEFORE the limit is applied, so a duplicate
    costs the subscriber nothing: the slot goes to the next real job rather than to a
    second copy."""
    if not profile.get("id"):
        return []
    # `exclude_sent` does the already-sent filter in SQL so the `limit * 6` window is 30
    # *candidates*, not 30 rows of which most are already in the subscriber's inbox. Without
    # it the window silently tightened as someone's history grew — 21 of 30 slots spent on
    # sent jobs after 15 days, and a 1-job email where 5 were available (see store.py).
    # `holdable_from` re-checks the reach rule at send time. The retrieval gate
    # (`geo.reach_predicate`, 2026-08-17) stops new picks that are remote-but-bound-to-another-
    # country, but `matches` is history: on the day it shipped one subscriber still held 49
    # unsent, score-6-to-8 picks the rule refuses, so the next email would have looked like the
    # one that prompted the fix. It also catches a posting `backfill_remote_reach` reclassified
    # after it was matched. In SQL for the same reason `exclude_sent` is — see store.py.
    picks = store.matched_jobs(profile["id"], limit=limit * 6, exclude_sent=True,
                               holdable_from=geo.clean_countries(profile.get("countries")))
    # Kept deliberately: this is the guarantee ("a job is never emailed twice"), and it must
    # not depend on an optimisation flag staying switched on. It is now a no-op, and a
    # cheap one — if it ever drops a row again, the SQL above regressed.
    already = store.already_sent_ids(profile["id"])
    unsent = [j for j in picks if j["posting_id"] not in already]
    # Seeded with what was emailed recently, so a relisted ad is caught across days too —
    # by the time the new id appears, the old one is filtered by `already` and would
    # otherwise sail through as a fresh job.
    seen: dict[str, set[str | None]] = {}
    for company, title, city in store.sent_job_keys(profile["id"], days=REPEAT_WINDOW_DAYS):
        key = dedupe_key(company, title)
        if key:
            seen.setdefault(key, set()).add(city or None)
    unsent = _drop_repeats(unsent, seen)
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

def _lang(profile: dict) -> str:
    """The subscriber's language, cleaned. One place, so every renderer agrees."""
    return i18n.clean_locale(profile.get("language"))


def _manage_url(profile: dict, action: str = "") -> str:
    """Footer links: preferences + pause both land on the self-service page (frontend);
    unsubscribe is the API's one-click page.

    The frontend links carry the subscriber's language — landing a Czech reader on the English
    preferences page would undo the point of translating the email. The unsubscribe page is
    served by the API, which renders in English for now."""
    tok = profile.get("manage_token", "")
    if action == "unsub":
        return links.unsubscribe_link(tok)
    return links.preferences_link(tok, _lang(profile))


def _matches_url(profile: dict) -> str:
    """The 'see all matches' page (everything the matcher found, not just the emailed few)."""
    return links.matches_link(profile.get("manage_token", ""), _lang(profile))


def safe_url(url: str | None) -> str:
    """Return `url` only if it is a plain http(s) link, else '#'.

    Posting URLs come from third-party feeds we do not control and land in an email `href`.
    `html.escape` stops attribute breakout but says nothing about the *scheme*, so a feed
    serving `javascript:` or `data:text/html,...` would ship an active link out under our
    DKIM signature. Most mail clients block those, which is a reason not to rely on them.
    """
    u = (url or "").strip()
    return u if u[:7].lower() == "http://" or u[:8].lower() == "https://" else "#"


def _tags(job: dict, lang: str = i18n.DEFAULT_LOCALE) -> list[str]:
    tags = []
    if job.get("region"):
        tags.append(str(job["region"]).upper())
    # "Hybrid" is checked first and is never also "Remote": before migration 012 these
    # postings carried no work tag at all, so a Prague office job with two days from home
    # looked identical in the email to one with five days in the office.
    if job.get("work_mode") == "hybrid":
        tags.append(i18n.t(lang, "tag_hybrid"))
    elif job.get("remote_signal") or (job.get("region") in ("eu", "worldwide")):
        tags.append(i18n.t(lang, "tag_remote"))
    if job.get("seniority"):
        # The fallback un-snake-cases first: `entry_level` title-cased is "Entry_level", which
        # is what shipping a two-word level through a renderer written for one-word ones looks
        # like. It is only reachable for a value no catalogue knows, which after the 2026-08-12
        # backfill means a level added in code and not in `service/i18n.py`.
        _key = f'seniority_{str(job["seniority"]).lower()}'
        tags.append(i18n.t(lang, _key)
                    if _key in i18n.MESSAGES[i18n.DEFAULT_LOCALE]
                    else str(job["seniority"]).replace("_", " ").capitalize())
    if job.get("work_type") == "freelance/contract":
        tags.append(i18n.t(lang, "tag_freelance"))
    if job.get("is_part_time"):
        tags.append(i18n.t(lang, "tag_part_time"))
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
    lang = _lang(profile)
    when = i18n.format_date(lang, datetime.now())

    # Quiet day: don't claim "N new roles for you" when nothing cleared the bar — say so.
    if _is_weak(jobs):
        return i18n.t(lang, "subject_weak",
                      count=i18n.plural(lang, "explore_count", n), when=when)

    cats = [j.get("role_category") for j in jobs if j.get("role_category")]
    role = ""
    if cats:
        top, hits = Counter(cats).most_common(1)[0]
        # Only claim a category when it genuinely characterises the digest. A 2-of-5 plurality
        # would make the subject a lie for most of the email.
        if hits / len(cats) >= 0.6:
            role = i18n.subject_word(lang, top)

    # Two plural sets rather than a {role} hole in one: Czech, Polish and German each want the
    # category word somewhere English does not put it, and several inflect the noun after it.
    count = (i18n.plural(lang, "subject_new_named", n, role=role) if role
             else i18n.plural(lang, "subject_new", n))
    return i18n.t(lang, "subject_for_you", count=count, when=when)


# ---------------------------------------------------------------- HTML ---------

def _job_html(job: dict, lang: str = i18n.DEFAULT_LOCALE) -> str:
    esc = html.escape
    tags = "".join(
        f'<span style="font:600 10px {SANS};letter-spacing:.04em;text-transform:uppercase;'
        f'color:{C["muted"]};background:{C["surface2"]};border:1px solid {C["line"]};'
        f'border-radius:999px;padding:2px 8px;margin-right:5px;white-space:nowrap;">{esc(t)}</span>'
        for t in _tags(job, lang)
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
          <div style="font:700 15px {SANS};color:{C['ink']};">{esc(job.get("title") or i18n.t(lang, "role_fallback"))}
            <span style="font-weight:400;color:{C['muted']};">— {esc(job.get('company') or '')}</span></div>
          <div style="margin:6px 0;">{tags}</div>
          <div style="font:400 14px {SANS};color:{C['muted']};">{esc(job.get('summary') or '')}</div>
          <a href="{esc(safe_url(job.get('url')))}" style="font:700 12px {SANS};color:{C['brand']};
            text-decoration:none;display:inline-block;margin-top:5px;">{esc(i18n.t(lang, "view_apply"))}</a>
        </td>
      </tr></table>
    </td></tr>"""


def _see_all_html(profile: dict, shown: int, total_matches: int | None,
                  lang: str = i18n.DEFAULT_LOCALE) -> str:
    """A CTA button linking to the full matches page. Shown when the matcher found more
    than the few we emailed."""
    if not total_matches:
        return ""
    if total_matches > shown:
        label = i18n.plural(lang, "see_all_label", total_matches)
        sub = i18n.t(lang, "see_all_sub", shown=shown)
    else:
        label = i18n.t(lang, "open_matches_label")
        sub = i18n.t(lang, "open_matches_sub")
    return f"""
    <tr><td style="padding:2px 26px 20px;text-align:center;">
      <a href="{html.escape(_matches_url(profile))}" style="display:inline-block;font:700 13px {SANS};
        color:#ffffff;background:{C['brand']};text-decoration:none;border-radius:10px;padding:11px 22px;">{label}</a>
      <div style="font:400 11px {SANS};color:{C['faint']};margin-top:8px;">{html.escape(sub)}</div>
    </td></tr>"""


def _greeting_html(jobs: list[dict], lang: str = i18n.DEFAULT_LOCALE) -> str:
    """The line under the subject. On a quiet day it is honest about *why* the picks are
    thinner, rather than calling weak matches 'fresh matches ranked for you'.

    The bolded fragment is a separate catalogue entry from the rest of the sentence, so a
    translation can put the emphasis where its own grammar wants it instead of inheriting
    English clause order."""
    n = len(jobs)
    style = f'<p style="font:400 14px {SANS};color:{C["muted"]};margin:12px 0 4px;">'
    bold = f'<b style="color:{C["ink"]};">'
    if _is_weak(jobs):
        return (f'{style}{i18n.t(lang, "good_morning")} '
                f'{bold}{i18n.t(lang, "weak_lead")}</b> '
                f'{i18n.plural(lang, "weak_rest", n)}</p>')
    return (f'{style}{i18n.t(lang, "good_morning")} '
            f'{bold}{i18n.plural(lang, "fresh_count", n)}</b> '
            f'{i18n.t(lang, "fresh_rest")}</p>')


def render_html(profile: dict, jobs: list[dict], base_url: str = BASE_URL,
                total_matches: int | None = None) -> str:
    esc = html.escape
    global BASE_URL
    BASE_URL = base_url
    lang = _lang(profile)
    subject = subject_line(profile, jobs)
    when = datetime.now().strftime("%H:%M")
    rows = "".join(_job_html(j, lang) for j in jobs)
    see_all = _see_all_html(profile, len(jobs), total_matches, lang)
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
      {_greeting_html(jobs, lang)}
    </td></tr>
    <!-- jobs -->
    <tr><td style="padding:6px 26px 18px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>
    </td></tr>
    {see_all}
    <!-- footer -->
    <tr><td style="padding:16px 26px;background:{C['surface2']};border-top:1px solid {C['line']};text-align:center;">
      <div style="font:600 12px {SANS};margin-bottom:7px;">
        <a href="{_manage_url(profile)}" style="color:{C['brand']};text-decoration:none;">{esc(i18n.t(lang, "refine"))}</a>
        &nbsp;·&nbsp;
        <a href="{_manage_url(profile,'pause')}" style="color:{C['brand']};text-decoration:none;">{esc(i18n.t(lang, "pause"))}</a>
        &nbsp;·&nbsp;
        <a href="{_manage_url(profile,'unsub')}" style="color:{C['brand']};text-decoration:none;">{esc(i18n.t(lang, "unsubscribe"))}</a>
      </div>
      <div style="font:400 11px {SANS};color:{C['faint']};">
        {esc(i18n.t(lang, "footer_sent", email=profile.get("email") or ""))}</div>
    </td></tr>
  </table>
</td></tr></table></body></html>"""


def render_text(profile: dict, jobs: list[dict], base_url: str = BASE_URL,
                total_matches: int | None = None) -> str:
    lang = _lang(profile)
    n = len(jobs)
    greeting = i18n.plural(lang, "weak_text" if _is_weak(jobs) else "fresh_text", n)
    lines = [subject_line(profile, jobs), "", greeting, ""]
    for j in jobs:
        tags = " · ".join(_tags(j, lang))
        title = j.get("title") or i18n.t(lang, "role_fallback")
        lines.append(f"[{j['score']}/10] {title} — {j.get('company','')}  ({tags})")
        lines.append(f"    {j.get('summary','')}")
        lines.append(f"    {safe_url(j.get('url'))}")
        lines.append("")
    if total_matches and total_matches > len(jobs):
        lines.append(f'{i18n.plural(lang, "see_all_text", total_matches)} {_matches_url(profile)}')
        lines.append("")
    lines += ["—" * 20,
              f'{i18n.t(lang, "text_preferences")} {_manage_url(profile)}',
              f'{i18n.t(lang, "text_unsubscribe")} {_manage_url(profile,"unsub")}',
              i18n.t(lang, "footer_sent_text", email=profile.get("email", ""))]
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
