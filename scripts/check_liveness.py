"""Probe candidate URLs for liveness. HEAD first, GET if HEAD is rejected.

Heuristic: status 200 + page content longer than a stub → likely live.
Some ATS sites return 200 even for closed roles, so we also look for
common closed-role markers in the HTML body.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


CANDIDATES = [
    ("Shoptet — Junior Data Analyst", "https://www.startupjobs.cz/nabidka/102845/junior-data-analyst-data-analyst"),
    ("Data Mind — Data Engineer", "https://www.startupjobs.cz/nabidka/102491/"),
    ("Sudolabs — AI Engineer", "https://www.startupjobs.cz/nabidka/88845/"),
    ("Allegro — Data Analyst (SMART!)", "https://www.adzuna.pl/land/ad/5701997293"),
    ("N-iX — Middle Data Engineer", "https://www.adzuna.pl/land/ad/5679800163"),
    ("Veneficus — traineeship", "https://www.adzuna.nl/land/ad/5481870095"),
    ("BizzTreat — Medior Data Analyst", "https://www.startupjobs.cz/nabidka/103869/medior-data-analyst-moznost-remote-spoluprace"),
    ("Airtable — Business Analytics", "https://job-boards.greenhouse.io/airtable/jobs/8470036002"),
    ("Airtable — Marketing Analytics", "https://job-boards.greenhouse.io/airtable/jobs/8434307002"),
    ("Airtable — AI & Analytics Platform", "https://job-boards.greenhouse.io/airtable/jobs/8434287002"),
    # Also probe the other CZ 8s the user might still want:
    ("CDN77 — Business Data Engineer", "https://www.startupjobs.cz/nabidka/102695/"),
    ("BARTON — DWH/ETL Python full-remote", "https://www.jobs.cz/rpd/2001179651/"),
    ("Adrez — Data Analyst / Analytics Engineer", "https://www.startupjobs.cz/nabidka/103877/data-analyst-analytics-engineer"),
]


CLOSED_MARKERS = [
    "nabídka byla již obsazena",
    "nabídka již byla obsazena",
    "nabídka již není aktuální",
    "tato nabídka již není aktivní",
    "neexistuje nebo již byla obsazena",
    "this job is no longer available",
    "this position is no longer available",
    "this position has been filled",
    "we are no longer accepting applications",
    "job has been filled",
    "this job has expired",
    "this listing is no longer active",
    "no longer accepting applications",
    "page not found",
    "stránka neexistuje",
]

TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.I)


def probe(url: str, retries: int = 2) -> tuple[str, int, str]:
    """Return (verdict, status_code, note)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    }
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        except requests.RequestException as exc:
            return ("ERROR", 0, str(exc)[:80])
        if resp.status_code == 429 and attempt < retries:
            time.sleep(5 * (attempt + 1))
            continue
        break
    status = resp.status_code
    body_lower = resp.text.lower()
    title_m = TITLE_RE.search(resp.text)
    title = (title_m.group(1).strip()[:80]) if title_m else ""
    if status >= 400:
        return ("DEAD", status, f"title: {title}")
    for marker in CLOSED_MARKERS:
        if marker in body_lower:
            return ("CLOSED", status, f"{marker[:40]} | title: {title}")
    if len(resp.text) < 1500:
        return ("STUB", status, f"body {len(resp.text)}b | title: {title}")
    return ("LIVE", status, f"title: {title}")


if __name__ == "__main__":
    print(f"{'verdict':<8} {'status':<6} {'label':<48} note")
    print("-" * 110)
    for label, url in CANDIDATES:
        verdict, status, note = probe(url)
        print(f"{verdict:<8} {status:<6} {label:<48} {note}")
        if verdict == "LIVE":
            print(f"         {url}")
