"""The emails must be in the subscriber's language, and correct in it.

Two separate failures, both silent:

  - **Wrong language.** A digest built for a Czech subscriber that renders English is not an
    error anywhere — it sends, it delivers, it looks fine in the outbox. Only the person
    reading it knows. So the tests assert on rendered output for a profile, not on the
    catalogue in isolation.
  - **Wrong grammar in the right language.** `f"{n} nabídky"` renders for every n and is wrong
    for most of them. Czech and Slovak split 2–4 from 5+; Polish splits again. The plural
    categories are pinned against CLDR here because nothing downstream can notice.
"""

import pytest

from service import digest, i18n, transactional

# CLDR integer plural categories. Hand-checked against the Unicode plural rules chart; these
# are the values that make "2 nabídky / 5 nabídek" come out right.
CLDR = {
    "en": {1: "one", 0: "other", 2: "other", 5: "other", 22: "other"},
    "de": {1: "one", 0: "other", 2: "other", 5: "other"},
    "es": {1: "one", 0: "other", 2: "other", 5: "other"},
    "it": {1: "one", 0: "other", 2: "other", 5: "other"},
    # French groups 0 with 1.
    "fr": {0: "one", 1: "one", 2: "other", 5: "other"},
    # Czech/Slovak: 1 / 2-4 / 5+
    "cs": {1: "one", 2: "few", 4: "few", 5: "other", 0: "other", 11: "other"},
    "sk": {1: "one", 2: "few", 4: "few", 5: "other", 0: "other"},
    # Polish: 1 / 2-4 (but not 12-14) / everything else
    "pl": {1: "one", 2: "few", 4: "few", 22: "few", 5: "many", 12: "many", 13: "many",
           14: "many", 0: "many", 25: "many"},
}


@pytest.mark.parametrize("locale", i18n.LOCALES)
def test_plural_categories_match_cldr(locale):
    for n, expected in CLDR[locale].items():
        assert i18n.plural_category(locale, n) == expected, (
            f"{locale}: {n} should be {expected!r}, got {i18n.plural_category(locale, n)!r}"
        )


@pytest.mark.parametrize("locale", i18n.LOCALES)
def test_every_plural_entry_has_the_forms_its_language_selects(locale):
    """`plural()` falls back to `other`, so a missing `few` renders the 5+ form for 2–4 —
    grammatically wrong, and invisible because something still renders."""
    needed = {i18n.plural_category(locale, n) for n in (1, 2, 5, 22)}
    for key, value in i18n.MESSAGES[locale].items():
        if not isinstance(value, dict):
            continue
        missing = needed - set(value)
        assert not missing, f"{locale}.{key} is missing plural form(s) {sorted(missing)}"


@pytest.mark.parametrize("locale", i18n.LOCALES)
def test_no_language_is_missing_a_key_english_has(locale):
    missing = set(i18n.MESSAGES["en"]) - set(i18n.MESSAGES[locale])
    assert not missing, f"{locale} falls back to English for: {sorted(missing)}"


@pytest.mark.parametrize("locale", i18n.LOCALES)
def test_placeholders_survive_translation(locale):
    """A translator dropping `{url}` produces an email with no link in it and no error."""
    import re
    for key, value in i18n.MESSAGES["en"].items():
        forms = value if isinstance(value, dict) else {"other": value}
        expected = set()
        for template in forms.values():
            expected |= set(re.findall(r"\{(\w+)\}", template))
        got = set()
        translated = i18n.MESSAGES[locale][key]
        for template in (translated if isinstance(translated, dict) else {"x": translated}).values():
            got |= set(re.findall(r"\{(\w+)\}", template))
        assert expected == got, f"{locale}.{key} placeholders {sorted(got)} != {sorted(expected)}"


@pytest.mark.parametrize("raw,expected", [
    ("cs", "cs"), ("CS", "cs"), (" cs ", "cs"), ("cs-CZ", "cs"), ("de-AT", "de"),
    ("nl", "en"), ("", "en"), (None, "en"), ("gibberish", "en"), (123, "en"),
])
def test_clean_locale_never_produces_a_language_we_cannot_render(raw, expected):
    """This runs inside the send loop. A stale or hostile column value must degrade to
    English, never raise — refusing to send a digest over a presentation detail turns a
    cosmetic problem into a delivery failure."""
    assert i18n.clean_locale(raw) == expected


# ------------------------------------------------------------- rendered output ---

def _profile(lang: str) -> dict:
    return {"id": "p1", "email": "someone@example.com", "manage_token": "TOK",
            "language": lang, "role_categories": ["data_analysis"]}


def _jobs(n: int, score: int = 8) -> list[dict]:
    return [{"posting_id": f"j{i}", "title": "Data Analyst", "company": "Acme",
             "url": "https://example.com/j", "score": score, "summary": "why it fits",
             "role_category": "data_analysis", "seniority": "junior",
             "work_mode": "hybrid", "region": "cz"} for i in range(n)]


@pytest.mark.parametrize("locale", i18n.LOCALES)
def test_the_digest_renders_in_the_subscribers_language(locale):
    profile, jobs = _profile(locale), _jobs(3)
    subject = digest.subject_line(profile, jobs)
    text = digest.render_text(profile, jobs)
    html_body = digest.render_html(profile, jobs)

    assert i18n.t(locale, "good_morning") in text
    assert i18n.t(locale, "good_morning") in html_body
    # The footer actions, which are what a subscriber actually clicks.
    assert i18n.t(locale, "unsubscribe") in html_body
    assert subject, "empty subject"
    if locale != "en":
        # The single strongest signal that nothing fell through to English.
        assert "Good morning." not in text
        assert "Good morning." not in html_body


@pytest.mark.parametrize("locale", i18n.LOCALES)
def test_digest_links_point_at_the_subscribers_language(locale, monkeypatch):
    monkeypatch.setenv("SITE_URL", "https://jobdigest.eu")
    html_body = digest.render_html(_profile(locale), _jobs(2))
    assert f"https://jobdigest.eu/{locale}/preferences/" in html_body, (
        "the preferences link does not land on the language the email is written in"
    )


@pytest.mark.parametrize("locale", i18n.LOCALES)
@pytest.mark.parametrize("render", ["confirm", "welcome", "manage_link"])
def test_transactional_emails_render_in_the_subscribers_language(locale, render):
    fn = getattr(transactional, f"render_{render}")
    subject, html_body, text = fn("someone@example.com", "https://jobdigest.eu/x", locale)
    assert subject == i18n.t(locale, f"{render.replace('_link', '')}_subject")
    assert f'<html lang="{locale}"' in html_body
    assert "https://jobdigest.eu/x" in text
    if locale != "en":
        assert i18n.t("en", f"{render.replace('_link', '')}_title") not in html_body


def test_a_quiet_day_says_so_in_every_language():
    """The weak-digest wording is the one most likely to be left untranslated: it only
    renders on days when nothing cleared EMAIL_MIN_SCORE."""
    for locale in i18n.LOCALES:
        weak = _jobs(2, score=digest.EMAIL_MIN_SCORE - 2)
        subject = digest.subject_line(_profile(locale), weak)
        assert i18n.t(locale, "weak_lead").split(" —")[0][:12] in subject or \
            i18n.t(locale, "subject_weak").split("{")[0][:12] in subject, (
                f"{locale}: quiet-day subject did not use the translated wording: {subject!r}"
            )


@pytest.mark.parametrize("locale", i18n.LOCALES)
def test_the_subject_date_is_not_english_in_other_languages(locale):
    """`strftime("%b")` renders in the C locale, so every language got an English month —
    "3 nové nabídky pro vás — 2 Aug". Caught in production output, not by a unit test, which
    is why there is one now."""
    import datetime as dt
    rendered = i18n.format_date(locale, dt.datetime(2026, 8, 2))
    if locale != "en":
        assert "Aug" not in rendered, f"{locale} date still uses an English month: {rendered!r}"
    assert "2" in rendered and "8" in rendered or "Aug" in rendered


@pytest.mark.parametrize("locale", ["cs", "sk", "pl"])
@pytest.mark.parametrize("n", [1, 3, 7])
def test_a_named_subject_does_not_mismatch_adjective_case(locale, n):
    """Czech/Slovak/Polish inflect the counted noun with the plural category, and an adjective
    has to agree with it — "3 nové datové nabídky" but "5 nových datových nabídek". A single
    `SUBJECT_WORDS` string therefore cannot be an adjective: it was, and produced
    "3 nové datových nabídky", which is wrong in a way only a native reader notices.

    The role word is now a case-neutral noun in apposition, so this asserts it stays one.
    """
    word = i18n.subject_word(locale, "data_analysis")
    rendered = i18n.plural(locale, "subject_new_named", n, role=word)
    assert "(" in rendered and ")" in rendered, (
        f"{locale} named subject no longer isolates the role word: {rendered!r} — an inline "
        "adjective cannot agree across all plural categories"
    )
    # The genitive-plural adjective endings that were wrong at n=1..4.
    assert not word.endswith(("ových", "ářských", "árskych", "ých")), (
        f"{locale} SUBJECT_WORDS['data_analysis'] is an inflected adjective ({word!r}); "
        "it must be a case-neutral noun"
    )


def test_an_unknown_stored_language_still_sends_in_english():
    """`profiles.language` is checked only for shape in SQL. A row holding something the
    catalogue does not know must produce an English digest, not an exception inside the
    send loop."""
    profile = {**_profile("en"), "language": "nl"}
    assert "Good morning." in digest.render_text(profile, _jobs(2))
