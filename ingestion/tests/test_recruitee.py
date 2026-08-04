"""Recruitee: hybrid is not remote, and the curated list stays identity-checked.

The `remote_signal` test is the one that matters. That flag exempts a posting from the
location gate entirely, so getting it wrong is not cosmetic — it is an on-site job in the
wrong country landing in a subscriber's inbox, which is exactly the 2026-07-28 incident
(3 744 of 3 744 jobs.cz postings wrongly flagged, 4 830 rows repaired). Recruitee gives
`remote`, `hybrid` and `on_site` as separate booleans, which makes folding hybrid into
remote a one-word mistake that nothing else would notice.
"""

from __future__ import annotations

from ingestion.sources.recruitee import COMPANIES, RecruiteeSource, _salary, _text


def _offer(**over) -> dict:
    base = {
        "careers_url": "https://strv.recruitee.com/o/marketing-designer-prague",
        "title": "Marketing Designer",
        "company_name": "STRV",
        "city": "Prague",
        "location": "Prague, Praha, Czechia",
        "country_code": "cz",
        "description": "<p>Shape how the world sees us.</p>",
        "requirements": "<ul><li>3 years</li></ul>",
        "published_at": "2026-06-26 13:11:16 UTC",
        "remote": False,
        "hybrid": False,
        "on_site": True,
        "salary": {"min": "70000", "max": "80000", "period": "month", "currency": "CZK"},
    }
    base.update(over)
    return base


def test_hybrid_is_not_remote():
    """A hybrid role is a commute and must stay subject to the location gate."""
    p = RecruiteeSource().normalize([_offer(remote=False, hybrid=True, on_site=False)])[0]
    assert p.remote_signal is False


def test_remote_flag_is_honoured():
    p = RecruiteeSource().normalize([_offer(remote=True, hybrid=False, on_site=False)])[0]
    assert p.remote_signal is True


def test_country_comes_from_the_posting_not_a_constant():
    """Recruitee boards carry non-CZ offices; a source-level constant would mislabel them."""
    rows = RecruiteeSource().normalize([_offer(country_code="de"), _offer(country_code=None)])
    assert rows[0].country_code == "DE"
    assert rows[1].country_code is None


def test_description_is_plain_text_and_includes_requirements():
    p = RecruiteeSource().normalize([_offer()])[0]
    assert "<p>" not in p.description and "<li>" not in p.description
    assert "Shape how the world sees us." in p.description
    assert "3 years" in p.description, "requirements must not be dropped"


def test_salary_is_parsed_and_absent_salary_is_none():
    assert _salary(_offer())[0] == "70000–80000 CZK/month"
    assert _salary(_offer(salary=None)) == (None, None)
    assert _salary(_offer(salary={"min": "90000", "period": "month", "currency": "CZK"}))[0] \
        == "90000 CZK/month"


def test_posting_without_url_or_title_is_skipped():
    assert RecruiteeSource().normalize([_offer(careers_url=None), _offer(title="  ")]) == []


def test_curated_companies_are_lowercase_and_unique():
    """These become subdomains; a duplicate is a doubled fetch and doubled shortlist slots."""
    assert COMPANIES == [c.lower() for c in COMPANIES]
    assert len(COMPANIES) == len(set(COMPANIES))


def test_text_helper_handles_empty_and_none():
    assert _text(None) is None
    assert _text("<p></p>") is None


def test_impostor_slugs_never_return_to_the_curated_list():
    """A live Recruitee board is not evidence that it belongs to the company it is named for.

    Recruitee refuses an invented slug outright — `zzz-not-a-company-9174` gets no answer —
    so `probe_api_slugs` guessing a household name and getting a 200 reads exactly like a
    genuine find. It is not one. On 2026-08-04 the slugs `accenture`, `ccc`, `samsung` and
    `wp` each answered with one or two Amsterdam/Berlin postings titled "Senior Marketer
    (Sample)" or "(Muster)" — Recruitee's own demo content on an unclaimed vanity slug — and
    `ey`, which had been shipping in this list since 2026-08-03, turned out to be the same
    thing: three Amsterdam offers, one of them literally "Senior Marketer (Sample)".

    The failure that reaches a subscriber is a fabricated marketing job in a Polish digest
    under a household brand name, which is worse than a thin digest. Re-adding one is a
    one-word edit to a list of one-word entries, and the discovery CSV will keep proposing
    them on every run, with a live job count next to each.
    """
    from ingestion.sources.recruitee import KNOWN_IMPOSTORS

    offenders = KNOWN_IMPOSTORS & set(COMPANIES)
    assert not offenders, (
        f"{sorted(offenders)} answer with a live board that is not that company's — "
        "identity-check against the postings' own company and city, not a 200"
    )


def test_recruitee_demo_postings_are_not_ingested():
    """Seed content inside a genuine board must not reach a subscriber.

    Found on `trask` — a real Czech consultancy with real Prague roles — where three of five
    offers on 2026-08-04 were Recruitee demo rows a customer never deleted. They normalized
    like any other job, so they were scoreable and emailable. "Recruiter (Sample)" arriving
    at a named employer is a product that looks broken.
    """
    src = RecruiteeSource()
    demo = [_offer(title="Senior Marketer (Sample)"), _offer(title="Recruiter (Sample)"),
            _offer(title="Senior Marketer (Muster)"),
            _offer(title="Pracovní template - Freelancer")]
    assert src.normalize(demo) == []


def test_real_postings_survive_the_demo_filter():
    """The filter's only safe error is a miss, so prove it is not over-broad.

    Open-application and talent-pool ads are posted deliberately by the employer and a
    subscriber may want to answer one; a title merely *containing* a marketing word is an
    ordinary job. If this test ever fails, the filter has started deleting real work.
    """
    src = RecruiteeSource()
    real = ["Senior Marketer", "Nevidíš svoji pozici? Nevadí. Napiš nám.",
            "Didn't find a suitable position for you? Let us know that you are interested!",
            "Blockchain Developer - Talent Pool", "Templating Engine Developer",
            "Java developer", "Sample Preparation Technician"]
    got = [p.title for p in src.normalize([_offer(title=t) for t in real])]
    assert got == real
