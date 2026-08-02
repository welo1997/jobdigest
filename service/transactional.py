"""Transactional emails for the digest product — confirm (double opt-in), welcome, manage link.

These are the small lifecycle emails the public API sends, kept separate from the daily
`digest.py` renderer. Same Direction-A look (warm paper, teal, Georgia display), inline
styles, with a plain-text alternative for every HTML part.

    render_confirm(email, confirm_url, lang) -> (subject, html, text)
    render_welcome(email, manage_url, lang)  -> (subject, html, text)
    render_manage_link(email, manage_url, lang) -> (subject, html, text)

`lang` is the subscriber's language (`profiles.language`, see migration 013). It defaults to
English so every existing caller and test keeps working, but the API passes the real value —
a confirmation email in a language the person did not sign up in is the first thing they see
of the product.
"""

from __future__ import annotations

import html

from service import i18n
from service.digest import C, SANS, SERIF


def _shell(inner_html: str, preheader: str = "", lang: str = i18n.DEFAULT_LOCALE) -> str:
    """Wrap body HTML in the shared Direction-A email frame."""
    pre = (f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
           f'{html.escape(preheader)}</div>') if preheader else ""
    return f"""<!doctype html><html lang="{html.escape(i18n.clean_locale(lang))}"><head><meta charset="utf-8">
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
    {html.escape(i18n.t(lang, "shell_footer"))}</div>
</td></tr></table></body></html>"""


def _button(url: str, label: str) -> str:
    return (f'<a href="{html.escape(url)}" style="display:inline-block;background:{C["brand"]};'
            f'color:#fff;font:700 15px {SANS};text-decoration:none;padding:13px 26px;'
            f'border-radius:8px;">{html.escape(label)}</a>')


def _paste_link(url: str, lang: str) -> str:
    esc = html.escape
    return (f'<p style="font:400 13px {SANS};color:{C["faint"]};line-height:1.5;">'
            f'{esc(i18n.t(lang, "link_fallback"))}<br>'
            f'<a href="{esc(url)}" style="color:{C["brand"]};word-break:break-all;">{esc(url)}</a></p>')


def _title(text: str) -> str:
    return (f'<div style="font:700 22px {SERIF};color:{C["ink"]};letter-spacing:-.01em;">'
            f'{html.escape(text)}</div>')


def _body(text: str, margin: str = "14px 0 22px") -> str:
    return (f'<p style="font:400 15px {SANS};color:{C["muted"]};line-height:1.55;margin:{margin};">'
            f'{html.escape(text)}</p>')


def _note(text: str, margin_top: str = "20px") -> str:
    return (f'<p style="font:400 13px {SANS};color:{C["faint"]};line-height:1.5;'
            f'margin-top:{margin_top};">{html.escape(text)}</p>')


def render_confirm(
    email: str, confirm_url: str, lang: str = i18n.DEFAULT_LOCALE
) -> tuple[str, str, str]:
    """Double opt-in: the one email a pending subscriber gets before anything else."""
    inner = (
        _title(i18n.t(lang, "confirm_title"))
        + _body(i18n.t(lang, "confirm_body"))
        + f'<div style="margin:4px 0 22px;">{_button(confirm_url, i18n.t(lang, "confirm_button"))}</div>'
        + _paste_link(confirm_url, lang)
        + _note(i18n.t(lang, "confirm_ignore"))
    )
    return (
        i18n.t(lang, "confirm_subject"),
        _shell(inner, i18n.t(lang, "confirm_preheader"), lang),
        i18n.t(lang, "confirm_text", url=confirm_url),
    )


def render_welcome(
    email: str, manage_url: str, lang: str = i18n.DEFAULT_LOCALE
) -> tuple[str, str, str]:
    """Sent right after a subscriber confirms — sets expectations + the manage link."""
    inner = (
        _title(i18n.t(lang, "welcome_title"))
        + _body(i18n.t(lang, "welcome_body"), "14px 0 20px")
        + f'<div style="margin:4px 0 22px;">{_button(manage_url, i18n.t(lang, "welcome_button"))}</div>'
        + _note(i18n.t(lang, "welcome_note"), "0")
    )
    return (
        i18n.t(lang, "welcome_subject"),
        _shell(inner, i18n.t(lang, "welcome_preheader"), lang),
        i18n.t(lang, "welcome_text", url=manage_url),
    )


def render_manage_link(
    email: str, manage_url: str, lang: str = i18n.DEFAULT_LOCALE
) -> tuple[str, str, str]:
    """Passwordless "here's your settings link" — sent when someone asks (from the site) for
    the link to an existing subscription. It only ever reaches the address's own inbox, and the
    link itself is the credential, so there is nothing to leak by sending it: an attacker who
    knows the email but not the inbox gains nothing. The copy is deliberately reassuring for the
    case where the request wasn't made by the owner — no action happens just from receiving it."""
    inner = (
        _title(i18n.t(lang, "manage_title"))
        + _body(i18n.t(lang, "manage_body"))
        + f'<div style="margin:4px 0 22px;">{_button(manage_url, i18n.t(lang, "manage_button"))}</div>'
        + _paste_link(manage_url, lang)
        + _note(i18n.t(lang, "manage_ignore"))
    )
    return (
        i18n.t(lang, "manage_subject"),
        _shell(inner, i18n.t(lang, "manage_preheader"), lang),
        i18n.t(lang, "manage_text", url=manage_url),
    )
