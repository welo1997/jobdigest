"""`geo.countries_in_scope`, `geo.reach_countries`, and the macro-region words in eight languages.

Three changes of 2026-08-15, each of which was a silent miss rather than an error:

  * a scope that is a **list** of locations was resolved as one phrase, so `countries_named`'s
    "an explicit name discards every city-implied country" rule — correct within one phrase —
    threw away four countries out of five;
  * `postings.country_code` holds one country and a posting routinely names several, so every
    country but one was reachable by no filter at all;
  * `_MACRO_REGION` and `_EEA_AREA` read "Europe" in English and French only, so a German board
    writing "Europaweit" classified as no scope whatever.

Each case below either was a live payload or is the direct negative of one. The regression block
at the end is the load-bearing half: these rules were bought with three production false positives
and must not be traded away for the recall above.
"""

from __future__ import annotations

import pytest

from service import geo


# --- a scope that is a list of places ------------------------------------------------------

def test_a_list_of_locations_unions_its_countries_instead_of_picking_one():
    """The live miss. Five cities in five countries read as "Ukraine" and nothing else.

    Only `ua` is a token this module reads as a country name outright; Warsaw, Bucharest, Tallinn
    and Barcelona are *implied* countries. One explicit hit filled the `named` bucket, the whole
    `implied` bucket was discarded as designed, and a role open across five countries classified
    `country` — naming the one country the posting arguably says least about.
    """
    scope = "Warsaw, PL; Kyiv, UA; Bucharest, RO; Tallinn, EE; Barcelona, ES"
    countries, _ = geo.countries_in_scope(scope)
    assert countries == {"PL", "UA", "RO", "EE", "ES"}
    assert geo.classify_reach(scope) == ("region", ["eea"])


def test_within_one_entry_a_named_country_still_discards_the_implied_ones():
    """The rule the fix must not break — RemoteOK's "London, London, Ontario, Canada".

    No separator, so this is one phrase and behaves exactly as it did before: Canada alone. If
    splitting had been applied to commas instead of semicolons, this would read as two countries
    and a single-city job would look open across an ocean.
    """
    countries, _ = geo.countries_in_scope("London, London, Ontario, Canada")
    assert countries == {"CA"}
    assert geo.remote_reach("London, London, Ontario, Canada") == "country"


def test_a_scope_with_no_separator_is_unchanged():
    for scope in ("Anywhere in the United States", "Remote, Bangalore", "EMEA",
                  "Paris offices, Germany, France, Portugal (remote)"):
        assert geo.countries_in_scope(scope) == geo.countries_named(scope)


# --- reach_countries -----------------------------------------------------------------------

def test_reach_countries_needs_two_or_it_says_nothing():
    """One country is what `country_code` already holds. A column repeating another one only
    gives the two something to disagree about."""
    assert geo.reach_countries("Remote, Germany", "Berlin") == []
    assert geo.reach_countries(None, "Praha") == []
    assert geo.reach_countries(None, None) == []


def test_reach_countries_unions_the_scope_and_the_location():
    assert geo.reach_countries("Karlsruhe, Germany; London, United Kingdom",
                               "Remote job") == ["DE", "GB"]
    # Ashby's shape: one phrase, the secondaries named outright, the primary's city implied and
    # therefore discarded *within that phrase* — but France is named too, so it survives.
    assert geo.reach_countries("Paris offices, Germany, France, Portugal (remote)",
                               "Paris offices") == ["DE", "FR", "PT"]
    # Free-text location alone, with no scope field at all (Remotive, Jobicy, Working Nomads).
    assert geo.reach_countries(None, "USA, Canada") == ["CA", "US"]


def test_reach_countries_never_reads_the_description():
    """Prose is read through a keyhole for *reach*, and must not be read at all for this.

    `anywhere` taken from body copy was right one time in three, and five of eighteen
    prose-derived verdicts were an employer's head office read as an eligibility rule. Minting a
    country-filter entry out of that would be a stronger claim on the same weak evidence — the
    posting would then appear under a country nobody said it was open in.
    """
    prose = ("Acme is based in Berlin and has offices in Paris and Madrid. "
             "Candidates must be based in Portugal.")
    assert geo.reach_countries(None, "Lisbon") == []
    # Even passed as the location argument it is only the two fields, never a third:
    with pytest.raises(TypeError):
        geo.reach_countries(None, "Lisbon", prose)          # no description parameter exists


def test_an_anywhere_scope_is_not_a_list_of_countries():
    """"Anywhere in the World" reaches everywhere and names nobody. `reach_areas` is what carries
    that; filling `reach_countries` with a guess would put a promise on a posting that made none.
    """
    assert geo.reach_countries("Anywhere in the World", "Anywhere in the World") == []
    assert geo.reach_areas("Anywhere in the World") == ["eea", "na"]


# --- the macro-region words, in the languages the boards write in --------------------------

EUROPE_IN_EIGHT_LANGUAGES = [
    ("Europaweit (Remote)", "de"),
    ("Remote, Europa", "de/nl/es/it/pl/pt"),
    ("Europees, remote", "nl"),
    ("Remote - Evropa", "cs/sk"),
    ("Praca zdalna - Europa", "pl"),
    ("Remote, tutta Europa", "it"),
    ("Trabajo remoto en Europa", "es"),
    ("Remote - Eurooppa", "fi"),
    ("Nordisk remote", "sv/da/no"),
    ("Skandinavien", "sv/de"),
]


@pytest.mark.parametrize("scope, language", EUROPE_IN_EIGHT_LANGUAGES)
def test_europe_is_read_in_more_than_english(scope, language):
    """Every one of these classified as *no scope at all* until 2026-08-15.

    Country names were never the gap — `COUNTRY_ALIASES` has read "Deutschland" and "Nederland"
    for as long as it has existed. Only the macro-region words were English, so the boards whose
    inventory the EU-International row exists to surface were the ones it could not read.
    """
    reach, areas = geo.classify_reach(scope)
    assert reach == "region", f"{language}: {scope!r} still reads as {reach}"
    assert areas == ["eea"], f"{language}: {scope!r} reaches {areas}"


@pytest.mark.parametrize("scope", ["Remote, weltweit", "Wereldwijd remote",
                                   "Remote, en cualquier lugar", "Remote - in tutto il mondo"])
def test_worldwide_is_read_in_more_than_english(scope):
    assert geo.classify_reach(scope) == ("anywhere", ["eea", "na"])


def test_norden_is_excluded_under_the_georgia_rule():
    """"Norden" is Swedish/Danish/Norwegian for the Nordics — and a town in Lower Saxony, and the
    ordinary German word for "the north". It is left out on the `georgia` rule, checked rather
    than assumed, and `nordisk` carries the same meaning with no collision.

    This test exists so the omission reads as a decision rather than an oversight, and so that
    adding the word later has to argue with something.
    """
    assert geo.remote_reach("Hela Norden") is None
    assert geo.remote_reach("Nordisk") == "region"


# --- regressions: bought with three production false positives ----------------------------

@pytest.mark.parametrize("scope, expected", [
    # A named country outranks every wider signal.
    ("Anywhere in the United States", ("country", [])),
    ("United States | UTC-10..UTC-5, UTC+14", ("country", [])),
    ("London, London, Ontario, Canada", ("country", [])),
    # Wide signals still work where no country is named.
    ("Remote - Europe", ("region", ["eea"])),
    ("Remote (EMEA)", ("region", ["eea"])),
    ("Remote, EMEA or AMER", ("region", ["eea", "na"])),
    ("Remote (APAC)", ("region", [])),
    ("Remote, Canada; Remote, US", ("region", ["na"])),
    ("CET +/- 3", ("region", ["eea"])),
    ("Remote - Iberia", ("region", ["eea"])),
    # A company name is not a scope. `europ\\w*` would have matched both of these; the stems are
    # enumerated instead precisely so it does not. The second case is the one that bites — the
    # first names a city, so the country rule answers before any region word is reached, and a
    # loose stem would hide behind it.
    ("Europcar fleet office, Paris", ("country", [])),
    ("Remote - Europcar Mobility Group", (None, [])),
    ("Europlan, remote", (None, [])),
    # The German town, with its country stated: countries win before any region word is reached.
    ("Norden, Germany", ("country", [])),
])
def test_the_precedence_rules_survive_the_new_vocabulary(scope, expected):
    assert geo.classify_reach(scope) == expected
