"""Split a discovery CSV's live, supported hits into NEW vs already-carried.

Discovery re-reports every board it finds, including ones an adapter already lists — so a hit
being `supported=yes` with jobs>0 does NOT mean it is new. Wiring one that is already carried
duplicates the company across two rows (or two ATSes) and `test_curated_boards.py` fails.
This prints only the hits worth wiring, by scanning the actual adapter token lists + the
greenhouse seed for each token.

    python scripts/pending_boards.py scripts/gb_ats_discovery.csv
"""
from __future__ import annotations
import argparse, csv, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUPPORTED = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday",
             "recruitee", "workable", "oraclecloud", "teamtailor"}


def carried_tokens() -> set[str]:
    blob = ""
    for p in (ROOT / "ingestion" / "sources").glob("*.py"):
        blob += p.read_text(encoding="utf-8", errors="ignore").lower()
    blob += (ROOT / "dbt" / "seeds" / "target_companies.csv").read_text(
        encoding="utf-8", errors="ignore").lower()
    # Any token that appears as a quoted/CSV word anywhere in the adapters counts as carried.
    return blob


def is_carried(blob: str, token: str) -> bool:
    return re.search(r'[\'",/ ]' + re.escape(token.lower()) + r'\b', blob) is not None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--min-jobs", type=int, default=1)
    args = ap.parse_args()
    blob = carried_tokens()
    new, old = [], []
    with open(args.csv, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                jobs = int(r["jobs"])
            except ValueError:
                continue
            if jobs < args.min_jobs or r["ats"] not in SUPPORTED:
                continue
            (old if is_carried(blob, r["token"]) else new).append(r)
    print(f"\n=== NEW (not yet carried): {len(new)} ===")
    for r in sorted(new, key=lambda x: -int(x["jobs"])):
        e = f"/{r['extra']}" if r["extra"] else ""
        print(f"  {r['ats']:16}{r['token']}{e:32} ({r['jobs']:>5})  [{r['company']}]")
    print(f"\n=== already carried (skip): {len(old)} ===")
    for r in old:
        print(f"  {r['ats']}:{r['token']}  [{r['company']}]")


if __name__ == "__main__":
    main()
