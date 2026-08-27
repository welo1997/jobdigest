"""Automated identity triage for `discover_ats` output — the impostor check `inspect_hits.py`
does by eye, made a machine step.

**Offline tool, not an ingestion source.** `discover_ats` walks a company's careers pages, reads
the ATS slug off them, and writes a CSV of live boards (`jobs > 0`). Proving a board *serves
jobs* is not proving it *belongs to the company whose name it spells* — the whole history of this
discovery work is boards that returned a live 200 while belonging to someone else (`ashby:eon`,
`lever:zeiss`, Recruitee demo content), and that check was a human reading `inspect_hits.py`
output. This reads the same board postings and returns a verdict:

- **PASS** — the ATS exposes the employer's *own* name (`teamtailor` `hiringOrganization.name`,
  `workable` account `name`, `greenhouse` board `name`, `recruitee` `company_name`) and it matches
  the company the CSV probed for, and the board serves target-region postings.
- **REJECT** — a *positive* name mismatch: the impostor caught, not merely unproven.
- **REVIEW** — everything the machine cannot settle: an ATS that exposes no employer name
  (`lever`, `ashby`), a board with no postings in the target region, or a borderline name. That
  stays a human's call — the same call the curated adapter-list comments already encode — but the
  evidence is attached, so a REVIEW is a smaller job than a cold `inspect_hits` read. The verified
  PASS rows are still hand-copied into the curated lists; the copy is deliberately not automated.

Why only this half is automated
--------------------------------
The original design also tried to *generate* the candidate list automatically, to remove the
hand-assembled `scripts/cz_companies.csv`. Two sources were tried and **both are non-viable, each
verified against live data on 2026-08-27** — recorded here so no one rebuilds them:

- **Certificate Transparency (crt.sh / certspotter) → dead.** `teamtailor` and `recruitee` serve
  every tenant under a single wildcard cert (`CN=*.teamtailor.com`, SAN `*.teamtailor.com` +
  `teamtailor.com`). No per-tenant certificate is ever issued, so CT logs cannot enumerate tenant
  slugs at all — the log holds only the ATS's own infra subdomains. crt.sh being down was a red
  herring; the approach never worked.
- **ARES (CZ register, CZ-NACE 62) → a flood, not a seed.** `czNace:["62"]` matches ~151 000
  subjects (ARES caps a query at 1 000 and 400s above it), and even narrowed to joint-stock `a.s.`
  it is ~3 100 — overwhelmingly dormant micro-`s.r.o.` that run no ATS. Near-zero yield; the
  CZ-is-platform-bound measurement met head-on.
- **Common Crawl → no coverage.** Its URL index for `*.teamtailor.com` returns only
  `www.teamtailor.com`; tenant boards are `noindex`/low-rank and are not crawled.

So candidate *generation* stays manual (a company list, the `discover_ats` walk). What is
automated is the identity *read* on the boards that walk finds.

Usage
-----
    python scripts/discover_ats.py --companies scripts/cz_companies.csv --out scratch/hits.csv
    python scripts/discover_seed.py verify scratch/hits.csv                 # EEA-wide
    python scripts/discover_seed.py verify scratch/hits.csv --country CZ
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import discover_ats as da  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("seed")
logging.getLogger("ingestion.politeness").setLevel(logging.ERROR)

#: The EEA membership set — what "target region" defaults to. This is the same distinction the
#: rest of the repo draws (see `geo.EEA_COUNTRIES`): GB is deliberately *out*. Kept as a local
#: literal rather than importing `service.geo` because `scripts/` must not depend on `service/`
#: (it is not in the pipeline image), and this needs only membership, not resolution.
EEA = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IS",
    "IE", "IT", "LV", "LI", "LT", "LU", "MT", "NL", "NO", "PL", "PT", "RO", "SK", "SI",
    "ES", "SE",
}

#: Country *names* (any language we are likely to meet in a location string) → ISO-3166 alpha-2.
#: Only the countries that actually appear on these boards need an entry; an unknown name is left
#: unresolved and treated as "not in target", never guessed into one — the on-site-is-unprovable
#: discipline, applied to a location string.
_COUNTRY_NAMES = {
    "czech": "CZ", "czechia": "CZ", "czech republic": "CZ", "česko": "CZ", "česká": "CZ",
    "poland": "PL", "polska": "PL", "polsko": "PL",
    "germany": "DE", "deutschland": "DE", "německo": "DE",
    "france": "FR", "francie": "FR",
    "spain": "ES", "españa": "ES", "espana": "ES",
    "italy": "IT", "italia": "IT",
    "netherlands": "NL", "nederland": "NL", "holland": "NL",
    "slovakia": "SK", "slovensko": "SK",
    "austria": "AT", "österreich": "AT", "osterreich": "AT",
    "sweden": "SE", "sverige": "SE",
    "norway": "NO", "norge": "NO",
    "finland": "FI", "suomi": "FI",
    "denmark": "DK", "danmark": "DK",
    "ireland": "IE", "portugal": "PT", "belgium": "BE", "belgië": "BE",
    "romania": "RO", "românia": "RO", "hungary": "HU", "magyarország": "HU",
    "greece": "GR", "ελλάδα": "GR", "bulgaria": "BG", "croatia": "HR", "hrvatska": "HR",
    "estonia": "EE", "latvia": "LV", "lithuania": "LT", "slovenia": "SI", "slovenija": "SI",
    "luxembourg": "LU", "cyprus": "CY", "malta": "MT", "iceland": "IS",
    "united kingdom": "GB", "uk": "GB", "england": "GB",  # resolved so it can be *excluded*
    "united states": "US", "usa": "US",
}

#: Which curated list a PASS row would be pasted into, per ATS. Purely for the report's
#: "ready to paste" grouping — the copy is still a human's, deliberately.
_WIRE_TARGET = {
    "greenhouse": "dbt/seeds/target_companies.csv",
    "lever": "ingestion/sources/lever.py  (ORGS)",
    "ashby": "ingestion/sources/ashby.py  (ORGS)",
    "workable": "ingestion/sources/workable.py  (ACCOUNTS)",
    "teamtailor": "ingestion/sources/teamtailor.py  (TENANTS)",
    "recruitee": "ingestion/sources/recruitee.py  (COMPANIES)",
}


# --- identity ------------------------------------------------------------------------------

def _norm(name: str) -> list[str]:
    """A company name reduced to comparable word tokens: diacritics stripped, legal forms and
    corporate noise removed (reusing `discover_ats._NOISE`), lowercased, alphanumerics only."""
    base = unicodedata.normalize("NFKD", name or "")
    base = "".join(c for c in base if not unicodedata.combining(c))
    base = re.sub(r"\(.*?\)", " ", base)
    base = da._NOISE.sub(" ", base)
    return [w for w in re.findall(r"[a-z0-9]+", base.lower()) if len(w) > 1]


def names_match(expected: str, actual: str) -> bool:
    """Do two company names plausibly denote the same employer?

    The impostor trap is a *guessed* slug resolving to a live board that belongs to a different
    company of a similar name (`ashby:eon` → a US startup, not E.ON). The board's own name is
    the evidence that settles it. Match on either a token-subset (one name's significant words
    are all present in the other — "ROI Hunter" vs "ROI Hunter s.r.o.") or a high whole-string
    similarity, so an abbreviation or an added legal form passes while a different company does
    not."""
    a, b = _norm(expected), _norm(actual)
    if not a or not b:
        return False
    sa, sb = set(a), set(b)
    smaller, larger = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    # A subset match is only trustworthy when the shared part is *distinctive*: two or more
    # tokens. A single common word being a subset of a longer name is the `ashby:amber` trap —
    # "Amber s.r.o." is not "Amber Studio Bucharest", and the repo told them apart by city, not
    # name. So a one-token name must nearly *equal* the other, judged by whole-string similarity.
    if len(smaller) >= 2 and smaller <= larger:
        return True
    return SequenceMatcher(None, "".join(a), "".join(b)).ratio() >= 0.72


def country_of(loc: str) -> str | None:
    """Best-effort ISO-3166 alpha-2 for a free-text location, or None if unreadable.

    An explicit two-letter code wins; otherwise a country *name* anywhere in the string. Reads
    right-to-left on the assumption the country trails the city ("Poznań, Poland"). Deliberately
    conservative: an unrecognised string is None, never a guess — a wrong country here would
    wire a board on false evidence."""
    if not loc:
        return None
    text = loc.strip()
    low = text.lower()
    # Longest names first so "czech republic" wins over a bare "czech".
    for name in sorted(_COUNTRY_NAMES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", low):
            return _COUNTRY_NAMES[name]
    # A trailing bare ISO2 token ("Prague, CZ").
    m = re.search(r"[,\s]([A-Z]{2})\b\s*$", text)
    if m and (m.group(1) in EEA or m.group(1) in {"GB", "US"}):
        return m.group(1)
    return None


@dataclass
class Board:
    """What a live board tells us about itself, best-effort."""
    n: int
    employer: str
    countries: list[str]


def board_identity(ats: str, token: str, extra: str = "") -> Board:
    """Read a board's own postings for employer name and posting countries.

    One request per board, under the politeness gate. Every endpoint here is the same public,
    keyless one `discover_ats._verify` already proved answers; the difference is that this reads
    the *employer name* out too, which is what turns a live-board check into an identity one."""
    def get(url: str) -> dict | list | None:
        r = da._get_api(url)
        try:
            return r.json() if r else None
        except ValueError:
            return None

    employer, countries = "", []
    try:
        if ats == "teamtailor":
            d = get(f"https://{token}.teamtailor.com/jobs.json") or {}
            items = d.get("items", [])
            for p in items:
                jp = p.get("_jobposting") or {}
                if not employer:
                    employer = ((jp.get("hiringOrganization") or {}).get("name") or "").strip()
                for site in (jp.get("jobLocation") or []):
                    cc = ((site or {}).get("address") or {}).get("addressCountry")
                    if cc:
                        countries.append(str(cc).upper()[:2])
            return Board(len(items), employer, countries)
        if ats == "workable":
            d = get(f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true") or {}
            employer = (d.get("name") or "").strip()
            jobs = d.get("jobs", [])
            for p in jobs:
                cc = country_of(f"{p.get('city','')}, {p.get('country','')}")
                if cc:
                    countries.append(cc)
            return Board(len(jobs), employer, countries)
        if ats == "greenhouse":
            meta = get(f"https://boards-api.greenhouse.io/v1/boards/{token}") or {}
            employer = (meta.get("name") or "").strip()
            d = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs") or {}
            jobs = d.get("jobs", [])
            for p in jobs:
                cc = country_of((p.get("location") or {}).get("name", ""))
                if cc:
                    countries.append(cc)
            return Board(len(jobs), employer, countries)
        if ats == "recruitee":
            d = get(f"https://{token}.recruitee.com/api/offers/") or {}
            offers = d.get("offers", [])
            for p in offers:
                if not employer:
                    employer = (p.get("company_name") or "").strip()
                cc = country_of(p.get("country") or p.get("location") or p.get("city") or "")
                if cc:
                    countries.append(cc)
            return Board(len(offers), employer, countries)
        if ats == "ashby":
            d = get(f"https://api.ashbyhq.com/posting-api/job-board/{token}") or {}
            jobs = d.get("jobs", [])
            for p in jobs:
                cc = country_of(p.get("location", ""))
                if cc:
                    countries.append(cc)
            return Board(len(jobs), employer, countries)   # ashby exposes no org name → REVIEW
        if ats == "lever":
            d = get(f"https://api.lever.co/v0/postings/{token}?mode=json")
            jobs = d if isinstance(d, list) else []
            for p in jobs:
                cc = country_of((p.get("categories") or {}).get("location", ""))
                if cc:
                    countries.append(cc)
            return Board(len(jobs), employer, countries)   # lever exposes no org name → REVIEW
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    return Board(-1, employer, countries)


@dataclass
class Verdict:
    status: str          # PASS | REVIEW | REJECT
    employer: str
    countries: list[str]
    n: int
    reason: str


def identity_verdict(ats: str, token: str, extra: str, expected_name: str | None,
                     target: set[str], sampler=None) -> Verdict:
    """PASS / REVIEW / REJECT for one live board. `expected_name` is the company the discovery
    CSV probed for (the impostor is a slug that resolved to a *different* company of that name);
    None disables the name check. `sampler` is resolved at call time (not bound as a default) so
    a test can monkeypatch `board_identity`."""
    sampler = sampler or board_identity
    b = sampler(ats, token, extra)
    uniq = sorted(set(b.countries))
    if b.n <= 0:
        return Verdict("REVIEW", b.employer, uniq, b.n, "no postings sampled")

    # Impostor check — a positive mismatch is the only REJECT, and it needs both a name to
    # match against and a name read off the board. Absence of either is REVIEW, not PASS.
    if expected_name and b.employer and not names_match(expected_name, b.employer):
        return Verdict("REJECT", b.employer, uniq, b.n,
                       f"name mismatch: probed {expected_name!r}, board is {b.employer!r}")

    in_target = [c for c in uniq if c in target]
    if not in_target:
        where = ", ".join(uniq) or "unknown"
        return Verdict("REVIEW", b.employer, uniq, b.n,
                       f"no postings in target region (saw: {where})")
    if not b.employer:
        return Verdict("REVIEW", b.employer, uniq, b.n,
                       "region matches but this ATS exposes no employer name")
    return Verdict("PASS", b.employer, uniq, b.n,
                   f"employer '{b.employer}' in {', '.join(in_target)}")


# --- orchestration -------------------------------------------------------------------------

@dataclass
class Row:
    company: str
    ats: str
    token: str
    extra: str
    jobs: int
    verdict: Verdict


def verify_csv(path: str, target: set[str]) -> list[Row]:
    """Run `identity_verdict` over every live, supported row of a `discover_ats` output CSV.

    The CSV's `company` column is the name the walk probed for, so it is the `expected_name` the
    impostor check needs. Rows with `jobs <= 0` or on an unsupported ATS are skipped — the same
    filter `inspect_hits.py` applies."""
    rows: list[Row] = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                jobs = int(r.get("jobs", "-1"))
            except (TypeError, ValueError):
                jobs = -1
            ats, token = r.get("ats", ""), r.get("token", "")
            if jobs <= 0 or ats not in da.SUPPORTED:
                continue
            company = (r.get("company") or "").strip()
            v = identity_verdict(ats, token, r.get("extra", ""),
                                 expected_name=company or None, target=target)
            rows.append(Row(company, ats, token, r.get("extra", ""), jobs, v))
            logger.info("%-6s %-28s %s:%s (%d) — %s", v.status, company or token, ats, token,
                        jobs, v.reason)
    return rows


def _write(rows: list[Row], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    order = {"PASS": 0, "REVIEW": 1, "REJECT": 2}
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["company", "ats", "token", "extra", "jobs", "verdict", "employer",
                    "countries", "reason"])
        for r in sorted(rows, key=lambda r: (order[r.verdict.status], r.ats, r.company)):
            w.writerow([r.company, r.ats, r.token, r.extra, r.jobs, r.verdict.status,
                        r.verdict.employer, "|".join(r.verdict.countries), r.verdict.reason])


def _report(rows: list[Row]) -> None:
    counts = {"PASS": 0, "REVIEW": 0, "REJECT": 0}
    for r in rows:
        counts[r.verdict.status] += 1
    logger.info("\n%d live boards: %d PASS, %d REVIEW, %d REJECT",
                len(rows), counts["PASS"], counts["REVIEW"], counts["REJECT"])
    passes = [r for r in rows if r.verdict.status == "PASS"]
    if not passes:
        return
    logger.info("\nReady to paste (identity-verified; the copy stays yours):")
    for ats in sorted({r.ats for r in passes}):
        logger.info("\n  # -> %s", _WIRE_TARGET.get(ats, ats))
        for r in sorted((p for p in passes if p.ats == ats), key=lambda r: r.token):
            logger.info('    "%s",  # %d — %s (%s)', r.token, r.jobs, r.verdict.employer,
                        ", ".join(r.verdict.countries))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["verify"])
    ap.add_argument("csv", help="a discover_ats output CSV")
    ap.add_argument("--country", help="restrict target region to one ISO2 (e.g. CZ); "
                                      "default is the whole EEA")
    ap.add_argument("--out", default="scratch/seed_verdicts.csv")
    args = ap.parse_args(argv)

    target = {args.country.upper()} if args.country else EEA
    rows = verify_csv(args.csv, target)
    _write(rows, Path(args.out))
    _report(rows)
    logger.info("Wrote %s", args.out)


if __name__ == "__main__":
    main()
