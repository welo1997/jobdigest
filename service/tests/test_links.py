"""Every frontend link must name a language, and the old ones must keep working.

The site is exported once per locale, so `/preferences/` stopped being a page the moment the
site gained languages — `/en/preferences/` is the page. Two separate promises follow, and
breaking either is silent:

  - **New links must be prefixed.** A link built by concatenating onto `site_url()` renders
    fine, sends fine, and 404s in the subscriber's browser a day later. Nothing on this side
    ever learns that the person who wanted to change their preferences couldn't.
  - **Old links must not break.** Every digest, confirmation and magic-link email ever sent
    carries an un-prefixed path, and delivered mail cannot be rewritten. The redirect in
    `web/Caddyfile` is what keeps those alive; this file pins that it is still there, because
    it is one line in a config nothing else tests and its absence looks like nothing at all.
"""

import re
from pathlib import Path

import pytest

from service import links

ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = ROOT / "web" / "Caddyfile"
LOCALES_TS = ROOT / "web" / "i18n" / "config.ts"

# The paths that appear in already-sent mail, in the OAuth callback, or in a bookmark.
LEGACY_PATHS = ["/preferences", "/matches", "/manage", "/check-inbox", "/privacy", "/terms"]


def _caddy_directives() -> str:
    """The Caddyfile with comment lines removed.

    Matching against the raw file is a trap this test already fell into: the comment
    explaining *why* `/404.html` is not in the try_files chain contains the words
    "try_files chain", so a bare `re.search(r"try_files ...")` matched the prose and the
    assertion passed no matter what the real directive said. Parse the config, not the
    commentary about it.
    """
    return "\n".join(
        line for line in CADDYFILE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


@pytest.fixture(autouse=True)
def _site(monkeypatch):
    monkeypatch.setenv("SITE_URL", "https://jobdigest.eu")


def test_site_locale_is_a_locale_the_frontend_actually_exports():
    """`SITE_LOCALE=xx` would point every email at a language that was never built."""
    match = re.search(r"export const LOCALES = \[(.*?)\]", LOCALES_TS.read_text(encoding="utf-8"), re.S)
    assert match, "LOCALES not found in web/i18n/config.ts"
    exported = set(re.findall(r'"([a-z]{2})"', match.group(1)))
    assert links.SITE_LOCALE in exported, (
        f"SITE_LOCALE={links.SITE_LOCALE!r} is not exported by the frontend; "
        f"available: {sorted(exported)}"
    )


@pytest.mark.parametrize("build,expected", [
    (lambda: links.preferences_link("tok"), "/en/preferences/?token=tok"),
    (lambda: links.matches_link("tok"), "/en/matches/?token=tok"),
    (lambda: links.site_page("manage"), "/en/manage/"),
    (lambda: links.site_page(), "/en/"),
])
def test_frontend_links_are_language_prefixed(build, expected):
    assert build() == f"https://jobdigest.eu{expected}"


def test_no_frontend_link_is_built_by_concatenating_onto_site_url():
    """`f"{site_url()}/preferences"` is the shape that breaks. `site_url()` is legitimate on
    its own — a bare link to the homepage is fine, the language gate negotiates from there —
    so what is banned is a *path* glued onto it."""
    offenders = []
    for path in sorted((ROOT / "service").glob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"(SITE_URL|site_url\(\))\s*\}?/[a-z]", line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, (
        "frontend URLs must be built with links.site_page(), which adds the language "
        "prefix:\n  " + "\n  ".join(offenders)
    )


def test_caddy_still_redirects_the_paths_that_are_already_in_subscribers_inboxes():
    conf = _caddy_directives()
    matcher = re.search(r"@legacy path ([^\n]+)", conf)
    assert matcher, (
        "web/Caddyfile has no @legacy path matcher — every magic link in every email already "
        "sent now 404s, and delivered mail cannot be rewritten."
    )
    covered = set(matcher.group(1).split())
    missing = [p for p in LEGACY_PATHS if p not in covered]
    assert not missing, f"legacy redirect no longer covers: {missing}"

    redir = re.search(r"redir @legacy (\S+) (\d+)", conf)
    assert redir, "@legacy is matched but nothing redirects it"
    target, code = redir.group(1), redir.group(2)
    assert target == f"/{links.SITE_LOCALE}{{uri}}", (
        f"legacy redirect target {target!r} does not send to the emailed language, or drops "
        "{uri} — which is what carries the one-time token through the hop."
    )
    assert code == "301", "the old paths are permanently moved; anything else re-asks forever"


def test_unknown_urls_get_a_real_404_not_a_soft_one():
    """`try_files … /404.html` serves the not-found page with HTTP 200.

    That is a soft 404: the page says Not Found and the status says "this is a real document,
    index it". Search engines take the status. With eight locales there are now 8× as many
    plausible-looking URLs to get this wrong on, and nothing about it is visible from the
    browser — you only find it in a crawl report months later.
    """
    conf = _caddy_directives()

    static = re.search(r"^\s*try_files ([^\n]+)", conf, re.M)
    assert static, "no try_files directive found"
    assert "/404.html" not in static.group(1), (
        "404.html is back in the try_files chain, so every unknown URL is served as HTTP 200"
    )

    errors = re.search(r"handle_errors \{(.*?)\n\t\}", conf, re.S)
    assert errors, "no handle_errors block — unknown URLs fall through to Caddy's bare error"
    body = errors.group(1)
    assert "/404.html" in body, "handle_errors does not render the 404 page"
    assert "status {err.status_code}" in body, (
        "handle_errors renders 404.html but file_server answers 200 without an explicit "
        "status — the soft 404 is intact, just moved."
    )


def _ts_string_list(text: str, name: str) -> list[str]:
    match = re.search(rf"export const {name} = \[(.*?)\]", text, re.S)
    assert match, f"{name} not found"
    return re.findall(r'"([a-z]{2})"', match.group(1))


def test_legal_locales_are_locales_the_site_exports():
    """A legal page in a language the site does not build is a link to nothing."""
    conf = LOCALES_TS.read_text(encoding="utf-8")
    legal = _ts_string_list(conf, "LEGAL_LOCALES")
    assert legal, "LEGAL_LOCALES is empty — every legal link would 404"
    unknown = set(legal) - set(_ts_string_list(conf, "LOCALES"))
    assert not unknown, f"LEGAL_LOCALES names languages the site does not export: {sorted(unknown)}"


@pytest.mark.parametrize("page", ["privacy", "terms"])
def test_legal_pages_build_exactly_the_legal_locales(page):
    """The parent layout builds all eight locales; these two routes must build the subset.

    Returning `LOCALES` here would export `/de/privacy/` rendering English under a German
    `lang` attribute — worse than the fallback, because it looks translated and is not.
    """
    src = (ROOT / "web" / "app" / "(site)" / "[locale]" / page / "page.tsx").read_text(encoding="utf-8")
    params = re.search(r"generateStaticParams\(\)\s*\{(.*?)\n\}", src, re.S)
    assert params, f"{page}/page.tsx has no generateStaticParams — it would build all 8 locales"
    assert "LEGAL_LOCALES" in params.group(1), (
        f"{page}/page.tsx does not restrict itself to LEGAL_LOCALES"
    )


def test_nothing_links_to_a_legal_page_without_going_through_legal_href():
    """`href="/privacy/"` is unprefixed (404) and `href={href("/privacy")}` is prefixed with a
    language the page may not exist in. `legalHref` is the only construction that is right for
    a reader of all eight languages."""
    offenders = []
    web = ROOT / "web"
    for path in sorted([*(web / "app").rglob("*.tsx"), *(web / "components").rglob("*.tsx")]):
        # /v2 is the unlinked legacy wizard and is not part of the localised site.
        if "v2" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'href=(?:"|\{")/(?:[a-z]{2}/)?(?:privacy|terms)', line):
                offenders.append(f"{path.relative_to(web)}:{n}: {line.strip()}")
    assert not offenders, (
        "legal links must be built with legalHref(locale, …):\n  " + "\n  ".join(offenders)
    )


def test_the_language_gate_forwards_the_query_string():
    """`/` is the negotiator, and the OAuth callback used to land on it as `/?google=signup`.
    A redirect that drops the query turns that into an ordinary signup with the visitor's
    pre-OAuth picks stranded in sessionStorage — no error, just a wizard that forgot."""
    gate = (ROOT / "web" / "app" / "(plain)" / "page.tsx").read_text(encoding="utf-8")
    assert re.search(r"localeHref\(\s*target\s*,\s*`/\$\{search\}\$\{hash\}`", gate), (
        "the / language gate no longer carries the query string and fragment across"
    )
