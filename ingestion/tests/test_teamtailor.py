"""Teamtailor: country + salary come off the structured feed, and noise is dropped.

The load-bearing properties, each written to fail if broken:
  - country_code resolves from the posting's own schema.org jobLocation, never a source-level
    constant (Teamtailor tenants post across several countries — Mintos is LV + LT + DE).
  - talent-pool / "submit your CV" entries are dropped: they carry no real role and would
    only pad a subscriber's shortlist.
  - the curated TENANTS list stays lowercased and free of obvious duplicates — a hygiene
    check, since a wrong slug is a silent dark board.
"""

from __future__ import annotations

from ingestion.sources.teamtailor import TENANTS, TeamtailorSource, _salary


def _item(**over) -> dict:
    jp = {
        "@type": "JobPosting",
        "title": "Credit Risk Analyst",
        "description": "<p>Analyse <strong>risk</strong>.</p>",
        "datePosted": "2026-06-18",
        "hiringOrganization": {"@type": "Organization", "name": "Mintos"},
        "baseSalary": {"@type": "MonetaryAmount", "currency": "EUR",
                       "value": {"unitText": "MONTH", "minValue": "2500", "maxValue": "2900"}},
        "jobLocation": [{"@type": "Place", "address": {
            "@type": "PostalAddress", "addressLocality": "Rīga", "addressCountry": "LV"}}],
    }
    jp.update(over.pop("jp", {}))
    base = {
        "id": "abc",
        "title": "Credit Risk Analyst",
        "url": "https://mintos.teamtailor.com/jobs/1-credit-risk-analyst",
        "date_published": "2026-06-18T16:19:45+03:00",
        "content_html": "<p>fallback</p>",
        "_jobposting": jp,
        "_tenant": "mintos",
    }
    base.update(over)
    return base


def test_country_and_location_from_the_posting():
    """Country is the posting's own addressCountry, uppercased — not a source constant."""
    rows = TeamtailorSource().normalize([
        _item(),
        _item(jp={"jobLocation": [{"address": {"addressLocality": "Lithuania",
                                               "addressCountry": "LT"}}]}),
    ])
    assert rows[0].country_code == "LV"
    assert rows[0].location == "Rīga, LV"
    assert rows[1].country_code == "LT"


def test_missing_location_is_kept_as_unknown():
    """No jobLocation → null country (kept for the AI matcher), never dropped or guessed."""
    p = TeamtailorSource().normalize([_item(jp={"jobLocation": []})])[0]
    assert p.country_code is None
    assert p.company == "Mintos"


def test_salary_parsed_from_monetary_amount():
    raw, cur = _salary(_item()["_jobposting"])
    assert cur == "EUR" and raw == "2500-2900 EUR/MONTH"
    # single-value salary must not render a bogus range
    raw2, _ = _salary({"baseSalary": {"currency": "EUR",
                                       "value": {"unitText": "MONTH", "minValue": "3000"}}})
    assert raw2 == "3000 EUR/MONTH"


def test_company_is_the_hiring_organization_not_the_slug():
    p = TeamtailorSource().normalize([_item()])[0]
    assert p.company == "Mintos", "company must come from hiringOrganization, not the tenant slug"


def test_description_is_plain_text():
    p = TeamtailorSource().normalize([_item()])[0]
    assert "<p>" not in p.description and "<strong>" not in p.description
    assert "Analyse" in p.description and "risk" in p.description


def test_talent_pool_entries_are_dropped():
    """A 'submit your CV' entry carries no role; it must not reach the shortlist."""
    rows = TeamtailorSource().normalize([
        _item(title="Didn't find the perfect position? Submit your CV here"),
        _item(title="Open Application"),
        _item(),  # a real one survives
    ])
    assert len(rows) == 1 and rows[0].title == "Credit Risk Analyst"


def test_rows_without_url_or_title_are_dropped():
    rows = TeamtailorSource().normalize([_item(url=None), _item(title="")])
    assert rows == []


def test_source_name():
    assert TeamtailorSource().source_name == "teamtailor"


def test_tenants_are_clean():
    assert TENANTS, "tenant list must not be empty"
    assert all(t == t.lower().strip() for t in TENANTS), "slugs must be lowercased/trimmed"
    assert len(TENANTS) == len(set(TENANTS)), "no duplicate tenants"
