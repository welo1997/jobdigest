"""Automated candidate generation + identity verdict around `discover_ats`.

**Offline discovery tool, not an ingestion source.** Like `discover_ats`, it never runs in the
digest window; you run it, read the report, and the *verified* rows are hand-copied into the
curated adapter lists. What this adds is the removal of the two manual ends `discover_ats` left:
the hand-assembled input list (`scripts/cz_companies.csv`) and the by-eye identity check
(`scripts/inspect_hits.py`). Both are now machine-produced, so a run needs no company list typed
in advance and hands back a PASS / REVIEW / REJECT verdict per board instead of a wall of
postings to squint at.

Two automatable candidate sources, **neither an aggregator's database** — the jobs.cz route is
closed on Alma Career's §4.11 and the EU database right (96/9/EC), and enumerating employers off
their board is a *more* substantial extraction than the postings `gather()` already excludes
(see `discover_ats`'s "Deliberately not derived from jobs.cz"):

1. **Certificate Transparency** (`crt.sh`) → tenant slugs for the subdomain-per-tenant ATSes
   (`teamtailor`, `recruitee`). Every tenant that ever provisioned TLS on `*.teamtailor.com` is
   listed in a public append-only log; the subdomain label *is* the ATS slug. Nothing is
   guessed, so the impostor trap that dogs name-derived slugs (`ashby:eon`, `lever:zeiss`) does
   not arise here — the only open question is whether the board serves postings we want, which
   is a country filter, not an identity one.
2. **ARES** (the Czech open business register, CZ-NACE 62 = programming/IT) → Czech company
   *names* → the existing `discover_ats.probe_api_slugs` for the path-based ATSes
   (`greenhouse`/`lever`/`ashby`/`workable`), whose slug is a single guessable token. Here a
   guessed slug **can** collide with another real company, so identity must be able to REJECT.

Both feed one gate, `identity_verdict`, which automates what `inspect_hits.py` does by eye:
read the board's own postings, extract the employer name and posting countries, and decide.

- **PASS** only where the ATS exposes the employer's *own* name (`teamtailor`
  `hiringOrganization.name`, `workable` account `name`, `greenhouse` board `name`) and it either
  matches the name we probed for or — for a CT slug, where there is no name to match against —
  is simply read off the feed while the board serves target-region postings.
- **REJECT** only on a *positive* name mismatch — the impostor caught, not merely unproven.
- **REVIEW** for everything the machine cannot settle: an ATS that exposes no employer name
  (`lever`, `ashby`), a board with no postings in the target region, or a borderline name. That
  stays a human's call — the same call the curated adapter-list comments already encode — and a
  REVIEW is a smaller job than a cold `inspect_hits` read, because the evidence is attached.

Output is `discover_ats`'s CSV schema plus `verdict`/`reason`/`employer` columns, so
`inspect_hits.py` and the curated wiring flow keep working unchanged.

Usage
-----
    python scripts/discover_seed.py ct                       # teamtailor + recruitee, EEA-wide
    python scripts/discover_seed.py ct --host teamtailor.com --limit 300
    python scripts/discover_seed.py ares --country CZ --limit 200
    python scripts/discover_seed.py ct --out scratch/ct_seed.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingestion import politeness  # noqa: E402
from scripts import discover_ats as da  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("seed")
logging.getLogger("ingestion.politeness").setLevel(logging.ERROR)

TIMEOUT = 20

#: The EEA membership set — what "target region" defaults to. This is the same distinction the
#: rest of the repo draws (see `geo.EEA_COUNTRIES`): GB is deliberately *out*. Kept as a local
#: literal rather than importing `service.geo` because `scripts/` must not depend on `service/`
#: (it is not in the pipeline image), and this list needs only membership, not resolution.
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
    "united states": "US", "usa": "US", "remote": None,
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
    # Longest names first so "czech republic" wins over a bare "czech".
    low = text.lower()
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
                     target: set[str], sampler=board_identity) -> Verdict:
    """PASS / REVIEW / REJECT for one live board. `expected_name` is the name we probed for
    (ARES path) or None (CT path, where the slug came from the tenant's own subdomain and there
    is nothing to match against). `sampler` is injected so the decision tree is testable without
    a network."""
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


# --- candidate sources ---------------------------------------------------------------------

def harvest_ct(host: str, limit: int = 0) -> list[str]:
    """Tenant slugs for a subdomain-per-tenant ATS, from Certificate Transparency (`crt.sh`).

    `crt.sh?q=%.host&output=json` returns every certificate whose SAN matches the wildcard; the
    subdomain label directly left of `host` is the ATS slug. Deduped, marketing/infrastructure
    labels dropped. A public, append-only transparency log — not anyone's job database."""
    url = f"https://crt.sh/?q=%25.{host}&output=json"
    r = da._get_api(url)
    if r is None:
        logger.warning("crt.sh returned nothing for %s", host)
        return []
    try:
        rows = r.json()
    except ValueError:
        return []
    suffix = "." + host
    drop = {"www", "api", "app", "help", "blog", "static", "cdn", "mail", "support", "status",
            "assets", "media", "dev", "staging", "test", "demo"}
    slugs: set[str] = set()
    for row in rows:
        for name in str(row.get("name_value", "")).splitlines():
            name = name.strip().lower().lstrip("*.")
            if not name.endswith(suffix):
                continue
            label = name[: -len(suffix)]
            if "." in label or "*" in label or not label or label in drop:
                continue
            slugs.add(label)
    out = sorted(slugs)
    return out[:limit] if limit else out


def ares_it_companies(limit: int = 200, nace: str = "620") -> list[str]:
    """Czech company names in a CZ-NACE class (default 62 = programming/IT consultancy), from
    the ARES open register. Names only — ARES publishes no website — which is why these feed the
    name-only `probe_api_slugs` path rather than the domain walk. Paged 100 at a time."""
    url = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/vyhledat"
    names: list[str] = []
    start = 0
    while len(names) < limit:
        body = {"czNace": [nace], "pocet": 100, "start": start}
        try:
            politeness.throttle(url)
            r = requests.post(url, headers={**politeness.HEADERS,
                                            "Content-Type": "application/json"},
                              data=json.dumps(body), timeout=TIMEOUT)
            if r.status_code != 200:
                break
            subj = r.json().get("ekonomickeSubjekty", [])
        except (requests.RequestException, ValueError):
            break
        if not subj:
            break
        for s in subj:
            name = (s.get("obchodniJmeno") or "").strip()
            if name:
                names.append(name)
        start += 100
    return names[:limit]


# --- orchestration -------------------------------------------------------------------------

@dataclass
class Row:
    company: str
    ats: str
    token: str
    extra: str
    jobs: int
    verdict: Verdict


def _run_ct(hosts: list[str], target: set[str], limit: int) -> list[Row]:
    ats_of = {"teamtailor.com": "teamtailor", "recruitee.com": "recruitee"}
    rows: list[Row] = []
    for host in hosts:
        ats = ats_of.get(host)
        if ats not in da.SUPPORTED:
            logger.warning("skipping %s: not a supported subdomain-per-tenant ATS", host)
            continue
        slugs = harvest_ct(host, limit)
        logger.info("crt.sh %s: %d candidate slugs", host, len(slugs))
        for i, slug in enumerate(slugs, 1):
            hit = da.Hit(company=slug, domain="", tier="ct", ats=ats, token=slug)
            da._verify(hit)
            if hit.jobs <= 0:
                continue
            v = identity_verdict(ats, slug, "", expected_name=None, target=target)
            rows.append(Row(v.employer or slug, ats, slug, "", hit.jobs, v))
            logger.info("[%s %d/%d] %-6s %-24s %s (%d) — %s",
                        host, i, len(slugs), v.status, slug, v.employer, hit.jobs, v.reason)
    return rows


def _run_ares(target: set[str], limit: int, nace: str) -> list[Row]:
    names = ares_it_companies(limit, nace)
    logger.info("ARES CZ-NACE %s: %d companies", nace, len(names))
    rows: list[Row] = []
    for i, name in enumerate(names, 1):
        hits = da.probe_api_slugs(name, domain="", tier="ares", already=set())
        for hit in hits:
            if hit.jobs <= 0 or hit.ats not in da.SUPPORTED:
                continue
            v = identity_verdict(hit.ats, hit.token, hit.extra, expected_name=name, target=target)
            rows.append(Row(name, hit.ats, hit.token, hit.extra, hit.jobs, v))
            logger.info("[%d/%d] %-6s %-28s %s:%s (%d) — %s",
                        i, len(names), v.status, name, hit.ats, hit.token, hit.jobs, v.reason)
    return rows


def _write(rows: list[Row], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["company", "ats", "token", "extra", "jobs", "supported", "verdict",
                    "employer", "countries", "reason"])
        for r in sorted(rows, key=lambda r: ({"PASS": 0, "REVIEW": 1, "REJECT": 2}[r.verdict.status],
                                             r.ats, r.company)):
            w.writerow([r.company, r.ats, r.token, r.extra, r.jobs,
                        "yes" if r.ats in da.SUPPORTED else "no", r.verdict.status,
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
    ap.add_argument("mode", choices=["ct", "ares"])
    ap.add_argument("--host", action="append",
                    help="CT host(s) to harvest (default: teamtailor.com + recruitee.com)")
    ap.add_argument("--country", help="restrict target region to one ISO2 (e.g. CZ); "
                                      "default is the whole EEA")
    ap.add_argument("--nace", default="620", help="ARES CZ-NACE class (default 620 = IT)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="scripts/seed_candidates.csv")
    args = ap.parse_args(argv)

    target = {args.country.upper()} if args.country else EEA
    if args.mode == "ct":
        hosts = args.host or ["teamtailor.com", "recruitee.com"]
        rows = _run_ct(hosts, target, args.limit)
    else:
        rows = _run_ares(target, args.limit or 200, args.nace)

    _write(rows, Path(args.out))
    _report(rows)
    logger.info("Wrote %s", args.out)


if __name__ == "__main__":
    main()
