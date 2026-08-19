"""Sources ingested on *unestablished* permission, and why that set must stay deliberate.

The standing rule in `docs/sources.md` is that permission unestablished is not permission —
the Bundesagentur error. A source that publishes no terms has not permitted anything; it has
merely not spoken, and a live endpoint is not a grant.

**On 2026-08-15 the owner reversed that call for a named, finite set.** These sources publish
no terms of use at all, their robots.txt permits the paths fetched, and nothing anywhere on
the host refuses automated reading. That is a decision about risk appetite on the owner's own
product, and it is recorded in `CLAUDE.md` and `docs/sources.md` rather than only in a commit
message.

What this test protects is the *boundary*, not the decision:

  - The set is **enumerated**. Wiring a sixth source on the same reasoning fails here, so it
    has to be a deliberate edit with a reason attached rather than a habit that spreads.
  - It does **not** extend to a source that refuses. Everything in groups 1–4 of
    `docs/sources.md` — an explicit anti-automation clause, a personal-use-only licence, a
    refusing robots.txt, an application gate — is untouched, and
    `test_source_exclusions.py` still guards the ones with working adapters.
  - It does **not** license ignoring robots.txt. Every adapter here still calls
    `politeness.robots_allows` and `throttle`; `test_politeness.py` is unchanged.

The failure this prevents is the quiet one: "we already read boards with no terms" becoming a
general argument for reading anything that has not explicitly said no.
"""

from __future__ import annotations

import search_jobs

#: Wired deliberately on 2026-08-15 despite publishing no terms. Keep the reason with the name.
UNESTABLISHED_PERMISSION = {
    "RemoteInEuropeSource": (
        "remoteineurope.com — no terms, no privacy policy, none in Wayback; robots permits "
        "the job pages and disallows only /search, /companies, /checkout, /post-a-job-2 and "
        "/internal/. Wired for its multi-country `schema-loc` scopes."
    ),
    # NvaSource was here until 2026-08-19, when it was removed from gather() for lack of
    # product use (see test_source_exclusions.py::test_nva_is_not_ingested). Its permission
    # was never the issue; its ~73-minute crawl for ~570 unwanted Latvian rows was.
    "GoldenCareersSource": (
        "goldencareers.com.cy — a Cypriot agency board. No terms and no privacy policy; "
        "robots.txt is a Yoast block with a bare `Disallow:` (allow-all) advertising the "
        "sitemap that carries the vacancies. ~62 postings, employer anonymised."
    ),
}


def _ingested() -> set[str]:
    return {cls.__name__ for cls in search_jobs.source_classes(True)} | {
        cls.__name__ for cls in search_jobs.source_classes(False)
    }


def test_the_unestablished_permission_set_is_exactly_what_was_decided():
    """A sixth source on this reasoning must be a deliberate edit, not a drift.

    Mutation check: wire any other no-terms source into `gather()` without adding it here and
    this fails, which is the entire point.
    """
    ingested = _ingested()
    known = set(UNESTABLISHED_PERMISSION)

    missing = known - ingested
    assert not missing, (
        f"{sorted(missing)} is listed as wired on unestablished permission but is not in "
        "gather(). Either it was removed — in which case delete it here and say so in "
        "docs/sources.md — or the wiring broke."
    )


def test_every_entry_carries_its_reasoning():
    """A bare name here is how a decision becomes folklore."""
    for name, reason in UNESTABLISHED_PERMISSION.items():
        assert len(reason) > 60, f"{name} needs the actual reason recorded, not a label"
        assert "robots" in reason.lower(), (
            f"{name}: say what robots.txt permits — that is the half of the argument that "
            "is not about silence."
        )


def test_this_does_not_reach_the_sources_that_refuse():
    """The excluded set is untouched by the 2026-08-15 decision.

    Alma Career, LinkedIn, EuroJobs and SmartRecruiters all *refuse* — in terms or in robots.
    Unestablished permission is silence; a refusal is speech. Conflating the two is the
    failure this asserts against.
    """
    ingested = _ingested()
    for refuser in ("JobsCzSource", "ProfesiaSource", "LinkedInSource",
                    "EuroJobsSource", "SmartRecruitersSource"):
        assert refuser not in ingested, (
            f"{refuser} refuses us and must stay out of gather(). The 2026-08-15 decision "
            "covers sources that published nothing, not sources that said no — see "
            "test_source_exclusions.py."
        )
        assert refuser not in UNESTABLISHED_PERMISSION, (
            f"{refuser} refuses us; it can never belong in this set."
        )
