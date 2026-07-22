#!/usr/bin/env python3
"""Push a JobDigest ops alert when a job fails — to Telegram and/or email.

Invoked by `jobdigest-alert@.service`, wired to the JobDigest job units with
`OnFailure=jobdigest-alert@%n.service`. The moment match-export, match-import or the nightly
backup exits non-zero, you get an alert with the failed unit and its last log lines.

Deliberately standalone (stdlib only) and NOT importing `service.mailer`: it runs on the
HOST, not inside the app container, and must still work when the app itself is broken — which
is exactly when it matters.

Channels (configure either or both in deploy/.env, or the environment):
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID   -> instant phone push via a Telegram bot
  RESEND_API_KEY + JOBDIGEST_ALERT_EMAIL  -> email (MAIL_FROM optional, reuses the app's)
At least one channel must be configured. A failure to send on one channel does not stop the
other.

Setup helper:  `jobdigest-alert.py --telegram-chatid`  prints the chat id(s) that have
messaged your bot, so you can drop TELEGRAM_CHAT_ID into .env.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

ENV_FILE = os.environ.get("JOBDIGEST_ENV_FILE", "/opt/jobdigest/deploy/.env")
TG_API = "https://api.telegram.org"


def from_env(key: str, default: str | None = None) -> str | None:
    """Prefer a real environment variable; fall back to the deploy .env file."""
    if os.environ.get(key):
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


def send_telegram(token: str, chat_id: str, text: str) -> str:
    # Telegram caps a message at 4096 chars; keep well under with room for headers.
    payload = json.dumps({
        "chat_id": chat_id, "text": text[:3800], "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(
        f"{TG_API}/bot{token}/sendMessage", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode())
    if not body.get("ok"):
        raise RuntimeError(f"telegram: {body}")
    return f"telegram:{body['result']['message_id']}"


def send_email(subject: str, html: str, text: str, *, api_key: str, mail_from: str,
               to: str) -> str:
    payload = json.dumps({
        "from": mail_from, "to": [to], "subject": subject, "html": html, "text": text,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Resend is behind Cloudflare, which 403s urllib's default UA (see service/mailer).
            "User-Agent": "JobDigest-watchdog/1.0 (+https://jobdigest.eu)",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode())
    return f"resend:{body.get('id', 'ok')}"


def print_telegram_chatids() -> int:
    """Setup helper: list the chats that have messaged the bot (message it once first)."""
    token = from_env("TELEGRAM_BOT_TOKEN")
    if not token:
        print(f"no TELEGRAM_BOT_TOKEN in env or {ENV_FILE}", file=sys.stderr)
        return 1
    with urllib.request.urlopen(f"{TG_API}/bot{token}/getUpdates", timeout=20) as resp:
        data = json.loads(resp.read().decode())
    results = data.get("result", [])
    if not results:
        print("No updates yet — open Telegram, send your bot any message, then re-run.")
        return 1
    seen = set()
    for upd in results:
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat", {})
        cid = chat.get("id")
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        who = chat.get("username") or chat.get("title") or chat.get("first_name") or "?"
        print(f"  TELEGRAM_CHAT_ID={cid}   ({chat.get('type')}, {who})")
    print("\nAdd the right line above to /opt/jobdigest/deploy/.env")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--telegram-chatid":
        return print_telegram_chatids()

    unit = sys.argv[1] if len(sys.argv) > 1 else "a JobDigest job"
    host = os.uname().nodename

    log = subprocess.run(
        ["journalctl", "-u", unit, "-n", "20", "--no-pager"],
        capture_output=True, text=True,
    ).stdout.strip() or "(no journal output)"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    subject = f"⚠ JobDigest: {unit} failed on {host}"
    # No `ssh <host>` line: `host` is the box's internal name and won't resolve from a laptop.
    # The systemctl/journalctl commands are what you run once you're on the VPS.
    text = (
        f"{unit} failed at {stamp} on {host}.\n\n"
        f"Last log lines:\n{log}\n\n"
        f"On the VPS:\n"
        f"  systemctl status {unit}\n"
        f"  journalctl -u {unit} -n 50 --no-pager"
    )
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = f"<pre style='font:13px/1.5 ui-monospace,monospace;white-space:pre-wrap'>{esc}</pre>"

    tg_token = from_env("TELEGRAM_BOT_TOKEN")
    tg_chat = from_env("TELEGRAM_CHAT_ID")
    api_key = from_env("RESEND_API_KEY")
    mail_from = from_env("MAIL_FROM", "JobDigest <hello@jobdigest.eu>")
    to = from_env("JOBDIGEST_ALERT_EMAIL")

    results, errors = [], []
    if tg_token and tg_chat:
        try:
            results.append(send_telegram(tg_token, tg_chat, text))
        except Exception as exc:  # noqa: BLE001 — try the other channel, don't crash
            errors.append(f"telegram: {exc}")
    if api_key and to:
        try:
            results.append(send_email(subject, html, text,
                                      api_key=api_key, mail_from=mail_from, to=to))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"email: {exc}")

    if not tg_token and not api_key:
        print("jobdigest-alert: no channel configured (set TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID "
              f"or RESEND_API_KEY+JOBDIGEST_ALERT_EMAIL in {ENV_FILE})", file=sys.stderr)
        return 1

    for e in errors:
        print(f"jobdigest-alert: {e}", file=sys.stderr)
    if results:
        print("jobdigest-alert sent:", ", ".join(results))
        return 0
    return 1  # everything configured failed to send


if __name__ == "__main__":
    sys.exit(main())
