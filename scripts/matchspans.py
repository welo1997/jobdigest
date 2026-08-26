#!/usr/bin/env python3
"""Attribute a category's stored titles to the exact taxonomy fragment that won them.

    python scripts/matchspans.py --dump                  # pull the corpus from prod (read-only)
    python scripts/matchspans.py healthcare              # rank the fragments that claimed it
    python scripts/matchspans.py healthcare --branch 12  # every title one fragment claimed
    python scripts/matchspans.py --all --min-rows 20     # the whole corpus, worst-first
    python scripts/matchspans.py --check                 # prove the instrument is faithful

**This is the misfile instrument.** `notes/categorization/PLAN.md` item 5 ends by pointing the
next pass "not at what is uncategorised, but at what is categorised confidently and wrongly",
and a misfile cannot be rescued the way a miss can: the AI matcher never sees the row, because
the row is not in the shortlist it was retrieved for. Nothing reports one. `unmet_demand_terms()`
will not — it reads demand, not supply. The watchdog will not — it counts `uncategorised`, and a
misfile is the opposite of that. The only thing that finds them is reading the corpus, and the
only way to read 100k titles is to group them by *what claimed them*.

`taxonomy.PATTERNS` is one compiled alternation per category, so the fragment that fired is a
single top-level branch of that alternation. Group the corpus by branch and "is anything
misfiled here" becomes a ranked list: a branch whose titles do not belong to the category is a
misfile, and its posting total is the size of the damage. That is how `\\bengineer\\b` (718 rows),
`\\bhr\\b` reading an hourly rate (101) and `ekonom` reading the employer *Mekonomen* were found.

Why the branch and not the matched text
---------------------------------------
The first version of this grouped on ``m.group(0)`` — the matched substring. That works only
while every branch is a bare word. It silently degrades the moment a branch carries an anchored
guard, and this file's patterns carry seven of them::

    ^(?!.*(?:software|engineer|supply chain)).*\\bgp\\b

``.*`` gobbles, so ``group(0)`` is the whole title prefix: every title lands under its own
unique key, the branch's real posting total is scattered across dozens of one-row buckets, and
the fragment most likely to be a misfile is the one this tool renders invisible. Exactly the
guards that need auditing are the ones it could not see.

So the branch is identified by the engine itself. Each top-level branch is wrapped in a named
group and the pattern recompiled with the same flags; adding capture groups cannot change what
Python's `re` matches, so the instrumented pattern is the original pattern plus a report of
which alternative won. `--check` asserts that equivalence over the whole corpus rather than
asserting it in a comment — if the instrumented pattern and the real one ever disagree about a
single title, every number this tool prints is worthless, and that is the one failure it must
not have quietly.

Two more properties, both learned the hard way:

- **It recomputes with TODAY's taxonomy** rather than trusting the stored `role_category`.
  `upsert_postings` rewrites the column on conflict, so a pattern edit drains over one ingest
  cycle with no backfill and the DB value can be a day stale. Rows where the two disagree are
  reported separately: those are already-fixed or newly-broken, not findings.
- **It is a file, never a heredoc.** A ``\\b`` in a ``python - <<'PY'`` heredoc arrives as
  ``\\x08`` and the anchor silently fails to match — 0 reported where the truth was 22. That has
  cost four sessions; CLAUDE.md records three of them.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service import taxonomy  # noqa: E402

#: Where `--dump` writes and every other mode reads. Gitignored: it is a 100k-row extract of
#: production, and while it holds public job titles and no personal data whatsoever (the query
#: below touches `postings` only, never `profiles`), a corpus dump is still not a repo artefact.
DEFAULT_CACHE = Path(os.environ.get("MATCHSPANS_CACHE", ROOT / "scripts" / ".corpus-titles.csv"))

#: One row per (category, title) with its posting count and the boards it came from. Aggregate
#: over `postings`; `is_active` because a misfile only costs something while the row is
#: retrievable. `source` matters because a misfile confined to one board is an adapter's hint or
#: an employer's house style, and a misfile spread across ten is our own vocabulary.
DUMP_SQL = """
select coalesce(role_category, 'uncategorised') as category,
       count(*) as postings,
       string_agg(distinct source, ' ' order by source) as sources,
       title
from postings
where is_active and title is not null
group by 1, 4
order by 2 desc
"""


# ----------------------------------------------------------------- the instrument ---


def top_level_branches(source: str) -> list[str]:
    """Split a regex source on its top-level `|`, respecting groups, classes and escapes.

    A naive `source.split("|")` cuts inside `(?:er|ère)` and `[äæ]` and produces fragments
    that do not compile — which is why this is a scanner and not a one-liner.
    """
    out: list[str] = []
    depth = 0
    in_class = False
    start = 0
    i = 0
    n = len(source)
    while i < n:
        c = source[i]
        if c == "\\":
            i += 2  # an escaped char can be '|', '(' or '[' — skip both bytes
            continue
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "|" and depth == 0:
            out.append(source[start:i])
            start = i + 1
        i += 1
    out.append(source[start:])
    return out


class Instrument:
    """One category's pattern, recompiled so the engine names the branch that matched."""

    def __init__(self, category: str, pattern: "re.Pattern[str]") -> None:
        self.category = category
        self.pattern = pattern
        self.branches = top_level_branches(pattern.pattern)

        empty = [i for i, b in enumerate(self.branches) if not b.strip()]
        if empty:
            raise ValueError(
                f"{category}: top-level branch {empty} is empty, so the alternation matches "
                f"every title. That is a bug in taxonomy.PATTERNS, not in this tool."
            )
        # A numbered backreference is the one construct wrapping cannot survive: the added
        # groups renumber everything after them, so \1 would point somewhere else. Refuse
        # loudly rather than misattribute silently.
        if re.search(r"(?<!\\)\\[1-9]", pattern.pattern):
            raise ValueError(
                f"{category}: pattern carries a numbered backreference; wrapping branches in "
                f"groups would renumber it. Convert it to a named group first."
            )

        wrapped = "|".join(f"(?P<b{i}>{b})" for i, b in enumerate(self.branches))
        self.instrumented = re.compile(wrapped, pattern.flags)
        self._names = [f"b{i}" for i in range(len(self.branches))]

    def attribute(self, title: str) -> int | None:
        """Index of the branch that won `title`, or None if the category does not match."""
        m = self.instrumented.search(title)
        if m is None:
            return None
        for i, name in enumerate(self._names):
            if m.group(name) is not None:
                return i
        return None  # unreachable: an alternation match participates in exactly one branch

    def faithful(self, title: str) -> bool:
        """The instrumented pattern must match exactly where the real one does."""
        a = self.pattern.search(title)
        b = self.instrumented.search(title)
        if a is None or b is None:
            return (a is None) == (b is None)
        return a.span() == b.span()


def instruments() -> dict[str, Instrument]:
    return {cat: Instrument(cat, pat) for cat, pat in taxonomy.PATTERNS}


# ---------------------------------------------------------------------- the corpus ---


class Row:
    __slots__ = ("category", "postings", "sources", "title")

    def __init__(self, category: str, postings: int, sources: str, title: str) -> None:
        self.category = category
        self.postings = postings
        self.sources = sources
        self.title = title


def dump(cache: Path, csv_only: bool = False) -> int:
    """Pull the corpus through `scripts/prod_query.sh` — read-only, server-side enforced."""
    script = ROOT / "scripts" / "prod_query.sh"
    proc = subprocess.run(
        ["bash", str(script), "--csv", DUMP_SQL.strip()],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return proc.returncode
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(proc.stdout, encoding="utf-8", newline="")
    rows = max(0, proc.stdout.count("\n") - 1)
    if not csv_only:
        print(f"wrote {rows} (category, title) rows to {cache}")
    return 0


def load(cache: Path) -> list[Row]:
    if not cache.exists():
        raise SystemExit(
            f"no corpus at {cache} — run `python scripts/matchspans.py --dump` first "
            f"(or point MATCHSPANS_CACHE at an existing dump)."
        )
    out: list[Row] = []
    with cache.open(encoding="utf-8", newline="") as fh:
        for rec in csv.DictReader(fh):
            try:
                postings = int(rec["postings"])
            except (KeyError, TypeError, ValueError):
                continue
            out.append(Row(rec["category"], postings, rec.get("sources") or "", rec["title"] or ""))
    return out


# ---------------------------------------------------------------------- reporting ---


class Bucket:
    def __init__(self) -> None:
        self.rows: list[Row] = []

    @property
    def postings(self) -> int:
        return sum(r.postings for r in self.rows)

    @property
    def titles(self) -> int:
        return len(self.rows)

    @property
    def sources(self) -> set[str]:
        return {s for r in self.rows for s in r.sources.split() if s}


def group(rows: list[Row], category: str, inst: Instrument) -> tuple[dict[int, Bucket], list[Row]]:
    """Bucket every title TODAY's taxonomy files under `category` by the branch that won it.

    Returns (buckets, stale) — `stale` is the rows whose stored category is not what the
    current taxonomy says, which are drift rather than findings.
    """
    buckets: dict[int, Bucket] = collections.defaultdict(Bucket)
    stale: list[Row] = []
    for row in rows:
        live = taxonomy.classify(row.title)
        if live != category:
            if row.category == category:
                stale.append(row)
            continue
        idx = inst.attribute(row.title)
        buckets[-1 if idx is None else idx].rows.append(row)
    return buckets, stale


def report_category(rows: list[Row], category: str, inst: Instrument, *, min_rows: int,
                    titles: int, branch: int | None) -> None:
    relevant = [r for r in rows if r.category == category or taxonomy.classify(r.title) == category]
    buckets, stale = group(relevant, category, inst)
    total = sum(b.postings for b in buckets.values())

    print(f"# {category}: {sum(b.titles for b in buckets.values())} distinct titles, "
          f"{total} postings (recomputed with today's taxonomy)")
    if stale:
        n = sum(r.postings for r in stale)
        example = "; ".join(
            f"{r.title!r}->{taxonomy.classify(r.title)}"
            for r in sorted(stale, key=lambda r: -r.postings)[:3])
        print(f"# {len(stale)} stored titles ({n} postings) classify DIFFERENTLY today — "
              f"drift, not findings: {example}")
    print()

    if branch is not None:
        bucket = buckets.get(branch)
        src = inst.branches[branch] if 0 <= branch < len(inst.branches) else "<none>"
        if bucket is None:
            print(f"branch {branch} ({src!r}) claimed nothing")
            return
        print(f"branch {branch}  {src!r}\n"
              f"{bucket.postings} postings, {bucket.titles} titles, "
              f"sources: {' '.join(sorted(bucket.sources))}\n")
        for row in sorted(bucket.rows, key=lambda r: -r.postings):
            print(f"{row.postings:6d}  {row.title}")
        return

    ranked = sorted(buckets.items(), key=lambda kv: -kv[1].postings)
    for idx, bucket in ranked:
        if bucket.postings < min_rows:
            continue
        src = "<hint or source_category, no pattern matched>" if idx < 0 else inst.branches[idx]
        print(f"{bucket.postings:6d} postings  {bucket.titles:5d} titles  "
              f"branch={idx:3d}  {src!r}")
        for row in sorted(bucket.rows, key=lambda r: -r.postings)[:titles]:
            print(f"         {row.postings:5d}  {row.title}")
        print()


def report_all(rows: list[Row], insts: dict[str, Instrument], *, min_rows: int,
               titles: int, as_json: bool) -> None:
    """Every branch in the taxonomy, ranked by postings — the fan-out's work list."""
    by_cat: dict[str, list[Row]] = collections.defaultdict(list)
    for row in rows:
        live = taxonomy.classify(row.title)
        if live in insts:
            by_cat[live].append(row)

    findings = []
    for category, cat_rows in by_cat.items():
        inst = insts[category]
        buckets: dict[int, Bucket] = collections.defaultdict(Bucket)
        for row in cat_rows:
            idx = inst.attribute(row.title)
            buckets[-1 if idx is None else idx].rows.append(row)
        for idx, bucket in buckets.items():
            if bucket.postings < min_rows:
                continue
            findings.append({
                "category": category,
                "branch": idx,
                "fragment": inst.branches[idx] if idx >= 0 else "<no pattern matched>",
                "postings": bucket.postings,
                "titles": bucket.titles,
                "sources": sorted(bucket.sources),
                "examples": [r.title for r in sorted(bucket.rows, key=lambda r: -r.postings)[:titles]],
            })
    findings.sort(key=lambda f: -f["postings"])

    if as_json:
        json.dump(findings, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return
    print(f"# {len(findings)} branches at >= {min_rows} postings, worst-first\n")
    for f in findings:
        print(f"{f['postings']:6d} postings  {f['titles']:5d} titles  "
              f"{f['category']}[{f['branch']}]  {f['fragment']!r}")
        for t in f["examples"]:
            print(f"         {t}")
        print()


def check(rows: list[Row], insts: dict[str, Instrument]) -> int:
    """Prove the instrumented patterns match exactly where the real ones do."""
    bad = 0
    for category, inst in insts.items():
        for row in rows:
            if not inst.faithful(row.title):
                bad += 1
                if bad <= 10:
                    print(f"MISMATCH {category}: {row.title!r} "
                          f"real={inst.pattern.search(row.title)} "
                          f"instrumented={inst.instrumented.search(row.title)}")
    n_branches = sum(len(i.branches) for i in insts.values())
    if bad:
        print(f"\n{bad} disagreements — every number this tool prints is unsafe until that is 0")
        return 1
    print(f"ok: {len(insts)} categories, {n_branches} branches, {len(rows)} titles, "
          f"instrumented == real on all of them")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("category", nargs="?", help="the category to break down")
    ap.add_argument("--dump", action="store_true", help="refresh the corpus from production")
    ap.add_argument("--all", action="store_true", help="rank every branch in every category")
    ap.add_argument("--check", action="store_true", help="instrumented == real, on the corpus")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--min-rows", type=int, default=1, help="hide branches below this many postings")
    ap.add_argument("--titles", type=int, default=6, help="example titles per branch")
    ap.add_argument("--branch", type=int, help="list every title one branch claimed")
    ap.add_argument("--json", action="store_true", help="machine-readable (--all only)")
    a = ap.parse_args(argv)

    if a.dump:
        rc = dump(a.cache)
        if rc or not (a.category or a.all or a.check):
            return rc

    insts = instruments()

    if a.check:
        return check(load(a.cache), insts)
    if a.all:
        report_all(load(a.cache), insts, min_rows=a.min_rows, titles=a.titles, as_json=a.json)
        return 0
    if not a.category:
        ap.error("give a category, or --all / --check / --dump")
    if a.category not in insts:
        print(f"no pattern for {a.category!r}; known: {', '.join(sorted(insts))}", file=sys.stderr)
        return 2
    report_category(load(a.cache), a.category, insts[a.category],
                    min_rows=a.min_rows, titles=a.titles, branch=a.branch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
