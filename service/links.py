"""Canonical builders for the links that go into emails.

Two public bases, because the destinations live in different places:

  SITE_URL        the Next.js frontend (e.g. https://jobdigest.eu)
                  -> the /preferences self-service page
  API_PUBLIC_URL  the FastAPI backend as reachable from the internet
                  (e.g. https://jobdigest.eu/api) -> /confirm, /unsubscribe pages

Local dev defaults match `uvicorn ... --port 8811` + `next dev` on :3000. In prod set
SITE_URL=https://<domain> and API_PUBLIC_URL=https://<domain>/api (Caddy strips /api).
"""

from __future__ import annotations

import os
from urllib.parse import quote


def site_url() -> str:
    return os.environ.get("SITE_URL", "http://localhost:3000").rstrip("/")


def api_public_url() -> str:
    # Fall back to BASE_URL (older name) then the local API port.
    return os.environ.get(
        "API_PUBLIC_URL", os.environ.get("BASE_URL", "http://127.0.0.1:8811")
    ).rstrip("/")


def preferences_link(token: str) -> str:
    return f"{site_url()}/preferences?token={quote(token)}"


def matches_link(token: str) -> str:
    """The full 'all matches' page on the frontend (everything the matcher found today)."""
    return f"{site_url()}/matches?token={quote(token)}"


def confirm_link(token: str) -> str:
    return f"{api_public_url()}/confirm?token={quote(token)}"


def unsubscribe_link(token: str) -> str:
    return f"{api_public_url()}/unsubscribe?token={quote(token)}"
