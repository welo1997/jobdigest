"""The misfile instrument has to be faithful, or every number it prints is fiction.

`scripts/matchspans.py` groups the corpus by the taxonomy fragment that claimed each title.
It is a measuring device, and a measuring device that is wrong is worse than none: the misfile
pass of 2026-08-22 moved ~5 600 postings on the strength of what it reported, and nothing else
in the repo can see a misfile to contradict it — the watchdog counts `uncategorised`, and a
misfile is the opposite of that.

Three properties, each pinned so that breaking it goes red:

  - **The splitter loses nothing.** Re-joining the branches must reproduce the pattern source
    byte for byte. A scanner that mis-handles a nested `(?:er|ère)` or a `[äæ]` class silently
    drops vocabulary, and the report would then attribute titles to the wrong fragment.
  - **The instrumented pattern is the real pattern.** Adding capture groups cannot change what
    Python's `re` matches — but "cannot" is a claim about the library, and this file is where
    it gets checked against the actual patterns rather than asserted in a comment.
  - **An anchored guard collapses to ONE bucket.** This is the bug the promotion fixed. The
    scratchpad version grouped on `m.group(0)`, which for `^(?!…).*\\bgp\\b` is the whole title
    prefix — so each title got its own key and the fragment most in need of an audit was the
    one the tool could not see. Mutation-check: switch `attribute` back to `group(0)` and the
    last test goes red.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from service import taxonomy

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "matchspans.py"


def _load():
    spec = importlib.util.spec_from_file_location("matchspans", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["matchspans"] = module
    spec.loader.exec_module(module)
    return module


ms = _load()

#: Titles that exercise the shapes the splitter and the attribution have to survive: bare
#: words, stems, diacritics, the bound Italian/French/German heads, the sector-word deferral,
#: and every anchored negative-lookahead guard in the file. Real production text where the
#: comments in `taxonomy.py` quote it.
TITLES = [
    "Senior Nurse",
    "Assistant GP Vacancy",
    "GP-Supply Chain and Quality Management",
    "Senior Software Engineer, Scale GP",
    "Sjuksköterska till akuten",
    "Stipendiat i veterinærmedisin",
    "Veterinær søkes til smådyrklinikk",
    "Jurist inom Hälso- och sjukvårdsjuridik",
    "Kock till Äldreomsorgen Alingsås",
    "Data scientist, Folktandvården",
    "Full-stack vývojář ve zdravotnictví",
    "Mekanikkonstruktör",
    "Konstruktér do vývoje",
    "Addetto/a vendite",
    "Technicien(ne) maintenance",
    "Chargé de recrutement",
    "Ingénieur de production",
    "Java vývojář",
    "Program Manager",
    "Legal Program Manager",
    "Head of Partnerships",
    "Corporate Strategy Director",
    "Miljøterapeut i bofellesskap",
    "Specialist",
    "Vil du bli med på laget?",
    "AI Application Operations & Maintenance Engineer",
    "Experienced Scientist, Biotherapeutics RD",
    "Przedstawiciel handlowy - branża farmaceutyczna",
    "Sektionschef",
    "Prodavač Mekonomen",
    "HR Administrator, 250 CZK/hr",
]


def test_every_pattern_can_be_instrumented():
    """An empty branch or a numbered backreference is refused loudly, not misattributed."""
    insts = ms.instruments()
    assert set(insts) == {c for c, _ in taxonomy.PATTERNS}
    assert sum(len(i.branches) for i in insts.values()) > 500  # it reads a real vocabulary


@pytest.mark.parametrize("category,pattern", list(taxonomy.PATTERNS))
def test_splitting_loses_nothing(category, pattern):
    """Re-joining the top-level branches must reproduce the source exactly.

    Anything less means the scanner ate vocabulary — silently, and in a way that shows up as
    titles attributed to the wrong fragment rather than as an error.
    """
    branches = ms.top_level_branches(pattern.pattern)
    assert "|".join(branches) == pattern.pattern, f"{category}: splitter lost or added text"
    assert all(b.strip() for b in branches), f"{category}: an empty branch matches everything"


@pytest.mark.parametrize("category,pattern", list(taxonomy.PATTERNS))
def test_every_branch_compiles_on_its_own(category, pattern):
    """A branch cut through a nested group would not compile — the cheapest proof of the cut."""
    for i, branch in enumerate(ms.top_level_branches(pattern.pattern)):
        try:
            re.compile(branch, pattern.flags)
        except re.error as exc:  # pragma: no cover - only fires on a broken splitter
            pytest.fail(f"{category} branch {i} does not compile alone: {branch!r} ({exc})")


@pytest.mark.parametrize("category,_pattern", list(taxonomy.PATTERNS))
def test_instrumented_matches_where_the_real_pattern_matches(category, _pattern):
    """Same match or same miss, and the same span, on every probe title."""
    inst = ms.instruments()[category]
    for title in TITLES:
        assert inst.faithful(title), f"{category} disagrees with itself on {title!r}"


def test_attribution_names_a_branch_that_actually_matches():
    """The reported fragment must be one that fires on the title, alone."""
    insts = ms.instruments()
    for title in TITLES:
        live = taxonomy.classify(title)
        inst = insts.get(live)
        if inst is None:  # uncategorised, or won on a hint
            continue
        idx = inst.attribute(title)
        if idx is None:
            continue
        branch = re.compile(inst.branches[idx], inst.pattern.flags)
        assert branch.search(title), (
            f"{live}: attributed {title!r} to {inst.branches[idx]!r}, which does not match it")


#: Three real-shaped titles claimed by ONE fragment — the anchored GP guard. `.*` gobbles, so
#: each has a different `group(0)`; the fragment that claimed them is the same.
_ANCHORED = ["Assistant GP Vacancy", "Locum GP needed", "Salaried GP, Manchester"]


def test_group0_would_scatter_an_anchored_guard():
    """The premise of the next test, asserted separately so a change of premise is visible."""
    inst = ms.instruments()["healthcare"]
    matched = {inst.pattern.search(t).group(0) for t in _ANCHORED}
    assert len(matched) == 3, f"group(0) no longer scatters these: {matched}"
    assert all(taxonomy.classify(t) == "healthcare" for t in _ANCHORED)


def test_the_report_buckets_an_anchored_guard_as_one_fragment():
    """The bug this promotion exists to fix, asserted on the report's own bucket key.

    The scratchpad version keyed buckets on the matched substring. For
    `^(?!.*(?:software|engineer|supply chain)).*\\bgp\\b` that is the whole title prefix, so
    every title got its own key: the branch's real posting total was scattered across one-row
    buckets and the fragment most in need of an audit was the one the tool could not see.

    Mutation-check: key `matchspans.group` on `m.group(0)` instead of the branch and this goes
    red — one bucket of 30 postings becomes three of 10.
    """
    inst = ms.instruments()["healthcare"]
    rows = [ms.Row("healthcare", 10, "nhsjobs", t) for t in _ANCHORED]

    buckets, stale = ms.group(rows, "healthcare", inst)

    assert not stale
    assert len(buckets) == 1, (
        f"one fragment claimed all three titles but the report made {len(buckets)} buckets — "
        f"the totals it prints are scattered")
    (idx, bucket), = buckets.items()
    assert bucket.postings == 30 and bucket.titles == 3
    assert "gp" in inst.branches[idx]
