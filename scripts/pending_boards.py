"""Split a discovery CSV's live, supported hits into NEW vs already-carried.

Discovery re-reports every board it finds, including ones an adapter already lists — so a hit
being `supported=yes` with jobs>0 does NOT mean it is new. Wiring one that is already carried
duplicates the company across two rows (or two ATSes) and `test_curated_boards.py` fails.

**It reads the adapters' actual list structures, not their file text.** That is the whole point
of this rewrite (2026-08-07). The previous version concatenated every file under
`ingestion/sources/` plus the Greenhouse seed into one lowercased blob and asked whether the
token appeared anywhere in it, which was wrong in two independent ways:

  - **A token mentioned in prose counted as carried.** These adapters carry long explanatory
    comments naming the boards that were *rejected* — so a rejected impostor's slug reads as
    already wired, and the board never gets looked at again. `ashby:julius` and `ashby:novo`
    both flagged this way while being absent from `ORGS`.
  - **It ignored which ATS the token belonged to.** A slug carried on Teamtailor counted as
    carried on Workday. `workday:zendesk` flagged off an unrelated mention.

Both errors point the same direction — a *false* "already carried", which silently drops a
genuine board from the report. Four sessions in a row hit it (`ashby:post`, `recruitee:max`,
`workday:thales`, then `ashby:novo`/`ashby:julius`/`workday:zendesk`), each time working around
it by hand. Reading the structures removes the class.

Self-check: `--self-test` asserts a known-carried and a known-absent board on every adapter it
can import, so a refactor that renames a list attribute fails loudly here rather than silently
reporting everything as new.

    python scripts/pending_boards.py scripts/gb_ats_discovery.csv
    python scripts/pending_boards.py --self-test
"""
from __future__ import annotations
import argparse, csv, importlib, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SUPPORTED = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday",
             "recruitee", "workable", "oraclecloud", "teamtailor"}

#: ats -> (module, attribute holding the curated list). Greenhouse reads the dbt seed instead,
#: and workday/oraclecloud hold tuples whose first element is the tenant.
_ATTRS = {
    "lever": ("ingestion.sources.lever", "ORGS"),
    "ashby": ("ingestion.sources.ashby", "ORGS"),
    "recruitee": ("ingestion.sources.recruitee", "COMPANIES"),
    "workable": ("ingestion.sources.workable", "ACCOUNTS"),
    "teamtailor": ("ingestion.sources.teamtailor", "TENANTS"),
    "smartrecruiters": ("ingestion.sources.smartrecruiters", "TENANTS"),
    "workday": ("ingestion.sources.workday", "SITES"),
    "oraclecloud": ("ingestion.sources.oraclecloud", "SITES"),
}


def _flatten(value) -> set[str]:
    """Tokens out of a list / dict / list-of-tuples, lowercased.

    Workday and Oracle store `(tenant, region, site, name)`; the tenant is element 0 and is
    what a discovery row's `token` holds.
    """
    out: set[str] = set()
    items = value.keys() if isinstance(value, dict) else value
    for item in items:
        token = item[0] if isinstance(item, (tuple, list)) and item else item
        if isinstance(token, str):
            out.add(token.strip().lower())
    return out


def carried_by_ats() -> dict[str, set[str]]:
    carried: dict[str, set[str]] = {}
    for ats, (mod_name, attr) in _ATTRS.items():
        try:
            carried[ats] = _flatten(getattr(importlib.import_module(mod_name), attr))
        except Exception as exc:  # noqa: BLE001 — an offline tool must not die on one adapter
            # Loud, because an empty set here reports every board as new — the failure this
            # rewrite exists to prevent, arriving from the other side.
            print(f"WARNING: could not read {mod_name}.{attr} ({exc}); "
                  f"{ats} rows cannot be checked", file=sys.stderr)
            carried[ats] = set()
    seed = ROOT / "dbt" / "seeds" / "target_companies.csv"
    try:
        with open(seed, encoding="utf-8") as fh:
            carried["greenhouse"] = {r["board_token"].strip().lower()
                                     for r in csv.DictReader(fh)
                                     if (r.get("ats") or "").strip() == "greenhouse"}
    except OSError as exc:
        print(f"WARNING: could not read {seed} ({exc})", file=sys.stderr)
        carried["greenhouse"] = set()
    return carried


def self_test() -> int:
    """One carried and one absent token per adapter, so a renamed attribute fails loudly."""
    carried = carried_by_ats()
    cases = [
        ("greenhouse", "intercom", True), ("greenhouse", "definitely-not-a-board", False),
        ("ashby", "pleo", True), ("ashby", "julius", False), ("ashby", "novo", False),
        ("lever", "spotify", True), ("lever", "definitely-not-a-board", False),
        ("teamtailor", "mintos", True), ("teamtailor", "akerbp", False),
        ("workday", "kone", True), ("workday", "definitely-not-a-tenant", False),
    ]
    bad = 0
    for ats, token, expected in cases:
        got = token in carried.get(ats, set())
        if got != expected:
            print(f"FAIL {ats}:{token} — expected carried={expected}, got {got}")
            bad += 1
    print(f"self-test: {len(cases) - bad}/{len(cases)} passed")
    for ats in sorted(carried):
        print(f"  {ats:16} {len(carried[ats]):4} tokens")
    return 1 if bad else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?")
    ap.add_argument("--min-jobs", type=int, default=1)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(self_test())
    if not args.csv:
        ap.error("csv is required unless --self-test")

    carried = carried_by_ats()
    new, old = [], []
    with open(args.csv, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                jobs = int(r["jobs"])
            except (ValueError, TypeError):
                continue
            if jobs < args.min_jobs or r["ats"] not in SUPPORTED:
                continue
            token = (r["token"] or "").strip().lower()
            (old if token in carried.get(r["ats"], set()) else new).append(r)

    print(f"\n=== NEW (not yet carried): {len(new)} ===")
    for r in sorted(new, key=lambda x: -int(x["jobs"])):
        e = f"/{r['extra']}" if r["extra"] else ""
        print(f"  {r['ats']:16}{r['token']}{e:32} ({r['jobs']:>5})  [{r['company']}]")
    print(f"\n=== already carried (skip): {len(old)} ===")
    for r in old:
        print(f"  {r['ats']}:{r['token']}  [{r['company']}]")

    # Same company reaching two different ATSes inside ONE discovery run is the other way a
    # duplicate gets wired, and the NEW list above cannot show it — both rows are new. Ashby
    # and Lever both answer for Frontify; Ashby and Teamtailor both answer for Lunar, and only
    # one of those two is the Danish bank.
    by_company: dict[str, list[str]] = {}
    for r in new:
        by_company.setdefault(r["company"], []).append(f"{r['ats']}:{r['token']}")
    dupes = {c: b for c, b in by_company.items() if len(b) > 1}
    if dupes:
        print(f"\n=== same company on >1 ATS — pick ONE ({len(dupes)}) ===")
        for company, boards in sorted(dupes.items()):
            print(f"  {company:28} {', '.join(boards)}")


if __name__ == "__main__":
    main()
