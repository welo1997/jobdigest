#!/usr/bin/env python3
"""Email a JobDigest ops alert via Resend when a job fails.

Invoked by `jobdigest-alert@.service`, which is wired to the JobDigest job units with
`OnFailure=jobdigest-alert@%n.service`. So the moment match-export, match-import or the
nightly backup exits non-zero, you get an email with the failed unit and its last log lines.

Deliberately standalone (stdlib only) and NOT importing `service.mailer`: it runs on the
HOST, not inside the app container, and must still work when the app itself is broken — which
is exactly when it matters. It reuses the app's own Resend key + sender from deploy/.env, so
there is no second credential to manage.

Config (from deploy/.env, or the environment):
  RESEND_API_KEY          required — the app already has it
  MAIL_FROM               optional — defaults to "JobDigest <hello@jobdigest.eu>"
  JOBDIGEST_ALERT_EMAIL   required — where alerts go (kept out of git; set in .env on the box)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

ENV_FILE = os.environ.get("JOBDIGEST_ENV_FILE", "/opt/jobdigest/deploy/.env")


def from_env(key: str, default: str | None = None) -> str | None:
    """Prefer a real environment variable; fall back to the deploy .env file."""
    if key in os.environ and os.environ[key]:
        return os.environ[key]
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line[len(key) + 1:].strip().strip('"').strip("'")
    except OSError:
        pass
    return default


def main() -> int:
    unit = sys.argv[1] if len(sys.argv) > 1 else "a JobDigest job"
    host = os.uname().nodename

    api_key = from_env("RESEND_API_KEY")
    mail_from = from_env("MAIL_FROM", "JobDigest <hello@jobdigest.eu>")
    to = from_env("JOBDIGEST_ALERT_EMAIL")
    if not api_key:
        print(f"jobdigest-alert: no RESEND_API_KEY in {ENV_FILE}; cannot send", file=sys.stderr)
        return 1
    if not to:
        print(f"jobdigest-alert: no JOBDIGEST_ALERT_EMAIL set (env or {ENV_FILE})",
              file=sys.stderr)
        return 1

    log = subprocess.run(
        ["journalctl", "-u", unit, "-n", "20", "--no-pager"],
        capture_output=True, text=True,
    ).stdout.strip() or "(no journal output)"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    subject = f"⚠ JobDigest: {unit} failed on {host}"
    text = (
        f"{unit} failed at {stamp} on {host}.\n\n"
        f"Last log lines:\n{log}\n\n"
        f"Investigate:\n"
        f"  ssh deploy@{host}\n"
        f"  systemctl status {unit}\n"
        f"  journalctl -u {unit} -n 50 --no-pager"
    )
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = f"<pre style='font:13px/1.5 ui-monospace,monospace;white-space:pre-wrap'>{esc}</pre>"

    payload = json.dumps({
        "from": mail_from, "to": [to], "subject": subject, "html": html, "text": text,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Resend is behind Cloudflare, which 403s urllib's default UA (see service/mailer).
            "User-Agent": "JobDigest-watchdog/1.0 (+https://jobdigest.eu)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
        print("jobdigest-alert sent:", body.get("id", "ok"))
        return 0
    except Exception as exc:  # noqa: BLE001 — last-ditch alerter; never raise, just report
        print(f"jobdigest-alert: send failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
