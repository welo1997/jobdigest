"""Sources that are deliberately not ingested, and why they must stay that way.

An exclusion is only as durable as the reason attached to it. Both of these were removed for
a stated cause, and the failure mode is identical in each case: someone notices the Czech
digest looks thin, sees a working adapter sitting in `ingestion/sources/`, and adds it back
without ever learning why it left. These tests are where that person finds out.
"""

from __future__ import annotations

import inspect

import search_jobs


def _gather_source() -> str:
    return inspect.getsource(search_jobs.gather)


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
    src = _gather_source()
    for name in ("JobsCzSource", "ProfesiaSource"):
        assert name not in src, (
            f"{name} is back in gather(). It was excluded 2026-08-03 on the terms of use of "
            "Alma Career, which owns both Jobs.cz and Profesia — see the comment in gather() "
            "and CLAUDE.md. This test failing means either a mistake or a decision that "
            "needs recording here."
        )


def test_linkedin_and_eurojobs_are_not_ingested():
    """LinkedIn's User Agreement prohibits scraping and they enforce it; EuroJobs sits behind
    a Cloudflare interstitial and returns nothing. Both adapters exist and are wired to
    nothing, which is the state a README once described as coverage."""
    src = _gather_source()
    for name in ("LinkedInSource", "EuroJobsSource"):
        assert name not in src, f"{name} must not be ingested — see CLAUDE.md"


def test_the_excluded_adapters_still_import():
    """Kept as code, so an exclusion can be reversed by a decision rather than a rewrite."""
    from ingestion.sources.jobscz import JobsCzSource
    from ingestion.sources.profesia import ProfesiaSource

    assert JobsCzSource().source_name == "jobscz"
    assert ProfesiaSource().source_name == "profesia"
