"""`discover_ats.SUPPORTED` must not drift from the adapters `gather()` actually runs.

It is a copy of a fact that lives in `search_jobs.gather()`, and a copy that drifts *downward*
is worse than no copy at all: the discovery report prints `no` next to a board we could
ingest today, and a `no` is indistinguishable from not having found it.

That happened. On 2026-08-04 `recruitee`, `workable` and `oraclecloud` had been live sources
for days and were all reported unsupported. The thin-country pass found **four Greek employer
boards on Workable** — Viva.com, Blueground, Skroutz and Hellas Direct — for a country holding
43 active postings in the entire corpus, and every one of them printed as unsupported.

The check runs the other way too. A name in `SUPPORTED` with no adapter behind it would send
someone hand-copying tokens into a file that does not exist.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

discover = importlib.import_module("scripts.discover_ats")


def _adapter_modules() -> set[str]:
    """Every module in `ingestion/sources` that defines a source class."""
    import ingestion.sources as pkg
    names = set()
    for module_name in [p.stem for p in Path(pkg.__file__).parent.glob("*.py")
                        if p.stem != "__init__"]:
        mod = importlib.import_module(f"ingestion.sources.{module_name}")
        if any(isinstance(obj, type) and obj.__module__ == mod.__name__
               and hasattr(obj, "source_name") for obj in vars(mod).values()):
            names.add(module_name)
    return names


def test_every_supported_name_has_an_adapter():
    """A name here with no adapter behind it is an invitation to copy tokens nowhere."""
    modules = _adapter_modules()
    missing = sorted(discover.SUPPORTED - modules)
    assert not missing, f"SUPPORTED names an ATS with no adapter: {missing}"


@pytest.mark.parametrize("ats", ["greenhouse", "lever", "ashby", "smartrecruiters",
                                 "workday", "recruitee", "workable", "oraclecloud"])
def test_every_live_ats_adapter_is_marked_supported(ats):
    """The direction that actually lost findings: an adapter we run, reported as `no`.

    Listed explicitly rather than derived, because the derived set would also contain
    `jobscz`, `profesia`, `linkedin` and `eurojobs` — adapters that exist as code and are
    deliberately **not** in `gather()`. Those must never be marked supported, which is the
    other half of the guarantee and is asserted below.
    """
    assert ats in discover.SUPPORTED


def test_the_excluded_adapters_are_never_marked_supported():
    """jobs.cz and profesia are excluded on Alma Career's terms; LinkedIn and EuroJobs fetch
    nothing. A discovery report calling any of them supported would be an invitation to
    re-add a source this repo decided against."""
    for ats in ("jobscz", "profesia", "linkedin", "eurojobs", "teamio", "bamboohr",
                "personio"):
        assert ats not in discover.SUPPORTED, ats
    # Teamio is Alma Career's own ATS and BambooHR is refused on its ToS §4.2 — both are
    # recorded as restricted rather than merely absent, so a hit is reported and flagged
    # rather than reappearing next run as an apparent free win.
    assert "teamio" in discover.RESTRICTED
    assert "bamboohr" in discover.RESTRICTED
