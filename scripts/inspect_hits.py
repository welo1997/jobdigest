"""Print identity evidence for each live, supported board in a discovery CSV.

The discovery script proves a board *serves jobs*; it cannot prove the board belongs to the
company whose name it spells, nor that the postings are real rather than Recruitee/-style demo
content. That check is "read the board's own postings for company and city", and this does it
in bulk: for every row with jobs>0 on a supported ATS, fetch a few postings and print their
titles + locations so a human can eyeball impostors, demo rows, and wrong-country inventory
before anything is wired in.

    python scripts/inspect_hits.py scripts/fr_ats_discovery.csv
    python scripts/inspect_hits.py scripts/fr_ats_discovery.csv --min-jobs 3
"""
from __future__ import annotations
import argparse, csv, sys, json
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingestion import politeness  # noqa: E402
import requests  # noqa: E402

TIMEOUT = 20
SUPPORTED = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday",
             "recruitee", "workable", "oraclecloud"}


def _get(url, method="GET", **kw):
    try:
        politeness.throttle(url)
        r = requests.request(method, url, headers=politeness.HEADERS, timeout=TIMEOUT, **kw)
        return r if r.status_code == 200 else None
    except requests.RequestException:
        return None


def sample(ats, tok, extra):
    """Return (n, [(title, location), ...]) for up to ~8 postings, best-effort."""
    try:
        if ats == "greenhouse":
            r = _get(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs")
            j = r.json().get("jobs", []) if r else []
            return len(j), [(p.get("title", ""), p.get("location", {}).get("name", "")) for p in j[:8]]
        if ats == "lever":
            r = _get(f"https://api.lever.co/v0/postings/{tok}?mode=json")
            j = r.json() if r else []
            return len(j), [(p.get("text", ""), (p.get("categories") or {}).get("location", "")) for p in j[:8]]
        if ats == "ashby":
            r = _get(f"https://api.ashbyhq.com/posting-api/job-board/{tok}")
            j = r.json().get("jobs", []) if r else []
            return len(j), [(p.get("title", ""), p.get("location", "")) for p in j[:8]]
        if ats == "smartrecruiters":
            r = _get(f"https://api.smartrecruiters.com/v1/companies/{tok}/postings?limit=8")
            j = r.json().get("content", []) if r else []
            def loc(p):
                l = p.get("location", {}) or {}
                return f"{l.get('city','')}, {l.get('country','')}"
            return len(j), [(p.get("name", ""), loc(p)) for p in j]
        if ats == "recruitee":
            r = _get(f"https://{tok}.recruitee.com/api/offers/")
            j = r.json().get("offers", []) if r else []
            return len(j), [(p.get("title", ""), p.get("location", "") or p.get("city", "")) for p in j[:8]]
        if ats == "workable":
            r = _get(f"https://apply.workable.com/api/v1/widget/accounts/{tok}?details=true")
            j = r.json().get("jobs", []) if r else []
            def loc(p):
                return f"{p.get('city','')}, {p.get('country','')}"
            return len(j), [(p.get("title", ""), loc(p)) for p in j[:8]]
        if ats == "oraclecloud":
            region, _, site = extra.partition("/")
            url = (f"https://{tok}.fa.{region}.oraclecloud.com/hcmRestApi/resources/latest/"
                   f"recruitingCEJobRequisitions?onlyData=true&expand=requisitionList&"
                   f"finder=findReqs;siteNumber={site or 'CX_1'},limit=8,offset=0")
            r = _get(url)
            items = r.json().get("items", []) if r else []
            reqs = items[0].get("requisitionList", []) if items else []
            return (items[0].get("TotalJobsCount", 0) if items else 0), \
                   [(p.get("Title", ""), p.get("PrimaryLocation", "")) for p in reqs[:8]]
        if ats == "workday":
            shard, _, site = extra.partition("/")
            url = f"https://{tok}.{shard}.myworkdayjobs.com/wday/cxs/{tok}/{site}/jobs"
            r = _get(url, method="POST", headers={**politeness.HEADERS, "Content-Type": "application/json"},
                     json={"limit": 8, "offset": 0, "searchText": ""})
            if not r:
                return 0, []
            d = r.json()
            return d.get("total", 0), [(p.get("title", ""),
                                        p.get("locationsText", "")) for p in d.get("jobPostings", [])[:8]]
    except (ValueError, KeyError, TypeError, requests.RequestException, IndexError):
        return -1, []
    return -1, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--min-jobs", type=int, default=1)
    args = ap.parse_args()

    with open(args.csv, encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh)]
    for r in rows:
        try:
            jobs = int(r["jobs"])
        except ValueError:
            continue
        if jobs < args.min_jobs or r["ats"] not in SUPPORTED:
            continue
        n, items = sample(r["ats"], r["token"], r.get("extra", ""))
        print(f"\n### {r['company']}  ->  {r['ats']}:{r['token']}"
              f"{'/' + r['extra'] if r['extra'] else ''}  (reported {jobs}, sampled {n})")
        cities = Counter()
        for title, loc in items:
            loc = (loc or "").replace("\n", " ").strip()
            cities[loc] += 1
            print(f"    - {title[:60]:60}  |  {loc[:40]}")
        if cities:
            print(f"    cities: {dict(cities)}")


if __name__ == "__main__":
    main()
