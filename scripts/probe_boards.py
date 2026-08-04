"""Ask every curated board whether it still serves jobs, and print the dead ones.

**Offline audit tool, not an ingestion source** — the same class as `discover_ats.py`. You
run it by hand, read the report, and edit the seed or the adapter list yourself.

Why this exists
---------------
CLAUDE.md says: *"A board that goes dark is a silent zero: `fetch` skips a non-200 without an
error-level log. Re-probe the lists rather than assuming (`dbtlabsinc` and `nubank` were both
dead when the seed was last checked)."* Nothing in the repo actually did that re-probing, so
"re-probe rather than assume" was itself an assumption. A curated list is the one input that
cannot fail loudly: a token that stops resolving costs exactly one company's inventory, every
day, in silence, and the only symptom is a number in `select source, count(*)` that nobody has
a baseline for.

It reads the lists from the adapters themselves rather than from a copy, so a list that grows
is covered without touching this file. Two failure shapes it distinguishes, because they need
different fixes:

* **dead** — the API answered, and the board is gone or empty. Remove the row.
* **unreachable** — no answer at all (timeout, DNS, 5xx). Do *not* remove on one bad run;
  re-run before editing, because a transient failure and a retired board look identical here
  and only one of them is worth a commit.

Every count is measured from the ATS's own API. Nothing is inferred from a name resolving.

One known blind spot, found by mutation-checking this file
----------------------------------------------------------
`oraclecloud` verifies the **tenant, not the site**. Probing Vertiv's real tenant with the
invented site `CX_999` returns 2 240 jobs, not zero — Oracle ignores an unrecognised
`siteNumber` and answers for the tenant. That is the same shape as Remotive's ignored
`?category=` and Platsbanken's ORed filters: a parameter that silently does nothing. So an
`oraclecloud` row reading "live" proves the tenant answers; it does not prove the site slug in
`SITES` is still right. Every other ATS here rejects a bad token outright, verified the same
way. If an Oracle site ever needs checking, compare its count against the number recorded in
the `SITES` comment — a site that has silently fallen back to the tenant will not match it.

Usage
-----
    python scripts/probe_boards.py                 # everything
    python scripts/probe_boards.py --only greenhouse,lever
    python scripts/probe_boards.py --out scratch/boards.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from ingestion import politeness  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("probe")
logging.getLogger("ingestion.politeness").setLevel(logging.ERROR)

TIMEOUT = 25

#: Sentinel for "no answer at all", kept distinct from a measured 0. A board that answers
#: with zero jobs is a decision (drop it); a board that never answered is a re-run.
UNREACHABLE = -1


@dataclass
class Board:
    ats: str
    label: str          # what to grep for when editing the list
    token: str
    extra: str = ""
    jobs: int = UNREACHABLE
    note: str = ""


def _get(url: str) -> requests.Response | None:
    try:
        politeness.throttle(url)
        r = requests.get(url, headers={**politeness.HEADERS, "Accept": "application/json"},
                         timeout=TIMEOUT)
        return r if r.status_code == 200 else None
    except requests.RequestException:
        return None


# --- where the curated lists live -------------------------------------------------------
# Imported from the adapters, never copied. A list that grows is covered for free; a list
# that moves breaks this import loudly, which is the right failure.

def collect() -> list[Board]:
    boards: list[Board] = []

    from ingestion.sources.greenhouse import GreenhouseSource
    # Via the source's own loader rather than re-reading the CSV, so this also fails when the
    # seed does not reach the caller — the `.dockerignore` trap that once made the largest
    # source load zero boards for the life of an image.
    for tok in GreenhouseSource()._board_tokens:  # noqa: SLF001
        boards.append(Board("greenhouse", "dbt/seeds/target_companies.csv", tok))

    from ingestion.sources.lever import ORGS as LEVER
    boards += [Board("lever", "ingestion/sources/lever.py:ORGS", t) for t in LEVER]

    from ingestion.sources.ashby import ORGS as ASHBY
    boards += [Board("ashby", "ingestion/sources/ashby.py:ORGS", t) for t in ASHBY]

    from ingestion.sources.smartrecruiters import TENANTS
    boards += [Board("smartrecruiters", "ingestion/sources/smartrecruiters.py:TENANTS", t)
               for t in TENANTS]

    from ingestion.sources.recruitee import COMPANIES
    boards += [Board("recruitee", "ingestion/sources/recruitee.py:COMPANIES", t)
               for t in COMPANIES]

    from ingestion.sources.workable import ACCOUNTS
    boards += [Board("workable", "ingestion/sources/workable.py:ACCOUNTS", t)
               for t in ACCOUNTS]

    from ingestion.sources.workday import SITES as WD
    for row in WD:
        tenant, shard, site = row[0], row[1], row[2]
        boards.append(Board("workday", "ingestion/sources/workday.py:SITES", tenant,
                            f"{shard}/{site}"))

    from ingestion.sources.oraclecloud import SITES as ORA
    for row in ORA:
        tenant, region, site = row[0], row[1], row[2]
        boards.append(Board("oraclecloud", "ingestion/sources/oraclecloud.py:SITES", tenant,
                            f"{region}/{site}"))

    return boards


# --- verification -----------------------------------------------------------------------

def verify(b: Board) -> Board:
    try:
        if b.ats == "greenhouse":
            r = _get(f"https://boards-api.greenhouse.io/v1/boards/{b.token}/jobs")
            b.jobs = len(r.json().get("jobs", [])) if r else UNREACHABLE
        elif b.ats == "lever":
            r = _get(f"https://api.lever.co/v0/postings/{b.token}?mode=json")
            b.jobs = len(r.json()) if r else UNREACHABLE
        elif b.ats == "ashby":
            r = _get(f"https://api.ashbyhq.com/posting-api/job-board/{b.token}")
            b.jobs = len(r.json().get("jobs", [])) if r else UNREACHABLE
        elif b.ats == "smartrecruiters":
            r = _get(f"https://api.smartrecruiters.com/v1/companies/{b.token}/postings?limit=10")
            b.jobs = int(r.json().get("totalFound", 0)) if r else UNREACHABLE
        elif b.ats == "recruitee":
            r = _get(f"https://{b.token}.recruitee.com/api/offers/")
            b.jobs = len(r.json().get("offers", [])) if r else UNREACHABLE
        elif b.ats == "workable":
            r = _get(f"https://apply.workable.com/api/v1/widget/accounts/{b.token}?details=true")
            b.jobs = len(r.json().get("jobs", [])) if r else UNREACHABLE
        elif b.ats == "oraclecloud":
            region, _, site = b.extra.partition("/")
            url = (f"https://{b.token}.fa.{region}.oraclecloud.com/hcmRestApi/resources/"
                   f"latest/recruitingCEJobRequisitions")
            finder = f"findReqs;siteNumber={site},limit=1,offset=0"
            r = _get(f"{url}?onlyData=true&expand=requisitionList&finder={finder}")
            items = r.json().get("items", []) if r else []
            b.jobs = int(items[0].get("TotalJobsCount", 0)) if items else UNREACHABLE
        elif b.ats == "workday":
            shard, _, site = b.extra.partition("/")
            url = f"https://{b.token}.{shard}.myworkdayjobs.com/wday/cxs/{b.token}/{site}/jobs"
            politeness.throttle(url)
            r = requests.post(url, headers={**politeness.HEADERS,
                                            "Content-Type": "application/json"},
                              json={"limit": 1, "offset": 0, "searchText": ""},
                              timeout=TIMEOUT)
            b.jobs = int(r.json().get("total", 0)) if r.status_code == 200 else UNREACHABLE
    except (ValueError, KeyError, TypeError, IndexError, requests.RequestException) as exc:
        b.jobs = UNREACHABLE
        b.note = type(exc).__name__
    return b


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated ATS names")
    ap.add_argument("--out", default="scripts/board_health.csv")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    boards = collect()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        boards = [b for b in boards if b.ats in wanted]

    logger.info("Probing %d boards across %d ATSes...\n",
                len(boards), len({b.ats for b in boards}))

    done: list[Board] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(verify, b) for b in boards]
        for fut in as_completed(futures):
            done.append(fut.result())

    dead = [b for b in done if b.jobs == 0]
    gone = [b for b in done if b.jobs == UNREACHABLE]
    live = [b for b in done if b.jobs > 0]

    for title, rows in (("DEAD (answered, zero jobs — remove the row)", dead),
                        ("UNREACHABLE (no answer — re-run before editing)", gone)):
        if rows:
            logger.info("%s", title)
            for b in sorted(rows, key=lambda x: (x.ats, x.token)):
                logger.info("  %-16s %-28s %s %s", b.ats, b.token + (f" [{b.extra}]" if b.extra else ""),
                            b.label, b.note)
            logger.info("")

    by_ats: dict[str, list[Board]] = {}
    for b in done:
        by_ats.setdefault(b.ats, []).append(b)
    logger.info("%-16s %6s %6s %6s %9s", "ats", "live", "dead", "gone", "jobs")
    for ats in sorted(by_ats):
        rows = by_ats[ats]
        logger.info("%-16s %6d %6d %6d %9d", ats,
                    len([b for b in rows if b.jobs > 0]),
                    len([b for b in rows if b.jobs == 0]),
                    len([b for b in rows if b.jobs == UNREACHABLE]),
                    sum(b.jobs for b in rows if b.jobs > 0))
    logger.info("%-16s %6d %6d %6d %9d", "TOTAL", len(live), len(dead), len(gone),
                sum(b.jobs for b in live))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ats", "token", "extra", "jobs", "status", "list", "note"])
        for b in sorted(done, key=lambda x: (x.ats, -x.jobs, x.token)):
            status = "live" if b.jobs > 0 else ("dead" if b.jobs == 0 else "unreachable")
            w.writerow([b.ats, b.token, b.extra, b.jobs, status, b.label, b.note])
    logger.info("\nWrote %s", out)


if __name__ == "__main__":
    main()
