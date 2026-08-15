"""`scope_raw` on the four ATS adapters — the multi-location arrays they were reading one entry of.

`test_scope_raw.py` covers the six *remote boards*, which is where the 2026-08-14 pass looked. The
ATS adapters were never probed, and each of them holds a `locations[]` array it reads exactly one
element of. Measured against live payloads on 2026-08-15, 16 boards per source:

    teamtailor   32 of 174 postings name >1 location, 19 of them >1 country   <- the worst
    lever        32 of 349 name >1 location, 4 remote and naming >1 country
    recruitee    12 of 106 name >1 location, 3 remote and naming >1 country
    workable      1 of 427 — and that one is open in six countries, stored as one

Every payload below is the shape a live API actually returned. Three guarantees per source, the
same ones `test_scope_raw.py` asserts plus one:

  1. the other locations reach `scope_raw`;
  2. they do **not** leak into `location` — `geo.resolve_location` takes the first country n-gram
     it finds, so a secondary in front of the real city re-homes the job;
  3. a single-location posting stores `None` rather than a copy of `location`, because a column
     that repeats another one only invites the two to disagree.
"""

from ingestion.sources.lever import LeverSource
from ingestion.sources.recruitee import RecruiteeSource
from ingestion.sources.teamtailor import TeamtailorSource
from ingestion.sources.workable import WorkableSource
from service import geo


# --- Teamtailor: schema.org `jobLocation[]`, read by `_first_address` alone ---------------

def _tt_item(slug, locations):
    return {
        "url": f"https://printfulinc.teamtailor.com/jobs/{slug}",
        "title": "Backend Engineer",
        "_tenant": "printfulinc",
        "_jobposting": {
            "description": "<p>Build services.</p>",
            "hiringOrganization": {"name": "Printful"},
            "jobLocation": [{"address": {"addressLocality": city, "addressCountry": cc}}
                            for city, cc in locations],
        },
    }


TT_ITEMS = [
    # The live shape that prompted this: twelve countries, stored as Barcelona.
    _tt_item("platform", [("Barcelona", "ES"), ("Rīga", "LV"), ("London", "GB"),
                          ("Berlin", "DE"), ("Warsaw", "PL"), ("Amsterdam", "NL"),
                          ("Prague", "CZ"), ("Lisbon", "PT"), ("Bucharest", "RO")]),
    # Two offices, two countries — the ordinary case.
    _tt_item("data", [("Rīga", "LV"), ("Berlin", "DE")]),
    # One location: nothing to add, so nothing is stored.
    _tt_item("support", [("Espoo", "FI")]),
]


def test_teamtailor_keeps_every_joblocation_not_just_the_first():
    postings = TeamtailorSource().normalize(TT_ITEMS)
    twelve, two, one = postings

    assert twelve.scope_raw.startswith("Barcelona, ES; Rīga, LV; London, GB")
    assert two.scope_raw == "Rīga, LV; Berlin, DE"
    # A lone `jobLocation` is `location` verbatim; storing it again says nothing.
    assert one.scope_raw is None

    # (2) The secondaries never reach `location`, which still names the first address only.
    assert twelve.location == "Barcelona, ES"
    assert twelve.country_code == "ES"
    assert two.location == "Rīga, LV"


def test_teamtailor_scope_makes_the_other_countries_reachable():
    """The whole point: eleven country filters used to answer this posting with silence."""
    twelve = TeamtailorSource().normalize(TT_ITEMS)[0]
    countries = geo.reach_countries(twelve.scope_raw, twelve.location)
    for expected in ("ES", "LV", "GB", "DE", "PL", "NL", "CZ", "PT", "RO"):
        assert expected in countries, f"{expected} unreachable"


# --- Lever: `categories.allLocations` ------------------------------------------------------

LEVER_ITEMS = [
    {   # MoonPay, live 2026-08-15: six countries in one posting, stored as the United Kingdom
        "hostedUrl": "https://jobs.lever.co/moonpay/eng",
        "text": "Senior Engineer",
        "_org": "moonpay",
        "workplaceType": "remote",
        "descriptionPlain": "Build payments.",
        "categories": {"location": "United Kingdom - Remote",
                       "allLocations": ["United Kingdom - Remote", "Spain - Remote",
                                        "Poland - Remote", "Portugal - Remote",
                                        "Romania - Remote"]},
    },
    {   # Spotify: a multi-entry list that is an *office pair*, not a remote scope. Both offices
        # are real places the job can be held, so the countries count; the reach does not, and
        # `ingest` never asks for one because the posting is not fully remote.
        "hostedUrl": "https://jobs.lever.co/spotify/hybrid",
        "text": "Product Manager",
        "_org": "spotify",
        "workplaceType": "hybrid",
        "categories": {"location": "Stockholm", "allLocations": ["Stockholm", "London"]},
    },
    {   # One location: nothing added.
        "hostedUrl": "https://jobs.lever.co/qonto/single",
        "text": "Analyst",
        "_org": "qonto",
        "workplaceType": "onsite",
        "categories": {"location": "Paris", "allLocations": ["Paris"]},
    },
]


def test_lever_keeps_every_alllocation():
    multi, offices, single = LeverSource().normalize(LEVER_ITEMS)
    assert multi.scope_raw == ("United Kingdom - Remote; Spain - Remote; Poland - Remote; "
                               "Portugal - Remote; Romania - Remote")
    assert multi.location == "United Kingdom - Remote"
    assert geo.remote_reach(multi.scope_raw) == "region"
    assert geo.reach_areas(multi.scope_raw) == ["eea"]

    assert offices.scope_raw == "Stockholm; London"
    assert single.scope_raw is None


def test_lever_office_pair_is_two_countries_but_no_reach():
    """A hybrid job in two offices is holdable in two countries and reachable from neither.

    `reach_countries` answers "is there a job for me in Sweden", which is yes. `remote_reach`
    answers "where may I live", which for a hybrid role is a question with no honest answer — and
    `ingest.build_row` never asks it, because the posting is not fully remote.
    """
    offices = LeverSource().normalize(LEVER_ITEMS)[1]
    assert geo.reach_countries(offices.scope_raw, offices.location) == ["GB", "SE"]


# --- Workable: `locations[]`, whose `countryCode` the adapter already iterated -------------

WORKABLE_JOBS = [
    {   # CloudTalk, live 2026-08-15. `_country` already walked this array and took the first.
        "url": "https://apply.workable.com/cloudtalk/j/ABC",
        "title": "Support Engineer",
        "_account": "cloudtalk",
        "_account_name": "CloudTalk",
        "telecommuting": True,
        "city": "",
        "country": "Spain",
        "locations": [{"country": "Spain", "countryCode": "ES", "city": ""},
                      {"country": "Czechia", "countryCode": "CZ", "city": ""},
                      {"country": "United Kingdom", "countryCode": "GB", "city": ""},
                      {"country": "Ireland", "countryCode": "IE", "city": ""},
                      {"country": "Portugal", "countryCode": "PT", "city": ""},
                      {"country": "Netherlands", "countryCode": "NL", "city": ""}],
    },
    {
        "url": "https://apply.workable.com/netguru/j/DEF",
        "title": "Designer",
        "_account": "netguru",
        "telecommuting": False,
        "city": "Poznań",
        "country": "Poland",
        "locations": [{"country": "Poland", "countryCode": "PL", "city": "Poznań"}],
    },
]


def test_workable_keeps_every_location_entry():
    multi, single = WorkableSource().normalize(WORKABLE_JOBS)
    assert multi.scope_raw == ("Spain; Czechia; United Kingdom; Ireland; Portugal; Netherlands")
    assert multi.location == "Spain"          # (2) no leak
    assert multi.country_code == "ES"
    assert geo.reach_countries(multi.scope_raw, multi.location) == \
        ["CZ", "ES", "GB", "IE", "NL", "PT"]
    assert single.scope_raw is None


# --- Recruitee: `locations[]`, where `city` is often the literal word "Remote" -------------

RECRUITEE_OFFERS = [
    {   # Chaos, live 2026-08-15
        "careers_url": "https://chaos.recruitee.com/o/devops",
        "title": "DevOps Engineer",
        "_company": "chaos",
        "company_name": "Chaos",
        "remote": True,
        "location": "Remote job",
        "country_code": "DE",
        "locations": [{"city": "Karlsruhe", "country": "Germany"},
                      {"city": "London", "country": "United Kingdom"}],
    },
    {   # Multi-*state*, one country. Must not read as a multi-country scope: a US state is not a
        # country, and `city` here is the literal word "Remote".
        "careers_url": "https://chaos.recruitee.com/o/sales",
        "title": "Account Executive",
        "_company": "chaos",
        "remote": True,
        "location": "Remote, North Carolina, United States",
        "country_code": "US",
        "locations": [{"city": "Remote", "country": "United States", "state": "North Carolina"},
                      {"city": "Remote", "country": "United States", "state": "Massachusetts"}],
    },
]


def test_recruitee_keeps_every_location_entry():
    two_countries, two_states = RecruiteeSource().normalize(RECRUITEE_OFFERS)
    assert two_countries.scope_raw == "Karlsruhe, Germany; London, United Kingdom"
    assert two_countries.location == "Remote job"        # (2) no leak
    assert geo.remote_reach(two_countries.scope_raw) == "region"

    # Two entries, one country — and the *state* is what differs, which Recruitee puts in a field
    # this adapter does not read. Both entries therefore render "Remote, United States", the
    # duplicate is dropped, and one distinct place is not a scope: `None`.
    #
    # That is the right answer twice over. A US state is not a country, so there is no second
    # country filter to reach; and had the state been included, "North Carolina" and
    # "Massachusetts" would be two places with no country between them that `geo` can resolve.
    assert two_states.scope_raw is None
    assert geo.reach_countries(two_states.scope_raw, two_states.location) == []
