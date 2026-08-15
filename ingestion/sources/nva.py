"""NVA CVVP — Latvia's State Employment Agency vacancy register.

**Not `nav.py`.** That is NAV, *Norway*'s register, one transposition away. Two national
employment services whose acronyms differ by a swapped letter both live in this directory;
check which one you are editing before changing anything that mentions a country.

Permission
----------
**No terms of use and no robots.txt exist on `cvvp.nva.gov.lv`** — the host answers 404 for
`/robots.txt` and `nva.gov.lv` publishes no terms page. Nothing refuses automated reading
because nothing says anything, which is "permission unestablished": historically an automatic
skip in this repo, and the reason this register sat recorded-but-unbuilt since 2026-08-07.

**The owner reversed that for a named set on 2026-08-15** and this is a member of it. See
`ingestion/tests/test_unestablished_permission.py`, which enumerates the set, and the entry in
`docs/sources.md`. It is a risk-appetite decision on the owner's own product, it does not
reach any source that *refuses*, and every request here still goes through
`politeness.robots_allows` and `throttle`.

Note what this is **not**: NVA also publishes a "Vakances" CSV on `data.gov.lv` under **CC0**,
and that one is cleanly licensed — but its own description scopes it to `Latvijas valsts
sektorā`, so the dedication covers only the public-sector subset and stops exactly where
third-party employer content begins (the same boundary ELA's licence draws). This adapter
reads the *live register* instead, which includes private employers, and that is precisely the
part CC0 does not cover. Do not describe this source as CC0.

It contains personal data, and we drop the block at the door
------------------------------------------------------------
The `mpsv` discipline, and it is not optional here. Every record carries a structured contact
block — `kontaktpersona`, `kontaktpersonas_epasts`, `kontaktpersonas_talrunis` — naming a
person and their address ("CV sūtīt uz: darbs@vidzemesslimnica.lv"). **Those three fields are
never read.** `_scrub` then strips emails and phone numbers out of `darba_apraksts`, because
employers paste them into the body as well.

Storing that block would add a new category of personal data to `postings`, which CLAUDE.md
security rule 4 forbids without editing the privacy policy in the same commit.

Scope: the register is the whole labour market, and most of it would dilute the corpus
--------------------------------------------------------------------------------------
Measured 2026-08-15: **4 460 active vacancies, 827 distinct professions**, and the head of that
distribution is `PALĪGSTRĀDNIEKS` (general labourer, 205), retail shop assistant (147), truck
driver (141), schoolteacher (139), construction worker (110), cook (109), nurse (98), cleaner
(72). Loading all of it would put several thousand manual and service vacancies into a corpus
whose subscribers are overwhelmingly looking for professional work — and
`query_shortlist_meta`'s widened path drops the recall predicate entirely, so a Latvian
subscriber who trips `SHORTLIST_FLOOR` would get whatever is newest in Rīga, which unfiltered
would be warehouse work. `KEEP_FIELDS` bounds it.

**This is NOT the same gate as `mpsv`'s, and the difference is a real cost.** `ISCO_MAJOR_KEEP`
filters by *skill level* — ISCO majors 1–3, managers/professionals/technicians — so `mpsv`
keeps nurses and teachers, who are ISCO 2. `darb_joma` is a **sector**, not a level, and it
cannot express "the professionals within healthcare". Excluding
`Veselības aprūpe / Sociālā aprūpe` and `Izglītība / Zinātne` therefore drops registered nurses
and schoolteachers along with care assistants — and `healthcare`, `education` and `social_care`
are all real `taxonomy.CATEGORIES` members, so those are roles this product *can* match.

That is a deliberate precision-over-recall trade on a source whose head is manual work, not a
claim that the excluded rows are worthless. The right fix is a profession-level rule over
`kla_profesija_nosaukums` (827 distinct Latvian names — "key before vocabulary", so build the
answer key first). Until then, widening is one constant.

The register offers no ISCO code, so the gate is `darb_joma` — NVA's own sector field.
Enumerated from a 220-vacancy random sample rather than guessed (there is no classifier
endpoint; `pub_darb_joma` and friends are all 404):

    Ražošana                                        31    excluded
    Tirdzniecība / Mārketings                       30    excluded — mostly retail floor staff
    Izglītība / Zinātne                             30    excluded
    Veselības aprūpe / Sociālā aprūpe                27    excluded
    Būvniecība / Nekustamais īpašums                 23    excluded
    Valsts pārvalde                                  13    KEPT
    Pakalpojumi                                      13    excluded
    Transports / Loģistika                           13    excluded
    Ēdināšana / Pārtikas rūpniecība                   9    excluded
    Informācijas tehnoloģijas / Telekomunikācijas     6    KEPT
    Lauksaimniecība / Vide                            6    excluded
    Vadība / Administrēšana                           5    KEPT
    Kultūra / Māksla                                  4    excluded
    Drošība / Glābšanas dienesti / Aizsardzība        4    excluded
    Elektronika / Enerģētika / Elektroenerģija        3    KEPT
    Cita                                              2    excluded — unlabelled, not a sector
    Jurisprudence / Tieslietas                        1    KEPT

**`Tirdzniecība / Mārketings` is the judgement call.** It is 13.6% of the register and it holds
both marketing roles (which the taxonomy models) and retail shop assistants (the second most
common profession in the whole file). A sector gate cannot separate them, and admitting ~600
shop-assistant rows to reach a handful of marketing ones is the dilution this gate exists to
prevent. Excluded deliberately; revisit with a profession-level rule, not by flipping this.

Expected yield is ~12.7% of 4 460 ≈ **570 vacancies**. Also worth correcting the record:
`docs/sources.md` says "IT/telecoms is 21 rows", which was true of the *CC0 public-sector CSV*.
In this register IT/telecom is ~2.7% ≈ **120 rows** — still small, but five times what the file
implies.

Cost, stated plainly
--------------------
The list endpoint caps at `limit=100` and pages by `offset`; unknown query parameters are
**silently ignored** (a `darb_joma` filter returns the unfiltered set, verified), so there is
no server-side filtering and `darb_joma` lives only on the detail. That means one request per
vacancy to learn the sector: **~4 460 requests, ~75 minutes at 1 s/host, to keep ~570 rows.**
That is a poor ratio and it is the honest price of this source. `MAX_DETAILS` bounds it.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Iterator, Optional

import requests

from ingestion import politeness
from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

API = "https://cvvp.nva.gov.lv/data/"
LIST_ENTITY = "pub_vakance_list"
DETAIL_ENTITY = "pub_vakance"

#: The link a subscriber clicks. `cvvp` is an AngularJS ui-router app with **no html5Mode**, so
#: the route lives in the fragment: state `pub.vakances.view` is `/:id` nested under `/pub` and
#: `/vakances`. Read off `/js/app.*.js` rather than guessed — this is the MPSV failure exactly
#: (`?id=` was never a route there), and a URL must carry whatever the site needs to resolve it.
#:
#: **Verified 2026-08-15 by a static trace of the shipped bundle** (recorded in
#: `check_links.BROWSER_CONFIRMED`), because the fragment never reaches the server and so no
#: HTTP client can ever tell a real vacancy from the app's empty page — `check_links` returns
#: SHELL here permanently. The trace runs end to end: the state above resolves the fragment
#: form; `PubVakViewCtrl` reads `$stateParams.id` and calls `PubVakance.get({id})`; and
#: `PubVakance` is built by the generic factory `$resource("./data/" + entity + "/:id")`, i.e.
#: `/data/pub_vakance/{id}` — the endpoint this adapter already pulls real records from. The
#: template `pub-vak-view.html` lives in `templateCache`, so its 404 on a direct fetch is
#: expected rather than a break.
#:
#: **What that does not prove is rendering** — a login wall or an empty template would look the
#: same. Open one in a browser when convenient. It is recorded as a static trace rather than a
#: browser confirmation precisely so nobody reads it as more than it is.
JOB_URL = "https://cvvp.nva.gov.lv/#/pub/vakances/{vacancy_id}"

#: Server-enforced: `limit=200` and above return HTTP 400.
PAGE_SIZE = 100

#: Bounds a runaway list walk (~4 460 today) and the detail fan-out that follows it.
MAX_LIST_PAGES = 80
MAX_DETAILS = 6000

_TIMEOUT = 45

#: `darb_joma` values kept. A dilution guard, not a judgement about which work matters — the
#: same role `ISCO_MAJOR_KEEP` plays for `mpsv`. See the module docstring for the measured
#: distribution and for why `Tirdzniecība / Mārketings` is deliberately absent.
KEEP_FIELDS = frozenset({
    "Informācijas tehnoloģijas / Telekomunikācijas",
    "Vadība / Administrēšana",
    "Jurisprudence / Tieslietas",
    "Valsts pārvalde",
    "Elektronika / Enerģētika / Elektroenerģija",
})

#: `darb_joma` → `role_category`, where the sector maps cleanly onto a category the taxonomy
#: models. Every value must be a real `taxonomy.CATEGORIES` member — `classify` discards
#: anything else, and a title pattern still wins over this hint.
FIELD_CATEGORIES = {
    "Informācijas tehnoloģijas / Telekomunikācijas": "software_engineering",
}

_TAG_RE = re.compile(r"<[^>]+>")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
#: Latvian numbers: `+371 20123456`, `20123456`, `2 012 3456`, `67021206`.
_PHONE = re.compile(r"(?<!\d)(?:\+371[\s-]?)?\d{2}[\s-]?\d{3}[\s-]?\d{3}(?!\d)")

_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&#39;": "'", "&apos;": "'", "&shy;": "", "&#8203;": "",
}


def _unescape(text: str) -> str:
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)


def _text(html: Optional[str]) -> Optional[str]:
    """`darba_apraksts` is HTML fragments pasted from Word. Flatten to readable text."""
    if not html:
        return None
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", html)
    html = re.sub(r"(?i)<li[^>]*>", "• ", html)
    text = _unescape(_TAG_RE.sub(" ", html))
    text = text.replace("​", "").replace("\xad", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([.,;:!?%)])", r"\1", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() or None


def _scrub(text: Optional[str]) -> Optional[str]:
    """Remove contact details from free text.

    The structured contact block is simply never read; this is the other half, where an
    employer has typed "CV sūtīt uz ..." into the description. Replaced with a marker so the
    surrounding sentence still reads as Latvian.
    """
    if not text:
        return None
    text = _EMAIL.sub("[kontakti noņemti]", text)
    text = _PHONE.sub("[kontakti noņemti]", text)
    return text.strip() or None


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _city(address: Optional[str]) -> Optional[str]:
    """`adrese` is "LATVIJA, Jumaras iela 195, Valmiera, Valmieras nov.".

    Returns the whole thing minus the leading country token and lets `geo.resolve_location` do
    the actual work — this module must not become a second definition of geography.
    """
    if not address or not isinstance(address, str):
        return None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if parts and parts[0].upper() in {"LATVIJA", "LATVIA"}:
        parts = parts[1:]
    return ", ".join(parts) or None


class NvaSource(BaseSource):
    """Latvian State Employment Agency vacancies (live register; see module docstring)."""

    def __init__(self, keep_fields: Optional[frozenset[str]] = None,
                 max_details: int = MAX_DETAILS) -> None:
        self._keep_fields = KEEP_FIELDS if keep_fields is None else keep_fields
        self._max_details = max_details

    @property
    def source_name(self) -> str:
        return "nva"

    def _get(self, path: str, **params) -> Optional[object]:
        url = API + path
        if not politeness.robots_allows(url):
            logger.warning("NVA: robots.txt disallows %s", url)
            return None
        politeness.throttle(url)
        try:
            resp = requests.get(url, params=params or None,
                                headers=politeness.HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.debug("NVA: %s failed (%s)", url, exc)
            return None

    def _ids(self) -> list[int]:
        """Walk the list endpoint. `limit` caps at 100 server-side; page with `offset`."""
        seen: dict[int, None] = {}
        for page in range(MAX_LIST_PAGES):
            rows = self._get(LIST_ENTITY, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
            if not isinstance(rows, list) or not rows:
                break
            before = len(seen)
            for row in rows:
                if isinstance(row, dict) and row.get("id") is not None:
                    seen.setdefault(int(row["id"]), None)
            # Page by what came back, and stop when a page adds nothing — an endpoint that
            # ignores `offset` would otherwise loop until MAX_LIST_PAGES returning page one.
            if len(seen) == before:
                break
        else:
            logger.warning("NVA: hit MAX_LIST_PAGES (%d) — the register may be larger than "
                           "the walk", MAX_LIST_PAGES)
        return list(seen)

    def fetch(self) -> list[dict]:
        ids = self._ids()
        if not ids:
            logger.warning("NVA: list endpoint returned no vacancies")
            return []
        if len(ids) > self._max_details:
            logger.warning("NVA: %d vacancies, capping detail fetches at %d",
                           len(ids), self._max_details)
            ids = ids[:self._max_details]

        details: list[dict] = []
        kept = 0
        for vacancy_id in ids:
            payload = self._get(f"{DETAIL_ENTITY}/{vacancy_id}")
            if isinstance(payload, list):
                payload = payload[0] if payload else None
            if not isinstance(payload, dict):
                continue
            details.append(payload)
            if payload.get("darb_joma") in self._keep_fields:
                kept += 1
        logger.info("NVA: %d vacancies fetched, %d in kept fields", len(details), kept)
        return details

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            field = item.get("darb_joma")
            if field not in self._keep_fields:
                continue
            vacancy_id = item.get("id")
            title = (item.get("profesija") or "").strip()
            if vacancy_id is None or not title:
                continue

            url = JOB_URL.format(vacancy_id=vacancy_id)
            remote = item.get("ir_attalinati_veicams_darbs")

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=title,
                    company=(item.get("uznemums") or "").strip() or None,
                    url=url,
                    # `darba_apraksts` only. `kontaktpersona`, `kontaktpersonas_epasts` and
                    # `kontaktpersonas_talrunis` are never read — see the module docstring.
                    description=_scrub(_text(item.get("darba_apraksts"))),
                    location=_city(item.get("adrese")),
                    country_code="LV",
                    # The register is Latvia's, every vacancy is in Latvia, and `adrese` opens
                    # with "LATVIJA" on every record — this is the rare sound constant, the
                    # `mpsv` case rather than the Arbeitnow one.
                    remote_signal=True if remote is True else None,
                    salary_raw=(item.get("alga_no_lidz") or "").strip() or None,
                    currency="EUR" if item.get("alga_no_lidz") else None,
                    posted_at=_parse_date(item.get("publicesanas_datums")),
                    # NVA assigns the sector, so where it maps cleanly it beats a guess from a
                    # Latvian title read by English regexes.
                    source_category=FIELD_CATEGORIES.get(field),
                )
            )
        logger.info("NVA: normalised %d postings", len(postings))
        return postings
