"""Golden Careers (CY): a bespoke parser, and an agency listing that dedupe cannot collapse.

There is no JSON-LD here, so the fields come off server-rendered markup. A redesign breaks
that silently — `source_watchdog` would report SILENT ZERO — so these tests pin the shapes the
parser depends on, and the index page that must not become a posting.

The property worth understanding rather than merely testing: `company` is the **agency**, not
the hirer, because every ad reads "our client". `digest.dedupe_key` normalises
(company, title, city), so the same role on the employer's own ATS board carries a different
key and will NOT collapse. That is documented, not fixed.
"""

from __future__ import annotations

from ingestion.sources.goldencareers import (
    AGENCY,
    CATEGORY_HINTS,
    GoldenCareersSource,
    _description,
    _text,
)

PAGE = """
<html><body>
<section class="page-title"><div class="container">
  <h1>Automation Systems Programmer</h1>
  <div class="job-terms">
    <span class="job-category"><i class="fa"></i> Information Technology</span>
    <span class="job-location"><i class="fa"></i> Limassol</span>
  </div>
</div></section>
<section class="single-job-post">
  <div class="job-section">
    <h4 class="job-section-title">Job Description</h4>
    <div class="job-section-content"><p>Our client is a well established company.</p></div>
  </div>
  <div class="job-section">
    <h4 class="job-section-title">Job Responsibilities</h4>
    <div class="job-section-content"><ul><li>Know the products.</li></ul></div>
  </div>
</section>
</body></html>
"""


def _norm(**over):
    item = {
        "url": "https://www.goldencareers.com.cy/jobs/automation-systems-programmer/",
        "title": "Automation Systems Programmer",
        "category": "Information Technology",
        "location": "Limassol",
        "description": _description(PAGE),
    }
    item.update(over)
    posts = GoldenCareersSource().normalize([item])
    return posts[0] if posts else None


def test_the_description_joins_the_sections_with_their_headings():
    desc = _description(PAGE)
    assert "Job Description" in desc
    assert "Our client is a well established company." in desc
    assert "Job Responsibilities" in desc
    assert "• Know the products." in desc


def test_the_company_is_the_agency_and_that_is_deliberate():
    """The ads say "our client"; there is no employer name to store. Documented, not guessed."""
    assert _norm().company == AGENCY


def test_the_jobs_index_is_not_a_vacancy():
    """`/jobs/` sits in the same sitemap as the vacancies and must not become a posting."""
    src = GoldenCareersSource()
    kept = [u for u in ["https://www.goldencareers.com.cy/jobs/",
                        "https://www.goldencareers.com.cy/jobs",
                        "https://www.goldencareers.com.cy/jobs/a-real-role/"]
            if "/jobs/" in u and not u.rstrip("/").endswith("/jobs")]
    assert kept == ["https://www.goldencareers.com.cy/jobs/a-real-role/"]
    assert src.source_name == "goldencareers"


def test_category_hints_are_real_taxonomy_members():
    from service import taxonomy
    for value in CATEGORY_HINTS.values():
        assert value in taxonomy.CATEGORIES, f"{value!r} is not a taxonomy category"


def test_an_unmapped_category_yields_no_hint_rather_than_a_raw_string():
    """A raw third-party string is not a hint — `classify` discards it, and until 2026-08-08
    five adapters passing one had put 14% of the corpus in categories no query could name."""
    assert _norm(category="Engineering & Manufacturing").source_category is None
    assert _norm(category="Information Technology").source_category == "software_engineering"


def test_country_is_constant_but_the_city_is_read():
    posting = _norm()
    assert posting.country_code == "CY"
    assert posting.location == "Limassol"
    assert _norm(location="Nicosia").location == "Nicosia"


def test_no_date_is_published_so_none_is_claimed():
    assert _norm().posted_at is None


def test_a_page_without_a_title_is_dropped():
    assert GoldenCareersSource().normalize([{"url": "https://x/jobs/y", "title": ""}]) == []
    assert GoldenCareersSource().normalize([{"url": None, "title": "T"}]) == []


def test_text_handles_wordpress_entities():
    assert _text("<p>They&#8217;re hiring</p>") == "They’re hiring"
    assert _text("") == ""
    assert _text(None) == ""
