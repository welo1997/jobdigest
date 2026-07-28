"""Country + city geography for JobDigest's location preferences.

Why this exists: a subscriber living in Prague was being emailed on-site roles in Brno. The
old preference model could not express the difference — ``profiles.regions`` held only coarse
buckets (``cz | eu | uk | us | worldwide``), so "Czechia" meant *every* Czech city, and a
three-hour commute scored the same as one across town.

This module is the ONE definition of:

  - which countries can be selected (EU-27) and their display names;
  - which cities we offer per country, and their stable slugs;
  - how a posting's free-text ``location`` resolves to a (country, city) pair;
  - whether a posting is *fully* remote (hybrid is not — that is the whole point);
  - the SQL predicate that gates postings by a profile's location preferences;
  - the mapping between the new fields and the legacy ``regions`` column.

``web/lib/geo.ts`` mirrors the country and city tables for the browser, which cannot import
Python. ``service/tests/test_geo.py`` fails if the two drift, the same guard the role
taxonomy has.

Slugs are stored in the database (``postings.city``, ``profiles.cities``) and must therefore
be stable: ASCII, lower-case, dash-separated, derived from the English/common name
("prague", never "praha"). Display names keep their diacritics; local and alternative
spellings live in CITY_ALIASES so a posting that reads "Praha 4" or "München" still resolves.

A profile's city is stored qualified — ``"cz:prague"`` — because "any city in Germany, but
only Prague in Czechia" is a normal thing to want and an unqualified city list cannot say it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Optional

# --- countries -----------------------------------------------------------------
# EU-27, the selectable set. Code -> display name.
COUNTRIES: dict[str, str] = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France",
    "DE": "Germany", "GR": "Greece", "HU": "Hungary", "IE": "Ireland", "IT": "Italy",
    "LV": "Latvia", "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta",
    "NL": "Netherlands", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "SK": "Slovakia", "SI": "Slovenia", "ES": "Spain", "SE": "Sweden",
}

# Accepted as *stored* values but not offered in the UI: `regions` could already hold `uk`
# and `us`, and translating a legacy profile must not lose that (see
# `countries_and_scope_from_regions`).
LEGACY_COUNTRIES: dict[str, str] = {"GB": "United Kingdom", "US": "United States"}
KNOWN_COUNTRIES: dict[str, str] = {**COUNTRIES, **LEGACY_COUNTRIES}

# Country names as they appear in posting location text, normalised (see `normalise`).
COUNTRY_ALIASES: dict[str, str] = {
    "czech republic": "CZ", "czechia": "CZ", "cesko": "CZ", "ceska republika": "CZ",
    "germany": "DE", "deutschland": "DE", "austria": "AT", "osterreich": "AT",
    "slovakia": "SK", "slovak republic": "SK", "slovensko": "SK",
    "poland": "PL", "polska": "PL", "hungary": "HU", "magyarorszag": "HU",
    "netherlands": "NL", "the netherlands": "NL", "holland": "NL", "nederland": "NL",
    "belgium": "BE", "belgie": "BE", "belgique": "BE", "luxembourg": "LU",
    "france": "FR", "spain": "ES", "espana": "ES", "portugal": "PT",
    "italy": "IT", "italia": "IT", "greece": "GR", "hellas": "GR",
    "ireland": "IE", "eire": "IE", "denmark": "DK", "danmark": "DK",
    "sweden": "SE", "sverige": "SE", "finland": "FI", "suomi": "FI",
    "estonia": "EE", "eesti": "EE", "latvia": "LV", "latvija": "LV",
    "lithuania": "LT", "lietuva": "LT", "romania": "RO", "bulgaria": "BG",
    "croatia": "HR", "hrvatska": "HR", "slovenia": "SI", "slovenija": "SI",
    "cyprus": "CY", "malta": "MT",
    "united kingdom": "GB", "great britain": "GB", "england": "GB", "scotland": "GB",
    "united states": "US", "usa": "US",
}

# --- cities --------------------------------------------------------------------
# Curated "main cities" per country, in the order the UI offers them: slug -> display name.
# Deliberately a short, opinionated list (the places that actually carry postings) rather
# than a gazetteer — anything missing is reachable through the free-text box, see
# `location_predicate` for what that does and does not do.
CITIES: dict[str, dict[str, str]] = {
    "AT": {"vienna": "Vienna", "graz": "Graz", "linz": "Linz", "salzburg": "Salzburg",
           "innsbruck": "Innsbruck", "klagenfurt": "Klagenfurt"},
    "BE": {"brussels": "Brussels", "antwerp": "Antwerp", "ghent": "Ghent",
           "leuven": "Leuven", "liege": "Liège", "bruges": "Bruges"},
    "BG": {"sofia": "Sofia", "plovdiv": "Plovdiv", "varna": "Varna", "burgas": "Burgas",
           "ruse": "Ruse"},
    "HR": {"zagreb": "Zagreb", "split": "Split", "rijeka": "Rijeka", "osijek": "Osijek",
           "zadar": "Zadar"},
    "CY": {"nicosia": "Nicosia", "limassol": "Limassol", "larnaca": "Larnaca",
           "paphos": "Paphos"},
    "CZ": {"prague": "Prague", "brno": "Brno", "ostrava": "Ostrava", "plzen": "Plzeň",
           "olomouc": "Olomouc", "liberec": "Liberec", "hradec-kralove": "Hradec Králové",
           "pardubice": "Pardubice", "ceske-budejovice": "České Budějovice",
           "zlin": "Zlín", "usti-nad-labem": "Ústí nad Labem", "jihlava": "Jihlava"},
    "DK": {"copenhagen": "Copenhagen", "aarhus": "Aarhus", "odense": "Odense",
           "aalborg": "Aalborg"},
    "EE": {"tallinn": "Tallinn", "tartu": "Tartu"},
    "FI": {"helsinki": "Helsinki", "espoo": "Espoo", "tampere": "Tampere",
           "turku": "Turku", "oulu": "Oulu", "vantaa": "Vantaa"},
    "FR": {"paris": "Paris", "lyon": "Lyon", "marseille": "Marseille",
           "toulouse": "Toulouse", "bordeaux": "Bordeaux", "lille": "Lille",
           "nantes": "Nantes", "nice": "Nice", "strasbourg": "Strasbourg",
           "rennes": "Rennes", "grenoble": "Grenoble", "montpellier": "Montpellier"},
    "DE": {"berlin": "Berlin", "munich": "Munich", "hamburg": "Hamburg",
           "frankfurt": "Frankfurt", "cologne": "Cologne", "stuttgart": "Stuttgart",
           "dusseldorf": "Düsseldorf", "leipzig": "Leipzig", "dresden": "Dresden",
           "hannover": "Hannover", "nuremberg": "Nuremberg", "karlsruhe": "Karlsruhe",
           "mannheim": "Mannheim", "essen": "Essen", "bremen": "Bremen"},
    "GR": {"athens": "Athens", "thessaloniki": "Thessaloniki", "patras": "Patras",
           "heraklion": "Heraklion"},
    "HU": {"budapest": "Budapest", "debrecen": "Debrecen", "szeged": "Szeged",
           "gyor": "Győr", "pecs": "Pécs"},
    "IE": {"dublin": "Dublin", "cork": "Cork", "galway": "Galway", "limerick": "Limerick"},
    "IT": {"milan": "Milan", "rome": "Rome", "turin": "Turin", "bologna": "Bologna",
           "florence": "Florence", "naples": "Naples", "verona": "Verona",
           "padua": "Padua", "genoa": "Genoa", "catania": "Catania"},
    "LV": {"riga": "Riga", "daugavpils": "Daugavpils"},
    "LT": {"vilnius": "Vilnius", "kaunas": "Kaunas", "klaipeda": "Klaipėda"},
    "LU": {"luxembourg-city": "Luxembourg City", "esch-sur-alzette": "Esch-sur-Alzette"},
    "MT": {"valletta": "Valletta", "sliema": "Sliema", "birkirkara": "Birkirkara"},
    "NL": {"amsterdam": "Amsterdam", "rotterdam": "Rotterdam", "the-hague": "The Hague",
           "utrecht": "Utrecht", "eindhoven": "Eindhoven", "groningen": "Groningen",
           "delft": "Delft", "tilburg": "Tilburg", "nijmegen": "Nijmegen"},
    "PL": {"warsaw": "Warsaw", "krakow": "Kraków", "wroclaw": "Wrocław",
           "poznan": "Poznań", "gdansk": "Gdańsk", "lodz": "Łódź",
           "katowice": "Katowice", "szczecin": "Szczecin", "lublin": "Lublin",
           "bydgoszcz": "Bydgoszcz", "rzeszow": "Rzeszów"},
    "PT": {"lisbon": "Lisbon", "porto": "Porto", "braga": "Braga", "coimbra": "Coimbra",
           "faro": "Faro", "aveiro": "Aveiro"},
    "RO": {"bucharest": "Bucharest", "cluj-napoca": "Cluj-Napoca",
           "timisoara": "Timișoara", "iasi": "Iași", "brasov": "Brașov",
           "sibiu": "Sibiu", "constanta": "Constanța"},
    "SK": {"bratislava": "Bratislava", "kosice": "Košice", "zilina": "Žilina",
           "nitra": "Nitra", "presov": "Prešov", "banska-bystrica": "Banská Bystrica",
           "trnava": "Trnava"},
    "SI": {"ljubljana": "Ljubljana", "maribor": "Maribor", "celje": "Celje"},
    "ES": {"madrid": "Madrid", "barcelona": "Barcelona", "valencia": "Valencia",
           "seville": "Seville", "malaga": "Málaga", "bilbao": "Bilbao",
           "zaragoza": "Zaragoza", "palma": "Palma", "alicante": "Alicante",
           "murcia": "Murcia"},
    "SE": {"stockholm": "Stockholm", "gothenburg": "Gothenburg", "malmo": "Malmö",
           "uppsala": "Uppsala", "linkoping": "Linköping", "lund": "Lund"},
}

# Alternative spellings a posting might use, per country: alias -> canonical slug. Only
# needed where the alias is not already the normalised display name (that mapping is derived
# automatically below), i.e. local names and common English variants.
CITY_ALIASES: dict[str, dict[str, str]] = {
    "AT": {"wien": "vienna"},
    "BE": {"bruxelles": "brussels", "brussel": "brussels", "antwerpen": "antwerp",
           "anvers": "antwerp", "gent": "ghent", "gand": "ghent", "luik": "liege",
           "brugge": "bruges"},
    "BG": {"sofiya": "sofia"},
    "CZ": {"praha": "prague", "prag": "prague", "praga": "prague",
           "pilsen": "plzen", "budejovice": "ceske-budejovice",
           "usti": "usti-nad-labem", "hradec": "hradec-kralove"},
    "DK": {"kobenhavn": "copenhagen", "koebenhavn": "copenhagen", "kopenhagen": "copenhagen",
           "arhus": "aarhus"},
    "DE": {"munchen": "munich", "muenchen": "munich", "koln": "cologne",
           "koeln": "cologne", "nurnberg": "nuremberg", "nuernberg": "nuremberg",
           "frankfurt am main": "frankfurt", "duesseldorf": "dusseldorf",
           "hanover": "hannover"},
    "GR": {"athina": "athens", "athina attiki": "athens", "saloniki": "thessaloniki"},
    "HU": {"budapesta": "budapest"},
    "IT": {"milano": "milan", "roma": "rome", "torino": "turin", "firenze": "florence",
           "napoli": "naples", "padova": "padua", "genova": "genoa"},
    "LU": {"luxembourg": "luxembourg-city", "luxemburg": "luxembourg-city"},
    "NL": {"den haag": "the-hague", "s gravenhage": "the-hague", "hague": "the-hague"},
    "PL": {"warszawa": "warsaw", "warschau": "warsaw", "cracow": "krakow",
           "breslau": "wroclaw", "danzig": "gdansk"},
    "PT": {"lisboa": "lisbon", "oporto": "porto"},
    "RO": {"bucuresti": "bucharest", "bukarest": "bucharest", "cluj": "cluj-napoca"},
    "SK": {"pressburg": "bratislava"},
    "ES": {"sevilla": "seville", "saragossa": "zaragoza",
           "palma de mallorca": "palma", "barna": "barcelona"},
    "SE": {"goteborg": "gothenburg", "gothenburg": "gothenburg", "malmoe": "malmo"},
}

# Single-token city names that are also ordinary words. Matched only when the country is
# already known, so "Nice to have" in a location field cannot become Nice, France.
_AMBIGUOUS = {"nice", "split", "cork", "essen", "bari", "lund", "faro", "murcia"}

# How a country maps onto the coarse legacy `region` bucket. Everything else is "eu".
_REGION_OF = {"CZ": "cz", "GB": "uk", "US": "us"}

REMOTE_SCOPES = ("country", "eu", "worldwide")
DEFAULT_REMOTE_SCOPE = "eu"

MAX_COUNTRIES = len(KNOWN_COUNTRIES)
MAX_CITIES = 60
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")


# --- normalisation -------------------------------------------------------------

def normalise(text: Optional[str]) -> str:
    """Lower-case, strip diacritics, reduce anything else to single spaces.

    Location text arrives as "Praha 4 – Chodov", "Brno-střed" or "München, Bayern"; the
    resolver compares against this one flattened form so every spelling lands in the same
    place.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    # Polish ł / Nordic ø survive decomposition — map the ones that matter explicitly.
    ascii_only = (ascii_only.replace("ł", "l").replace("Ł", "l")
                            .replace("ø", "o").replace("Ø", "o")
                            .replace("đ", "d").replace("ß", "ss"))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()


def slugify_city(text: str) -> str:
    """Turn a typed city name into a storable slug ("Kolín" -> "kolin").

    Mirrors `slugifyCity` in web/lib/geo.ts — a free-text city typed in the browser has to
    produce the same value as one typed against the API.
    """
    return re.sub(r"\s+", "-", normalise(text))[:40].strip("-")


def is_valid_city_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug))


# --- lookup indexes (built once at import) -------------------------------------

def _build_city_index() -> dict[str, list[tuple[str, str]]]:
    index: dict[str, list[tuple[str, str]]] = {}

    def add(phrase: str, country: str, slug: str) -> None:
        key = normalise(phrase)
        if not key:
            return
        entry = (country, slug)
        bucket = index.setdefault(key, [])
        if entry not in bucket:
            bucket.append(entry)

    for country, cities in CITIES.items():
        for slug, display in cities.items():
            add(display, country, slug)          # "Hradec Králové" -> hradec kralove
            add(slug.replace("-", " "), country, slug)
    for country, aliases in CITY_ALIASES.items():
        for alias, slug in aliases.items():
            if slug not in CITIES.get(country, {}):
                raise ValueError(f"CITY_ALIASES[{country}][{alias}] -> unknown slug {slug!r}")
            add(alias, country, slug)
    return index


_CITY_INDEX = _build_city_index()
_COUNTRY_INDEX = {normalise(k): v for k, v in COUNTRY_ALIASES.items()}
_MAX_NGRAM = max(len(k.split()) for k in list(_CITY_INDEX) + list(_COUNTRY_INDEX))


def _ngrams(tokens: list[str]) -> Iterable[tuple[int, str]]:
    """Longest-first n-grams, left to right — so "hradec kralove" wins over "hradec"."""
    for start in range(len(tokens)):
        for size in range(min(_MAX_NGRAM, len(tokens) - start), 0, -1):
            yield start, " ".join(tokens[start:start + size])


# --- resolution ----------------------------------------------------------------

def resolve_location(location: Optional[str],
                     country_code: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Resolve a posting's free-text location to ``(country_code, city_slug)``.

    An explicit ``country_code`` from the source always wins — it is structured data and we
    only fall back to text mining because most boards give none (Greenhouse, Lever, Remotive
    and LinkedIn all pass ``None``, which is why country filtering could not previously be
    built on that column alone).

    Either half may come back ``None``, and that is a meaningful answer rather than a
    failure: "unknown" postings are deliberately *kept* by `location_predicate` and left for
    the AI matcher to judge, so a resolver miss costs precision, never recall.
    """
    cc = (country_code or "").strip().upper()
    country = cc if cc in KNOWN_COUNTRIES else None
    tokens = normalise(location).split()
    if not tokens:
        return country, None

    if country is None:
        for _, phrase in _ngrams(tokens):
            hit = _COUNTRY_INDEX.get(phrase)
            if hit:
                country = hit
                break

    for _, phrase in _ngrams(tokens):
        candidates = _CITY_INDEX.get(phrase)
        if not candidates:
            continue
        if country is not None:
            for c, slug in candidates:
                if c == country:
                    return country, slug
            continue
        # No country yet: only an unambiguous city may imply one.
        if len(candidates) == 1 and phrase not in _AMBIGUOUS:
            return candidates[0][0], candidates[0][1]
    return country, None


# Fully remote, as opposed to hybrid. "Hybrid in Brno" is the exact case this feature exists
# to exclude for a Prague subscriber, so any hint of hybrid or on-site disqualifies.
_REMOTE_LOC = re.compile(
    r"\b(remote|anywhere|worldwide|distributed|work from home|home based|wfh"
    r"|vzdalene|prace z domova|telearbeit)\b")
_REMOTE_STRONG = re.compile(
    r"\b(fully remote|100 remote|remote only|remote first|work from anywhere"
    r"|plne vzdalene|zcela vzdalene)\b")
_NOT_REMOTE = re.compile(
    r"\b(hybrid|hybridni|on site|onsite|in office|no remote|not remote"
    r"|remote (work )?(not|un)\w*|bez home office)\b")
# Czech/Slovak boards rarely write "hybrid" — they grade the home office instead, and the
# grade is the whole meaning: "moznost obcasne prace z domova" is a Prague office job with a
# perk, not a role someone in Brno can take. The qualifier must sit next to the home-office
# phrase, because the words alone are far too common in body copy to disqualify a posting.
_QUALIFIED_HOME_OFFICE = re.compile(
    r"\b(obcasn\w*|prevazne|castecne|prilezitostn\w*|moznost)\s+(?:\w+\s+){0,2}"
    r"(z domova|z domu|home office)\b")
# The English equivalent, and the same failure from a different source: Ashby postings say
# "This role is based in Paris. We use a hybrid work model of 3 days in the office per week"
# while the board's own flag claims remote (248 active postings, 2026-07-28). Only phrases
# that name an actual schedule or policy are listed — a bare "hybrid" is far too common to
# act on ("hybrid cloud", and German medical billing writes "hybrid-DRG").
_HYBRID_SCHEDULE = re.compile(
    r"hybrid work(ing)? (model|policy|arrangement|setup)"
    r"|\b(is|as) a hybrid role\b|\bthis hybrid role\b"
    r"|\b(\d+|one|two|three|four)\s+days?\s+(a week\s+|per week\s+)?"
    r"(in|from)\s+(the\s+|our\s+)?office")


def _contradicts_remote(text: str) -> bool:
    """Does this posting's own text describe an arrangement that is not fully remote?

    An explicit, unambiguous remote claim wins: a description may mention a hybrid model in
    passing (often to contrast with itself), and "fully remote" said outright is the stronger
    statement. Everything else here names a specific in-office expectation.
    """
    if _REMOTE_STRONG.search(text):
        return False
    return bool(_QUALIFIED_HOME_OFFICE.search(text) or _HYBRID_SCHEDULE.search(text))


def is_fully_remote(location: Optional[str], description: Optional[str] = None,
                    source_signal: Optional[bool] = None) -> bool:
    """Would this posting let someone work from another city entirely?

    ``source_signal`` is the board's own flag (``remote_signal``). It is trusted where it is
    genuinely structured data — the remote-only boards, Lever's ``workplaceType`` — but that
    trust is checked first against an explicit hybrid phrase, because it was misplaced once
    and nothing downstream could tell: jobs.cz derived the flag from a substring of a graded
    Czech tag, so 3 744 hybrid Prague/Brno jobs arrived flagged fully remote and were exempted
    from the location gate on the strength of it (found 2026-07-28, `ingestion/sources/
    jobscz.py`). A boolean from a scraper is a claim, not a fact; this is the one place that
    can refuse it, so refusing here is what lets a re-run of `backfill_geo` repair rows that
    are already stored.

    Otherwise we read the location field, and only the *unambiguous* phrases from the
    description: CZ postings say "home office" for two days a week, so a loose description
    scan would mark half of Prague as remote and re-open the bug this exists to close.
    """
    # Checked against both fields and before every other rule: a posting that describes an
    # in-office expectation is hybrid regardless of how it reached us or what it claims.
    if _contradicts_remote(normalise(f"{location or ''} {(description or '')[:2000]}")):
        return False
    if source_signal:
        return True
    loc = normalise(location)
    if _NOT_REMOTE.search(loc):
        return False
    if _REMOTE_LOC.search(loc):
        return True
    return bool(_REMOTE_STRONG.search(normalise(description)[:2000]))


# --- preference values ---------------------------------------------------------

def qualify(country: str, city_slug: str) -> str:
    """The stored form of a city preference: ``"cz:prague"``."""
    return f"{country.strip().lower()}:{city_slug.strip().lower()}"


def split_city(value: str) -> tuple[Optional[str], Optional[str]]:
    """Inverse of `qualify`. Returns ``(COUNTRY, slug)`` or ``(None, None)`` if malformed."""
    if not value or ":" not in str(value):
        return None, None
    country, _, slug = str(value).partition(":")
    country = country.strip().upper()
    slug = slug.strip().lower()
    if country not in KNOWN_COUNTRIES or not slug or not is_valid_city_slug(slug):
        return None, None
    return country, slug


def clean_countries(values: Any) -> list[str]:
    """Uppercase, de-duplicate, drop anything we don't know. Order is preserved."""
    out: list[str] = []
    for v in (values or []):
        code = str(v).strip().upper()
        if code in KNOWN_COUNTRIES and code not in out:
            out.append(code)
    return out[:MAX_COUNTRIES]


def clean_cities(values: Any, countries: Optional[Iterable[str]] = None) -> list[str]:
    """Normalise city preferences, dropping malformed ones and any whose country is not
    selected — a city restriction for a country the subscriber removed would silently narrow
    a filter they can no longer see in the UI."""
    allowed = set(clean_countries(countries)) if countries is not None else None
    out: list[str] = []
    for v in (values or []):
        country, slug = split_city(v)
        if not country or (allowed is not None and country not in allowed):
            continue
        value = qualify(country, slug)
        if value not in out:
            out.append(value)
    return out[:MAX_CITIES]


def clean_remote_scope(value: Any) -> str:
    scope = str(value or "").strip().lower()
    return scope if scope in REMOTE_SCOPES else DEFAULT_REMOTE_SCOPE


def regions_for(countries: Iterable[str], remote_scope: str) -> list[str]:
    """The legacy `regions` value implied by the new fields.

    `regions` is kept as a *derived* column: the matcher export, the digest tags and any
    older client still read it, and deriving it means the two can never disagree. It is
    always a superset of what `location_predicate` allows, so nothing depends on it for
    precision any more.
    """
    scope = clean_remote_scope(remote_scope)
    regions = {_REGION_OF.get(c, "eu") for c in clean_countries(countries)}
    if scope == "eu":
        regions |= {"cz", "eu"}
    elif scope == "worldwide":
        regions |= {"cz", "eu", "worldwide"}
    return sorted(regions) if regions else ["cz", "eu", "worldwide"]


def countries_and_scope_from_regions(regions: Iterable[str]) -> tuple[list[str], str]:
    """Translate a legacy `regions` value into (countries, remote_scope).

    Used by the migration's backfill and by any client that still sends only `regions`, so
    both paths land on the same fields the filter actually reads. Round-trips exactly with
    `regions_for` for the three values the old UI could produce (`cz`, `cz+eu`,
    `cz+eu+worldwide`), which is what makes the migration a no-op for existing subscribers.
    """
    regs = {str(r).strip().lower() for r in (regions or [])}
    countries: list[str] = []
    if regs & {"cz", "eu", "worldwide"}:
        countries.append("CZ")
    if "uk" in regs:
        countries.append("GB")
    if "us" in regs:
        countries.append("US")
    if "worldwide" in regs:
        scope = "worldwide"
    elif "eu" in regs:
        scope = "eu"
    else:
        scope = "country"
    return countries or ["CZ"], scope


def describe(countries: Iterable[str], cities: Iterable[str], remote_scope: str) -> str:
    """One human line for the matcher prompt, e.g.
    "Czechia (Prague, Brno only); Germany (any city); plus remote roles anywhere in the EU".
    """
    codes = clean_countries(countries)
    picked = clean_cities(cities, codes)
    by_country: dict[str, list[str]] = {}
    for value in picked:
        country, slug = split_city(value)
        if country:
            label = CITIES.get(country, {}).get(slug) or slug.replace("-", " ").title()
            by_country.setdefault(country, []).append(label)
    parts = []
    for code in codes:
        name = KNOWN_COUNTRIES.get(code, code)
        chosen = by_country.get(code)
        parts.append(f"{name} ({', '.join(chosen)} only)" if chosen else f"{name} (any city)")
    scope = clean_remote_scope(remote_scope)
    if scope == "eu":
        parts.append("plus fully remote roles anywhere in the EU")
    elif scope == "worldwide":
        parts.append("plus fully remote roles worldwide")
    else:
        parts.append("fully remote roles only if based in those countries")
    return "; ".join(parts) if codes else "—"


# --- the SQL gate --------------------------------------------------------------

def location_predicate(profile: dict, alias: str = "p") -> tuple[str, list[Any]]:
    """SQL fragment (plus params) restricting postings to a profile's chosen locations.

    The rule, in one place:

      * **Fully remote** postings are judged only by the remote scope — their city is
        irrelevant, which is the whole reason the two controls are separate.
      * **On-site or hybrid** postings must sit in a selected country, and — only for the
        countries where the subscriber actually named cities — in one of those cities.
        Naming no city for a country means "any city there".
      * **Unknowns are kept.** A posting whose country or city we could not resolve passes
        the gate and reaches the AI matcher, which reads the raw location text. Dropping
        them would be a stricter promise than the data supports: `country_code` is null for
        whole sources, and a free-text city the subscriber typed is not in `CITIES` at all,
        so it can never match `postings.city` — for those the matcher is the only enforcer.
        Precision is what a miss costs; recall is never lost.

    Falls back to the coarse `region` gate when a profile has no `countries` — a row that
    predates the migration, or an API client that only sent `regions`.
    """
    countries = clean_countries(profile.get("countries"))
    if not countries:
        regions = [str(r) for r in (profile.get("regions") or [])]
        return (f"{alias}.region = any(%s)", [regions]) if regions else ("true", [])

    cities = clean_cities(profile.get("cities"), countries)
    restricted = sorted({split_city(v)[0] for v in cities} - {None})
    remote = f"coalesce({alias}.remote_signal, false)"

    params: list[Any] = [countries]
    if restricted:
        # Only the countries the subscriber named cities for are narrowed. An unresolved city
        # or country passes (see the docstring), and a country with no named city passes
        # wholesale — "Czechia" still means all of Czechia unless they said otherwise.
        city_gate = (f"{alias}.city is null or {alias}.country_code is null"
                     f" or not ({alias}.country_code = any(%s))"
                     f" or (lower({alias}.country_code) || ':' || {alias}.city) = any(%s)")
        params += [restricted, cities]
    else:
        city_gate = "true"
    onsite = (f"({remote} = false and ({alias}.country_code is null"
              f" or {alias}.country_code = any(%s)) and ({city_gate}))")

    scope = clean_remote_scope(profile.get("remote_scope"))
    if scope == "worldwide":
        remote_gate = "true"
    elif scope == "eu":
        remote_gate = (f"({alias}.country_code = any(%s)"
                       f" or {alias}.region in ('cz','eu','worldwide'))")
        params.append(list(COUNTRIES))
    else:
        remote_gate = (f"({alias}.country_code = any(%s)"
                       f" or ({alias}.country_code is null and {alias}.region = any(%s)))")
        params += [countries, regions_for(countries, scope)]

    return f"({onsite} or ({remote} and {remote_gate}))", params
