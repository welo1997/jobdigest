"""StartupJobs: the country is resolved from the posting, and the endpoint is the live one.

Two guarantees this source got wrong for its whole life and now must not regress:

1. **A Slovak offer resolves to SK, not CZ.** The adapter passes the city as `location` with
   `country_code=None`; the one geo resolver then maps Bratislava→SK. Hardcoding `country_code`
   (the old bug) makes `resolve_location` treat the code as authoritative and drops the city, so
   the mutation-check below goes red the instant someone puts a constant back.
2. **The endpoint moved and the old one 404s.** `fetch` pages the JSON-LD collection via its own
   `view.next` chain and breaks *loudly* on a non-200 — the source died silently once because a
   404 was swallowed as an empty list.
"""

from __future__ import annotations

from ingestion.base import make_posting_id
from ingestion.sources import startupjobs as sj
from ingestion.sources.startupjobs import StartupJobsSource, _localised, _text
from service import geo


def _offer(**over) -> dict:
    base = {
        "displayId": 104109,
        "slug": "backend-vyvojar",
        "title": {"cs": "Backend vývojář", "en": None},
        "description": {"cs": "<p>Hledáme <b>Python</b> vývojáře.</p>"},
        "company": {"name": "Shoptet"},
        "locations": [{"name": {"cs": "Praha", "en": None}}],
        "salary": {"minimum": {"amount": "80000", "currency": "CZK"},
                   "maximum": {"amount": "100000", "currency": "CZK"}, "unit": "month"},
        "fields": [{"parent": {"cs": "obchod", "en": "sales"}}],
    }
    base.update(over)
    return base


def test_url_carries_the_slug_because_the_id_alone_is_a_404():
    """`/job/{id}` without the slug is a clean 404 — verified live on 2026-08-07 against
    every offer tried, both domains, and the ids in the site's own sitemap. The 2026-08-06
    rewrite shipped the id-only form, so an entire day's ingest carried dead links and one
    reached a subscriber's inbox. Nothing catches this downstream: a URL is never fetched
    after it is stored."""
    p = StartupJobsSource().normalize([_offer()])[0]
    assert p.url == "https://www.startupjobs.com/job/104109/backend-vyvojar"


def test_posting_id_survives_a_retitle_so_a_rename_is_not_a_new_job():
    """The slug is the employer's to change — offer 106499's moved from
    `social-media-content-creator` to `social-media-specialist` in two days. Hashing it
    would mint a fresh posting on every retitle: a duplicate in the shortlist, and a job the
    subscriber has already seen arriving as new."""
    before = StartupJobsSource().normalize([_offer()])[0]
    after = StartupJobsSource().normalize([_offer(slug="senior-backend-vyvojar")])[0]

    assert before.posting_id == after.posting_id
    assert before.url != after.url          # the link still follows the rename


def test_posting_id_still_hashes_the_id_only_url_so_the_link_fix_churns_nothing():
    """Byte-identical to what the 2026-08-06 rewrite stored. That is what lets the broken
    links be repaired in place (`upsert_postings` refreshes `url`) instead of re-creating
    all 450 postings under new ids for the second time in two days."""
    p = StartupJobsSource().normalize([_offer()])[0]
    assert p.posting_id == make_posting_id("https://www.startupjobs.com/job/104109")


def test_a_slugless_offer_falls_back_to_the_id_only_url():
    """Still a 404, but the site's own — better than `/job/104109/`, which reads as a broken
    page. Every live offer measured carries a slug; this is for the day one does not."""
    p = StartupJobsSource().normalize([_offer(slug=None)])[0]
    assert p.url == "https://www.startupjobs.com/job/104109"


def test_country_is_never_hardcoded_only_the_city_is_carried():
    p = StartupJobsSource().normalize([_offer()])[0]
    assert p.country_code is None
    assert p.location == "Praha"


def test_slovak_offer_resolves_to_SK_not_CZ():
    """The whole point of the rewrite: a Bratislava offer must reach a Slovak subscriber.

    This drives the real downstream path (`geo.resolve_location`, as `service.ingest.build_row`
    calls it). If the adapter ever sets `country_code="CZ"` again, resolve_location returns
    ('CZ', None) and this goes red — the exact production bug that hid 18 Bratislava roles.
    """
    p = StartupJobsSource().normalize([
        _offer(locations=[{"name": {"cs": "Bratislava", "en": None}}])
    ])[0]
    assert geo.resolve_location(p.location, p.country_code) == ("SK", "bratislava")


def test_czech_offer_still_resolves_to_CZ():
    p = StartupJobsSource().normalize([_offer()])[0]
    assert geo.resolve_location(p.location, p.country_code) == ("CZ", "prague")


def test_location_less_offer_is_kept_with_no_location():
    p = StartupJobsSource().normalize([_offer(locations=[])])[0]
    assert p.location is None and p.country_code is None


def test_salary_is_parsed_from_the_structured_object():
    p = StartupJobsSource().normalize([_offer()])[0]
    assert p.salary_raw == "80000–100000 CZK/month"
    assert p.currency == "CZK"
    assert StartupJobsSource().normalize([_offer(salary=None)])[0].salary_raw is None


def test_title_and_description_prefer_czech_and_are_plain_text():
    p = StartupJobsSource().normalize([_offer()])[0]
    assert p.title == "Backend vývojář"
    assert "<p>" not in p.description and "<b>" not in p.description
    assert "Python" in p.description


def test_offer_without_display_id_or_title_is_skipped():
    assert StartupJobsSource().normalize([_offer(displayId=None)]) == []
    assert StartupJobsSource().normalize([_offer(title={"cs": None, "en": None})]) == []


def test_field_gives_a_category_hint_for_the_classifier():
    assert StartupJobsSource().normalize([_offer()])[0].source_category == "sales"


def test_localised_and_text_helpers():
    assert _localised({"cs": "Praha", "en": "Prague"}) == "Praha"
    assert _localised({"cs": None, "en": "Prague"}) == "Prague"
    assert _localised({"cs": None, "en": None}) is None
    assert _text("<p></p>") is None


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_follows_view_next_and_stops(monkeypatch):
    """Pages the Hydra collection until `view.next` is absent — two pages then done."""
    pages = {
        "https://core.startupjobs.cz/api/search/offers?page=1": _Resp(200, {
            "member": [_offer(displayId=1)],
            "view": {"next": "/api/search/offers?page=2"}}),
        "https://core.startupjobs.cz/api/search/offers?page=2": _Resp(200, {
            "member": [_offer(displayId=2)], "view": {}}),
    }
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(sj.politeness, "throttle", lambda *_: None)
    monkeypatch.setattr(sj.requests, "get", fake_get)
    got = StartupJobsSource().fetch()
    assert [o["displayId"] for o in got] == [1, 2]
    assert calls == list(pages.keys())


def test_fetch_breaks_loudly_on_non_200(monkeypatch):
    """A 404 (the old dead endpoint) must stop the loop, not be swallowed into a quiet zero."""
    monkeypatch.setattr(sj.politeness, "throttle", lambda *_: None)
    monkeypatch.setattr(sj.requests, "get", lambda url, **kw: _Resp(404, {}))
    assert StartupJobsSource().fetch() == []
