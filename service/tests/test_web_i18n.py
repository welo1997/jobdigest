"""The eight language catalogues must be complete, not merely well-typed.

`web/i18n/schema.ts` makes a missing *section* a TypeScript error, so no catalogue can ship
without a `landing` or a `matches` block. What it cannot check is the inside of the lookup
tables: `roles`, `geo.countries` and friends are `Record<string, string>`, because the chip
ids they answer for include whatever a subscriber types into "Add another role…". A
`Record` with open keys is a `Record` that is never incomplete.

So the gap is real and silent in exactly the way this repo keeps getting bitten by. A role id
missing from `cs.ts` renders the raw id — a Czech visitor sees a chip labelled
`product_manager` — and nothing fails: not the build, not the type checker, not the page.
A country missing from `de.ts` quietly falls back to the English name mid-list.

These tests read the TypeScript as text, the same approach and for the same reason as
`test_geo.py` and `test_taxonomy.py`: the browser cannot import Python and pytest cannot
import TSX, so the agreement between them is asserted on the files themselves.
"""

import re
from pathlib import Path

import pytest

from service import geo

ROOT = Path(__file__).resolve().parents[2]
I18N = ROOT / "web" / "i18n"
CONFIG = I18N / "config.ts"
MESSAGES = I18N / "messages"
OPTIONS = ROOT / "web" / "lib" / "options.ts"


# ------------------------------------------------------------------ parsing ------

def _block(text: str, name: str) -> str:
    """Body of the object literal at `name:`, matched by counting braces.

    Brace counting rather than a lazy regex because these objects nest — `geo` contains four
    tables and `preview` contains four plural sets, and `.*?\\}` would stop at the first one.
    """
    match = re.search(rf"(?:^|[\s{{,])({re.escape(name)})\s*:\s*\{{", text)
    assert match, f"block {name!r} not found"
    start = text.index("{", match.end(1))
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
    raise AssertionError(f"unbalanced braces in block {name!r}")


def _keys(body: str) -> set[str]:
    """Keys of a flat object literal, quoted or bare."""
    return set(re.findall(r'(?:^|[{,])\s*"?([A-Za-z_][A-Za-z0-9_]*)"?\s*:', body, re.M))


def _catalogue(locale: str) -> str:
    path = MESSAGES / f"{locale}.ts"
    assert path.exists(), f"no catalogue for locale {locale!r} — expected {path}"
    return path.read_text(encoding="utf-8")


def _locales() -> list[str]:
    match = re.search(r"export const LOCALES = \[(.*?)\]", CONFIG.read_text(encoding="utf-8"), re.S)
    assert match, "LOCALES not found in web/i18n/config.ts"
    return re.findall(r'"([a-z]{2})"', match.group(1))


LOCALES = _locales()
OPTIONS_TEXT = OPTIONS.read_text(encoding="utf-8")


def _id_list(name: str) -> set[str]:
    match = re.search(rf"export const {name} = \[(.*?)\]", OPTIONS_TEXT, re.S)
    assert match, f"{name} not found in web/lib/options.ts"
    return set(re.findall(r'"([a-z]+)"', match.group(1)))


ROLE_IDS = set(re.findall(r'id:\s*"([a-z_]+)"', OPTIONS_TEXT))
WORK_TYPE_IDS = _id_list("WORK_TYPE_IDS")
SENIORITY_IDS = _id_list("SENIORITY_IDS")


def test_every_declared_locale_has_a_catalogue_and_is_wired_up():
    assert len(LOCALES) >= 2, "LOCALES parsed as fewer than two languages — the parse is broken"
    index = (I18N / "index.ts").read_text(encoding="utf-8")
    # Declared with a type annotation before the `=`, so this one is not an object literal
    # sitting directly behind its name and `_block` cannot find it.
    catalogues = re.search(r"const CATALOGUES[^=]*=\s*\{(.*?)\};", index, re.S)
    assert catalogues, "CATALOGUES map not found in web/i18n/index.ts"
    mapped = {entry.strip() for entry in catalogues.group(1).split(",")}
    for locale in LOCALES:
        _catalogue(locale)
        # Declared but not imported into CATALOGUES is a locale whose URLs are exported and
        # whose pages then render in English.
        assert re.search(rf"^import {locale} from \"./messages/{locale}\";", index, re.M), (
            f"{locale} is in LOCALES but not imported by web/i18n/index.ts"
        )
        assert locale in mapped, f"{locale} is imported but missing from the CATALOGUES map"


@pytest.mark.parametrize("locale", LOCALES)
def test_every_role_chip_has_a_label(locale):
    """A missing id renders as `product_manager` in the UI, with nothing failing."""
    missing = ROLE_IDS - _keys(_block(_catalogue(locale), "roles"))
    assert not missing, f"{locale}.ts is missing role labels for: {sorted(missing)}"


@pytest.mark.parametrize("locale", LOCALES)
def test_work_type_and_seniority_chips_have_labels(locale):
    text = _catalogue(locale)
    assert not WORK_TYPE_IDS - _keys(_block(text, "workTypes")), (
        f"{locale}.ts is missing a work-type label"
    )
    assert not SENIORITY_IDS - _keys(_block(text, "seniorities")), (
        f"{locale}.ts is missing a seniority label"
    )


@pytest.mark.parametrize("locale", LOCALES)
def test_work_modes_and_remote_scopes_are_fully_covered(locale):
    """These drive the location gate's controls. A missing one renders a blank <option>."""
    geo_block = _block(_catalogue(locale), "geo")
    for name in ("workModeLabel", "workModeHint"):
        missing = set(geo.WORK_MODES) - _keys(_block(geo_block, name))
        assert not missing, f"{locale}.ts geo.{name} is missing: {sorted(missing)}"
    missing = set(geo.REMOTE_SCOPES) - _keys(_block(geo_block, "remoteScope"))
    assert not missing, f"{locale}.ts geo.remoteScope is missing: {sorted(missing)}"


@pytest.mark.parametrize("locale", [loc for loc in LOCALES if loc != "en"])
def test_every_selectable_country_is_translated(locale):
    """English is exempt and deliberately empty: its country names live in `web/lib/geo.ts`,
    which `test_geo.py` pins against `service/geo.py`. Restating them in `en.ts` would create
    a second English spelling that no drift test can see."""
    present = _keys(_block(_block(_catalogue(locale), "geo"), "countries"))
    missing = set(geo.COUNTRIES) - present
    assert not missing, f"{locale}.ts geo.countries is missing: {sorted(missing)}"
    unknown = present - set(geo.COUNTRIES)
    assert not unknown, f"{locale}.ts geo.countries names non-selectable countries: {sorted(unknown)}"


def test_english_country_overlay_stays_empty():
    assert not _keys(_block(_block(_catalogue("en"), "geo"), "countries")), (
        "en.ts now overrides country names, so web/lib/geo.ts is no longer the single English "
        "spelling and test_geo.py can no longer see drift in what the site actually renders."
    )


# Plural sets, and the CLDR categories each language genuinely needs. Czech and Slovak split
# 2–4 from 5+; Polish splits again at 5 and needs a fraction form as well. Getting this wrong
# does not fail anything — it just renders "5 nové nabídky", which is broken Czech.
PLURAL_FIELDS = ["roleCount", "roleCountNamed", "freshMatches", "countTitle"]
REQUIRED_FORMS = {
    "cs": {"one", "few", "other"},
    "sk": {"one", "few", "other"},
    "pl": {"one", "few", "many", "other"},
}


@pytest.mark.parametrize("locale", LOCALES)
def test_plural_sets_carry_every_form_the_language_needs(locale):
    text = _catalogue(locale)
    required = REQUIRED_FORMS.get(locale, {"one", "other"})
    for field in PLURAL_FIELDS:
        forms = _keys(_block(text, field))
        assert "other" in forms, (
            f"{locale}.ts {field} has no `other` form — it is the fallback every other "
            "category degrades to, and the type requires it for that reason."
        )
        missing = required - forms
        assert not missing, (
            f"{locale}.ts {field} is missing plural form(s) {sorted(missing)}; "
            f"Intl.PluralRules will select one of them and fall back to `other`."
        )
