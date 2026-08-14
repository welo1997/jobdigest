"""`JobPosting.scope_raw` — the geographic scope the boards publish and we used to discard.

Four adapters were dropping a structured scope field they already fetched (measured 2026-08-14).
Every payload below is the shape a live API actually returned. Two guarantees are asserted for
each: the scope reaches `scope_raw`, **and** it does not leak into `location`, because
`geo.resolve_location` takes the first country n-gram it finds and a scope list placed in front
of the real city silently re-homes the job to another country.
"""

from ingestion.sources.ashby import AshbySource
from ingestion.sources.greenhouse import GreenhouseSource
from ingestion.sources.himalayas import HimalayasSource
from ingestion.sources.weworkremotely import WeWorkRemotelySource
from service import geo


# --- WeWorkRemotely: `region`, on 100 of 100 entries, previously location=None ----------

WWR_ENTRIES = [
    {   # the common case: a genuine work-from-anywhere role whose publisher sits in Stockholm
        "link": "https://weworkremotely.com/listings/proxify-shopify",
        "title": "Proxify AB: Senior Shopify Developer",
        "summary": "<p>Build stores.</p>",
        "region": "Anywhere in the World",
        "country": "",
        "state": "Stockholm",
        "published_parsed": (2026, 8, 10, 10, 0, 0, 0, 0, 0),
    },
    {   # country-bound, with the flag emoji WWR prefixes each country with
        "link": "https://weworkremotely.com/listings/sinclair-principal",
        "title": "Sinclair Broadcast Group: Sr. Principal Engineer",
        "summary": "<p>Broadcast systems.</p>",
        "region": "Texas",
        "country": "\U0001F1FA\U0001F1F8 United States of America",
        "state": "",
        "published_parsed": (2026, 8, 10, 10, 0, 0, 0, 0, 0),
    },
    {   # region-bound
        "link": "https://weworkremotely.com/listings/toptal-python",
        "title": "Toptal: Python Backend Developer",
        "summary": "<p>Contract work.</p>",
        "region": "Asia Only",
        "country": "",
        "state": "",
        "published_parsed": (2026, 8, 10, 10, 0, 0, 0, 0, 0),
    },
    {   # a region naming no scope — must stay unknown rather than be invented
        "link": "https://weworkremotely.com/listings/charles-web",
        "title": "Charles Technology: Web Developer",
        "summary": "<p>Web work.</p>",
        "region": "Remote",
        "country": "",
        "state": "",
        "published_parsed": (2026, 8, 10, 10, 0, 0, 0, 0, 0),
    },
]


def test_weworkremotely_captures_the_region_it_used_to_throw_away():
    """This source was the only one in the corpus that was 100% location-empty.

    `normalize` passed `location=None` outright while the feed carried `region`, `country` and
    `state` on every entry — so all 170 stored postings resolved to no country, and the emptiness
    looked like the board's fault. Nothing downstream could tell, because an unknown location is
    deliberately *kept* by the location gate.
    """
    postings = WeWorkRemotelySource().normalize(WWR_ENTRIES)
    assert [p.scope_raw for p in postings] == [
        "Anywhere in the World",
        "Texas, United States of America",
        "Asia Only",
        "Remote",
    ]
    assert [geo.remote_reach(p.scope_raw, p.location) for p in postings] == [
        "anywhere", "country", "region", None,
    ]


def test_weworkremotely_never_reads_the_employers_seat():
    """Proxify advertises "Anywhere in the World" with `state: Stockholm` — its own address.

    Reading `state` is wrong twice over, and this test exists because the first version of the
    adapter did: `resolve_location` stamps the work-from-anywhere role SE/stockholm, so it appears
    under the Sweden filter and vanishes from every other country's, *and* `geo.remote_reach` sees
    the implied country and downgrades a genuine `anywhere` to `country` — which is the exact
    value a "work from anywhere" filter is built on.
    """
    anywhere, texas, _asia, _unknown = WeWorkRemotelySource().normalize(WWR_ENTRIES)
    assert "Stockholm" not in (anywhere.scope_raw or "")
    assert "Stockholm" not in (anywhere.location or "")
    assert geo.resolve_location(anywhere.location) == (None, None)
    assert geo.remote_reach(anywhere.scope_raw, anywhere.location) == "anywhere"
    # `country` is about the candidate, so it stays — it is how a US-bound row resolves at all.
    assert texas.location == "Texas, United States of America"
    assert geo.resolve_location(texas.location)[0] == "US"


# --- Himalayas: `timezoneRestrictions`, fetched and dropped -----------------------------

def test_himalayas_captures_the_timezone_restrictions_it_documented_but_dropped():
    """The docstring claimed both restrictions were kept "in the location text". Only locations
    were, for the whole life of the adapter.

    These offsets are the only structured timezone data in the entire corpus, present on 20 of 20
    sampled postings, and the field the user's timezone question depends on.
    """
    items = [
        {"applicationLink": "https://himalayas.app/jobs/a", "title": "Field Engineer",
         "companyName": "Acme", "locationRestrictions": ["United States"],
         "timezoneRestrictions": [-10, -9, -8, -7, -6, -5, 14]},
        {"applicationLink": "https://himalayas.app/jobs/b", "title": "GM",
         "companyName": "Acme", "locationRestrictions": ["Singapore"],
         "timezoneRestrictions": [8]},
        {"applicationLink": "https://himalayas.app/jobs/c", "title": "Dev",
         "companyName": "Acme", "locationRestrictions": ["Canada"],
         "timezoneRestrictions": [-8, -7, -6, -5, -4, -3.5]},
    ]
    postings = HimalayasSource().normalize(items)
    assert [p.scope_raw for p in postings] == [
        "United States | UTC-10..UTC-5, UTC+14",
        "Singapore | UTC+8",
        "Canada | UTC-8..UTC-3.5",
    ]
    # And the band must not be mistaken for a wider grant than the country it sits inside.
    assert all(geo.remote_reach(p.scope_raw) == "country" for p in postings)


def test_himalayas_records_no_scope_where_the_board_recorded_none():
    """An empty `locationRestrictions` is Himalayas' way of saying "unrestricted", and it is
    deliberately *not* translated into a claim of `anywhere`.

    Inferring a promise from silence is the one error `geo.remote_reach` must never make: the
    publisher did not say it, and a filter that shows this row as work-from-anywhere is
    promising something nobody wrote down.
    """
    items = [{"applicationLink": "https://himalayas.app/jobs/d", "title": "Dev",
              "companyName": "Acme", "locationRestrictions": [], "timezoneRestrictions": []}]
    posting, = HimalayasSource().normalize(items)
    assert posting.scope_raw is None
    assert posting.location == "Remote"
    assert geo.remote_reach(posting.scope_raw, posting.location) is None


# --- Ashby: `secondaryLocations`, the other countries a role is open in ------------------

def test_ashby_captures_the_secondary_locations():
    """An Engineering Manager whose primary is "Paris offices" and whose secondaries are Germany,
    France and Portugal is a role you may hold from three countries. On the primary alone it is
    indistinguishable from a Paris desk job."""
    items = [{
        "jobUrl": "https://jobs.ashbyhq.com/pennylane/em",
        "title": "Engineering Manager",
        "location": "Paris offices",
        "secondaryLocations": [
            {"location": "Germany"}, {"location": "France"}, {"location": "Portugal (remote)"},
        ],
        "isRemote": True,
        "isListed": True,
        "_org": "pennylane",
    }]
    posting, = AshbySource().normalize(items)
    assert posting.scope_raw == "Paris offices, Germany, France, Portugal (remote)"
    assert geo.remote_reach(posting.scope_raw) == "region"


def test_ashby_never_lets_a_secondary_location_reach_the_location_column():
    """This is the whole reason `scope_raw` is a separate field.

    `resolve_location` takes the first country n-gram it finds, so "Paris offices, Germany,
    France" resolves to DE with no city — and the Paris job leaves every Paris subscriber's
    digest while appearing in Germany's. The failure is invisible: a country resolved is a
    country resolved.
    """
    items = [{
        "jobUrl": "https://jobs.ashbyhq.com/pennylane/em",
        "title": "Engineering Manager", "location": "Paris",
        "secondaryLocations": [{"location": "Germany"}, {"location": "Munich"}],
        "isRemote": True, "isListed": True, "_org": "pennylane",
    }]
    posting, = AshbySource().normalize(items)
    assert posting.location == "Paris"
    assert geo.resolve_location(posting.location) == ("FR", "paris")
    # Proof the guard matters: the scope string on its own resolves somewhere else entirely.
    assert geo.resolve_location(posting.scope_raw)[0] == "DE"


def test_ashby_scope_is_none_when_there_is_nothing_to_say():
    items = [{"jobUrl": "https://jobs.ashbyhq.com/x/y", "title": "Dev", "location": "",
              "secondaryLocations": [], "isListed": True, "_org": "x"}]
    posting, = AshbySource().normalize(items)
    assert posting.scope_raw is None


# --- Greenhouse: `offices` --------------------------------------------------------------

def test_greenhouse_folds_the_office_countries_into_the_scope():
    """`location.name` already answers the question most of the time ("Remote, Italy"). `offices`
    is the country-level list that names the country a bare city leaves out."""
    items = [
        {"absolute_url": "https://boards.greenhouse.io/gitlab/jobs/1",
         "title": "AI Engineer", "location": {"name": "Remote, Bangalore"},
         "offices": [{"name": "India"}], "_board_token": "gitlab"},
        {"absolute_url": "https://boards.greenhouse.io/gitlab/jobs/2",
         "title": "AI Transformation Owner", "location": {"name": "Remote, United States"},
         "offices": [], "_board_token": "gitlab"},
    ]
    bangalore, us = GreenhouseSource().normalize(items)
    assert bangalore.scope_raw == "Remote, Bangalore, India"
    assert bangalore.location == "Remote, Bangalore"       # the office list stays out
    assert us.scope_raw == "Remote, United States"
    assert geo.remote_reach(bangalore.scope_raw) == "country"


def test_greenhouse_drops_the_office_placeholders():
    """"No office" and "Remote" are Greenhouse bookkeeping, not places."""
    items = [{"absolute_url": "https://boards.greenhouse.io/x/jobs/3", "title": "Dev",
              "location": {"name": "Remote"},
              "offices": [{"name": "No office"}, {"name": "Remote"}], "_board_token": "x"}]
    posting, = GreenhouseSource().normalize(items)
    assert posting.scope_raw == "Remote"
    assert geo.remote_reach(posting.scope_raw) is None


# --- the field's default ----------------------------------------------------------------

def test_scope_raw_defaults_to_none_so_every_other_adapter_is_unaffected():
    """Most boards publish no scope field, and None must not be read as a claim of anything.

    `geo.remote_reach` falls through to `location` when it is absent, which is how the sources
    that carry their scope *in* the location text (Jobicy's `jobGeo`, Remotive's
    `candidate_required_location`, Working Nomads) keep working without duplicating a column.
    """
    from ingestion.base import JobPosting
    assert JobPosting(posting_id="x", source="y").scope_raw is None
    assert geo.remote_reach(None, "Worldwide") == "anywhere"
