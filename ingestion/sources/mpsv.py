"""MPSV / Úřad práce ČR — the Czech public employment service's vacancy register.

Open data, published by the Ministry of Labour and Social Affairs under an explicit licence
declaration, refreshed daily. **39 063 vacancies covering the whole of Czechia**, which is
the only source in this repo of that scale for CZ.

Why this one is legally different from jobs.cz
----------------------------------------------
The reason Jobs.cz and Profesia left `gather()` on 2026-08-03 was Alma Career's terms
forbidding automated extraction, backed by the EU database right (96/9/EC). MPSV's DCAT
metadata takes the opposite position **in writing**, per distribution:

    databáze_chráněná_zvláštními_právy:
        .../podmínky-užití/není-chráněna-zvláštním-právem-pořizovatele-databáze/
    autorské_dílo:  .../podmínky-užití/neobsahuje-autorská-díla/

That is the sui generis database right — the exact right at issue with Alma Career —
expressly disclaimed by the publisher. `robots.txt` on data.mpsv.cz is `Disallow:` (allow
all). This is not "another scraper we hope is fine"; it is a public body publishing a
register for reuse.

It contains personal data, and we drop it at the door
-----------------------------------------------------
The same metadata declares `osobní_údaje: .../podmínky-užití/obsahuje-osobní-údaje/`, and it
is not a formality: every record carries `prvniKontaktSeZamestnavatelem`, a named human with
their direct email and phone. JobDigest's privacy policy is a specification (see CLAUDE.md
security rule 4) and it makes no promise covering third-party recruiter contacts, so **this
adapter never reads that block**, and `_scrub` strips loose emails and phone numbers out of
the free-text description as well — employers routinely paste "volejte pí Novákovou na
608…" into it. What reaches the database is the employer *organisation*, the role, the place
and the money. Nothing here is a natural person.

That deletion is deliberate and load-bearing: ingesting the contact block would add a new
category of personal data to `postings`, which is exactly the change rule 4 says cannot ship
without editing the privacy policy in the same commit. Not collecting it is cheaper and
safer than collecting it and explaining it.

Scope: white-collar only, and that is a real trade-off
------------------------------------------------------
The register is the whole Czech labour market, so it is mostly manual and service work —
the largest single profession is *Dělníci v oblasti výstavby a údržby budov* (949). Loading
all 39 063 would put ~32 000 kitchen, warehouse and construction vacancies into a corpus
whose matcher knows nine mostly-technical categories, and `query_shortlist_meta`'s widened
path drops the recall predicate entirely — so a subscriber who trips `SHORTLIST_FLOOR` would
get a shortlist of whatever is newest in their city, which after this source would be
cleaning jobs. `ISCO_MAJOR_KEEP` bounds that to managers, professionals and technicians:
**7 339 vacancies, 692 of them ICT.**

It is one constant and one edit to widen, and the cost of widening is dilution rather than
error. Do not widen it without looking at what a real subscriber's shortlist becomes.
"""

from __future__ import annotations

import codecs
import gzip
import json
import logging
import os
import re
import tempfile
from datetime import date, datetime
from typing import Iterator, Optional

import requests
from urllib3.exceptions import ProtocolError

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

DATA_URL = "https://data.mpsv.cz/od/soubory/volna-mista/volna-mista.json.gz"
OBCE_URL = "https://data.mpsv.cz/od/soubory/ciselniky/obce.json"

#: The public portal, and the link a subscriber actually clicks. `up.gov.cz` is a
#: client-rendered app whose router reads the **fragment**, not the query string: the vacancy
#: lives at `#/volna-mista-detail/{id}`, and `?id={id}` is not a route at all — the app
#: ignores it and renders its own empty search page. That was this adapter's link for its
#: whole life, shipped because it was *"stable and unique per vacancy"* and honestly labelled
#: **unverified**: the server returns an identical shell for a bogus id, so it cannot be
#: checked without a browser, and a stored URL is never fetched again. So a subscriber found
#: it the only way left — one was emailed `?id=67251104` on 2026-08-08 and it went nowhere.
#:
#: This is the startupjobs 2026-08-06 failure in a second place, and the shape is the same
#: both times: a source that returns the right number of rows can still return dead links,
#: and *"unverified"* in a comment is a bug nobody has looked at yet. A URL must carry
#: whatever the site needs to resolve it.
#:
#: `urlAdresa` is still not a substitute: present on only 3% of records and usually the
#: employer's homepage ("www.ssok.cz"), often without a scheme.
JOB_URL = "https://up.gov.cz/volna-mista-v-cr#/volna-mista-detail/{portal_id}"

#: What `posting_id` hashes — **deliberately not `JOB_URL`, and deliberately never fetched.**
#: `posting_id = md5(url)`, so a vacancy's identity is whatever string the adapter puts in
#: `url`; hashing the corrected link would re-create all ~7 300 MPSV rows under fresh ids,
#: both copies active for a staleness window and competing for shortlist slots — the
#: startupjobs churn, this time self-inflicted. This string is byte-identical to what every
#: previous run stored, so **repairing the link churns no ids**: the rows keep their identity
#: and have their `url` corrected in place on the next ingest (`upsert_postings` refreshes
#: `url` for exactly this case).
#:
#: It is the better seed on its own merits, too. `portalId` is the register's own immutable
#: key, whereas the route is the portal's to restructure — and it has already changed once.
ID_URL = "https://up.gov.cz/volna-mista-v-cr?id={portal_id}"

#: ISCO-08 major groups to keep: 1 managers, 2 professionals, 3 technicians. See the module
#: docstring — this is a dilution guard, not a judgement about which work matters.
ISCO_MAJOR_KEEP = frozenset("123")

#: CZ-ISCO → `role_category`, keyed on the ISCO-08 prefix (3 digits where a 2-digit group
#: spans several categories, 2 where it does not). Added 2026-08-09.
#:
#: **This is the authoritative answer and the title is a guess**, which is the opposite of the
#: usual situation here. The publisher assigns the code; `taxonomy.classify` infers from a
#: Czech title with English regexes and gets 81% of this source wrong (uncategorised). Passing
#: the mapped value as `source_category` lets `classify` use it as the fallback it already has
#: — title patterns still win, so an English-titled Czech ad is unaffected.
#:
#: **Every value must be a real `taxonomy.CATEGORIES` member**; `classify` discards anything
#: else (the 2026-08-08 hint guard), so a typo here silently means "uncategorised" rather than
#: an error. `test_mpsv.py` pins it.
#:
#: ISCO 21 (science and engineering professionals) and 31 (science and engineering associate
#: professionals) map to `engineering`, added 2026-08-09 — the 1 654 mechanical, electrical and
#: civil engineers and technicians that had no category until then and sat uncategorised. The
#: 2-digit map is deliberately coarse: groups 211–213 (physicists, mathematicians, life
#: scientists) and 216 (architects) also sit under 21, so a handful are filed as engineering
#: rather than the science/design category the taxonomy still lacks. A readable title overrides
#: it (patterns win over the hint), and the held-out ISCO/SSYK scorer will surface any concentrated
#: miss — the register's own grouping is the best signal available, and 21/31-as-engineering is
#: what it asserts. The specific ICT codes 251/252 keep their finer mapping (checked first).
ISCO_CATEGORIES: dict[str, str] = {
    # 1 managers
    "121": "operations", "122": "sales", "132": "manufacturing_production",
    "133": "devops_platform", "141": "hospitality", "142": "sales",
    # 2 professionals
    "21": "engineering",                              # science & engineering (251/252 override)
    "22": "healthcare", "23": "education",
    "241": "finance_accounting", "242": "operations", "243": "marketing",
    "251": "software_engineering", "252": "devops_platform",
    "261": "legal",
    # 3 associate professionals
    "31": "engineering",                              # science & engineering technicians
    "32": "healthcare",
    "331": "finance_accounting", "332": "sales", "333": "operations",
    "334": "other_tech_function",
    "351": "customer_support", "352": "devops_platform",
}


def _isco_category(item: dict) -> Optional[str]:
    """The publisher's occupation code as a `role_category`, longest prefix first."""
    code = str((item.get("profeseCzIsco") or {}).get("id") or "").split("/")[-1]
    for n in (3, 2):
        hit = ISCO_CATEGORIES.get(code[:n])
        if hit:
            return hit
    return None

#: Monthly pay in CZK is mandatory in this register — 100% of records carry `mesicniMzdaOd`,
#: against ~30–40% salary coverage everywhere else in the repo.
CURRENCY = "CZK"

_TIMEOUT = 180
_CHUNK = 1 << 20
#: Download attempts. The archive is one short request; a partial read is worth retrying.
_RETRIES = 3

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
#: Czech phone numbers, written every way employers actually write them.
_PHONE = re.compile(r"(?:\+420[\s-]?)?(?:\d{3}[\s-]?){2}\d{3}\b")


def _scrub(text: Optional[str]) -> Optional[str]:
    """Remove direct contact details from free text.

    The structured contact block is simply never read; this covers the other half, where an
    employer has typed a recruiter's mobile into the description. Replaced with a marker
    rather than deleted so the surrounding sentence still reads as Czech.
    """
    if not text:
        return None
    text = _EMAIL.sub("[kontakt odstraněn]", text)
    text = _PHONE.sub("[kontakt odstraněn]", text)
    return text.strip() or None


def _iter_objects(stream: Iterator[bytes]) -> Iterator[dict]:
    """Yield each object of the top-level ``polozky`` array from a byte stream.

    The uncompressed document is 186 MB and `json.load` on it costs well over a gigabyte of
    Python objects — too much to ask of the pipeline container for a source that is filtered
    down to 19% of its rows anyway. This walks the buffer tracking brace depth (and string
    state, so a `{` inside a description cannot desynchronise it) and hands back one record
    at a time, so peak memory is one record plus the read buffer.
    """
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buf = ""
    pos = 0                 # scan cursor within buf
    consumed = 0            # everything before this has been yielded; compacted once a chunk
    started = False
    depth = 0
    start = -1
    in_str = False
    esc = False

    for chunk in stream:
        # Incremental, not `chunk.decode()`: a 1 MiB read lands mid-codepoint often enough
        # that per-chunk decoding quietly corrupts a Czech character every few megabytes.
        buf += decoder.decode(chunk)
        if not started:
            key = buf.find('"polozky"')
            if key == -1:
                buf = buf[-64:]                     # keep a tail in case the key straddles
                continue
            bracket = buf.find("[", key)
            if bracket == -1:
                continue
            buf = buf[bracket + 1:]
            pos = consumed = 0
            started = True

        n = len(buf)
        while pos < n:
            ch = buf[pos]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = pos
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        yield json.loads(buf[start:pos + 1])
                    except ValueError:              # a truncated record is skipped, not fatal
                        logger.warning("MPSV: unparseable record, skipped")
                    consumed = pos + 1
                    start = -1
            pos += 1

        # Compact once per chunk, never per record. Slicing the buffer after every object
        # made this quadratic — 39 000 records against a 1 MiB window copied tens of
        # gigabytes and the first live run did not finish inside ten minutes.
        if consumed:
            buf = buf[consumed:]
            pos -= consumed
            if start >= 0:
                start -= consumed
            consumed = 0


def _municipalities() -> dict[str, str]:
    """RÚIAN municipality code -> name. Without it every location is an opaque id."""
    politeness.throttle(OBCE_URL)
    resp = requests.get(OBCE_URL, headers=politeness.HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    out: dict[str, str] = {}
    for row in resp.json().get("polozky", []):
        code = str(row.get("kod") or row.get("id", "")).split("/")[-1]
        name = row.get("nazev")
        if isinstance(name, dict):
            name = name.get("cs")
        if code and name:
            out[code] = name
    return out


def _cs(value) -> Optional[str]:
    """Czech string out of MPSV's ``{"cs": ...}`` language maps."""
    if isinstance(value, dict):
        return value.get("cs")
    return value if isinstance(value, str) else None


def _isco_major(item: dict) -> str:
    code = str((item.get("profeseCzIsco") or {}).get("id") or "").split("/")[-1]
    return code[:1]


def _city(item: dict, obce: dict[str, str]) -> Optional[str]:
    """Municipality name for a vacancy.

    `mistoVykonuPrace.obec` is populated on only 1 008 of the 7 339 kept rows; the address on
    the workplace itself covers 7 334, so it is the primary and the other the fallback.
    """
    place = item.get("mistoVykonuPrace") or {}
    for site in place.get("pracoviste") or []:
        code = str(((site.get("adresa") or {}).get("obec") or {}).get("id") or "").split("/")[-1]
        if code in obce:
            return obce[code]
    code = str((place.get("obec") or {}).get("id") or "").split("/")[-1]
    return obce.get(code)


def _salary(item: dict) -> Optional[str]:
    lo, hi = item.get("mesicniMzdaOd"), item.get("mesicniMzdaDo")
    if lo and hi and hi != lo:
        return f"{lo}–{hi} CZK/měsíc"
    if lo:
        return f"od {lo} CZK/měsíc"
    return None


def _posted(item: dict) -> Optional[date]:
    raw = item.get("datumVlozeni")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        return None


class MpsvSource(BaseSource):
    """Czech public employment service vacancies (open data, daily full dump)."""

    def __init__(self, isco_major: Optional[frozenset[str]] = None):
        self._keep = isco_major or ISCO_MAJOR_KEEP

    @property
    def source_name(self) -> str:
        return "mpsv"

    def _download(self) -> Optional[str]:
        """Fetch the gzipped dump to a temp file, with retries. Path, or None.

        Deliberately *not* parsed straight off the socket. Decompressing `resp.raw` in flight
        keeps one HTTP connection open for the whole parse, and the first live run died on
        `IncompleteRead(7263433 bytes read, 9405399 more expected)` — a broken connection
        two-thirds of the way through, raised by urllib3 from under `resp.raw` where
        `requests`' own exception wrapping could not see it. The archive is 16.6 MB
        compressed; taking it in one short request and parsing from disk turns a partial read
        into a retryable download instead of a half-ingested source.
        """
        for attempt in range(1, _RETRIES + 1):
            politeness.throttle(DATA_URL)
            try:
                with requests.get(DATA_URL, headers=politeness.HEADERS, timeout=_TIMEOUT,
                                  stream=True) as resp:
                    resp.raise_for_status()
                    expected = int(resp.headers.get("Content-Length") or 0)
                    handle = tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False)
                    written = 0
                    with handle:
                        for chunk in resp.iter_content(_CHUNK):
                            handle.write(chunk)
                            written += len(chunk)
                if expected and written < expected:
                    raise OSError(f"short read: {written} of {expected} bytes")
                return handle.name
            except (requests.RequestException, OSError, ProtocolError) as exc:
                logger.warning("MPSV download attempt %d/%d failed: %s",
                               attempt, _RETRIES, exc)
        logger.error("MPSV: giving up after %d download attempts", _RETRIES)
        return None

    def fetch(self) -> list[dict]:
        """Download the gzipped dump, keeping only the ISCO groups we model."""
        if not politeness.robots_allows(DATA_URL):
            logger.warning("MPSV: robots.txt disallows %s", DATA_URL)
            return []
        path = self._download()
        if path is None:
            return []
        kept: list[dict] = []
        try:
            # The URL ends in `.gz`, but the server also sends `Content-Encoding: gzip`, so
            # `requests` decompresses it in flight and what reaches disk is plain JSON —
            # `gzip.open` then fails with "Not a gzipped file (b'{\\n')". Sniffing the magic
            # bytes works whichever way the server, or a proxy in front of it, behaves.
            with open(path, "rb") as probe:
                gzipped = probe.read(2) == b"\x1f\x8b"
            opener = gzip.open if gzipped else open
            with opener(path, "rb") as handle:
                stream = iter(lambda: handle.read(_CHUNK), b"")
                for item in _iter_objects(stream):
                    if _isco_major(item) in self._keep:
                        kept.append(item)
        except (OSError, EOFError) as exc:
            logger.error("MPSV: archive unreadable (%s) — %d parsed before it stopped",
                         exc, len(kept))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        logger.info("MPSV: kept %d vacancies (ISCO major %s)",
                    len(kept), "".join(sorted(self._keep)))
        return kept

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        if not raw_items:
            return []
        try:
            obce = _municipalities()
        except requests.RequestException as exc:
            logger.warning("MPSV: municipality list unavailable (%s) — cities will be null",
                           exc)
            obce = {}

        out: list[JobPosting] = []
        for item in raw_items:
            portal_id = item.get("portalId")
            title = _cs(item.get("pozadovanaProfese"))
            if not portal_id or not title:
                continue
            out.append(JobPosting(
                # Hashes the id-only form, not the link — see ID_URL.
                posting_id=make_posting_id(ID_URL.format(portal_id=portal_id)),
                source=self.source_name,
                title=title.strip(),
                # The publisher's own occupation code, mapped. `classify` prefers title
                # patterns and falls back to this, so it only fires where the regexes miss —
                # which for a Czech-titled register is most of the time.
                source_category=_isco_category(item),
                company=(item.get("zamestnavatel") or {}).get("nazev"),
                url=JOB_URL.format(portal_id=portal_id),
                # Contact details are stripped here and the `prvniKontaktSeZamestnavatelem`
                # block is never touched at all — see the module docstring.
                description=_scrub(_cs(item.get("upresnujiciInformace"))),
                location=_city(item, obce),
                # A source-level country constant is normally wrong (Arbeitnow hardcoded "DE"
                # and mislabelled 168 of 375 postings). This is the sound exception the rule
                # allows: the Úřad práce registers vacancies *in Czechia* by statute, so the
                # country is a property of the register, not a guess about the text.
                country_code="CZ",
                # Left to `geo.work_mode`, which reads the description. The register has no
                # remote field, and inventing one would be the 2026-07-28 bug again.
                remote_signal=None,
                salary_raw=_salary(item),
                currency=CURRENCY if _salary(item) else None,
                posted_at=_posted(item),
            ))
        return out
