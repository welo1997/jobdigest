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
import unicodedata
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


def _blocks(text: str, name: str) -> list[str]:
    """Every object literal at `name:`, not just the first.

    Plural sets share names across sections — `matches` and `hidden` both carry a
    `countTitle` and a `showing`. Checking only the first occurrence would leave the second
    unchecked, which is precisely the silent gap these tests exist to close.
    """
    out: list[str] = []
    for match in re.finditer(rf"(?:^|[\s{{,])({re.escape(name)})\s*:\s*\{{", text):
        start = text.index("{", match.end(1))
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append(text[start + 1:i])
                    break
        else:
            raise AssertionError(f"unbalanced braces in block {name!r}")
    assert out, f"block {name!r} not found"
    return out


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
    """The string ids of an `export const NAME = [...]` array in web/lib/options.ts.

    `[a-z_]+`, not `[a-z]+`: `SENIORITY_IDS` gained `entry_level` on 2026-08-12 and the
    narrower pattern silently skipped it, which would have left the catalogue-completeness
    test below passing over a chip no catalogue had a label for — a drift check that cannot
    see the drift is worse than none, because it reports coverage it never had.
    """
    match = re.search(rf"export const {name} = \[(.*?)\]", OPTIONS_TEXT, re.S)
    assert match, f"{name} not found in web/lib/options.ts"
    ids = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert ids, f"{name} parsed as empty — the array shape in options.ts has changed"
    return ids


ROLE_IDS = set(re.findall(r'id:\s*"([a-z_]+)"', OPTIONS_TEXT))
WORK_TYPE_IDS = _id_list("WORK_TYPE_IDS")
SENIORITY_IDS = _id_list("SENIORITY_IDS")

# The English search word each chip contributes when no category models it — the third spelling
# `resolveRoleId` accepts, and the one that works in every language, so it belongs in the
# collision check alongside the id and the translated label.
ROLE_KEYWORDS = dict(
    re.findall(r'id:\s*"([a-z_]+)"\s*,\s*category:[^,]+,\s*keyword:\s*"([^"]+)"', OPTIONS_TEXT)
)


def _role_labels(locale: str) -> dict[str, str]:
    """`roles` in one catalogue, as id → rendered label."""
    return dict(re.findall(r'"?([a-z_]+)"?\s*:\s*"([^"]*)"', _block(_catalogue(locale), "roles")))


def _normalize_role_text(text: str) -> str:
    """Python mirror of `normalizeRoleText` in `web/lib/options.ts`.

    A copy, and knowingly so — pytest cannot run the TypeScript. What keeps it honest is
    `test_the_typed_role_resolver_still_folds_case_and_accents` below, which fails if the
    original stops applying any of the four folds this reimplements.
    """
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    spaced = re.sub(r"[_\-./]+", " ", stripped.lower())
    return re.sub(r"\s+", " ", spaced).strip()


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


def test_the_root_redirect_runs_before_the_language_gate_is_painted():
    """`/` sends a visitor to their language twice over, and the order is the whole point.

    `app/(plain)/page.tsx` negotiates in a `useEffect`, which cannot run until React has
    hydrated — so before 2026-08-12 the bundle had to download and execute before anyone was
    forwarded, and "Choose your language" flashed on screen for about a second on every visit
    to the bare domain. The fix is a synchronous inline script emitted *above* the markup, so
    the parser redirects before it reaches the card.

    Move that `<script>` below `{children}` and the flash comes back with nothing failing —
    the redirect still works, it is just slower than the paint. That is what this pins.
    """
    layout = (ROOT / "web" / "app" / "(plain)" / "layout.tsx").read_text(encoding="utf-8")

    script = layout.find("__html: LOCALE_REDIRECT")
    children = layout.find("{children}")
    assert script > 0, "the inline locale redirect is gone from the plain layout"
    assert children > 0, "{children} not found — the layout was restructured"
    assert script < children, (
        "the locale redirect is emitted after {children}, so the language gate paints first "
        "— that is the flash this script exists to remove"
    )

    # It also has to be inert on the other routes this layout serves (/privacy, /terms, /v2).
    assert 'location.pathname!=="/"' in layout, (
        "the redirect is not guarded on the root path — it would fire on /privacy and /terms"
    )

    # And the locale list must be interpolated, never typed out again: a ninth language is
    # added to LOCALES, and nothing else should need to know.
    assert "JSON.stringify(LOCALES)" in layout, (
        "the redirect hardcodes its locale list instead of reading i18n/config"
    )
    for locale in LOCALES:
        assert f'"{locale}"' not in layout.split("const LOCALE_REDIRECT")[1].split("`;")[0], (
            f"{locale} is written literally into the redirect script — that is the copy that "
            "drifts when a language is added"
        )


@pytest.mark.parametrize("locale", LOCALES)
def test_every_role_chip_has_a_label(locale):
    """A missing id renders as `product_manager` in the UI, with nothing failing."""
    missing = ROLE_IDS - _keys(_block(_catalogue(locale), "roles"))
    assert not missing, f"{locale}.ts is missing role labels for: {sorted(missing)}"


@pytest.mark.parametrize("locale", LOCALES)
def test_no_two_roles_share_a_spelling_in_any_language(locale):
    """`resolveRoleId` turns typed text into a role id, and it returns the **first** match.

    That is what lets someone type "Data Engineer" into "Add another role…" and get the
    `data_engineer` chip — its skills and its `role_categories` entry — instead of a raw string
    that reaches only the keyword path. It resolves three spellings per role: the id, the
    English `keyword`, and the label in the visitor's own language, so a Czech visitor typing
    "Datový analytik" stores exactly what an English visitor tapping the chip stores.

    The hazard is a *collision*. If two roles normalise to the same text in some language, the
    earlier one in `ROLE_OPTIONS` wins and the later one becomes unreachable by typing — in
    that language only. Nothing fails: the visitor gets a real chip with a real category, just
    the wrong one, and it renders in the language they are reading so it looks deliberate. That
    is the same shape as the label-keyed `ROLE_CAT` bug this whole file exists because of, and
    it can arrive from a translation alone, with no code change.

    Marketing and Finance are the pair to watch: both map to `other_tech_function`, so a
    catalogue that rendered either as its category name would collide.
    """
    labels = _role_labels(locale)
    seen: dict[str, tuple[str, str]] = {}
    for role_id in sorted(ROLE_IDS):
        for kind, spelling in (
            ("id", role_id),
            ("keyword", ROLE_KEYWORDS.get(role_id, "")),
            ("label", labels.get(role_id, "")),
        ):
            key = _normalize_role_text(spelling)
            if not key:
                continue
            if key in seen and seen[key][0] != role_id:
                other_id, other_kind = seen[key]
                raise AssertionError(
                    f"{locale}.ts: {role_id}'s {kind} and {other_id}'s {other_kind} both "
                    f"normalise to {key!r} — typing it resolves to {other_id} "
                    f"(first in ROLE_OPTIONS), so {role_id} cannot be reached by typing"
                )
            seen.setdefault(key, (role_id, kind))


def test_the_typed_role_resolver_still_folds_case_and_accents():
    """A guard on the mirror above, not on the browser.

    `_normalize_role_text` reimplements `normalizeRoleText` in Python so the collision test can
    run at all, and a copy is exactly what goes stale: soften the TypeScript to compare raw
    strings and the collision test above keeps passing against rules the site no longer applies.
    Assert the four folds are still in the source — the accent strip is the one that carries
    Czech, Slovak and Polish, and is the one a "simplify this regex" edit would drop.
    """
    body = re.search(
        r"export const normalizeRoleText[^;]+;", OPTIONS_TEXT, re.S
    )
    assert body, "normalizeRoleText not found in web/lib/options.ts"
    src = body.group(0)
    for fold, why in (
        ('normalize("NFD")', "decomposition, without which the accent strip cannot match"),
        # Asserted as the escape sequence, because that is what the file holds — the TS writes
        # the combining range as `̀-ͯ` rather than as the (invisible) characters
        # themselves. Searching for the decoded form finds nothing and fails on a correct file.
        (r"\u0300-\u036f", "the combining-mark strip that folds Czech/Slovak/Polish accents"),
        ("toLowerCase()", "case folding"),
        (r"\s+", "whitespace collapsing"),
    ):
        assert fold in src, (
            f"normalizeRoleText no longer applies {fold} ({why}) — "
            "service/tests/test_web_i18n.py's Python mirror is now wrong"
        )


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
PLURAL_FIELDS = ["roleCount", "roleCountNamed", "freshMatches", "countTitle", "showing",
                 "selected", "hiddenLink"]
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
        for i, body in enumerate(_blocks(text, field)):
            forms = _keys(body)
            where = f"{locale}.ts {field}" + (f" (#{i + 1})" if i else "")
            assert "other" in forms, (
                f"{where} has no `other` form — it is the fallback every other "
                "category degrades to, and the type requires it for that reason."
            )
            missing = required - forms
            assert not missing, (
                f"{where} is missing plural form(s) {sorted(missing)}; "
                f"Intl.PluralRules will select one of them and fall back to `other`."
            )
