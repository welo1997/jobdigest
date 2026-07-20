"""Provider-agnostic email sending.

The daily pipeline calls `send(...)`; the backend is chosen at deploy time via env,
so the product code never hard-codes Resend/SES/SMTP. In Phase A the default `file`
backend writes the email to service/outbox/ so the whole flow is testable with no
account and no network.

    MAIL_BACKEND = file (default) | console | smtp | resend
    MAIL_FROM    = "JobDigest <hello@jobdigest.eu>"

Backends:
  file     -> writes .html + .txt to outbox/, returns the path
  console  -> prints a summary to stdout
  smtp     -> SMTP_HOST/PORT/USER/PASS (STARTTLS); adds List-Unsubscribe headers
  resend   -> RESEND_API_KEY via the Resend HTTP API

One-click unsubscribe (List-Unsubscribe + List-Unsubscribe-Post) is attached whenever
a `list_unsubscribe` URL is supplied — required by Gmail/Yahoo bulk-sender rules.
"""

from __future__ import annotations

import os
import re
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from typing import Optional

MAIL_FROM = os.environ.get("MAIL_FROM", "JobDigest <hello@jobdigest.eu>")
OUTBOX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outbox")


def _safe(name: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "_", name.lower())[:60]


def _backend() -> str:
    return os.environ.get("MAIL_BACKEND", "file").lower()


def send(to: str, subject: str, html: str, text: str,
         list_unsubscribe: Optional[str] = None) -> str:
    """Send one email via the configured backend. Returns a backend-specific ref."""
    backend = _backend()
    if backend == "file":
        return _send_file(to, subject, html, text)
    if backend == "console":
        print(f"[mail:console] to={to} subject={subject!r} ({len(html)} bytes html)")
        return "console"
    if backend == "smtp":
        return _send_smtp(to, subject, html, text, list_unsubscribe)
    if backend == "resend":
        return _send_resend(to, subject, html, text, list_unsubscribe)
    raise ValueError(f"unknown MAIL_BACKEND: {backend}")


def _send_file(to: str, subject: str, html: str, text: str) -> str:
    os.makedirs(OUTBOX, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.join(OUTBOX, f"{stamp}-{_safe(to)}")
    with open(base + ".html", "w", encoding="utf-8") as f:
        f.write(html)
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(f"To: {to}\nSubject: {subject}\n\n{text}")
    return base + ".html"


def _mime(to: str, subject: str, html: str, text: str,
          list_unsubscribe: Optional[str]) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM
    msg["To"] = to
    if list_unsubscribe:
        msg["List-Unsubscribe"] = f"<{list_unsubscribe}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def _send_smtp(to: str, subject: str, html: str, text: str,
               list_unsubscribe: Optional[str]) -> str:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASS")
    msg = _mime(to, subject, html, text, list_unsubscribe)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ctx)
        if user:
            s.login(user, pw)
        s.sendmail(parseaddr(MAIL_FROM)[1], [to], msg.as_string())
    return f"smtp:{host}"


def _send_resend(to: str, subject: str, html: str, text: str,
                 list_unsubscribe: Optional[str]) -> str:
    import json
    import urllib.request

    key = os.environ["RESEND_API_KEY"]
    payload = {"from": MAIL_FROM, "to": [to], "subject": subject,
               "html": html, "text": text}
    if list_unsubscribe:
        payload["headers"] = {
            "List-Unsubscribe": f"<{list_unsubscribe}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # Resend's API is fronted by Cloudflare, which returns 403 "error code: 1010"
            # (banned browser signature) for urllib's default Python-urllib UA. A normal
            # User-Agent avoids the bot block.
            "User-Agent": "JobDigest/1.0 (+https://jobdigest.eu)",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode())
    return f"resend:{body.get('id','ok')}"
