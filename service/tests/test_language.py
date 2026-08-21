"""The language detector and its guards — no database needed.

`service/language.py` is the one definition of "what language is this ad written in", and the
axis is only as safe as its two guards: a minimum text length and a confidence floor. These
tests pin that a full description in each language resolves to its code, that short or mixed text
resolves to ``None`` (the safe, keep-the-posting answer), and that the preference/predicate
helpers behave. Built from realistic ad prose, the same way `test_experience.py` is built from
real postings — see that file's note on why the fixtures are real text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from service import language

ROOT = Path(__file__).resolve().parents[2]
WEB_LANG = ROOT / "web" / "lib" / "language.ts"

# Full descriptions, each comfortably over `MIN_CHARS`. Kept ASCII-foldable where the language
# allows so the file stays portable; the detector reads the words, not the diacritics.
DESCRIPTIONS = {
    "en": ("We are looking for a Senior Data Engineer to build and maintain our dbt and "
           "Snowflake pipelines. You will work with analysts across the whole company on data "
           "quality, modelling and reporting, and help us scale our modern data stack."),
    "cs": ("Hledáme datového analytika do našeho týmu. Budete pracovat s daty z e-commerce a "
           "připravovat reporty pro management. Vyžadujeme znalost SQL a Pythonu a schopnost "
           "prezentovat výsledky. Nabízíme zajímavou práci v rostoucí firmě a dobré podmínky."),
    "de": ("Wir suchen einen erfahrenen Data Engineer fuer den Aufbau und die Wartung unserer "
           "Datenpipelines. Sie arbeiten mit modernen Cloud-Technologien und unterstuetzen "
           "unsere Analysten bei der Modellierung und Aufbereitung von Daten fuer das Team."),
    "ru": ("Мы ищем опытного аналитика данных для работы с большими массивами информации. "
           "Требуется опыт работы с SQL и Python, знание статистики и умение строить дашборды "
           "и отчёты для руководства. Вы будете тесно сотрудничать с командой разработки."),
    "pl": ("Poszukujemy analityka danych do naszego zespolu. Bedziesz pracowac z danymi z "
           "e-commerce i przygotowywac raporty dla zarzadu. Wymagamy znajomosci SQL oraz "
           "Pythona, a takze umiejetnosci prezentacji wynikow i pracy w zespole projektowym."),
}


@pytest.mark.parametrize("code, text", DESCRIPTIONS.items())
def test_a_full_description_resolves_to_its_language(code, text):
    assert language.detect(text) == code


def test_short_text_is_unknown_not_a_guess():
    """A bare title is not enough to refuse a job on — under `MIN_CHARS` the answer is None,
    so the posting passes the gate and the matcher judges it."""
    assert language.detect("Data Engineer") is None
    assert language.detect("Datovy analytik Praha") is None
    assert language.detect("", "Senior Developer") is None


def test_empty_and_none_are_unknown():
    assert language.detect(None) is None
    assert language.detect("") is None
    assert language.detect(None, None) is None


def test_markup_is_stripped_before_detection():
    """Descriptions arrive as HTML soup; the tags and URLs must not steer the verdict."""
    html = f"<p>{DESCRIPTIONS['en']}</p><ul><li>SQL</li></ul><a href='https://x.co'>apply</a>"
    assert language.detect(html) == "en"


def test_title_is_folded_into_the_text():
    """A short foreign title on a long English body still reads as English — the description
    dominates, which is the right call (English body = readable)."""
    assert language.detect(DESCRIPTIONS["en"], "Vyvojar") == "en"


def test_an_out_of_set_code_is_never_returned():
    """`detect` only ever returns a code in `LANGUAGES`; the restricted model cannot emit one
    outside it, and the guard makes that explicit."""
    for text in DESCRIPTIONS.values():
        result = language.detect(text)
        assert result is None or result in language.LANGUAGES


# --------------------------------------------------------- preference helpers ---

def test_clean_languages_keeps_known_codes_in_canonical_order():
    assert language.clean_languages(["CS", "en", " ru "]) == ["en", "cs", "ru"]


def test_clean_languages_drops_unknown_and_empties():
    assert language.clean_languages(["zz", "xx"]) == []
    assert language.clean_languages([]) == []
    assert language.clean_languages(None) == []


def test_describe_is_none_when_empty_and_a_sentence_otherwise():
    assert language.describe([]) is None
    sentence = language.describe(["cs", "en"])
    assert sentence is not None
    assert "English" in sentence and "Czech" in sentence


# ------------------------------------------------------------ the SQL fragment ---

def test_predicate_widens_on_silence():
    assert language.language_predicate({}) == ("true", [])
    assert language.language_predicate({"understood_languages": []}) == ("true", [])
    assert language.language_predicate({"understood_languages": ["zz"]}) == ("true", [])


def test_predicate_keeps_null_and_english_and_the_declared_set():
    sql, params = language.language_predicate({"understood_languages": ["cs"]})
    assert "is null" in sql
    assert language.ALWAYS_READABLE in params           # English is always admitted
    assert ["cs"] in params


# ------------------------------------------------------------------ web drift ---

def _ts_object(name: str) -> dict:
    """Pull a `const NAME: ... = {...};` object out of language.ts (written as JSON)."""
    text = WEB_LANG.read_text(encoding="utf-8")
    match = re.search(rf"export const {name}[^=]*=\s*(\{{.*?\n\}});", text, re.S)
    assert match, f"{name} not found in {WEB_LANG.name}"
    return json.loads(match.group(1))


def test_frontend_language_ids_match_the_python_definition():
    """Order and content: the chips render in this order and these codes are what the form
    POSTs; a code the API does not know is rejected by `_check_understood_languages`, so drift
    here is a preferences page that cannot save."""
    text = WEB_LANG.read_text(encoding="utf-8")
    match = re.search(r"export const LANGUAGE_IDS = \[(.*?)\]", text, re.S)
    assert match, "LANGUAGE_IDS not found in web/lib/language.ts"
    assert tuple(re.findall(r'"([a-z]+)"', match.group(1))) == language.LANGUAGES


def test_frontend_labels_match_the_python_definition():
    assert _ts_object("LANGUAGE_LABEL") == language.LANGUAGE_LABELS, (
        "web/lib/language.ts and service/language.py disagree about a language's English name."
    )


def test_every_language_has_an_english_label_and_an_endonym():
    """A missing entry in a `Record<string, string>` is invisible to TypeScript — only this
    catches a code with no label to render."""
    assert set(language.LANGUAGE_LABELS) == set(language.LANGUAGES)
    endonyms = _ts_object("LANGUAGE_ENDONYM")
    assert set(endonyms) == set(language.LANGUAGES), (
        "web/lib/language.ts LANGUAGE_ENDONYM is missing or has an extra language code."
    )
