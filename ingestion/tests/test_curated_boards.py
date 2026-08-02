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
from ingestion.sources.greenhouse import GreenhouseSource
from ingestion.sources.lever import ORGS as LEVER
from ingestion.sources.smartrecruiters import TENANTS as SMARTRECRUITERS
from ingestion.sources.workday import SITES as WORKDAY

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "dbt" / "seeds" / "target_companies.csv"
SEED_REL = "dbt/seeds/target_companies.csv"


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


# --- the seed has to reach the backend image, not just the repo ------------------------
#
# Greenhouse is the largest source, and it reads its board tokens from a file under dbt/.
# The backend image deliberately excludes dbt/ as market-intel, which made GreenhouseSource
# load zero boards in production for the life of the image: the loader catches
# FileNotFoundError, logs a warning, and returns []. Nothing errored, no timer failed, and
# `select source, count(*) from postings` simply had no greenhouse row. These tests fail if
# the build stops shipping the one file that keeps the largest source alive.

def _directives(path: Path) -> list[str]:
    """Lines with comments stripped. A guard that can match its own comment cannot fail."""
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")]


def test_greenhouse_resolves_the_seed_the_dockerfile_copies():
    """The loader's path and the build's path must be the same one, in both directions."""
    resolved = Path(GreenhouseSource._load_target_tokens.__globals__["__file__"])
    expected = (resolved.parent / ".." / ".." / "dbt" / "seeds" / "target_companies.csv")
    assert expected.resolve() == SEED.resolve(), (
        f"greenhouse.py resolves {expected.resolve()}, but the seed is at {SEED.resolve()}"
    )
    copied = [d for d in _directives(ROOT / "Dockerfile")
              if d.upper().startswith("COPY") and SEED_REL in d]
    assert copied, (
        f"Dockerfile never copies {SEED_REL}; GreenhouseSource will load 0 boards in the "
        "image and say so only in a warning"
    )


def test_dockerignore_does_not_exclude_the_seed():
    directives = _directives(ROOT / ".dockerignore")
    assert SEED_REL not in [d.lstrip("!") for d in directives if not d.startswith("!")], \
        f"{SEED_REL} is excluded from the build context outright"
    # A bare `dbt/` is the trap: Docker never descends into an excluded directory, so a
    # later `!dbt/seeds/...` re-include silently does nothing.
    assert "dbt/" not in directives, (
        "`dbt/` excludes the directory wholesale — a `!dbt/seeds/target_companies.csv` "
        "after it does not re-include the file. Use the walk-down form (`dbt/*`, "
        "`!dbt/seeds`, `dbt/seeds/*`, `!dbt/seeds/target_companies.csv`)."
    )
    assert f"!{SEED_REL}" in directives, f"nothing re-includes {SEED_REL}"


def test_the_seed_actually_yields_greenhouse_boards():
    """The end the other two only approximate: the loader returns a non-empty list."""
    assert len(GreenhouseSource()._board_tokens) > 0, (
        "GreenhouseSource loaded no board tokens — the largest source would fetch nothing"
    )
