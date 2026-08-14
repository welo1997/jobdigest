"""`geo.remote_reach` — how far a fully-remote role reaches, and what it refuses to guess.

Every case below is either a string a live board actually published (measured 2026-08-14) or a
verdict that was **wrong on live data first**. The false positives are the point of the file: a
classifier that only ever sees the vocabulary it was written against proves nothing.

The asymmetry this file defends: saying `country` about a work-from-anywhere role costs a
subscriber one job they never saw. Saying `anywhere` about a US-only role sends someone to an
application they cannot legally make. So every rule is a positive detection, silence stays
silence, and where two readings compete the **narrower** one wins.
"""

import pytest

from service import geo


# --- what the boards actually publish -----------------------------------------------
#
# Each of these is a value observed in a live payload, with the source that wrote it. These are
# closed vocabularies filled in by publishers, which is why they are the trusted layer.

@pytest.mark.parametrize("scope, expected", [
    # weworkremotely `region` — the cleanest vocabulary in the corpus
    ("Anywhere in the World", "anywhere"),
    ("Europe Only", "region"),
    ("Asia Only", "region"),
    ("North America Only", "region"),
    ("Latin America Only", "region"),
    ("Texas, United States of America", "country"),
    ("Massachusetts", "country"),
    # A `region` naming no scope at all. WWR writes this and it must stay unknown — "Remote"
    # answers the work_mode question, not this one.
    ("Remote", None),
    # jobicy `jobGeo`
    ("Anywhere", "anywhere"),
    ("USA", "country"),
    ("UK", "country"),
    ("EMEA", "region"),
    ("APAC", "region"),
    ("LATAM", "region"),
    ("Canada,  USA", "region"),
    ("Brazil,  Canada,  France,  UK,  USA", "region"),
    ("Czechia", "country"),
    # remotive `candidate_required_location`
    ("Worldwide", "anywhere"),
    ("Europe", "region"),
    ("Americas, Europe, Asia, Africa, Oceania", "region"),
    ("Europe, EMEA, UK, Germany, France, European timezones", "region"),
    ("USA, Canada, USA timezones", "region"),
    # workingnomads `location`
    ("Global", "anywhere"),
    ("Remote Worldwide", "anywhere"),
    ("Europe, North America, Latin America, APAC", "region"),
    ("USA or Canada only", "region"),
    ("Time zone: CET (+/- 3 hours)", "region"),
    ("Pennsylvania, USA", "country"),
    ("United Kingdom", "country"),
    # greenhouse `location.name`
    ("Remote, Italy", "country"),
    ("Remote, United States", "country"),
    ("Remote, Bangalore", "country"),
    # ashby `location`
    ("Remote in the United States", "country"),
    ("Remote in the United States or Canada", "region"),
    # himalayas, via `_scope`: restrictions and offsets, joined
    ("United States | UTC-10..UTC-5, UTC+14", "country"),
    ("Mexico | UTC-8..UTC-5", "country"),
    ("Singapore | UTC+8", "country"),
    # A timezone band and nothing else is a wide scope: UTC+0 spans a dozen countries.
    ("UTC+0", "region"),
    ("UTC-8..UTC-3.5", "region"),
])
def test_the_scope_vocabulary_the_boards_publish(scope, expected):
    assert geo.remote_reach(scope) == expected


# --- the three precedence bugs, all found on live data --------------------------------

def test_anywhere_never_outranks_a_named_country():
    """RemoteOK publishes "Anywhere in the United States". That is a US-only role.

    The first version matched the word "anywhere" before resolving countries and called it
    `anywhere` — the single most expensive error this classifier can make, because it is exactly
    the value a "work from anywhere" filter promises.
    """
    assert geo.remote_reach("Anywhere in the United States") == "country"
    assert geo.remote_reach("Anywhere in Germany") == "country"


def test_a_named_country_outranks_a_timezone_band():
    """Himalayas restricts a US role to `["United States"]` and `[-10..-5, 14]`.

    The band is a refinement *inside* the country, not a wider grant. Reading it as scope made
    all 300 sampled Himalayas postings `region` and erased the distinction this column exists
    to draw — with nothing failing anywhere, because 300 plausible answers is what it looked
    like.
    """
    assert geo.remote_reach("United States | UTC-10..UTC-5, UTC+14") == "country"
    assert geo.remote_reach("Canada | UTC-8..UTC-3.5") == "country"


def test_a_named_country_outranks_a_country_implied_by_a_city():
    """RemoteOK writes "London, London, Ontario, Canada" — one Canadian city, not two countries.

    Reading the city as Great Britain alongside the stated Canada made a single-city job look
    open in two, which is the whole difference between "work from home in Ontario" and "live in
    either of two countries".
    """
    assert geo.remote_reach("London, London, Ontario, Canada") == "country"
    # But where nothing is named outright, an unambiguous city is the best evidence there is.
    assert geo.remote_reach("Remote, Bangalore") == "country"
    assert geo.remote_reach("Berlin, London") == "region"


def test_a_continent_inside_a_country_name_is_not_a_region():
    """"South Africa" must not read as the continent, nor "United States of America" as Americas.

    `countries_named` blanks out the country spans it consumed, and the macro-region pass runs on
    the residue — which is what makes trusting a bare "africa" safe at all.
    """
    assert geo.remote_reach("South Africa") == "country"
    assert geo.remote_reach("United States of America") == "country"
    assert geo.remote_reach("Latin America, Europe, Canada, UK, South Africa") == "region"


def test_anywhere_in_a_region_is_that_region():
    """"Anywhere in Europe" is Europe. `anywhere` speaks only when no narrower reading exists."""
    assert geo.remote_reach("Anywhere in Europe") == "region"
    assert geo.remote_reach("Anywhere in EMEA") == "region"
    assert geo.remote_reach("Anywhere in the World") == "anywhere"


def test_a_macro_region_beside_one_country_reads_as_that_country():
    """Jobicy tags hierarchically: "EMEA,  Germany" is a German job on an EMEA board.

    Deliberately the narrow reading. A disjunction ("the EU, UK or North America") loses by the
    same rule and is under-promised as `country` — that direction costs a subscriber a job they
    never saw, where the opposite costs them an application they cannot make.
    """
    assert geo.remote_reach("EMEA,  Germany") == "country"
    assert geo.remote_reach("APAC,  Hong Kong") == "country"


# --- silence stays silence ------------------------------------------------------------

@pytest.mark.parametrize("scope", [
    None, "", "   ", "Remote", "Remote job", "Distributed", "Flexible / Remote",
    "Práce vzdáleně", "Remote - International", "Fully remote",
])
def test_a_posting_that_names_no_scope_stays_unknown(scope):
    """None is a real answer and the second most common one — 22% of active remote rows.

    It must never be folded into `country` on the grounds that most remote work is. A guess here
    is a promise the posting never made, in either direction.
    """
    assert geo.remote_reach(scope) is None
    assert geo.remote_reach(None, scope) is None


def test_an_unrecognised_place_is_unknown_not_wrong():
    """Uruguay, the UAE and Gibraltar are not in `COUNTRY_ALIASES`, so they resolve to nothing.

    That is the same trade `resolve_location` makes: an unresolved place is *kept* and left to
    the AI matcher. It is a coverage gap, and it must not become a wrong answer.
    """
    for place in ("Uruguay", "UAE", "Gibraltar"):
        assert geo.remote_reach(place) is None


# --- prose is the last resort, and read through a keyhole ------------------------------

def test_an_employers_own_address_is_not_an_eligibility_rule():
    """Five of eighteen live description-derived verdicts were a company's head office.

    Every posting describes its employer. "Azumo is based in San Francisco" says nothing about
    where the holder of the job may live, and attaching that country to a role that may well be
    open across the EU is a wrong answer dressed as a precise one — so a bare "based in" is not
    a trigger. Losing the genuine "based in the greater San Diego area" statements with it is
    the correct price.
    """
    assert geo.remote_reach(None, None, "Azumo is based in San Francisco, California.") is None
    assert geo.remote_reach(
        None, None, "A Munich-based company is looking for a founding staff engineer.") is None
    assert geo.remote_reach(
        None, None, "Our company is based in Munich and hiring a staff engineer.") is None
    assert geo.remote_reach(
        None, None, "We are a global leader in payments with offices across Europe.") is None


@pytest.mark.parametrize("text, expected", [
    ("You must be located in Germany to apply.", "country"),
    ("Candidates must be eligible to work in the EU.", "region"),
    ("We can only employ in the US and Canada at this time.", "region"),
    ("Open to candidates residing in the United States.", "country"),
    ("This role is open to candidates based in LATAM.", "region"),
    ("Applicants must have the right to work in Germany.", "country"),
])
def test_a_stated_restriction_on_the_candidate_is_read(text, expected):
    assert geo.remote_reach(None, None, text) == expected


def test_a_restriction_outranks_a_perk_in_the_same_advert():
    """"Work from anywhere!" in the perks and "must be authorized to work in the US" in the
    requirements are the same posting. Only one of the two stops an application."""
    assert geo.remote_reach(
        None, None,
        "Perks: work from anywhere! You must be authorized to work in the United States."
    ) == "country"


def test_a_hedged_promise_is_not_a_promise():
    """Live text: "work from almost anywhere in the world — IDEXX offers employment contracts in
    30 countries". The sentence promises freedom and withdraws it in the same breath, and the
    hedge is the honest half."""
    assert geo.remote_reach(
        None, None,
        "You will work from almost anywhere in the world as a global company IDEXX offers "
        "employment contracts in 30 countries") is None


def test_anywhere_qualified_by_a_timezone_band_is_a_region():
    """Live text: "you can work from anywhere in the world as long as your main location is
    between UTC-5 and UTC+2". That is a band spanning a continent, not a global grant."""
    assert geo.remote_reach(
        None, None,
        "You can work from anywhere in the world as long as your main location is between "
        "UTC-5 and UTC+2.") == "region"
    # Unqualified, the same phrasing stands.
    assert geo.remote_reach(
        None, None, "Fully remote, work from anywhere. Salary 3000/month.") == "anywhere"


def test_a_site_wide_footer_cannot_reach_into_a_scope_window():
    """arbeitnow appends "Find more jobs in United Kingdom" to every one of its postings.

    `normalise` flattens the HTML away, so a scope window ran straight out of its sentence and
    into that footer, turning "right to work in Germany" into a two-country region. One word
    matching an entire source is a failure this repo has already paid for once (see
    `geo.is_place_term`); the window now stops at the first block-level tag remnant.
    """
    assert geo.remote_reach(
        None, None,
        "you must have the right to work in germany p p find a href https www arbeitnow "
        "co uk jobs in united kingdom") == "country"


# --- the layer ordering ---------------------------------------------------------------

def test_the_boards_own_field_outranks_the_location_text_and_prose():
    """A publisher who filled in a scope field has answered the question.

    Ashby's primary location is the office; the secondaries are where you may actually live. The
    scope field carries both, and it must win over the location column — which is precisely why
    `scope_raw` exists as its own field rather than being appended to `location`.
    """
    assert geo.remote_reach("Anywhere in the World", "Stockholm") == "anywhere"
    assert geo.remote_reach("Paris offices, Germany, France, Portugal", "Paris") == "region"
    # An empty scope field defers rather than blocking — None is not an answer of "no scope".
    assert geo.remote_reach(None, "Worldwide") == "anywhere"
    assert geo.remote_reach("", "Worldwide") == "anywhere"


def test_location_outranks_prose():
    """A location of "Worldwide" is not overturned by a stray country in the body copy."""
    assert geo.remote_reach(
        None, "Worldwide", "You must be located in Germany.") == "anywhere"


# --- the stored form ------------------------------------------------------------------

def test_clean_remote_reach_refuses_anything_it_did_not_define():
    assert geo.clean_remote_reach("anywhere") == "anywhere"
    assert geo.clean_remote_reach(" Region ") == "region"
    for junk in (None, "", "worldwide", "eu", "everywhere", 7, "unknown"):
        assert geo.clean_remote_reach(junk) is None


def test_reach_ids_are_ordered_widest_first():
    """`remote_reach` picks the narrowest of several prose readings by comparing indices, so the
    order of this tuple is load-bearing rather than cosmetic."""
    assert geo.REACH_IDS == ("anywhere", "region", "country")
    assert set(geo.REACH_LABELS) == set(geo.REACH_IDS)


# --- which areas a multi-country scope reaches -----------------------------------------
#
# `remote_reach` gives the breadth; this gives the place. The two are computed from one pass over
# the same text (`geo.classify_reach`), because reading them from separate calls lets a posting be
# `region` on the strength of its scope field and filed under the area named in its location field.

@pytest.mark.parametrize("scope, expected", [
    # Unrestricted reaches everything.
    ("Worldwide", ["eea", "na"]),
    ("Anywhere", ["eea", "na"]),
    ("Global", ["eea", "na"]),
    # Europe, in the many ways boards write it.
    ("Europe", ["eea"]),
    ("EMEA", ["eea"]),
    ("Remote-EMEA", ["eea"]),
    ("Remote-Western Europe", ["eea"]),
    ("Remote-Nordics", ["eea"]),
    ("Remote-DACH", ["eea"]),
    ("Remote-Iberia", ["eea"]),
    ("Remote (EU-based creators only)", ["eea"]),
    ("Anywhere in France, Belgium, Spain", ["eea"]),
    ("Remote, Austria; Remote, France; Remote, Germany", ["eea"]),
    ("Ireland,  UK", ["eea"]),
    # North America.
    ("North America", ["na"]),
    ("AMER", ["na"]),
    ("Remote, Canada; Remote, US", ["na"]),
    ("Remote in the United States or Canada", ["na"]),
    ("United States or Canada - Remote Opportunity", ["na"]),
    ("Israel, United States", ["na"]),
    # Both — the interesting set.
    ("Canada,  Europe,  USA", ["eea", "na"]),
    ("Remote | North America or Europe", ["eea", "na"]),
    ("Europe, North America, Latin America, APAC", ["eea", "na"]),
    ("EMEA,  LATAM,  Canada,  USA", ["eea", "na"]),
    # Neither: a real region, just not one we can name a row for.
    ("APAC", []),
    ("LATAM", []),
    ("Remote-South America", []),
    ("Middle East", []),
    ("Argentina, Brazil, Mexico", []),
    # Not a multi-country scope at all.
    ("Germany", []),
    ("USA", []),
    ("Remote, Bangalore", []),
    ("Remote", []),
    (None, []),
])
def test_which_areas_a_scope_reaches(scope, expected):
    assert geo.reach_areas(scope) == expected


def test_the_united_kingdom_is_not_the_eea():
    """`eea` means `EEA_COUNTRIES`, not `COUNTRIES`. GB is selectable and is not in the EEA.

    Merging the two sets here would tell a Czech visitor that a UK-only role is open to them. Same
    rule, and the same consequence, as the `EEA_COUNTRIES` decoupling in `location_predicate`.
    """
    assert geo.reach_areas("Australia, Canada, New Zealand, United Kingdom, United States") == ["na"]
    assert geo.reach_areas("Moldova, Serbia, United Kingdom") == []
    # Add one EEA country and it qualifies — the rule is membership, not word count.
    assert geo.reach_areas("Moldova, Serbia, United Kingdom, Ireland") == ["eea"]


def test_a_country_bound_scope_reaches_no_area():
    """The half of the design that makes the Country menu coherent: a Germany-remote job belongs
    under Germany, so it must reach no international area at all. Otherwise EU-International would
    fill up with single-country work-from-home roles and mean nothing."""
    for scope in ("Germany", "Remote, Italy", "Remote in the United States",
                  "United States | UTC-10..UTC-5, UTC+14", "Texas, USA"):
        reach, areas = geo.classify_reach(scope)
        assert reach == "country", scope
        assert areas == [], scope


def test_a_named_country_and_a_macro_region_are_unioned_not_chosen_between():
    """"Canada, Europe, USA" names two countries *and* a region, and reading only the countries
    files a role a European may hold as North-America-only. Found on 26 live postings while
    measuring; it moved 26 of them from `na` to `eea, na`."""
    assert geo.reach_areas("Canada,  Europe,  USA") == ["eea", "na"]
    assert geo.reach_areas("EMEA,  LATAM,  Canada,  USA") == ["eea", "na"]


def test_a_timezone_band_is_read_from_the_raw_text_not_the_normalised_one():
    """`geo.normalise` reduces punctuation to spaces, so "UTC+2" and "UTC-8" both become "utc 2"
    and "utc 8" with the sign — the entire signal — gone. A signed-offset pattern applied to
    normalised text is a regex that can never match, and it fails silently.

    Europe is UTC+0..+3, North America UTC-4..-10, and those are opposite answers.
    """
    assert geo.reach_areas("UTC+0") == ["eea"]
    assert geo.reach_areas("UTC+2") == ["eea"]
    assert geo.reach_areas("GMT+1") == ["eea"]
    assert geo.reach_areas("UTC-8..UTC-5") == ["na"]
    # Named European zones survive normalisation and are matched case-insensitively, because
    # boards write "CET", not "cet".
    assert geo.reach_areas("CET (+/- 3 hours)") == ["eea"]
    assert geo.reach_areas("Time zone: CET (+/- 3 hours)") == ["eea"]


def test_the_areas_come_from_the_same_field_as_the_verdict():
    """A posting whose scope field says EMEA and whose location says APAC is `region` from the
    scope field — the trusted layer — so its area must come from the scope field too.

    Reading the two from separate passes would file it under the wrong continent, and neither
    column would look wrong on its own.
    """
    assert geo.classify_reach("EMEA", "APAC") == ("region", ["eea"])
    assert geo.classify_reach("APAC", "Europe") == ("region", [])
    # With no scope field, the location answers both halves.
    assert geo.classify_reach(None, "Europe") == ("region", ["eea"])


def test_classify_reach_agrees_with_the_two_single_purpose_functions():
    """`ingest` and the backfill call `classify_reach`; the tests and `/jobs` reason about
    `remote_reach`. If the three disagree the column means one thing and the filter another."""
    for scope in ("Worldwide", "Europe", "APAC", "Canada, Europe, USA", "Germany", "Remote", None):
        assert geo.classify_reach(scope) == (geo.remote_reach(scope), geo.reach_areas(scope))


def test_clean_reach_areas_drops_what_it_does_not_know():
    """These arrive from a public URL parameter, and dropping can only widen — an empty list means
    the international rows were never ticked, so the search falls back to every place."""
    assert geo.clean_reach_areas(["eea", "na"]) == ["eea", "na"]
    assert geo.clean_reach_areas(["na", "eea"]) == ["eea", "na"], "REACH_AREAS order, not the input's"
    assert geo.clean_reach_areas("eea,na") == ["eea", "na"]
    assert geo.clean_reach_areas(["EEA", " na "]) == ["eea", "na"]
    assert geo.clean_reach_areas(["eea", "eea"]) == ["eea"]
    for junk in (None, "", [], ["apac"], ["worldwide"], ["country"], 7, 0.5, object()):
        assert geo.clean_reach_areas(junk) == [], junk
    assert geo.clean_reach_areas(["apac", "latam"]) == []
    # A dict is iterable over its keys, and a `{"eea": ...}` body would otherwise pass by
    # accident — pinned so the type check stays a type check.
    assert geo.clean_reach_areas({"eea": True}) == []


def test_the_area_ids_are_stable_and_labelled():
    """The ids are sent verbatim as the `intl` query parameter and mirrored in web/lib/geo.ts."""
    assert geo.REACH_AREAS == ("eea", "na")
    assert set(geo.REACH_AREA_LABELS) == set(geo.REACH_AREAS)
