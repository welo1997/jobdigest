"""The curated ATS company lists must not overlap.

Greenhouse, Lever, Ashby, SmartRecruiters and Workday are each read from a hand-maintained
list, and several companies are reachable on more than one — `clickhouse` serves the same
jobs through Greenhouse and Ashby, `qonto` through Lever and Ashby. Listing a company twice
does not produce two adverts a subscriber sees twice: `digest.dedupe_key` collapses them in
the email. It produces two *rows*, two `posting_id`s, and two slots consumed in the
~120-posting shortlist that the AI matcher is given — the cost lands on retrieval, where
nothing reports it.

The lists are edited by hand, months apart, while looking at a probe result rather than at
each other. That is exactly the kind of rule a test should hold.
"""

from __future__ import annotations

import csv
import itertools
from pathlib import Path

from ingestion.sources.ashby import ORGS as ASHBY
from ingestion.sources.lever import ORGS as LEVER
from ingestion.sources.smartrecruiters import TENANTS as SMARTRECRUITERS
from ingestion.sources.workday import SITES as WORKDAY

SEED = Path(__file__).resolve().parents[2] / "dbt" / "seeds" / "target_companies.csv"


def _greenhouse() -> list[str]:
    with SEED.open(newline="", encoding="utf-8") as handle:
        return [row["board_token"] for row in csv.DictReader(handle)
                if row.get("ats") == "greenhouse" and row.get("board_token")]


def _lists() -> dict[str, list[str]]:
    return {
        "greenhouse": _greenhouse(),
        "ashby": list(ASHBY),
        "lever": list(LEVER),
        "smartrecruiters": [t.lower() for t in SMARTRECRUITERS],
        "workday": [tenant.lower() for tenant, _, _ in WORKDAY],
    }


def test_no_company_is_listed_on_two_ats_boards():
    for (name_a, a), (name_b, b) in itertools.combinations(_lists().items(), 2):
        overlap = set(a) & set(b)
        assert not overlap, (
            f"{sorted(overlap)} is listed on both {name_a} and {name_b}; the same postings "
            "would be stored twice and consume two shortlist slots each"
        )


def test_no_list_repeats_itself():
    for name, tokens in _lists().items():
        duplicates = {t for t in tokens if tokens.count(t) > 1}
        assert not duplicates, f"{name} lists {sorted(duplicates)} more than once"


def test_workday_sites_are_complete_triples():
    """A Workday site needs tenant, host shard and slug — two of the three is a 404."""
    for entry in WORKDAY:
        assert len(entry) == 3, entry
        tenant, host, site = entry
        assert tenant and site, entry
        assert host.startswith("wd"), f"{tenant}: {host!r} is not a Workday host shard"
