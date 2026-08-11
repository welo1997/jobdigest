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


def test_the_excluded_adapters_still_import():
    """Kept as code, so an exclusion can be reversed by a decision rather than a rewrite."""
    from ingestion.sources.jobscz import JobsCzSource
    from ingestion.sources.profesia import ProfesiaSource

    assert JobsCzSource().source_name == "jobscz"
    assert ProfesiaSource().source_name == "profesia"
