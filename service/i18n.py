"""Email copy, in the subscriber's language.

The website has spoken eight languages since 2026-08-01; the emails did not, so a Czech
subscriber read a Czech site and then received English mail every morning. This module is the
email-side counterpart of `web/i18n/` — same eight locales, same plural discipline, same rule
that the *stored* data never depends on the language.

Three things to know before editing:

- **Plural categories are CLDR's, not "one vs many".** Czech and Slovak split 2–4 from 5+
  ("2 nabídky" vs "5 nabídek"); Polish splits again and has a separate `many`. Writing
  `f"{n} nabídky"` produces text that is wrong for most values of n, and nothing catches it
  because the string still renders. Every counted noun goes through `plural()`.
- **A missing key falls back to English, loudly enough to find.** `t()` raises on an unknown
  key (a typo is a bug) but silently uses English for a key a translation has not filled in
  yet — a half-translated language should degrade, not crash someone's digest.
- **`SUBJECT_WORDS` lives in `service/taxonomy.py` for English only.** The per-language
  versions are here, keyed by the same `role_category` values, and `test_taxonomy.py` fails if
  the two sets of keys drift.
"""

from __future__ import annotations

from typing import Any

LOCALES = ("en", "cs", "de", "sk", "pl", "es", "fr", "it")
DEFAULT_LOCALE = "en"


def clean_locale(value: Any) -> str:
    """Normalise anything stored or submitted into a locale this module can render.

    Unknown values become English rather than raising: the language of an email is a
    presentation detail, and refusing to send a digest because a column holds 'nl' would turn
    a cosmetic problem into a delivery failure.
    """
    text = str(value or "").strip().lower()
    if "-" in text:
        text = text.split("-", 1)[0]
    return text if text in LOCALES else DEFAULT_LOCALE


def plural_category(locale: str, n: int) -> str:
    """CLDR plural category for an integer count.

    Only the integer rules are implemented, because every use here is a count of jobs. The
    values are pinned by `test_i18n.py` against the categories CLDR specifies, so this cannot
    quietly drift into "close enough".
    """
    loc = clean_locale(locale)
    n = abs(int(n))
    if loc in ("cs", "sk"):
        if n == 1:
            return "one"
        if 2 <= n <= 4:
            return "few"
        return "other"
    if loc == "pl":
        if n == 1:
            return "one"
        if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
            return "few"
        return "many"
    if loc == "fr":
        # French puts 0 with 1, which is why "0 offre" is singular and "0 offers" is not.
        return "one" if n in (0, 1) else "other"
    return "one" if n == 1 else "other"


def t(locale: str, key: str, **vars: Any) -> str:
    """A plain string, formatted with `{name}` placeholders."""
    loc = clean_locale(locale)
    table = MESSAGES.get(loc, {})
    value = table.get(key, MESSAGES[DEFAULT_LOCALE].get(key))
    if value is None:
        raise KeyError(f"no message for key {key!r}")
    if not isinstance(value, str):
        raise TypeError(f"{key!r} is a plural entry — use plural()")
    return value.format(**vars) if vars else value


def plural(locale: str, key: str, n: int, **vars: Any) -> str:
    """A counted string. `{n}` is always available; pass any other placeholders as kwargs."""
    loc = clean_locale(locale)
    forms = MESSAGES.get(loc, {}).get(key) or MESSAGES[DEFAULT_LOCALE].get(key)
    if forms is None:
        raise KeyError(f"no message for key {key!r}")
    if not isinstance(forms, dict):
        raise TypeError(f"{key!r} is a plain string — use t()")
    template = forms.get(plural_category(loc, n)) or forms["other"]
    return template.format(n=n, **vars)


# Short month names for the one locale that writes dates that way. Hardcoded rather than
# taken from `locale.setlocale`, which is process-global, not thread-safe, and depends on
# locales being installed in the container — three ways for a subject line to come out wrong
# on a box nobody looked at.
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# How each language writes a short date. `strftime("%-d %b")` produced "2 Aug" in all eight,
# because %b renders in the C locale — a Czech subject line ending in an English month.
_DATE_STYLE = {
    "en": lambda d, m: f"{d} {_MONTHS_EN[m - 1]}",
    "cs": lambda d, m: f"{d}. {m}.",
    "sk": lambda d, m: f"{d}. {m}.",
    "pl": lambda d, m: f"{d}.{m:02d}",
    "de": lambda d, m: f"{d}.{m}.",
    "es": lambda d, m: f"{d}/{m}",
    "fr": lambda d, m: f"{d}/{m}",
    "it": lambda d, m: f"{d}/{m}",
}


def format_date(locale: str, when) -> str:
    """A short day+month for a subject line, written the way `locale` writes dates."""
    style = _DATE_STYLE.get(clean_locale(locale), _DATE_STYLE[DEFAULT_LOCALE])
    return style(when.day, when.month)


def subject_word(locale: str, category: str) -> str:
    """What a human calls a `role_category` in a subject line, in `locale`."""
    words = SUBJECT_WORDS.get(clean_locale(locale)) or SUBJECT_WORDS[DEFAULT_LOCALE]
    return words.get(category) or SUBJECT_WORDS[DEFAULT_LOCALE].get(
        category, category.replace("_", " ")
    )


# --------------------------------------------------------------- subject words ---
# Keyed by `role_category`. English mirrors `taxonomy.SUBJECT_WORDS`, which stays the single
# definition for the Python side; `test_taxonomy.py` fails if the key sets drift apart.
SUBJECT_WORDS: dict[str, dict[str, str]] = {
    "en": {
        "data_engineering": "data engineering", "data_analysis": "data",
        "machine_learning": "ML", "software_engineering": "engineering",
        "devops_platform": "platform", "product": "product", "design": "design",
        "social_media": "social media", "other_tech_function": "tech",
    },
    "cs": {
        "data_engineering": "datové inženýrství",
        "data_analysis": "data",
        "machine_learning": "ML",
        "software_engineering": "vývoj",
        "devops_platform": "platforma",
        "product": "produkt",
        "design": "design",
        "social_media": "social media",
        "other_tech_function": "tech",
    },
    "de": {
        "data_engineering": "Data-Engineering-", "data_analysis": "Daten-",
        "machine_learning": "ML-", "software_engineering": "Entwicklungs-",
        "devops_platform": "Plattform-", "product": "Produkt-", "design": "Design-",
        "social_media": "Social-Media-", "other_tech_function": "Tech-",
    },
    "sk": {
        "data_engineering": "dátové inžinierstvo",
        "data_analysis": "dáta",
        "machine_learning": "ML",
        "software_engineering": "vývoj",
        "devops_platform": "platforma",
        "product": "produkt",
        "design": "dizajn",
        "social_media": "social media",
        "other_tech_function": "tech",
    },
    "pl": {
        "data_engineering": "inżynieria danych",
        "data_analysis": "dane",
        "machine_learning": "ML",
        "software_engineering": "programowanie",
        "devops_platform": "platforma",
        "product": "produkt",
        "design": "projektowanie",
        "social_media": "social media",
        "other_tech_function": "tech",
    },
    "es": {
        "data_engineering": "de ingeniería de datos", "data_analysis": "de datos",
        "machine_learning": "de ML", "software_engineering": "de desarrollo",
        "devops_platform": "de plataforma", "product": "de producto",
        "design": "de diseño", "social_media": "de redes sociales",
        "other_tech_function": "tech",
    },
    "fr": {
        "data_engineering": "en data engineering", "data_analysis": "data",
        "machine_learning": "en ML", "software_engineering": "de développement",
        "devops_platform": "plateforme", "product": "produit", "design": "design",
        "social_media": "réseaux sociaux", "other_tech_function": "tech",
    },
    "it": {
        "data_engineering": "di data engineering", "data_analysis": "di dati",
        "machine_learning": "di ML", "software_engineering": "di sviluppo",
        "devops_platform": "di piattaforma", "product": "di prodotto",
        "design": "di design", "social_media": "social media",
        "other_tech_function": "tech",
    },
}


# ------------------------------------------------------------------- messages ----
# Plain strings are `str`; counted ones are a dict of CLDR categories and must define "other".
MESSAGES: dict[str, dict[str, Any]] = {
    "en": {
        # --- digest subject + greeting ---
        "subject_new": {"one": "{n} new role", "other": "{n} new roles"},
        "subject_new_named": {"one": "{n} new {role} role", "other": "{n} new {role} roles"},
        "subject_for_you": "{count} for you — {when}",
        "subject_weak": "No strong matches today — {count} to explore — {when}",
        "explore_count": {"one": "{n} role", "other": "{n} roles"},
        "good_morning": "Good morning.",
        "weak_lead": "No strong matches today",
        "weak_rest": {
            "one": "— but here is one worth a look, and the full list is on your matches page.",
            "other": "— but here are {n} worth a look, and the full list is on your matches page.",
        },
        "fresh_count": {"one": "{n} fresh match", "other": "{n} fresh matches"},
        "fresh_rest": "today, ranked for you.",
        "weak_text": {
            "one": "Good morning. No strong matches today — one weaker to explore below, and the full list is on your matches page.",
            "other": "Good morning. No strong matches today — {n} weaker ones to explore below, and the full list is on your matches page.",
        },
        "fresh_text": {
            "one": "Good morning. {n} fresh match today.",
            "other": "Good morning. {n} fresh matches today.",
        },
        # --- digest body ---
        "role_fallback": "Role",
        "view_apply": "View & apply →",
        "see_all_label": {"one": "See all {n} match →", "other": "See all {n} matches →"},
        "see_all_sub": "We emailed the {shown} strongest — browse the rest on your matches page.",
        "open_matches_label": "Open your matches page →",
        "open_matches_sub": "All your matches, on one page, any time.",
        "see_all_text": {"one": "See all {n} match:", "other": "See all {n} matches:"},
        "refine": "Refine preferences",
        "pause": "Pause 2 weeks",
        "unsubscribe": "Unsubscribe",
        "footer_sent": "Sent to {email} · You signed up at jobdigest.eu · One email a day.",
        "footer_sent_text": "Sent to {email} · One email a day.",
        "text_preferences": "Preferences:",
        "text_unsubscribe": "Unsubscribe:",
        # --- job tags ---
        "tag_hybrid": "Hybrid",
        "tag_remote": "Remote",
        "tag_freelance": "Freelance",
        "tag_part_time": "Part-time",
        "seniority_junior": "Junior",
        "seniority_mid": "Mid",
        "seniority_senior": "Senior",
        # --- shared email chrome ---
        "shell_footer": "JobDigest · One short email a day · Made in the EU",
        "link_fallback": "If the button doesn't work, paste this link into your browser:",
        # --- confirm ---
        "confirm_subject": "Confirm your JobDigest subscription",
        "confirm_title": "Confirm your subscription",
        "confirm_body": "One tap and your daily job digest is live. We'll send a short, ranked shortlist every morning at 7:00 — only roles worth your time.",
        "confirm_button": "Confirm & start my digest →",
        "confirm_ignore": "Didn't sign up? Just ignore this email — nothing happens without your confirmation.",
        "confirm_preheader": "Confirm to start your daily job digest.",
        "confirm_text": "Confirm your JobDigest subscription\n\nOne tap and your daily job digest is live. Confirm here:\n{url}\n\nDidn't sign up? Ignore this email — nothing happens without your confirmation.\n",
        # --- welcome ---
        "welcome_subject": "You're all set — your first JobDigest arrives tomorrow",
        "welcome_title": "You're all set ✓",
        "welcome_body": "Your subscription is confirmed. Your first curated digest lands at 7:00 tomorrow morning. Want to fine-tune what you see?",
        "welcome_button": "Edit my preferences →",
        "welcome_note": "You can change roles, skills, or frequency, pause for a while, or unsubscribe — all from that link, no password needed.",
        "welcome_preheader": "Confirmed — first digest tomorrow at 7:00.",
        "welcome_text": "You're all set — your first JobDigest arrives tomorrow\n\nYour subscription is confirmed. First digest at 7:00 tomorrow.\nEdit preferences / pause / unsubscribe: {url}\n",
        # --- manage link ---
        "manage_subject": "Your JobDigest settings link",
        "manage_title": "Manage your subscription",
        "manage_body": "Someone (hopefully you) asked for the link to manage your JobDigest subscription. Open it to change roles, skills or frequency, pause for a while, or unsubscribe — no password needed.",
        "manage_button": "Manage my preferences →",
        "manage_ignore": "Didn't ask for this? You can safely ignore this email — it only links to your own settings, and nothing changes unless you open it and make a change yourself.",
        "manage_preheader": "Your private link to manage your JobDigest subscription.",
        "manage_text": "Your JobDigest settings link\n\nSomeone asked for the link to manage your JobDigest subscription. Open it to edit\npreferences, pause, or unsubscribe — no password needed:\n{url}\n\nDidn't ask for this? Ignore this email — nothing changes unless you open it.\n",
    },

    "cs": {
        "subject_new": {"one": "{n} nová nabídka", "few": "{n} nové nabídky", "other": "{n} nových nabídek"},
        "subject_new_named": {"one": "{n} nová nabídka ({role})", "few": "{n} nové nabídky ({role})", "other": "{n} nových nabídek ({role})"},
        "subject_for_you": "{count} pro vás — {when}",
        "subject_weak": "Dnes žádné silné shody — {count} k prohlédnutí — {when}",
        "explore_count": {"one": "{n} nabídka", "few": "{n} nabídky", "other": "{n} nabídek"},
        "good_morning": "Dobré ráno.",
        "weak_lead": "Dnes žádné silné shody",
        "weak_rest": {
            "one": "— ale jedna stojí za podívání a celý seznam najdete na stránce s nabídkami.",
            "few": "— ale {n} stojí za podívání a celý seznam najdete na stránce s nabídkami.",
            "other": "— ale {n} jich stojí za podívání a celý seznam najdete na stránce s nabídkami.",
        },
        "fresh_count": {"one": "{n} nová shoda", "few": "{n} nové shody", "other": "{n} nových shod"},
        "fresh_rest": "dnes, seřazeno pro vás.",
        "weak_text": {
            "one": "Dobré ráno. Dnes žádné silné shody — níže je jedna slabší k prohlédnutí a celý seznam najdete na stránce s nabídkami.",
            "few": "Dobré ráno. Dnes žádné silné shody — níže jsou {n} slabší k prohlédnutí a celý seznam najdete na stránce s nabídkami.",
            "other": "Dobré ráno. Dnes žádné silné shody — níže je {n} slabších k prohlédnutí a celý seznam najdete na stránce s nabídkami.",
        },
        "fresh_text": {
            "one": "Dobré ráno. Dnes {n} nová shoda.",
            "few": "Dobré ráno. Dnes {n} nové shody.",
            "other": "Dobré ráno. Dnes {n} nových shod.",
        },
        "role_fallback": "Pozice",
        "view_apply": "Zobrazit a odpovědět →",
        "see_all_label": {"one": "Zobrazit {n} nabídku →", "few": "Zobrazit všechny {n} nabídky →", "other": "Zobrazit všech {n} nabídek →"},
        "see_all_sub": "Poslali jsme {shown} nejsilnějších — zbytek si projděte na stránce s nabídkami.",
        "open_matches_label": "Otevřít stránku s nabídkami →",
        "open_matches_sub": "Všechny vaše nabídky na jedné stránce, kdykoli.",
        "see_all_text": {"one": "Zobrazit {n} nabídku:", "few": "Zobrazit všechny {n} nabídky:", "other": "Zobrazit všech {n} nabídek:"},
        "refine": "Upravit nastavení",
        "pause": "Pozastavit na 2 týdny",
        "unsubscribe": "Odhlásit odběr",
        "footer_sent": "Odesláno na {email} · Přihlásili jste se na jobdigest.eu · Jeden e-mail denně.",
        "footer_sent_text": "Odesláno na {email} · Jeden e-mail denně.",
        "text_preferences": "Nastavení:",
        "text_unsubscribe": "Odhlášení:",
        "tag_hybrid": "Hybridně",
        "tag_remote": "Na dálku",
        "tag_freelance": "Freelance",
        "tag_part_time": "Částečný úvazek",
        "seniority_junior": "Junior",
        "seniority_mid": "Medior",
        "seniority_senior": "Senior",
        "shell_footer": "JobDigest · Jeden krátký e-mail denně · Vyrobeno v EU",
        "link_fallback": "Pokud tlačítko nefunguje, vložte tento odkaz do prohlížeče:",
        "confirm_subject": "Potvrďte odběr JobDigest",
        "confirm_title": "Potvrďte svůj odběr",
        "confirm_body": "Jedno kliknutí a váš denní přehled nabídek běží. Každé ráno v 7:00 pošleme krátký seřazený výběr — jen pozice, které stojí za váš čas.",
        "confirm_button": "Potvrdit a spustit přehled →",
        "confirm_ignore": "Neregistrovali jste se? Tento e-mail prostě ignorujte — bez vašeho potvrzení se nic nestane.",
        "confirm_preheader": "Potvrďte a spusťte svůj denní přehled nabídek.",
        "confirm_text": "Potvrďte odběr JobDigest\n\nJedno kliknutí a váš denní přehled nabídek běží. Potvrďte zde:\n{url}\n\nNeregistrovali jste se? Tento e-mail ignorujte — bez potvrzení se nic nestane.\n",
        "welcome_subject": "Vše je připraveno — první JobDigest dorazí zítra",
        "welcome_title": "Vše je připraveno ✓",
        "welcome_body": "Váš odběr je potvrzen. První vybraný přehled dorazí zítra ráno v 7:00. Chcete doladit, co uvidíte?",
        "welcome_button": "Upravit nastavení →",
        "welcome_note": "Pozice, dovednosti i frekvenci můžete změnit, odběr pozastavit nebo zrušit — vše z toho odkazu, bez hesla.",
        "welcome_preheader": "Potvrzeno — první přehled zítra v 7:00.",
        "welcome_text": "Vše je připraveno — první JobDigest dorazí zítra\n\nVáš odběr je potvrzen. První přehled zítra v 7:00.\nUpravit nastavení / pozastavit / odhlásit: {url}\n",
        "manage_subject": "Váš odkaz na nastavení JobDigest",
        "manage_title": "Správa vašeho odběru",
        "manage_body": "Někdo (snad vy) požádal o odkaz pro správu vašeho odběru JobDigest. Otevřete jej a změňte pozice, dovednosti nebo frekvenci, odběr pozastavte nebo zrušte — bez hesla.",
        "manage_button": "Spravovat nastavení →",
        "manage_ignore": "Nežádali jste o to? Tento e-mail můžete klidně ignorovat — vede jen k vašemu vlastnímu nastavení a nic se nezmění, dokud jej neotevřete a změnu sami neprovedete.",
        "manage_preheader": "Váš soukromý odkaz pro správu odběru JobDigest.",
        "manage_text": "Váš odkaz na nastavení JobDigest\n\nNěkdo požádal o odkaz pro správu vašeho odběru JobDigest. Otevřete jej a upravte\nnastavení, pozastavte nebo zrušte odběr — bez hesla:\n{url}\n\nNežádali jste o to? Tento e-mail ignorujte — nic se nezmění, dokud jej neotevřete.\n",
    },

    "de": {
        "subject_new": {"one": "{n} neue Stelle", "other": "{n} neue Stellen"},
        "subject_new_named": {"one": "{n} neue {role}Stelle", "other": "{n} neue {role}Stellen"},
        "subject_for_you": "{count} für dich — {when}",
        "subject_weak": "Heute keine starken Treffer — {count} zum Ansehen — {when}",
        "explore_count": {"one": "{n} Stelle", "other": "{n} Stellen"},
        "good_morning": "Guten Morgen.",
        "weak_lead": "Heute keine starken Treffer",
        "weak_rest": {
            "one": "— aber eine lohnt einen Blick, und die vollständige Liste steht auf deiner Trefferseite.",
            "other": "— aber {n} lohnen einen Blick, und die vollständige Liste steht auf deiner Trefferseite.",
        },
        "fresh_count": {"one": "{n} neuer Treffer", "other": "{n} neue Treffer"},
        "fresh_rest": "heute, für dich sortiert.",
        "weak_text": {
            "one": "Guten Morgen. Heute keine starken Treffer — unten steht ein schwächerer, die vollständige Liste ist auf deiner Trefferseite.",
            "other": "Guten Morgen. Heute keine starken Treffer — unten stehen {n} schwächere, die vollständige Liste ist auf deiner Trefferseite.",
        },
        "fresh_text": {
            "one": "Guten Morgen. Heute {n} neuer Treffer.",
            "other": "Guten Morgen. Heute {n} neue Treffer.",
        },
        "role_fallback": "Stelle",
        "view_apply": "Ansehen & bewerben →",
        "see_all_label": {"one": "Alle {n} Treffer ansehen →", "other": "Alle {n} Treffer ansehen →"},
        "see_all_sub": "Wir haben die {shown} stärksten geschickt — den Rest findest du auf deiner Trefferseite.",
        "open_matches_label": "Trefferseite öffnen →",
        "open_matches_sub": "Alle deine Treffer, auf einer Seite, jederzeit.",
        "see_all_text": {"one": "Alle {n} Treffer ansehen:", "other": "Alle {n} Treffer ansehen:"},
        "refine": "Einstellungen anpassen",
        "pause": "2 Wochen pausieren",
        "unsubscribe": "Abmelden",
        "footer_sent": "Gesendet an {email} · Du hast dich auf jobdigest.eu angemeldet · Eine E-Mail pro Tag.",
        "footer_sent_text": "Gesendet an {email} · Eine E-Mail pro Tag.",
        "text_preferences": "Einstellungen:",
        "text_unsubscribe": "Abmelden:",
        "tag_hybrid": "Hybrid",
        "tag_remote": "Remote",
        "tag_freelance": "Freelance",
        "tag_part_time": "Teilzeit",
        "seniority_junior": "Junior",
        "seniority_mid": "Mid-Level",
        "seniority_senior": "Senior",
        "shell_footer": "JobDigest · Eine kurze E-Mail pro Tag · Hergestellt in der EU",
        "link_fallback": "Wenn der Button nicht funktioniert, füge diesen Link in deinen Browser ein:",
        "confirm_subject": "Bestätige dein JobDigest-Abo",
        "confirm_title": "Abo bestätigen",
        "confirm_body": "Ein Tippen und dein täglicher Job-Digest läuft. Wir schicken jeden Morgen um 7:00 eine kurze, sortierte Auswahl — nur Stellen, die deine Zeit wert sind.",
        "confirm_button": "Bestätigen & Digest starten →",
        "confirm_ignore": "Nicht angemeldet? Ignoriere diese E-Mail einfach — ohne deine Bestätigung passiert nichts.",
        "confirm_preheader": "Bestätige, um deinen täglichen Job-Digest zu starten.",
        "confirm_text": "Bestätige dein JobDigest-Abo\n\nEin Tippen und dein täglicher Job-Digest läuft. Hier bestätigen:\n{url}\n\nNicht angemeldet? Ignoriere diese E-Mail — ohne Bestätigung passiert nichts.\n",
        "welcome_subject": "Alles bereit — dein erster JobDigest kommt morgen",
        "welcome_title": "Alles bereit ✓",
        "welcome_body": "Dein Abo ist bestätigt. Dein erster kuratierter Digest kommt morgen früh um 7:00. Willst du feinjustieren, was du siehst?",
        "welcome_button": "Einstellungen bearbeiten →",
        "welcome_note": "Du kannst Positionen, Fähigkeiten oder Häufigkeit ändern, eine Weile pausieren oder dich abmelden — alles über diesen Link, ohne Passwort.",
        "welcome_preheader": "Bestätigt — erster Digest morgen um 7:00.",
        "welcome_text": "Alles bereit — dein erster JobDigest kommt morgen\n\nDein Abo ist bestätigt. Erster Digest morgen um 7:00.\nEinstellungen / pausieren / abmelden: {url}\n",
        "manage_subject": "Dein JobDigest-Einstellungslink",
        "manage_title": "Abo verwalten",
        "manage_body": "Jemand (hoffentlich du) hat den Link zur Verwaltung deines JobDigest-Abos angefordert. Öffne ihn, um Positionen, Fähigkeiten oder Häufigkeit zu ändern, zu pausieren oder dich abzumelden — ohne Passwort.",
        "manage_button": "Einstellungen verwalten →",
        "manage_ignore": "Nicht angefordert? Du kannst diese E-Mail gefahrlos ignorieren — sie verlinkt nur auf deine eigenen Einstellungen, und nichts ändert sich, solange du sie nicht öffnest und selbst etwas änderst.",
        "manage_preheader": "Dein privater Link zur Verwaltung deines JobDigest-Abos.",
        "manage_text": "Dein JobDigest-Einstellungslink\n\nJemand hat den Link zur Verwaltung deines JobDigest-Abos angefordert. Öffne ihn, um\nEinstellungen zu ändern, zu pausieren oder dich abzumelden — ohne Passwort:\n{url}\n\nNicht angefordert? Ignoriere diese E-Mail — nichts ändert sich, solange du sie nicht öffnest.\n",
    },

    "sk": {
        "subject_new": {"one": "{n} nová ponuka", "few": "{n} nové ponuky", "other": "{n} nových ponúk"},
        "subject_new_named": {"one": "{n} nová ponuka ({role})", "few": "{n} nové ponuky ({role})", "other": "{n} nových ponúk ({role})"},
        "subject_for_you": "{count} pre vás — {when}",
        "subject_weak": "Dnes žiadne silné zhody — {count} na pozretie — {when}",
        "explore_count": {"one": "{n} ponuka", "few": "{n} ponuky", "other": "{n} ponúk"},
        "good_morning": "Dobré ráno.",
        "weak_lead": "Dnes žiadne silné zhody",
        "weak_rest": {
            "one": "— ale jedna stojí za pozretie a celý zoznam nájdete na stránke s ponukami.",
            "few": "— ale {n} stoja za pozretie a celý zoznam nájdete na stránke s ponukami.",
            "other": "— ale {n} ich stojí za pozretie a celý zoznam nájdete na stránke s ponukami.",
        },
        "fresh_count": {"one": "{n} nová zhoda", "few": "{n} nové zhody", "other": "{n} nových zhôd"},
        "fresh_rest": "dnes, zoradené pre vás.",
        "weak_text": {
            "one": "Dobré ráno. Dnes žiadne silné zhody — nižšie je jedna slabšia na pozretie a celý zoznam nájdete na stránke s ponukami.",
            "few": "Dobré ráno. Dnes žiadne silné zhody — nižšie sú {n} slabšie na pozretie a celý zoznam nájdete na stránke s ponukami.",
            "other": "Dobré ráno. Dnes žiadne silné zhody — nižšie je {n} slabších na pozretie a celý zoznam nájdete na stránke s ponukami.",
        },
        "fresh_text": {
            "one": "Dobré ráno. Dnes {n} nová zhoda.",
            "few": "Dobré ráno. Dnes {n} nové zhody.",
            "other": "Dobré ráno. Dnes {n} nových zhôd.",
        },
        "role_fallback": "Pozícia",
        "view_apply": "Zobraziť a odpovedať →",
        "see_all_label": {"one": "Zobraziť {n} ponuku →", "few": "Zobraziť všetky {n} ponuky →", "other": "Zobraziť všetkých {n} ponúk →"},
        "see_all_sub": "Poslali sme {shown} najsilnejších — zvyšok si prezrite na stránke s ponukami.",
        "open_matches_label": "Otvoriť stránku s ponukami →",
        "open_matches_sub": "Všetky vaše ponuky na jednej stránke, kedykoľvek.",
        "see_all_text": {"one": "Zobraziť {n} ponuku:", "few": "Zobraziť všetky {n} ponuky:", "other": "Zobraziť všetkých {n} ponúk:"},
        "refine": "Upraviť nastavenia",
        "pause": "Pozastaviť na 2 týždne",
        "unsubscribe": "Odhlásiť odber",
        "footer_sent": "Odoslané na {email} · Prihlásili ste sa na jobdigest.eu · Jeden e-mail denne.",
        "footer_sent_text": "Odoslané na {email} · Jeden e-mail denne.",
        "text_preferences": "Nastavenia:",
        "text_unsubscribe": "Odhlásenie:",
        "tag_hybrid": "Hybridne",
        "tag_remote": "Na diaľku",
        "tag_freelance": "Freelance",
        "tag_part_time": "Čiastočný úväzok",
        "seniority_junior": "Junior",
        "seniority_mid": "Medior",
        "seniority_senior": "Senior",
        "shell_footer": "JobDigest · Jeden krátky e-mail denne · Vyrobené v EÚ",
        "link_fallback": "Ak tlačidlo nefunguje, vložte tento odkaz do prehliadača:",
        "confirm_subject": "Potvrďte odber JobDigest",
        "confirm_title": "Potvrďte svoj odber",
        "confirm_body": "Jedno kliknutie a váš denný prehľad ponúk beží. Každé ráno o 7:00 pošleme krátky zoradený výber — len pozície, ktoré stoja za váš čas.",
        "confirm_button": "Potvrdiť a spustiť prehľad →",
        "confirm_ignore": "Neregistrovali ste sa? Tento e-mail jednoducho ignorujte — bez vášho potvrdenia sa nič nestane.",
        "confirm_preheader": "Potvrďte a spustite svoj denný prehľad ponúk.",
        "confirm_text": "Potvrďte odber JobDigest\n\nJedno kliknutie a váš denný prehľad ponúk beží. Potvrďte tu:\n{url}\n\nNeregistrovali ste sa? Tento e-mail ignorujte — bez potvrdenia sa nič nestane.\n",
        "welcome_subject": "Všetko je pripravené — prvý JobDigest dorazí zajtra",
        "welcome_title": "Všetko je pripravené ✓",
        "welcome_body": "Váš odber je potvrdený. Prvý vybraný prehľad dorazí zajtra ráno o 7:00. Chcete doladiť, čo uvidíte?",
        "welcome_button": "Upraviť nastavenia →",
        "welcome_note": "Pozície, zručnosti aj frekvenciu môžete zmeniť, odber pozastaviť alebo zrušiť — všetko z toho odkazu, bez hesla.",
        "welcome_preheader": "Potvrdené — prvý prehľad zajtra o 7:00.",
        "welcome_text": "Všetko je pripravené — prvý JobDigest dorazí zajtra\n\nVáš odber je potvrdený. Prvý prehľad zajtra o 7:00.\nUpraviť nastavenia / pozastaviť / odhlásiť: {url}\n",
        "manage_subject": "Váš odkaz na nastavenia JobDigest",
        "manage_title": "Správa vášho odberu",
        "manage_body": "Niekto (dúfajme, že vy) požiadal o odkaz na správu vášho odberu JobDigest. Otvorte ho a zmeňte pozície, zručnosti alebo frekvenciu, odber pozastavte alebo zrušte — bez hesla.",
        "manage_button": "Spravovať nastavenia →",
        "manage_ignore": "Nežiadali ste o to? Tento e-mail môžete pokojne ignorovať — vedie len k vášmu vlastnému nastaveniu a nič sa nezmení, kým ho neotvoríte a zmenu sami nevykonáte.",
        "manage_preheader": "Váš súkromný odkaz na správu odberu JobDigest.",
        "manage_text": "Váš odkaz na nastavenia JobDigest\n\nNiekto požiadal o odkaz na správu vášho odberu JobDigest. Otvorte ho a upravte\nnastavenia, pozastavte alebo zrušte odber — bez hesla:\n{url}\n\nNežiadali ste o to? Tento e-mail ignorujte — nič sa nezmení, kým ho neotvoríte.\n",
    },

    "pl": {
        "subject_new": {"one": "{n} nowa oferta", "few": "{n} nowe oferty", "many": "{n} nowych ofert", "other": "{n} nowej oferty"},
        "subject_new_named": {"one": "{n} nowa oferta ({role})", "few": "{n} nowe oferty ({role})", "many": "{n} nowych ofert ({role})", "other": "{n} nowej oferty ({role})"},
        "subject_for_you": "{count} dla Ciebie — {when}",
        "subject_weak": "Dziś brak mocnych dopasowań — {count} do obejrzenia — {when}",
        "explore_count": {"one": "{n} oferta", "few": "{n} oferty", "many": "{n} ofert", "other": "{n} oferty"},
        "good_morning": "Dzień dobry.",
        "weak_lead": "Dziś brak mocnych dopasowań",
        "weak_rest": {
            "one": "— ale jedna jest warta uwagi, a pełna lista jest na Twojej stronie dopasowań.",
            "few": "— ale {n} są warte uwagi, a pełna lista jest na Twojej stronie dopasowań.",
            "many": "— ale {n} jest wartych uwagi, a pełna lista jest na Twojej stronie dopasowań.",
            "other": "— ale {n} jest warte uwagi, a pełna lista jest na Twojej stronie dopasowań.",
        },
        "fresh_count": {"one": "{n} nowe dopasowanie", "few": "{n} nowe dopasowania", "many": "{n} nowych dopasowań", "other": "{n} nowego dopasowania"},
        "fresh_rest": "dziś, uszeregowane dla Ciebie.",
        "weak_text": {
            "one": "Dzień dobry. Dziś brak mocnych dopasowań — poniżej jedno słabsze do obejrzenia, a pełna lista jest na Twojej stronie dopasowań.",
            "few": "Dzień dobry. Dziś brak mocnych dopasowań — poniżej {n} słabsze do obejrzenia, a pełna lista jest na Twojej stronie dopasowań.",
            "many": "Dzień dobry. Dziś brak mocnych dopasowań — poniżej {n} słabszych do obejrzenia, a pełna lista jest na Twojej stronie dopasowań.",
            "other": "Dzień dobry. Dziś brak mocnych dopasowań — poniżej {n} słabszego do obejrzenia, a pełna lista jest na Twojej stronie dopasowań.",
        },
        "fresh_text": {
            "one": "Dzień dobry. Dziś {n} nowe dopasowanie.",
            "few": "Dzień dobry. Dziś {n} nowe dopasowania.",
            "many": "Dzień dobry. Dziś {n} nowych dopasowań.",
            "other": "Dzień dobry. Dziś {n} nowego dopasowania.",
        },
        "role_fallback": "Stanowisko",
        "view_apply": "Zobacz i aplikuj →",
        "see_all_label": {"one": "Zobacz {n} ofertę →", "few": "Zobacz wszystkie {n} oferty →", "many": "Zobacz wszystkie {n} ofert →", "other": "Zobacz {n} oferty →"},
        "see_all_sub": "Wysłaliśmy {shown} najmocniejszych — resztę przejrzysz na stronie dopasowań.",
        "open_matches_label": "Otwórz stronę dopasowań →",
        "open_matches_sub": "Wszystkie Twoje dopasowania na jednej stronie, o każdej porze.",
        "see_all_text": {"one": "Zobacz {n} ofertę:", "few": "Zobacz wszystkie {n} oferty:", "many": "Zobacz wszystkie {n} ofert:", "other": "Zobacz {n} oferty:"},
        "refine": "Zmień ustawienia",
        "pause": "Wstrzymaj na 2 tygodnie",
        "unsubscribe": "Wypisz się",
        "footer_sent": "Wysłane na {email} · Zapisałeś się na jobdigest.eu · Jeden e-mail dziennie.",
        "footer_sent_text": "Wysłane na {email} · Jeden e-mail dziennie.",
        "text_preferences": "Ustawienia:",
        "text_unsubscribe": "Wypisanie:",
        "tag_hybrid": "Hybrydowo",
        "tag_remote": "Zdalnie",
        "tag_freelance": "Freelance",
        "tag_part_time": "Część etatu",
        "seniority_junior": "Junior",
        "seniority_mid": "Mid",
        "seniority_senior": "Senior",
        "shell_footer": "JobDigest · Jeden krótki e-mail dziennie · Zrobione w UE",
        "link_fallback": "Jeśli przycisk nie działa, wklej ten link do przeglądarki:",
        "confirm_subject": "Potwierdź subskrypcję JobDigest",
        "confirm_title": "Potwierdź subskrypcję",
        "confirm_body": "Jedno kliknięcie i Twój codzienny przegląd ofert rusza. Każdego ranka o 7:00 wyślemy krótką, uszeregowaną listę — tylko oferty warte Twojego czasu.",
        "confirm_button": "Potwierdź i uruchom przegląd →",
        "confirm_ignore": "Nie zapisywałeś się? Po prostu zignoruj tę wiadomość — bez potwierdzenia nic się nie stanie.",
        "confirm_preheader": "Potwierdź, aby uruchomić codzienny przegląd ofert.",
        "confirm_text": "Potwierdź subskrypcję JobDigest\n\nJedno kliknięcie i Twój codzienny przegląd ofert rusza. Potwierdź tutaj:\n{url}\n\nNie zapisywałeś się? Zignoruj tę wiadomość — bez potwierdzenia nic się nie stanie.\n",
        "welcome_subject": "Wszystko gotowe — pierwszy JobDigest dotrze jutro",
        "welcome_title": "Wszystko gotowe ✓",
        "welcome_body": "Twoja subskrypcja jest potwierdzona. Pierwszy wyselekcjonowany przegląd dotrze jutro rano o 7:00. Chcesz dopracować to, co widzisz?",
        "welcome_button": "Edytuj ustawienia →",
        "welcome_note": "Możesz zmienić stanowiska, umiejętności lub częstotliwość, wstrzymać na jakiś czas albo się wypisać — wszystko z tego linku, bez hasła.",
        "welcome_preheader": "Potwierdzone — pierwszy przegląd jutro o 7:00.",
        "welcome_text": "Wszystko gotowe — pierwszy JobDigest dotrze jutro\n\nTwoja subskrypcja jest potwierdzona. Pierwszy przegląd jutro o 7:00.\nEdytuj ustawienia / wstrzymaj / wypisz się: {url}\n",
        "manage_subject": "Twój link do ustawień JobDigest",
        "manage_title": "Zarządzaj subskrypcją",
        "manage_body": "Ktoś (mamy nadzieję, że Ty) poprosił o link do zarządzania Twoją subskrypcją JobDigest. Otwórz go, aby zmienić stanowiska, umiejętności lub częstotliwość, wstrzymać albo się wypisać — bez hasła.",
        "manage_button": "Zarządzaj ustawieniami →",
        "manage_ignore": "Nie prosiłeś o to? Możesz spokojnie zignorować tę wiadomość — prowadzi tylko do Twoich własnych ustawień i nic się nie zmieni, dopóki jej nie otworzysz i sam czegoś nie zmienisz.",
        "manage_preheader": "Twój prywatny link do zarządzania subskrypcją JobDigest.",
        "manage_text": "Twój link do ustawień JobDigest\n\nKtoś poprosił o link do zarządzania Twoją subskrypcją JobDigest. Otwórz go, aby\nzmienić ustawienia, wstrzymać lub wypisać się — bez hasła:\n{url}\n\nNie prosiłeś o to? Zignoruj tę wiadomość — nic się nie zmieni, dopóki jej nie otworzysz.\n",
    },

    "es": {
        "subject_new": {"one": "{n} puesto nuevo", "other": "{n} puestos nuevos"},
        "subject_new_named": {"one": "{n} puesto nuevo {role}", "other": "{n} puestos nuevos {role}"},
        "subject_for_you": "{count} para ti — {when}",
        "subject_weak": "Hoy sin coincidencias fuertes — {count} para explorar — {when}",
        "explore_count": {"one": "{n} puesto", "other": "{n} puestos"},
        "good_morning": "Buenos días.",
        "weak_lead": "Hoy sin coincidencias fuertes",
        "weak_rest": {
            "one": "— pero hay uno que merece un vistazo, y la lista completa está en tu página de coincidencias.",
            "other": "— pero hay {n} que merecen un vistazo, y la lista completa está en tu página de coincidencias.",
        },
        "fresh_count": {"one": "{n} coincidencia nueva", "other": "{n} coincidencias nuevas"},
        "fresh_rest": "hoy, ordenadas para ti.",
        "weak_text": {
            "one": "Buenos días. Hoy sin coincidencias fuertes — abajo hay una más débil para explorar, y la lista completa está en tu página de coincidencias.",
            "other": "Buenos días. Hoy sin coincidencias fuertes — abajo hay {n} más débiles para explorar, y la lista completa está en tu página de coincidencias.",
        },
        "fresh_text": {
            "one": "Buenos días. Hoy {n} coincidencia nueva.",
            "other": "Buenos días. Hoy {n} coincidencias nuevas.",
        },
        "role_fallback": "Puesto",
        "view_apply": "Ver y aplicar →",
        "see_all_label": {"one": "Ver la {n} coincidencia →", "other": "Ver las {n} coincidencias →"},
        "see_all_sub": "Te hemos enviado las {shown} más fuertes — el resto está en tu página de coincidencias.",
        "open_matches_label": "Abrir tu página de coincidencias →",
        "open_matches_sub": "Todas tus coincidencias, en una página, cuando quieras.",
        "see_all_text": {"one": "Ver la {n} coincidencia:", "other": "Ver las {n} coincidencias:"},
        "refine": "Ajustar preferencias",
        "pause": "Pausar 2 semanas",
        "unsubscribe": "Darse de baja",
        "footer_sent": "Enviado a {email} · Te registraste en jobdigest.eu · Un correo al día.",
        "footer_sent_text": "Enviado a {email} · Un correo al día.",
        "text_preferences": "Preferencias:",
        "text_unsubscribe": "Baja:",
        "tag_hybrid": "Híbrido",
        "tag_remote": "Remoto",
        "tag_freelance": "Freelance",
        "tag_part_time": "Media jornada",
        "seniority_junior": "Junior",
        "seniority_mid": "Intermedio",
        "seniority_senior": "Senior",
        "shell_footer": "JobDigest · Un correo corto al día · Hecho en la UE",
        "link_fallback": "Si el botón no funciona, pega este enlace en tu navegador:",
        "confirm_subject": "Confirma tu suscripción a JobDigest",
        "confirm_title": "Confirma tu suscripción",
        "confirm_body": "Un toque y tu resumen diario de empleos está en marcha. Cada mañana a las 7:00 enviaremos una selección corta y ordenada — solo puestos que merecen tu tiempo.",
        "confirm_button": "Confirmar y empezar mi resumen →",
        "confirm_ignore": "¿No te registraste? Ignora este correo — no pasa nada sin tu confirmación.",
        "confirm_preheader": "Confirma para empezar tu resumen diario de empleos.",
        "confirm_text": "Confirma tu suscripción a JobDigest\n\nUn toque y tu resumen diario de empleos está en marcha. Confirma aquí:\n{url}\n\n¿No te registraste? Ignora este correo — no pasa nada sin tu confirmación.\n",
        "welcome_subject": "Todo listo — tu primer JobDigest llega mañana",
        "welcome_title": "Todo listo ✓",
        "welcome_body": "Tu suscripción está confirmada. Tu primer resumen seleccionado llega mañana a las 7:00. ¿Quieres afinar lo que ves?",
        "welcome_button": "Editar mis preferencias →",
        "welcome_note": "Puedes cambiar puestos, habilidades o frecuencia, pausar un tiempo o darte de baja — todo desde ese enlace, sin contraseña.",
        "welcome_preheader": "Confirmado — primer resumen mañana a las 7:00.",
        "welcome_text": "Todo listo — tu primer JobDigest llega mañana\n\nTu suscripción está confirmada. Primer resumen mañana a las 7:00.\nEditar preferencias / pausar / darse de baja: {url}\n",
        "manage_subject": "Tu enlace de ajustes de JobDigest",
        "manage_title": "Gestiona tu suscripción",
        "manage_body": "Alguien (esperamos que tú) ha pedido el enlace para gestionar tu suscripción a JobDigest. Ábrelo para cambiar puestos, habilidades o frecuencia, pausar o darte de baja — sin contraseña.",
        "manage_button": "Gestionar mis preferencias →",
        "manage_ignore": "¿No lo has pedido? Puedes ignorar este correo con tranquilidad — solo enlaza a tus propios ajustes, y nada cambia salvo que lo abras y hagas un cambio tú mismo.",
        "manage_preheader": "Tu enlace privado para gestionar tu suscripción a JobDigest.",
        "manage_text": "Tu enlace de ajustes de JobDigest\n\nAlguien ha pedido el enlace para gestionar tu suscripción a JobDigest. Ábrelo para\neditar preferencias, pausar o darte de baja — sin contraseña:\n{url}\n\n¿No lo has pedido? Ignora este correo — nada cambia salvo que lo abras.\n",
    },

    "fr": {
        "subject_new": {"one": "{n} nouveau poste", "other": "{n} nouveaux postes"},
        "subject_new_named": {"one": "{n} nouveau poste {role}", "other": "{n} nouveaux postes {role}"},
        "subject_for_you": "{count} pour vous — {when}",
        "subject_weak": "Aucune correspondance forte aujourd'hui — {count} à explorer — {when}",
        "explore_count": {"one": "{n} poste", "other": "{n} postes"},
        "good_morning": "Bonjour.",
        "weak_lead": "Aucune correspondance forte aujourd'hui",
        "weak_rest": {
            "one": "— mais en voici un qui mérite un coup d'œil, et la liste complète est sur votre page d'offres.",
            "other": "— mais en voici {n} qui méritent un coup d'œil, et la liste complète est sur votre page d'offres.",
        },
        "fresh_count": {"one": "{n} nouvelle offre", "other": "{n} nouvelles offres"},
        "fresh_rest": "aujourd'hui, classées pour vous.",
        "weak_text": {
            "one": "Bonjour. Aucune correspondance forte aujourd'hui — une plus faible à explorer ci-dessous, et la liste complète est sur votre page d'offres.",
            "other": "Bonjour. Aucune correspondance forte aujourd'hui — {n} plus faibles à explorer ci-dessous, et la liste complète est sur votre page d'offres.",
        },
        "fresh_text": {
            "one": "Bonjour. {n} nouvelle offre aujourd'hui.",
            "other": "Bonjour. {n} nouvelles offres aujourd'hui.",
        },
        "role_fallback": "Poste",
        "view_apply": "Voir et postuler →",
        "see_all_label": {"one": "Voir la {n} offre →", "other": "Voir les {n} offres →"},
        "see_all_sub": "Nous vous avons envoyé les {shown} plus fortes — le reste est sur votre page d'offres.",
        "open_matches_label": "Ouvrir votre page d'offres →",
        "open_matches_sub": "Toutes vos offres, sur une page, à tout moment.",
        "see_all_text": {"one": "Voir la {n} offre :", "other": "Voir les {n} offres :"},
        "refine": "Ajuster les préférences",
        "pause": "Mettre en pause 2 semaines",
        "unsubscribe": "Se désinscrire",
        "footer_sent": "Envoyé à {email} · Vous vous êtes inscrit sur jobdigest.eu · Un e-mail par jour.",
        "footer_sent_text": "Envoyé à {email} · Un e-mail par jour.",
        "text_preferences": "Préférences :",
        "text_unsubscribe": "Désinscription :",
        "tag_hybrid": "Hybride",
        "tag_remote": "Remote",
        "tag_freelance": "Freelance",
        "tag_part_time": "Temps partiel",
        "seniority_junior": "Junior",
        "seniority_mid": "Confirmé",
        "seniority_senior": "Senior",
        "shell_footer": "JobDigest · Un court e-mail par jour · Conçu dans l'UE",
        "link_fallback": "Si le bouton ne fonctionne pas, collez ce lien dans votre navigateur :",
        "confirm_subject": "Confirmez votre abonnement JobDigest",
        "confirm_title": "Confirmez votre abonnement",
        "confirm_body": "Un clic et votre digest quotidien est lancé. Nous enverrons chaque matin à 7:00 une courte sélection classée — uniquement des postes qui valent votre temps.",
        "confirm_button": "Confirmer et lancer mon digest →",
        "confirm_ignore": "Vous ne vous êtes pas inscrit ? Ignorez simplement cet e-mail — rien ne se passe sans votre confirmation.",
        "confirm_preheader": "Confirmez pour lancer votre digest quotidien.",
        "confirm_text": "Confirmez votre abonnement JobDigest\n\nUn clic et votre digest quotidien est lancé. Confirmez ici :\n{url}\n\nVous ne vous êtes pas inscrit ? Ignorez cet e-mail — rien ne se passe sans confirmation.\n",
        "welcome_subject": "Tout est prêt — votre premier JobDigest arrive demain",
        "welcome_title": "Tout est prêt ✓",
        "welcome_body": "Votre abonnement est confirmé. Votre premier digest arrive demain matin à 7:00. Envie d'affiner ce que vous recevez ?",
        "welcome_button": "Modifier mes préférences →",
        "welcome_note": "Vous pouvez changer de postes, de compétences ou de fréquence, faire une pause ou vous désinscrire — tout depuis ce lien, sans mot de passe.",
        "welcome_preheader": "Confirmé — premier digest demain à 7:00.",
        "welcome_text": "Tout est prêt — votre premier JobDigest arrive demain\n\nVotre abonnement est confirmé. Premier digest demain à 7:00.\nModifier / mettre en pause / se désinscrire : {url}\n",
        "manage_subject": "Votre lien de réglages JobDigest",
        "manage_title": "Gérer votre abonnement",
        "manage_body": "Quelqu'un (vous, on l'espère) a demandé le lien pour gérer votre abonnement JobDigest. Ouvrez-le pour changer de postes, de compétences ou de fréquence, faire une pause ou vous désinscrire — sans mot de passe.",
        "manage_button": "Gérer mes préférences →",
        "manage_ignore": "Vous n'avez rien demandé ? Vous pouvez ignorer cet e-mail sans risque — il ne mène qu'à vos propres réglages, et rien ne change tant que vous ne l'ouvrez pas pour modifier quelque chose vous-même.",
        "manage_preheader": "Votre lien privé pour gérer votre abonnement JobDigest.",
        "manage_text": "Votre lien de réglages JobDigest\n\nQuelqu'un a demandé le lien pour gérer votre abonnement JobDigest. Ouvrez-le pour\nmodifier vos préférences, faire une pause ou vous désinscrire — sans mot de passe :\n{url}\n\nVous n'avez rien demandé ? Ignorez cet e-mail — rien ne change tant que vous ne l'ouvrez pas.\n",
    },

    "it": {
        "subject_new": {"one": "{n} nuova posizione", "other": "{n} nuove posizioni"},
        "subject_new_named": {"one": "{n} nuova posizione {role}", "other": "{n} nuove posizioni {role}"},
        "subject_for_you": "{count} per te — {when}",
        "subject_weak": "Oggi nessuna corrispondenza forte — {count} da esplorare — {when}",
        "explore_count": {"one": "{n} posizione", "other": "{n} posizioni"},
        "good_morning": "Buongiorno.",
        "weak_lead": "Oggi nessuna corrispondenza forte",
        "weak_rest": {
            "one": "— ma ce n'è una che merita uno sguardo, e l'elenco completo è sulla tua pagina delle offerte.",
            "other": "— ma ce ne sono {n} che meritano uno sguardo, e l'elenco completo è sulla tua pagina delle offerte.",
        },
        "fresh_count": {"one": "{n} nuova offerta", "other": "{n} nuove offerte"},
        "fresh_rest": "oggi, ordinate per te.",
        "weak_text": {
            "one": "Buongiorno. Oggi nessuna corrispondenza forte — qui sotto una più debole da esplorare, e l'elenco completo è sulla tua pagina delle offerte.",
            "other": "Buongiorno. Oggi nessuna corrispondenza forte — qui sotto {n} più deboli da esplorare, e l'elenco completo è sulla tua pagina delle offerte.",
        },
        "fresh_text": {
            "one": "Buongiorno. Oggi {n} nuova offerta.",
            "other": "Buongiorno. Oggi {n} nuove offerte.",
        },
        "role_fallback": "Posizione",
        "view_apply": "Vedi e candidati →",
        "see_all_label": {"one": "Vedi {n} offerta →", "other": "Vedi tutte le {n} offerte →"},
        "see_all_sub": "Ti abbiamo inviato le {shown} più forti — il resto è sulla tua pagina delle offerte.",
        "open_matches_label": "Apri la tua pagina delle offerte →",
        "open_matches_sub": "Tutte le tue offerte, in una pagina, quando vuoi.",
        "see_all_text": {"one": "Vedi {n} offerta:", "other": "Vedi tutte le {n} offerte:"},
        "refine": "Modifica le preferenze",
        "pause": "Metti in pausa 2 settimane",
        "unsubscribe": "Disiscriviti",
        "footer_sent": "Inviato a {email} · Ti sei iscritto su jobdigest.eu · Una e-mail al giorno.",
        "footer_sent_text": "Inviato a {email} · Una e-mail al giorno.",
        "text_preferences": "Preferenze:",
        "text_unsubscribe": "Disiscrizione:",
        "tag_hybrid": "Ibrido",
        "tag_remote": "Da remoto",
        "tag_freelance": "Freelance",
        "tag_part_time": "Part-time",
        "seniority_junior": "Junior",
        "seniority_mid": "Intermedio",
        "seniority_senior": "Senior",
        "shell_footer": "JobDigest · Una breve e-mail al giorno · Fatto nell'UE",
        "link_fallback": "Se il pulsante non funziona, incolla questo link nel browser:",
        "confirm_subject": "Conferma il tuo abbonamento JobDigest",
        "confirm_title": "Conferma il tuo abbonamento",
        "confirm_body": "Un tocco e il tuo digest quotidiano è attivo. Ogni mattina alle 7:00 invieremo una breve selezione ordinata — solo posizioni che meritano il tuo tempo.",
        "confirm_button": "Conferma e avvia il digest →",
        "confirm_ignore": "Non ti sei iscritto? Ignora pure questa e-mail — senza la tua conferma non succede nulla.",
        "confirm_preheader": "Conferma per avviare il tuo digest quotidiano.",
        "confirm_text": "Conferma il tuo abbonamento JobDigest\n\nUn tocco e il tuo digest quotidiano è attivo. Conferma qui:\n{url}\n\nNon ti sei iscritto? Ignora questa e-mail — senza conferma non succede nulla.\n",
        "welcome_subject": "Tutto pronto — il tuo primo JobDigest arriva domani",
        "welcome_title": "Tutto pronto ✓",
        "welcome_body": "Il tuo abbonamento è confermato. Il primo digest arriva domani mattina alle 7:00. Vuoi mettere a punto quello che ricevi?",
        "welcome_button": "Modifica le mie preferenze →",
        "welcome_note": "Puoi cambiare posizioni, competenze o frequenza, mettere in pausa per un po' o disiscriverti — tutto da quel link, senza password.",
        "welcome_preheader": "Confermato — primo digest domani alle 7:00.",
        "welcome_text": "Tutto pronto — il tuo primo JobDigest arriva domani\n\nIl tuo abbonamento è confermato. Primo digest domani alle 7:00.\nModifica / metti in pausa / disiscriviti: {url}\n",
        "manage_subject": "Il tuo link alle impostazioni JobDigest",
        "manage_title": "Gestisci il tuo abbonamento",
        "manage_body": "Qualcuno (speriamo tu) ha chiesto il link per gestire il tuo abbonamento JobDigest. Aprilo per cambiare posizioni, competenze o frequenza, mettere in pausa o disiscriverti — senza password.",
        "manage_button": "Gestisci le mie preferenze →",
        "manage_ignore": "Non l'hai chiesto? Puoi tranquillamente ignorare questa e-mail — porta solo alle tue impostazioni e non cambia nulla finché non la apri e modifichi qualcosa tu.",
        "manage_preheader": "Il tuo link privato per gestire l'abbonamento JobDigest.",
        "manage_text": "Il tuo link alle impostazioni JobDigest\n\nQualcuno ha chiesto il link per gestire il tuo abbonamento JobDigest. Aprilo per\nmodificare le preferenze, mettere in pausa o disiscriverti — senza password:\n{url}\n\nNon l'hai chiesto? Ignora questa e-mail — non cambia nulla finché non la apri.\n",
    },
}
