"""Werken voor Nederland: the contact block must never reach the corpus.

This source publishes named individuals with their direct mobile numbers — 15 of 20 sampled
pages carried one (2026-08-15), typically two, under a "Stel gerust je vraag" heading. The
adapter's whole privacy posture is that the description is built from an **allowlist** of
content sections, so that block is not merely stripped but never read.

Each test below is written to fail if that breaks, not merely to pass:
  - the contact section's text, name and phone number are absent from the description;
  - an *unrecognised* section is dropped too, which is what makes it an allowlist — a denylist
    would admit a section the publisher adds tomorrow, silently and personally;
  - `_scrub` still catches a number typed into a body paragraph, the case the allowlist cannot
    see;
  - country comes from the posting, never from a source-level constant.
"""

from __future__ import annotations

from ingestion.sources.werkenvoornederland import (
    WerkenVoorNederlandSource,
    _description,
    _job_ld,
    _location,
    _salary,
    _scrub,
)

CONTACT_SECTION = """
<section id="stel_gerust_je_vraag">
  <h2>Stel gerust je vraag</h2>
  <p>Meer informatie over deze vacature</p>
  <p>Rianne van Os, Co&ouml;rdinator cluster informatie 06-25698769</p>
  <p>Swen van der Endt, Corporate recruiter DJI 06-21985529</p>
</section>
"""

PAGE = (
    """
<html><body>
<script type="application/ld+json">
{"@context":"https://schema.org/","@type":"JobPosting",
 "title":"Data Steward","datePosted":"2026-08-04","validThrough":"2026-08-28",
 "employmentType":"FULL_TIME",
 "hiringOrganization":{"@type":"Organization","name":" Ministerie van Justitie "},
 "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
   "addressLocality":"Den Haag","postalCode":"2511CB","addressCountry":"NL"}},
 "baseSalary":{"@type":"MonetaryAmount","currency":"EUR",
   "value":{"@type":"QuantitativeValue","minValue":4867,"maxValue":6679,"unitText":"MONTH"}}}
</script>
<main>
<section id="dit_ga_je_doen_anchor"><h2>Dit ga je doen</h2>
  <p>Je bewaakt de datakwaliteit.</p></section>
<section id="dit_vragen_wij_anchor"><h2>Dit vragen wij</h2>
  <ul><li>WO-niveau</li><li>Ervaring met SQL</li></ul></section>
"""
    + CONTACT_SECTION
    + """
</main></body></html>
"""
)


def _normalized(html: str = PAGE, url: str = "https://www.werkenvoornederland.nl/vacatures/x"):
    raw = [{"url": url, "ld": _job_ld(html), "description": _description(html)}]
    return WerkenVoorNederlandSource().normalize(raw)[0]


# -- the privacy guarantee ----------------------------------------------------------------


def test_the_contact_block_never_reaches_the_description():
    """The one guarantee that matters. Named people, their role and their mobile numbers.

    Mutation check: add `"stel_gerust_je_vraag"` to `_KEEP_SECTIONS` and this goes red on
    every assertion below.
    """
    desc = _normalized().description
    assert desc
    assert "Stel gerust je vraag" not in desc
    assert "Rianne van Os" not in desc
    assert "Swen van der Endt" not in desc
    assert "06-25698769" not in desc
    assert "06-21985529" not in desc
    # and the real content survived, or the test above would pass on an empty string
    assert "datakwaliteit" in desc
    assert "Ervaring met SQL" in desc


def test_an_unknown_section_is_dropped_because_this_is_an_allowlist():
    """A denylist would admit whatever the publisher adds next. This is the difference.

    `contactgegevens_nieuw` is invented — it is exactly the shape of a section that does not
    exist today and might tomorrow, carrying exactly the data we must not store.
    """
    page = PAGE.replace(
        CONTACT_SECTION,
        CONTACT_SECTION
        + '<section id="contactgegevens_nieuw"><p>Bel Jan Jansen op 06-11223344</p></section>',
    )
    desc = _description(page)
    assert desc
    assert "Jan Jansen" not in desc
    assert "06-11223344" not in desc


def test_scrub_catches_a_number_typed_into_a_body_paragraph():
    """The backstop for what the allowlist cannot see: contacts inside a content section."""
    for raw, gone in (
        ("Bel ons op 06-25698769 voor vragen.", "06-25698769"),
        ("Vragen? 070-4266323", "070-4266323"),
        ("Bereikbaar op +31 6 51450753.", "51450753"),
        ("Mail naar j.jansen@minjus.nl graag.", "j.jansen@minjus.nl"),
    ):
        cleaned = _scrub(raw)
        assert cleaned is not None
        assert gone not in cleaned, f"{gone!r} survived scrubbing of {raw!r}"
        assert "[contact verwijderd]" in cleaned


def test_scrub_leaves_ordinary_numbers_alone():
    """A salary or a year is not a phone number; over-scrubbing would gut the description."""
    text = "Schaal 11, 4867 tot 6679 euro per maand, 36 uur per week, per 1 januari 2026."
    assert _scrub(text) == text


# -- structured fields --------------------------------------------------------------------


def test_country_comes_from_the_posting_not_a_constant():
    """Every vacancy here is a Dutch state post, which is exactly when a constant looks safe.

    Arbeitnow hardcoded `country_code="DE"` for the same reason and mis-homed 168 of 375
    postings. If this page ever says BE, the adapter must say BE.
    """
    assert _normalized().country_code == "NL"
    belgian = PAGE.replace('"addressCountry":"NL"', '"addressCountry":"BE"')
    assert _normalized(belgian).country_code == "BE"


def test_no_address_means_no_country_rather_than_a_guess():
    stripped = PAGE.replace(
        '"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",'
        '\n   "addressLocality":"Den Haag","postalCode":"2511CB","addressCountry":"NL"}},',
        "",
    )
    location, country = _location(_job_ld(stripped).get("jobLocation"))
    assert location is None and country is None


def test_fields_are_normalised():
    posting = _normalized()
    assert posting.title == "Data Steward"
    assert posting.company == "Ministerie van Justitie"  # the feed pads it with spaces
    assert posting.location == "Den Haag"
    assert posting.salary_raw == "4867-6679 per month"
    assert posting.currency == "EUR"
    assert posting.posted_at.isoformat() == "2026-08-04"
    # A raw third-party string is not a category hint; `classify` would discard it anyway.
    assert posting.source_category is None
    # Nothing on these pages states a remote policy, so the adapter must not claim one.
    assert posting.remote_signal is None


def test_salary_handles_the_shapes_that_actually_occur():
    assert _salary(None) == (None, None)
    assert _salary({"currency": "EUR"}) == (None, "EUR")
    single = {"currency": "EUR", "value": {"minValue": 4000, "maxValue": 4000,
                                           "unitText": "MONTH"}}
    assert _salary(single) == ("4000 per month", "EUR")


def test_a_page_without_json_ld_is_skipped_not_guessed():
    assert _job_ld("<html><body><p>no ld here</p></body></html>") is None


def test_posting_id_is_derived_from_the_url():
    """`posting_id = md5(url)`, so a URL scheme change re-creates the corpus — the property
    `source_watchdog` watches for as ID CHURN."""
    a = _normalized(url="https://www.werkenvoornederland.nl/vacatures/a")
    b = _normalized(url="https://www.werkenvoornederland.nl/vacatures/b")
    assert a.posting_id != b.posting_id
    assert a.posting_id == _normalized(
        url="https://www.werkenvoornederland.nl/vacatures/a"
    ).posting_id


def test_a_posting_without_a_title_is_dropped():
    src = WerkenVoorNederlandSource()
    assert src.normalize([{"url": "https://x/vacatures/y", "ld": {}, "description": "d"}]) == []
    assert src.normalize([{"url": None, "ld": {"title": "T"}, "description": "d"}]) == []
