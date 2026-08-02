"""Canonical builders for the links that go into emails.

Two public bases, because the destinations live in different places:

  SITE_URL        the Next.js frontend (e.g. https://jobdigest.eu)
                  -> the /preferences self-service page
  API_PUBLIC_URL  the FastAPI backend as reachable from the internet
                  (e.g. https://jobdigest.eu/api) -> /confirm, /unsubscribe pages

Local dev defaults match `uvicorn ... --port 8811` + `next dev` on :3000. In prod set
SITE_URL=https://<domain> and API_PUBLIC_URL=https://<domain>/api (Caddy strips /api).

**Frontend pages are language-prefixed.** The static export is emitted once per locale, so
`/preferences/` is not a page — `/en/preferences/` is. Build every frontend link through
`site_page()` rather than concatenating onto `site_url()`, or the link 404s. The bare
un-prefixed paths still resolve, but only through the compatibility redirect in
`web/Caddyfile`, which exists for the links already sitting in subscribers' inboxes and must
not be relied on by anything generated from now on.

Emails are English, so links point at the English pages. There is no per-profile language
column; `SITE_LOCALE` is the one knob, and it has to name a locale that `web/i18n/config.ts`
actually exports.
"""

from __future__ import annotations

import os
from urllib.parse import quote

#: Which language the emailed links point at. Must be one of `LOCALES` in web/i18n/config.ts.
SITE_LOCALE = os.environ.get("SITE_LOCALE", "en")


def site_url() -> str:
    return os.environ.get("SITE_URL", "http://localhost:3000").rstrip("/")


def site_page(path: str = "", lang: str | None = None) -> str:
    """A frontend page URL, in `lang` (default `SITE_LOCALE`), with a trailing slash.

    `next.config.mjs` sets `trailingSlash: true`, so `/en/preferences/` is the exported path;
    emitting it directly keeps mail clients from following a redirect they don't have to.

    `lang` comes from `profiles.language`, so a Czech subscriber's digest links land on the
    Czech site. It is cleaned here rather than trusted: a link is generated inside a send loop,
    and an unexpected column value must not be able to produce a URL for a locale that was
    never exported.
    """
    from service import i18n     # local import: i18n imports nothing, but links is imported early
    locale = i18n.clean_locale(lang) if lang else SITE_LOCALE
    clean = path.strip("/")
    return f"{site_url()}/{locale}/" + (f"{clean}/" if clean else "")


def api_public_url() -> str:
    # Fall back to BASE_URL (older name) then the local API port.
    return os.environ.get(
        "API_PUBLIC_URL", os.environ.get("BASE_URL", "http://127.0.0.1:8811")
    ).rstrip("/")


def preferences_link(token: str, lang: str | None = None) -> str:
    return f"{site_page('preferences', lang)}?token={quote(token)}"


def matches_link(token: str, lang: str | None = None) -> str:
    """The full 'all matches' page on the frontend (everything the matcher found today)."""
    return f"{site_page('matches', lang)}?token={quote(token)}"


def confirm_link(token: str) -> str:
    return f"{api_public_url()}/confirm?token={quote(token)}"


def unsubscribe_link(token: str) -> str:
    return f"{api_public_url()}/unsubscribe?token={quote(token)}"
