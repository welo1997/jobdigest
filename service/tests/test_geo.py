"""Location preferences: resolution, remote detection, and the legacy translation.

`service/geo.py` is the one definition of where a subscriber can work, and three separate
consumers depend on it agreeing with itself:

  - ingest, which writes `postings.country_code` / `city` / `remote_signal`;
  - `store.query_shortlist`, whose SQL gate is built from the same tables;
  - `web/lib/geo.ts`, which the browser uses to offer the choices — it cannot import Python,
    so a slug that exists on one side only is a preference the filter can never apply.

The SQL gate's *behaviour* is pinned separately, against a real Postgres, in
`test_geo_sql.py`. Everything here is pure Python and always runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from service import geo

ROOT = Path(__file__).resolve().parents[2]
WEB_GEO = ROOT / "web" / "lib" / "geo.ts"


# ------------------------------------------------------------------- resolution ---

@pytest.mark.parametrize("location,country_code,expected", [
    ("Praha 4 - Chodov", None, ("CZ", "prague")),
    ("Praha", None, ("CZ", "prague")),
    ("Brno-střed", "CZ", ("CZ", "brno")),
    ("Hradec Králové", None, ("CZ", "hradec-kralove")),
    ("Plzeň", None, ("CZ", "plzen")),
    ("Pilsen, Czech Republic", None, ("CZ", "plzen")),
    ("München, Bayern", None, ("DE", "munich")),
    ("Berlin", None, ("DE", "berlin")),
    ("Frankfurt am Main", None, ("DE", "frankfurt")),
    ("Warszawa", None, ("PL", "warsaw")),
    ("Den Haag", None, ("NL", "the-hague")),
    ("Wien, Austria", None, ("AT", "vienna")),
    ("Bratislava", None, ("SK", "bratislava")),
    # Country resolves even when the city is not one we list.
    ("Kolín, Czech Republic", None, ("CZ", None)),
    ("Some Village, Germany", None, ("DE", None)),
    # A structured country_code from the source always wins over the text.
    ("Cambridge, MA", "US", ("US", None)),
    # Nothing to resolve — and that is a valid answer, not a failure.
    ("Remote, Europe", None, (None, None)),
    ("", None, (None, None)),
    (None, None, (None, None)),
])
def test_resolve_location(location, country_code, expected):
    assert geo.resolve_location(location, country_code) == expected


def test_an_ambiguous_word_needs_a_country_to_become_a_city():
    """"Nice" and "Split" are cities and ordinary words. Matching them off a bare token would
    put a French posting in front of someone who never picked France; requiring a known
    country first keeps the false positive out while still resolving the real thing."""
    assert geo.resolve_location("Nice to have", None) == (None, None)
    assert geo.resolve_location("Nice, France", None) == ("FR", "nice")
    assert geo.resolve_location("Nice", "FR") == ("FR", "nice")


def test_a_city_only_resolves_within_its_own_country():
    """`country_code` is authoritative, so a city name from elsewhere must not override it —
    that would file the posting under a country the subscriber did not choose."""
    assert geo.resolve_location("Berlin", "CZ") == ("CZ", None)


# ------------------------------------------------------------ remote vs hybrid ---

def test_hybrid_is_not_remote():
    """The whole feature rests on this line. A hybrid role in Brno means being in Brno most
    weeks, so it must stay subject to the city filter — if it were treated as remote it would
    bypass the gate and land in a Prague subscriber's inbox, which is the reported bug."""
    assert geo.is_fully_remote("Brno (hybrid)", None, None) is False
    assert geo.is_fully_remote("Praha - hybridní", None, None) is False
    assert geo.is_fully_remote("Berlin, on-site", None, None) is False
    # The cases that need the negative check rather than the mere absence of a keyword:
    # boards routinely write both words, and "hybrid remote" is hybrid.
    assert geo.is_fully_remote("Brno — hybrid remote", None, None) is False
    assert geo.is_fully_remote("Remote / hybrid, Praha", None, None) is False
    assert geo.is_fully_remote("Remote work not available, Brno", None, None) is False


def test_remote_is_read_from_the_location_field():
    assert geo.is_fully_remote("Remote", None, None) is True
    assert geo.is_fully_remote("Anywhere in Europe", None, None) is True
    assert geo.is_fully_remote("Remote (Germany)", None, None) is True


def test_a_boards_own_remote_flag_is_trusted():
    """Lever's workplaceType and the remote-only boards are structured data; text mining is
    only the fallback for the majority of sources that send nothing."""
    assert geo.is_fully_remote("Praha", None, True) is True
    assert geo.is_fully_remote(None, None, True) is True


def test_a_graded_czech_home_office_is_hybrid_however_it_arrived():
    """Czech boards write the grade, not the word "hybrid", and the grade is the meaning.

    "Možnost občasné práce z domova" is an office job in Praha with a perk. Jobs.cz derived
    `remote_signal` from the "z domova" substring, so 3 744 of these arrived flagged fully
    remote and skipped the location gate entirely — 40 of 50 evaluation personas had on-site
    Czech work in their shortlist, including subscribers who had picked only Germany or the
    Netherlands (2026-07-28). The board's flag is passed as True here deliberately: the
    guarantee is that an explicit hybrid phrase beats the flag, since a scraper's boolean is
    a claim and this is the only place that can refuse it.
    """
    assert geo.is_fully_remote("Brno", "Možnost občasné práce z domova", True) is False
    assert geo.is_fully_remote("Praha", "Práce převážně z domova", True) is False
    assert geo.is_fully_remote("Ostrava", "Částečně home office", True) is False
    assert geo.is_fully_remote(
        "Brno", "50 000 Kč; Odpověď do 2 týdnů; Možnost občasné práce z domova", True) is False


def test_an_english_in_office_schedule_beats_the_boards_remote_flag():
    """Ashby: "This role is based in Paris. We use a hybrid work model of 3 days in the office
    per week" — arriving with the board's remote flag set (248 active postings, 2026-07-28).
    A named office plus a fixed weekly schedule is not a role someone in Prague can take."""
    assert geo.is_fully_remote(
        "Paris, France",
        "This role is based in Paris. We use a hybrid work model of 3 days in the office "
        "per week. We offer relocation.", True) is False
    assert geo.is_fully_remote(
        "Singapore",
        "This role is based in our Singapore office and we use a hybrid work model with "
        "three days in the office per week.", True) is False


def test_the_english_rendering_of_the_czech_grade():
    """Profesia.sk writes the same graded field in English, and a Czech-only stem list is
    blind to it: "Košice, Slovakia (Job with occasional home office)" (320 postings)."""
    assert geo.is_fully_remote(
        "Košice, Slovakia (Job with occasional home office)", "", True) is False
    assert geo.is_fully_remote(
        "Bratislava, Slovakia (Job with mostly home office)", "", True) is False


def test_the_other_phrasings_ashby_actually_uses():
    """Each of these was missed by a single word on the first pass — "hybrid workING model",
    "N days a week FROM the office", "this IS A hybrid role" — and each is a named city."""
    assert geo.is_fully_remote(
        "Brussels", "This position is based in Brussels with a hybrid working model.",
        True) is False
    assert geo.is_fully_remote(
        "London", "Will work under our hybrid working model - 3 days a week from the office.",
        True) is False
    assert geo.is_fully_remote(
        "Paris", "This is a hybrid role, based in Paris.", True) is False


def test_a_posting_offering_remote_as_an_option_stays_remote():
    """"Hybrid/remote" and "hybrid or fully remote" offer the remote choice, so the subscriber
    who wants remote can take them. Only a stated in-office expectation disqualifies."""
    assert geo.is_fully_remote(
        "Netherlands", "Location: Netherlands (Amsterdam office available, hybrid/remote).",
        True) is True
    assert geo.is_fully_remote(
        "Lisbon", "A collaborative environment that embraces both remote and hybrid ways of "
        "working.", True) is True


def test_only_a_named_schedule_or_policy_disqualifies_not_the_bare_word():
    """"Hybrid" alone appears in unrelated technical and clinical prose. Acting on the bare
    word would demote genuinely remote engineering roles — the mirror-image bug."""
    assert geo.is_fully_remote(
        "Remote", "You will design hybrid cloud infrastructure across AWS and on-prem.",
        True) is True
    assert geo.is_fully_remote(
        "Remote job", "Abrechnung nach EBM, GOÄ, UV-GOÄ, Hybrid-DRG, AOP.", True) is True


def test_an_explicit_fully_remote_claim_wins_over_a_passing_hybrid_mention():
    assert geo.is_fully_remote(
        "Berlin", "Unlike employers using a hybrid work model, we are fully remote.",
        True) is True


def test_the_hybrid_qualifier_must_sit_next_to_the_home_office_phrase():
    """`možnost` and `částečně` are ordinary Czech words — disqualifying a posting for either
    on its own would silently strip genuinely remote roles from every remote_scope subscriber,
    which is the same class of bug pointing the other way."""
    assert geo.is_fully_remote(
        "Remote", "Možnost kariérního růstu a částečně flexibilní pracovní doba", None) is True
    assert geo.is_fully_remote("Praha", "Práce z domova", True) is True


def test_only_unambiguous_description_phrases_count_as_remote():
    """A Czech posting says "home office" for two days a week. Reading that as fully remote
    would mark half of Prague remote and re-open the bug from the other side, so the
    description is only consulted for phrases that cannot mean anything else."""
    assert geo.is_fully_remote("Praha", "home office 2 dny v týdnu", None) is False
    assert geo.is_fully_remote("Praha", "Nabízíme plně vzdáleně", None) is True
    assert geo.is_fully_remote("Praha", "This role is 100% remote", None) is True


# --------------------------------------------------- legacy regions round-trip ---

@pytest.mark.parametrize("regions", [
    ["cz"],                          # old "Czechia"
    ["cz", "eu"],                    # old "EU remote"
    ["cz", "eu", "worldwide"],       # old "Worldwide"
])
def test_legacy_regions_round_trip_exactly(regions):
    """This is what makes migration 010 a no-op: every value the old UI could produce
    translates to countries/scope and back to the identical `regions`. If it did not, applying
    the migration would silently change what existing subscribers are sent."""
    countries, scope = geo.countries_and_scope_from_regions(regions)
    assert geo.regions_for(countries, scope) == sorted(regions)


def test_derived_regions_are_never_narrower_than_the_countries_chosen():
    """`regions` is kept only as a coarse derived backstop. It must stay a superset of the
    real gate, or a posting the country filter allows could be dropped by a stale bucket."""
    assert "eu" in geo.regions_for(["DE"], "country")
    assert geo.regions_for(["CZ"], "country") == ["cz"]
    assert geo.regions_for(["CZ"], "worldwide") == ["cz", "eu", "worldwide"]
    assert geo.regions_for([], "country") == ["cz", "eu", "worldwide"]   # nothing chosen


# ---------------------------------------------------------- preference cleaning ---

def test_cities_for_an_unselected_country_are_dropped():
    """Removing a country in the UI must remove its cities too. Left behind, they would go on
    narrowing a filter the subscriber can no longer see — an invisible restriction is worse
    than a wrong one."""
    assert geo.clean_cities(["cz:prague", "de:berlin"], ["CZ"]) == ["cz:prague"]


def test_malformed_and_unknown_values_are_dropped_not_guessed():
    assert geo.clean_cities(["prague", "xx:prague", "cz:", ":prague"], ["CZ"]) == []
    assert geo.clean_countries(["cz", "DEU", "de", None, "DE"]) == ["CZ", "DE"]
    assert geo.clean_remote_scope("nonsense") == geo.DEFAULT_REMOTE_SCOPE


def test_a_typed_city_survives_cleaning():
    """The form lets someone type a town we do not curate. It cannot reach the SQL gate, but
    it does reach the matcher, so dropping it here would lose the only enforcement it has."""
    assert geo.clean_cities(["cz:kolin"], ["CZ"]) == ["cz:kolin"]
    assert geo.slugify_city("Kolín") == "kolin"
    assert geo.slugify_city("Ústí nad Labem") == "usti-nad-labem"


def test_describe_names_the_cities_and_the_remote_scope():
    """This string is what the AI matcher actually reads, so it has to be unambiguous about
    which countries are restricted to cities and which are not."""
    text = geo.describe(["CZ", "DE"], ["cz:prague", "cz:brno"], "eu")
    assert "Czechia (Prague, Brno only)" in text
    assert "Germany (any city)" in text
    assert "remote roles anywhere in the EU" in text
    assert geo.describe([], [], "eu") == "—"


# ------------------------------------------------------------- internal integrity ---

def test_every_alias_points_at_a_real_city():
    for country, aliases in geo.CITY_ALIASES.items():
        for alias, slug in aliases.items():
            assert slug in geo.CITIES[country], f"{country}:{alias} -> {slug}"


def test_slugs_are_stable_shaped():
    """Slugs are stored in `postings.city` and `profiles.cities`; a slug that would not
    survive `slugify_city` could never be produced by the form that has to match it."""
    for country, cities in geo.CITIES.items():
        for slug in cities:
            assert geo.is_valid_city_slug(slug), f"{country}:{slug}"
            assert geo.slugify_city(slug) == slug


def test_display_names_resolve_back_to_their_own_slug():
    for country, cities in geo.CITIES.items():
        for slug, display in cities.items():
            assert geo.resolve_location(display, country) == (country, slug)


# ------------------------------------------------------------------ web drift ---

def _ts_object(name: str) -> dict:
    """Pull a `const NAME: ... = {...};` object out of geo.ts. It is generated as JSON, so it
    parses as JSON — which is also the point: hand-editing it into non-JSON is a signal that
    the two sides are being maintained separately."""
    text = WEB_GEO.read_text(encoding="utf-8")
    match = re.search(rf"export const {name}[^=]*=\s*(\{{.*?\n\}});", text, re.S)
    assert match, f"{name} not found in {WEB_GEO.name}"
    return json.loads(match.group(1))


def test_frontend_countries_match_the_python_definition():
    assert _ts_object("COUNTRIES") == geo.COUNTRIES, (
        "web/lib/geo.ts and service/geo.py disagree about the selectable countries."
    )


def test_frontend_cities_match_the_python_definition():
    """Order matters as well as content: it is the order the picker offers, and both sides
    render the same list."""
    ts = _ts_object("CITIES")
    assert list(ts) == list(geo.CITIES)
    for country, cities in geo.CITIES.items():
        assert list(ts[country].items()) == list(cities.items()), country


def test_frontend_remote_scopes_match():
    text = WEB_GEO.read_text(encoding="utf-8")
    match = re.search(r"export const REMOTE_SCOPES = \[(.*?)\]", text, re.S)
    assert match
    assert tuple(re.findall(r'"([a-z]+)"', match.group(1))) == geo.REMOTE_SCOPES
