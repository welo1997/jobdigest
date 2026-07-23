"""Transactional emails for the digest product — confirm (double opt-in) and welcome.

These are the small lifecycle emails the public API sends, kept separate from the daily
`digest.py` renderer. Same Direction-A look (warm paper, teal, Georgia display), inline
styles, with a plain-text alternative for every HTML part.

    render_confirm(email, confirm_url) -> (subject, html, text)
    render_welcome(email, manage_url)  -> (subject, html, text)
"""

from __future__ import annotations

import html

from service.digest import C, SANS, SERIF


def _shell(inner_html: str, preheader: str = "") -> str:
    """Wrap body HTML in the shared Direction-A email frame."""
    pre = (f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
           f'{html.escape(preheader)}</div>') if preheader else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:{C['paper']};font-family:{SANS};">{pre}
<div style="height:3px;background:linear-gradient(90deg,#2b2e57,{C['brand']} 45%,#E0A32E);"></div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{C['paper']};padding:28px 12px;">
<tr><td align="center">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0"
    style="max-width:600px;background:{C['surface']};border:1px solid {C['line']};border-radius:16px;overflow:hidden;">
    <tr><td style="padding:24px 30px 8px;font:700 20px {SERIF};color:{C['ink']};">
      <span style="color:{C['brand']};">◐</span> Job<span style="color:{C['brand']};">Digest</span></td></tr>
    <tr><td style="padding:6px 30px 26px;">{inner_html}</td></tr>
  </table>
  <div style="font:400 11px {SANS};color:{C['faint']};margin-top:14px;">
    JobDigest · One short email a day · Made in the EU</div>
</td></tr></table></body></html>"""


def _button(url: str, label: str) -> str:
    return (f'<a href="{html.escape(url)}" style="display:inline-block;background:{C["brand"]};'
            f'color:#fff;font:700 15px {SANS};text-decoration:none;padding:13px 26px;'
            f'border-radius:8px;">{html.escape(label)}</a>')


def render_confirm(email: str, confirm_url: str) -> tuple[str, str, str]:
    """Double opt-in: the one email a pending subscriber gets before anything else."""
    subject = "Confirm your JobDigest subscription"
    esc = html.escape
    inner = f"""
      <div style="font:700 22px {SERIF};color:{C['ink']};letter-spacing:-.01em;">Confirm your subscription</div>
      <p style="font:400 15px {SANS};color:{C['muted']};line-height:1.55;margin:14px 0 22px;">
        One tap and your daily job digest is live. We'll send a short, ranked shortlist
        every morning at 7:00 — only roles worth your time.</p>
      <div style="margin:4px 0 22px;">{_button(confirm_url, "Confirm & start my digest →")}</div>
      <p style="font:400 13px {SANS};color:{C['faint']};line-height:1.5;">
        If the button doesn't work, paste this link into your browser:<br>
        <a href="{esc(confirm_url)}" style="color:{C['brand']};word-break:break-all;">{esc(confirm_url)}</a></p>
      <p style="font:400 13px {SANS};color:{C['faint']};margin-top:20px;">
        Didn't sign up? Just ignore this email — nothing happens without your confirmation.</p>"""
    text = ("Confirm your JobDigest subscription\n\n"
            "One tap and your daily job digest is live. Confirm here:\n"
            f"{confirm_url}\n\n"
            "Didn't sign up? Ignore this email — nothing happens without your confirmation.\n")
    return subject, _shell(inner, "Confirm to start your daily job digest."), text


def render_welcome(email: str, manage_url: str) -> tuple[str, str, str]:
    """Sent right after a subscriber confirms — sets expectations + the manage link."""
    subject = "You're all set — your first JobDigest arrives tomorrow"
    esc = html.escape
    inner = f"""
      <div style="font:700 22px {SERIF};color:{C['ink']};letter-spacing:-.01em;">You're all set ✓</div>
      <p style="font:400 15px {SANS};color:{C['muted']};line-height:1.55;margin:14px 0 20px;">
        Your subscription is confirmed. Your first curated digest lands at 7:00 tomorrow morning.
        Want to fine-tune what you see?</p>
      <div style="margin:4px 0 22px;">{_button(manage_url, "Edit my preferences →")}</div>
      <p style="font:400 13px {SANS};color:{C['faint']};line-height:1.5;">
        You can change roles, skills, or frequency, pause for a while, or unsubscribe — all from
        that link, no password needed.</p>"""
    text = ("You're all set — your first JobDigest arrives tomorrow\n\n"
            "Your subscription is confirmed. First digest at 7:00 tomorrow.\n"
            f"Edit preferences / pause / unsubscribe: {manage_url}\n")
    return subject, _shell(inner, "Confirmed — first digest tomorrow at 7:00."), text


def render_manage_link(email: str, manage_url: str) -> tuple[str, str, str]:
    """Passwordless "here's your settings link" — sent when someone asks (from the site) for
    the link to an existing subscription. It only ever reaches the address's own inbox, and the
    link itself is the credential, so there is nothing to leak by sending it: an attacker who
    knows the email but not the inbox gains nothing. The copy is deliberately reassuring for the
    case where the request wasn't made by the owner — no action happens just from receiving it."""
    subject = "Your JobDigest settings link"
    esc = html.escape
    inner = f"""
      <div style="font:700 22px {SERIF};color:{C['ink']};letter-spacing:-.01em;">Manage your subscription</div>
      <p style="font:400 15px {SANS};color:{C['muted']};line-height:1.55;margin:14px 0 22px;">
        Someone (hopefully you) asked for the link to manage your JobDigest subscription. Open it
        to change roles, skills or frequency, pause for a while, or unsubscribe — no password needed.</p>
      <div style="margin:4px 0 22px;">{_button(manage_url, "Manage my preferences →")}</div>
      <p style="font:400 13px {SANS};color:{C['faint']};line-height:1.5;">
        If the button doesn't work, paste this link into your browser:<br>
        <a href="{esc(manage_url)}" style="color:{C['brand']};word-break:break-all;">{esc(manage_url)}</a></p>
      <p style="font:400 13px {SANS};color:{C['faint']};margin-top:20px;">
        Didn't ask for this? You can safely ignore this email — it only links to your own settings,
        and nothing changes unless you open it and make a change yourself.</p>"""
    text = ("Your JobDigest settings link\n\n"
            "Someone asked for the link to manage your JobDigest subscription. Open it to edit\n"
            "preferences, pause, or unsubscribe — no password needed:\n"
            f"{manage_url}\n\n"
            "Didn't ask for this? Ignore this email — nothing changes unless you open it.\n")
    return subject, _shell(inner, "Your private link to manage your JobDigest subscription."), text
