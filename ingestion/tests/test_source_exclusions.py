"""Sources that are deliberately not ingested, and why they must stay that way.

An exclusion is only as durable as the reason attached to it. Both of these were removed for
a stated cause, and the failure mode is identical in each case: someone notices the Czech
digest looks thin, sees a working adapter sitting in `ingestion/sources/`, and adds it back
without ever learning why it left. These tests are where that person finds out.
"""

from __future__ import annotations

import search_jobs


def _ingested() -> set[str]:
    """The class names `gather()` will actually run, resolved rather than grepped.

    **This used to be `inspect.getsource(search_jobs.gather)` and every test below passed
    for free.** When `_source_classes` was split out of `gather` — so `source_watchdog`
    could read the expected source list instead of keeping a second copy — the list of
    class names left `gather`'s body with it. From that commit on, `gather`'s source text
    mentioned no adapter at all, so `assert "JobsCzSource" not in src` was asserting the
    absence of a string from a function that could never contain it. The Alma Career guard,
    which stands in front of a database-right question and 12 861 postings, was disarmed by
    a refactor that had nothing to do with it and went green the whole way.

    That is this repo's recurring shape, third time in the same lane after
    `pending_boards.py` grepping adapter *text* and the CI skip-check being a hand-written
    list instead of a glob: **a guard that reads code as strings breaks silently the moment
    the code moves, and silence is indistinguishable from a pass.**

    So this reads the resolved classes. Moving the list, renaming the function or wrapping
    it in another layer cannot disarm it — only actually ingesting the source can, which is
    the thing being guarded. `source_classes` constructs no adapter that touches the network
    (Adzuna and USAJOBS are probed behind try/except for credentials only).
    """
    return {cls.__name__ for cls in search_jobs.source_classes(True)} | {
        cls.__name__ for cls in search_jobs.source_classes(False)
    }


def test_alma_career_brands_are_not_ingested():
    """Jobs.cz and Profesia are excluded on terms grounds, not technical ones.

    Alma Career's Podmínky používání §4.11 forbids automated processing of data in their
    systems and reading presented data for further machine processing; §4.7(e) makes bulk
    automated reading grounds for termination. The same words — *vytěžovat / zužitkovávat* —
    are the statutory language of the EU database right (96/9/EC), which binds with or
    without a contract, so "we never accepted their terms" is not an answer.

    Their adapters still work and are still tested. That is the trap: re-adding one is a
    two-word edit and nothing else would notice. Re-add them when there is permission —
    not when the digest looks thin.
    """
    ingested = _ingested()
    for name in ("JobsCzSource", "ProfesiaSource"):
        assert name not in ingested, (
            f"{name} is back in gather(). It was excluded 2026-08-03 on the terms of use of "
            "Alma Career, which owns both Jobs.cz and Profesia — see the comment in gather() "
            "and CLAUDE.md. This test failing means either a mistake or a decision that "
            "needs recording here."
        )


def test_linkedin_and_eurojobs_are_not_ingested():
    """LinkedIn's User Agreement prohibits scraping and they enforce it; EuroJobs sits behind
    a Cloudflare interstitial and returns nothing. Both adapters exist and are wired to
    nothing, which is the state a README once described as coverage."""
    ingested = _ingested()
    for name in ("LinkedInSource", "EuroJobsSource"):
        assert name not in ingested, f"{name} must not be ingested — see CLAUDE.md"


def test_smartrecruiters_is_not_ingested():
    """SmartRecruiters refuses us at the host root, and it named our path to say so.

    `api.smartrecruiters.com/robots.txt` is, in full::

        User-agent: LinkedInBot
        Allow: /v1/companies/

        User-agent: *
        Disallow: /

    That is not a blanket rule we happen to fall under. It names **the exact path this
    adapter used** and grants it to one crawler that is not us. `dev.smartrecruiters.com`
    serves the byte-identical file, so their own developer documentation is refused too.

    Checked 2026-08-11 for a published grant that could sit above robots, because a
    documented self-service term would have outranked it: `www.smartrecruiters.com/legal/`
    lists **no API Terms of Use and no Developer Terms**, and the visitor-facing Terms of
    Use prohibit *"Use automatic means to access content or data from other users"* while
    granting no republication right. Nothing published permits this, and the only remaining
    route to permission is correspondence, which this repo does not do.

    **This is the inverse of the shape `docs/sources.md` warns about.** Fourteen sources
    permit in robots what they refuse in their terms, so the rule reads "never settle on
    robots alone" — but that cuts one way only. A *permissive* robots is evidence of nothing; a
    *refusing* one is dispositive on its own and the terms never need to be reached.

    The cost is deliberate and large: ~7 700 postings from one of the widest European
    sources in the stack, and the adapter worked. Re-add it only against a published
    permission, never because a country looks thin.
    """
    assert "SmartRecruitersSource" not in _ingested(), (
        "SmartRecruitersSource is back in gather(). It was removed 2026-08-11 because "
        "api.smartrecruiters.com/robots.txt refuses `*` at the root while granting our "
        "exact path to LinkedInBot, and no published API terms grant it back. This test "
        "failing means either a mistake or a decision that needs recording here."
    )


def test_nva_is_not_ingested():
    """NVA is excluded for lack of product use, not on terms — a different kind of reason.

    Latvia's live register (`cvvp.nva.gov.lv`) publishes no terms and its robots permits the
    paths, so it was wired 2026-08-15 under the unestablished-permission decision. It was
    removed 2026-08-19 on a plain cost/value call: the sector field it gates on lives only on
    each vacancy's detail page, so it fetched ~4 460 pages one-per-second to keep ~570 — **~30%
    of the whole pipeline's wall clock** — and the kept rows are low-professional-fit Latvian
    inventory (the register is the entire labour market) that no subscriber currently wants.
    The cheap pre-filter does not exist: the list carries the profession, but the sector is the
    employer's industry, so profession does not predict it (a cleaner at a ministry is
    `Valsts pārvalde`, a kept sector) — see the adapter's Cost section.

    Reversible by decision, not rewrite: the adapter still works and imports. Re-add it when
    there is a Latvian audience to serve, not because the digest looks thin — and if so,
    solve the crawl cost first (the 827-name profession answer-key project).
    """
    assert "NvaSource" not in _ingested(), (
        "NvaSource is back in gather(). It was removed 2026-08-19 for lack of product use at "
        "~30% of pipeline wall-clock — not a terms question. This test failing means either a "
        "mistake or a decision that needs recording here."
    )


def test_the_excluded_adapters_still_import():
    """Kept as code, so an exclusion can be reversed by a decision rather than a rewrite."""
    from ingestion.sources.jobscz import JobsCzSource
    from ingestion.sources.nva import NvaSource
    from ingestion.sources.profesia import ProfesiaSource
    from ingestion.sources.smartrecruiters import SmartRecruitersSource

    assert JobsCzSource().source_name == "jobscz"
    assert NvaSource().source_name == "nva"
    assert ProfesiaSource().source_name == "profesia"
    assert SmartRecruitersSource().source_name == "smartrecruiters"
