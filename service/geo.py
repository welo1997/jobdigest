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
# EEA_COUNTRIES: EU-27 **plus the rest of the EEA and Switzerland** — the area where an
# EU/EEA citizen may work without a permit (Norway, Iceland and Liechtenstein by the EEA
# Agreement, Switzerland by the free-movement accord). This set exists for ONE purpose: it is
# what the `eu` remote scope means ("Anywhere in the EU or EEA"). It is deliberately SEPARATE
# from the selectable `COUNTRIES` below — see the note there — so that adding a non-EEA country
# to the picker cannot silently widen what "in the EU or EEA" matches.
#
# It was EU-27 for most of this project's life until 2026-08-04, and that was a quiet mistake:
# on the day it changed, production held 28 active Swiss postings, 23 Norwegian and 2 Icelandic
# that no subscriber could select — already ingested, reachable only by a worldwide remote
# scope. The four codes were already in `COUNTRY_ALIASES` so the gate could *exclude* them,
# which is what made them invisible rather than merely unoffered.
EEA_COUNTRIES: dict[str, str] = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France",
    "DE": "Germany", "GR": "Greece", "HU": "Hungary", "IE": "Ireland", "IT": "Italy",
    "LV": "Latvia", "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta",
    "NL": "Netherlands", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "SK": "Slovakia", "SI": "Slovenia", "ES": "Spain", "SE": "Sweden",
    # EEA-EFTA and Switzerland.
    "CH": "Switzerland", "IS": "Iceland", "LI": "Liechtenstein", "NO": "Norway",
}

# The selectable set: the EEA + Switzerland, **plus the United Kingdom and the United States**
# (2026-08-05). GB and US were "stored but not offered" for most of this project's life, on the
# assumption that every subscriber was an EU/EEA citizen for whom a London- or Austin-onsite
# role needed a visa and correctly should not appear. That assumption changed: the product now
# serves UK and US citizens too, and for them an on-site role at home is exactly what they
# want. Both codes were already in `COUNTRY_ALIASES` and `FOREIGN_CITIES` (so the gate could
# resolve and formerly *exclude* them), which is what makes this a promotion rather than new
# plumbing. Note the consequence made explicit in `location_predicate`: a US-located onsite
# role now surfaces for a subscriber who picked the US, and a US fully-remote role still needs
# a `worldwide` scope, never `eu` — that is the EEA_COUNTRIES decoupling doing its job.
#
# LI has no curated cities beyond Vaduz, which is correct rather than lazy: the whole country
# is smaller than most of the metropolitan areas in this table.
COUNTRIES: dict[str, str] = {**EEA_COUNTRIES, "GB": "United Kingdom",
                             "US": "United States", "CA": "Canada"}

# Nothing is stored-but-unoffered any more (GB/US are now in COUNTRIES). Kept as an empty
# mapping so any caller or test that still references the name resolves to "nothing extra".
LEGACY_COUNTRIES: dict[str, str] = {}
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
    # The two-letter forms, which are how these two countries are actually written in a
    # location field and were the largest unresolved strings in production: "London, UK"
    # (272), "Remote - US" (558), "Remote U.S." (57). `normalise` splits on punctuation, so
    # "U.S." arrives as the two tokens "u s" and needs its own entry. Both are matched as
    # whole tokens, never substrings — "Columbus, OH" is one token and cannot contain "us".
    "uk": "GB", "u k": "GB", "us": "US", "u s": "US", "u s a": "US",
    "costa rica": "CR",
    # CZ/SK exonyms. A Czech or Slovak advert names the country in its own language, and
    # without these the posting resolves to *no* country — which the location gate keeps,
    # because an unknown country is left for the AI matcher. p09 (Prague-only) was therefore
    # shown CNC machinist work in "Elburg, Netherlands", "Nemecko, Holandsko" and
    # "Švajčiarsko": the text named the country outright and we simply could not read it.
    "nemecko": "DE", "nemecku": "DE", "rakousko": "AT", "rakusko": "AT", "rakusku": "AT",
    "holandsko": "NL", "holandsku": "NL", "nizozemi": "NL", "nizozemsko": "NL",
    "polsko": "PL", "polsku": "PL", "madarsko": "HU", "madarsku": "HU",
    "francie": "FR", "francuzsko": "FR", "spanelsko": "ES", "spanielsko": "ES",
    "italie": "IT", "taliansko": "IT", "belgicko": "BE", "svedsko": "SE", "svedsku": "SE",
    "dansko": "DK", "finsko": "FI", "irsko": "IE", "rumunsko": "RO", "bulharsko": "BG",
    "chorvatsko": "HR", "slovinsko": "SI", "recko": "GR", "portugalsko": "PT",
    "velka britanie": "GB", "anglie": "GB", "spojene state": "US",
    # CH and NO were here long before they were selectable, under the rule that a country we
    # cannot name is a country we cannot exclude. They are now offered as preferences too
    # (see COUNTRIES); the aliases do the same job either way — resolve the posting's country
    # so the gate can act on it rather than keeping it as unknown.
    "switzerland": "CH", "schweiz": "CH", "suisse": "CH", "svizzera": "CH",
    "svycarsko": "CH", "svajciarsko": "CH",
    "norway": "NO", "norge": "NO", "noreg": "NO", "norsko": "NO", "norwegen": "NO",
    # `island` is deliberately ABSENT, for the same reason as `georgia`. It is Icelandic and
    # German for Iceland, but matching is per token n-gram, so it would resolve "Long Island,
    # NY", "Rhode Island" and "Staten Island" to Iceland and delete them from every US
    # subscriber's digest. `islandia` (ES/PL) and `islanda` (IT) are safe because they are
    # not also ordinary English words.
    "iceland": "IS", "islandia": "IS", "islanda": "IS", "islande": "IS",
    "liechtenstein": "LI",
    # Still not selectable, and named for the original reason: to be excludable.
    "ukraine": "UA", "ukrajina": "UA", "serbia": "RS", "srbsko": "RS",
    # The same rule, applied to the countries the enterprise ATS sources actually post from.
    # Workday and SmartRecruiters reach employers who hire globally — NVIDIA, Philips, Adobe,
    # Delivery Hero, Bosch — and their non-European requisitions arrive naming the country in
    # plain English and nothing else: "India, Bengaluru", "Taiwan, Hsinchu", "Suzhou, China".
    # Every one of those resolved to *no country*, and an unknown country is deliberately
    # KEPT by `location_predicate` and handed to the AI matcher. So each was spending a slot
    # in the ~120-posting shortlist of subscribers who can only work in the EU, to be rejected
    # by a model that had to read it first. Naming them is what turns that into an exclusion.
    # Sampled from real unresolved locations on 2026-08-01, most frequent first.
    "israel": "IL", "india": "IN", "taiwan": "TW", "china": "CN",
    "hong kong": "HK", "vietnam": "VN", "viet nam": "VN", "singapore": "SG",
    "indonesia": "ID", "thailand": "TH", "malaysia": "MY", "philippines": "PH",
    "japan": "JP", "south korea": "KR", "korea": "KR", "australia": "AU",
    "new zealand": "NZ", "brazil": "BR", "brasil": "BR", "mexico": "MX",
    "argentina": "AR", "chile": "CL", "colombia": "CO", "peru": "PE",
    "canada": "CA", "turkey": "TR", "turkiye": "TR", "egypt": "EG",
    "south africa": "ZA", "nigeria": "NG", "kenya": "KE", "morocco": "MA",
    "united arab emirates": "AE", "saudi arabia": "SA", "qatar": "QA",
    "pakistan": "PK", "bangladesh": "BD", "sri lanka": "LK",
    "montenegro": "ME", "north macedonia": "MK", "albania": "AL",
    "bosnia and herzegovina": "BA", "moldova": "MD",
    "armenia": "AM", "iceland": "IS", "belarus": "BY", "kazakhstan": "KZ",
    # Deliberately absent: "georgia". The country name is also a US state, and the country
    # lookup runs before the city lookup — so "Atlanta, Georgia" would resolve to Georgia the
    # country and be excluded from the digest of every subscriber who selected the US. A US
    # state is far more likely than Tbilisi in this inventory, and being unknown is the
    # cheaper mistake.
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
    # EEA-EFTA and Switzerland, added 2026-08-04 with the countries themselves.
    "CH": {"zurich": "Zürich", "geneva": "Geneva", "basel": "Basel", "bern": "Bern",
           "lausanne": "Lausanne", "zug": "Zug", "lugano": "Lugano"},
    "NO": {"oslo": "Oslo", "bergen": "Bergen", "trondheim": "Trondheim",
           "stavanger": "Stavanger", "tromso": "Tromsø"},
    "IS": {"reykjavik": "Reykjavík"},
    # One city, and that is correct rather than unfinished: Liechtenstein is smaller than
    # most single entries elsewhere in this table.
    "LI": {"vaduz": "Vaduz"},
    # United States and United Kingdom, curated 2026-08-05 with the countries themselves.
    # These were resolver-only entries in FOREIGN_CITIES (country, no slug) for the whole of
    # the project's EEA-only life; promoting a name to a selectable city means MOVING it out of
    # FOREIGN_CITIES, or `check_no_shadowed_cities` raises at import. Only names that are
    # unambiguously US/GB are promoted: the Bay Area suburbs, "San Jose" (also Costa Rica) and
    # names that are also US *and* UK cities (Cambridge, Birmingham) are deliberately left in
    # FOREIGN_CITIES as country-only, so they still resolve to the right country but carry no
    # slug — a subscriber's null-city posting passes the gate anyway (see location_predicate).
    "US": {"san-francisco": "San Francisco", "new-york": "New York",
           "los-angeles": "Los Angeles", "seattle": "Seattle", "austin": "Austin",
           "boston": "Boston", "chicago": "Chicago", "washington-dc": "Washington, D.C.",
           "atlanta": "Atlanta", "dallas": "Dallas", "denver": "Denver",
           "san-diego": "San Diego", "houston": "Houston", "miami": "Miami",
           "philadelphia": "Philadelphia", "minneapolis": "Minneapolis"},
    "GB": {"london": "London", "manchester": "Manchester", "edinburgh": "Edinburgh",
           "glasgow": "Glasgow", "bristol": "Bristol", "leeds": "Leeds",
           "cardiff": "Cardiff", "belfast": "Belfast", "oxford": "Oxford",
           "liverpool": "Liverpool"},
    # Canada, selectable since 2026-08-07. Chosen from what production actually carries, not
    # from a list of big cities: Toronto ~461 rows, Vancouver ~100, Montréal ~31. Calgary,
    # Ottawa and Winnipeg carry almost nothing today and are offered because a subscriber
    # would expect to pick them.
    #
    # Two promotions were flagged as collisions and cleared by measurement rather than by
    # argument. **`vancouver`** is also Vancouver, Washington — and all 128 Vancouver rows in
    # production are Canadian, none American. **`edmonton`** is also a north-London district,
    # and `_ngrams` scans by start index, so a promoted CA name in token 0 beats a GB city in
    # token 1 — but all 20 Edmonton rows are Alberta. Both are recorded here so a future change
    # knows the collision is real even though the corpus does not contain it.
    #
    # Deliberately NOT promoted, and each for a different reason: `mississauga` (58 rows) is a
    # Toronto suburb — the Bay Area precedent, country-only; `quebec` is the province as well
    # as the city, so a `quebec` slug would repeat the `ontario` bug; `waterloo` is named by
    # test_geo.py as the canonical shadowing hazard (Belgium, and a London station);
    # `british columbia` and `alberta` are provinces — the US-state precedent.
    "CA": {"toronto": "Toronto", "vancouver": "Vancouver", "montreal": "Montréal",
           "calgary": "Calgary", "ottawa": "Ottawa", "winnipeg": "Winnipeg"},
}

# Alternative spellings a posting might use, per country: alias -> canonical slug. Only
# needed where the alias is not already the normalised display name (that mapping is derived
# automatically below), i.e. local names and common English variants.
CITY_ALIASES: dict[str, dict[str, str]] = {
    "AT": {"wien": "vienna"},
    # EEA-EFTA and Switzerland. Swiss adverts name a city in whichever of the four national
    # languages the employer writes in, so Geneva arrives as Genève, Genf or Ginevra.
    "CH": {"geneve": "geneva", "genf": "geneva", "ginevra": "geneva",
           "zuerich": "zurich", "basle": "basel", "bale": "basel", "berne": "bern",
           "losanna": "lausanne"},
    "NO": {"tromsoe": "tromso", "trondhjem": "trondheim"},
    "IS": {"reykjavik": "reykjavik"},
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
    # US: the Bay Area's common spellings collapse onto San Francisco, and the two New York
    # boroughs onto New York — a subscriber picking "San Francisco" wants the metro. Resolver
    # -only, so not mirrored in geo.ts (the drift test checks CITIES, not CITY_ALIASES).
    "US": {"sf": "san-francisco", "south san francisco": "san-francisco",
           "san francisco bay area": "san-francisco", "sf bay area": "san-francisco",
           "bay area": "san-francisco", "silicon valley": "san-francisco",
           "nyc": "new-york", "new york city": "new-york", "brooklyn": "new-york",
           "washington dc": "washington-dc"},
}

# Single-token city names that are also ordinary words. Matched only when the country is
# already known, so "Nice to have" in a location field cannot become Nice, France.
_AMBIGUOUS = {"nice", "split", "cork", "essen", "bari", "lund", "faro", "murcia"}

# --- places outside the selectable set, named only so the gate can exclude them ---------
#
# `COUNTRY_ALIASES` already names ~60 countries we never offer, under the rule that a country
# we cannot name is a country we cannot exclude. It only works when the posting writes the
# country out. **The largest single group of postings in production writes a bare city
# instead**, and no country at all: measured 2026-08-04, 7 147 of 30 043 active postings had
# a null `country_code`, 4 681 of them Greenhouse and 1 134 Ashby, and the top strings were
# "San Francisco" (275), "London" (218), "New York" (167), "Chicago" (114), "Toronto" (89).
#
# Those are not European postings, but `location_predicate` keeps an unknown country and
# hands it to the AI matcher — so every one of them was spending a slot in the ~120-posting
# shortlist of a subscriber who can only work in the EEA, to be rejected by a model that had
# to read it first. This is the "India, Bengaluru" problem of 2026-08-01, in the one form
# `COUNTRY_ALIASES` structurally cannot reach.
#
# Three rules hold this table safe, and all three are the `georgia` rule in a new place:
#
#   1. **It resolves a country and never a city.** The selectable US/GB cities live in
#      `CITIES` and resolve to a slug there; this table is the *uncurated remainder* plus
#      every non-selectable country, and it returns a country with no slug. It is deliberately
#      NOT mirrored in `web/lib/geo.ts` — see `test_geo.py`.
#   2. **It runs after the selectable-city lookup**, so it can never overrule a `CITIES`
#      resolution. "Berlin" is Germany, and "London" is London GB, before this table is ever
#      consulted.
#   3. **A name that is also a European city, a common word, or a country stays out.**
#      Excluded on purpose: `georgia` (US state, already the rule), `cambridge`,
#      `birmingham`, `paris`, `berlin`, `vienna`, `athens`, `naples`, `milan`,
#      `dublin`, `odessa`, `moscow`, `victoria`, `phoenix`, `springfield`. (`manchester` was
#      here until 2026-08-05; it is now a curated GB city.)
#
# One consequence changed on 2026-08-05, when GB and US became selectable: US-versus-Canada now
# matters where it did not. "London" resolves to GB (a curated city), so "London, ON" resolves
# to GB rather than Canada — invisible while neither was selectable, a real (if rare) error now.
# It is accepted: the English London dwarfs the Ontario one in this inventory, and the
# alternative (dropping London GB) would cost every UK subscriber their capital.
FOREIGN_CITIES: dict[str, str] = {
    # United States — the metros with a curated selectable city (CITIES["US"]) and their common
    # spellings (CITY_ALIASES["US"]) are deliberately NOT here: those resolve to (US, slug) via
    # the city lookup, which runs first. What remains is the *uncurated* US remainder — Bay
    # Area suburbs, smaller metros, and the state names — which resolves to the US with NO slug.
    # A US-selecting subscriber still gets them, because a null city passes the gate; before
    # 2026-08-05 the same rows let the EEA gate exclude them.
    "bellevue": "US", "redmond": "US", "boulder": "US", "san jose": "US",
    "santa clara": "US", "sunnyvale": "US", "mountain view": "US", "palo alto": "US",
    "menlo park": "US", "san mateo": "US", "oakland": "US", "berkeley": "US",
    "cupertino": "US", "orlando": "US", "pittsburgh": "US", "detroit": "US",
    "salt lake city": "US", "las vegas": "US", "portland": "US", "raleigh": "US",
    "charlotte": "US", "nashville": "US", "scottsdale": "US", "tempe": "US",
    "arlington va": "US", "bentonville": "US", "st louis": "US",
    # US state and territory names. "Georgia" is absent for the reason it is absent from
    # COUNTRY_ALIASES; "Washington" is safe because the state and the capital are one country.
    "california": "US", "washington": "US", "washington state": "US",
    "illinois": "US", "texas": "US",
    "massachusetts": "US", "new jersey": "US", "pennsylvania": "US", "colorado": "US",
    "arizona": "US", "florida": "US", "north carolina": "US", "virginia": "US",
    "ohio": "US", "michigan": "US", "oregon": "US", "utah": "US", "tennessee": "US",
    # United Kingdom: the GB cities are now curated and selectable (CITIES["GB"]), so they
    # resolve to (GB, slug) via the city lookup and are NOT here. `cambridge` and `birmingham`
    # are the exception — each is also a real US city, and with both countries selectable a
    # wrong guess is now visible either way, so they are left out of both tables and resolve to
    # no country (kept for the matcher) rather than to the wrong one.
    # Canada. Toronto, Vancouver, Montréal, Calgary, Ottawa and Winnipeg **moved into CITIES**
    # on 2026-08-07 when CA became selectable — moved, not copied, or
    # `check_no_shadowed_cities` raises at import. What stays here is country-only on purpose:
    # `mississauga` is a Toronto suburb (the Bay Area precedent), `quebec` is a province as
    # well as a city, `waterloo` is the shadowing hazard test_geo.py names (Belgium, London),
    # and the two provinces follow the US-state precedent. `ontario` remains absent from both
    # tables — it is also a city in California, and it would shadow it in "Ontario, California".
    "waterloo": "CA", "mississauga": "CA", "quebec": "CA",
    "british columbia": "CA", "alberta": "CA",
    # California towns that used to reach a country only through the `ca` subdivision code,
    # which was removed in the same change (see SUBDIVISION_CODES). Measured on production:
    # dropping the code left 22 locations / 77 active rows with no country, of which these four
    # are every one appearing more than once. The 18 single-row towns are deliberately not
    # listed — unknown is *kept* by the gate, so the cost is precision, not deletion, and a
    # table of one-offs would rot. `irvine` is deliberately excluded despite appearing: Irvine
    # is also a town in Scotland, and with GB and US both selectable that is the
    # `cambridge`/`birmingham` rule.
    "el segundo": "US", "poway": "US", "lompoc": "US", "fresno": "US",
    # Rest of world, from the same measurement. Country names themselves are already in
    # COUNTRY_ALIASES; these are the cities that arrive without one.
    "bengaluru": "IN", "bangalore": "IN", "gurugram": "IN", "gurgaon": "IN",
    "hyderabad": "IN", "pune": "IN", "chennai": "IN", "noida": "IN", "mumbai": "IN",
    "sydney": "AU", "melbourne": "AU", "brisbane": "AU", "perth": "AU", "canberra": "AU",
    "auckland": "NZ", "wellington": "NZ",
    "tokyo": "JP", "osaka": "JP", "yokohama": "JP",
    "seoul": "KR", "shanghai": "CN", "shenzhen": "CN", "beijing": "CN", "suzhou": "CN",
    "guangzhou": "CN", "hangzhou": "CN", "taipei": "TW", "hsinchu": "TW",
    "sao paulo": "BR", "rio de janeiro": "BR", "buenos aires": "AR", "bogota": "CO",
    "mexico city": "MX", "guadalajara": "MX", "monterrey": "MX", "san jose costa rica": "CR",
    "tel aviv": "IL", "jerusalem": "IL", "haifa": "IL",
    "dubai": "AE", "abu dhabi": "AE", "riyadh": "SA", "doha": "QA", "cairo": "EG",
    "manila": "PH", "cebu": "PH", "jakarta": "ID", "bangkok": "TH",
    "kuala lumpur": "MY", "ho chi minh city": "VN", "hanoi": "VN",
    "lagos": "NG", "nairobi": "KE", "cape town": "ZA", "johannesburg": "ZA",
    "istanbul": "TR", "ankara": "TR", "karachi": "PK", "lahore": "PK", "dhaka": "BD",
    "belgrade": "RS", "beograd": "RS", "novi sad": "RS", "kyiv": "UA", "kiev": "UA",
    "bogota dc": "CO", "santiago de chile": "CL", "lima peru": "PE",
}

# A trailing subdivision code — "Austin, TX" — names no country the resolver can read, and
# is the only thing left in the string once the city is one we do not carry. It is the last
# rule tried, and the narrowest: it fires only on the **final** token, and only for codes
# that cannot be a country a subscriber may select.
#
# `DE` (Delaware) and `MT` (Montana) are excluded because they are Germany and Malta, and
# `NL` (Newfoundland) and `SK` (Saskatchewan) because they are the Netherlands and Slovakia.
# **`CA` (California) joined them on 2026-08-07, when Canada became selectable.** It is the
# sharpest of the five: `SUBDIVISION_CODES["ca"] = "US"` meant "Kelowna, BC, CA" resolved to
# the United States — a British Columbia posting filed as American, deleted from the Canadian
# who wants it and delivered to the American who cannot take it. That is the Delaware failure
# exactly, and `test_no_subdivision_code_can_shadow_a_country_anyone_may_select` fires on it.
# The test was not weakened; the code was removed.
#
# The cost was measured rather than estimated: dropping it left **22 locations / 77 active
# rows** with no country. Most California strings never needed it — "San Francisco, CA",
# "Los Angeles, CA" and "Palo Alto, CA" all resolve from the city — and the four recurring
# towns that did are now in FOREIGN_CITIES. Unknown is *kept* by the gate, so what remains is
# a precision cost on ~30 rows, never a deletion.
#
# Every other collision is with a country nobody can select (`IL` Israel, `IN` India,
# `MA` Morocco, `CO` Colombia …) and is therefore invisible.
SUBDIVISION_CODES: dict[str, str] = {
    # US states, less DE and MT.
    **{c: "US" for c in (
        "al", "ak", "az", "ar", "co", "ct", "dc", "fl", "ga", "hi", "ia", "id", "il",
        "in", "ks", "ky", "la", "ma", "md", "me", "mi", "mn", "mo", "ms", "nc", "nd", "ne",
        "nh", "nj", "nm", "nv", "ny", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx",
        "ut", "va", "vt", "wa", "wi", "wv", "wy")},
    # Canadian provinces, less NL and SK.
    **{c: "CA" for c in ("ab", "bc", "mb", "nb", "ns", "nt", "nu", "on", "pe", "qc", "yt")},
}

# How a country maps onto the coarse legacy `region` bucket. Everything else is "eu".
_REGION_OF = {"CZ": "cz", "GB": "uk", "US": "us"}

REMOTE_SCOPES = ("country", "eu", "worldwide")
DEFAULT_REMOTE_SCOPE = "eu"

# How much of the job happens in an office. Stored on `postings.work_mode` (null = we could
# not tell) and selected on `profiles.work_modes`. Ordered office-first, which is the order
# the UI offers them in.
WORK_MODES = ("onsite", "hybrid", "remote")
DEFAULT_WORK_MODES = list(WORK_MODES)
WORK_MODE_LABELS = {
    "onsite": "On-site", "hybrid": "Hybrid", "remote": "Fully remote",
}

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
_FOREIGN_INDEX = {normalise(k): v for k, v in FOREIGN_CITIES.items()}


def check_no_shadowed_cities(foreign: Iterable[str], offered: Iterable[str]) -> None:
    """Raise if a foreign place name is also a city we offer.

    A name may not sit in both tables: `_CITY_INDEX` returns a city a subscriber can select
    and `_FOREIGN_INDEX` a country they never can, so a collision is a European posting
    resolving to another continent and vanishing from the digest of everyone who chose that
    country. Nothing would fail — which is why this runs at import, on the real tables, and
    is a named function so a test can prove it is still wired up.
    """
    clash = sorted(set(foreign) & set(offered))
    if clash:
        raise ValueError(f"FOREIGN_CITIES shadows a selectable city: {clash}")


check_no_shadowed_cities(_FOREIGN_INDEX, _CITY_INDEX)
_MAX_NGRAM = max(len(k.split())
                 for k in list(_CITY_INDEX) + list(_COUNTRY_INDEX) + list(_FOREIGN_INDEX))


def is_place_term(word: str) -> bool:
    """True if a single word names a city or country this module already filters on.

    Exists to keep geography out of *keyword recall*. The location gate has already decided
    where a subscriber will accept work, so a place name in their free-text label adds no
    reach — it only lets any posting whose text happens to mention the place satisfy the
    recall predicate. Observed on 2026-07-28: the label "Brno design" searched for **brno**
    and returned a personal banker, a tobacconist's assistant, two librarians and an
    upholsterer; the label "Germany" matched arbitrary's footer *"Find more English Speaking
    Jobs in Germany"*, present on all 1 267 of its postings, so one word matched an entire
    source. See `store._shortlist_terms`.
    """
    key = normalise(word)
    return bool(key) and (key in _CITY_INDEX or key in _COUNTRY_INDEX
                          or key in _FOREIGN_INDEX)


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

    if country is None:
        # Nothing we offer matched. Before giving up — which keeps the posting for everyone,
        # because an unknown country passes the gate — try the two rules that can only ever
        # name a country outside the selectable set. Both run here, last, so neither can
        # overrule a city or country resolved above.
        for _, phrase in _ngrams(tokens):
            hit = _FOREIGN_INDEX.get(phrase)
            if hit:
                return hit, None
        # "Austin, TX": a subdivision code, and only as the final token.
        country = SUBDIVISION_CODES.get(tokens[-1])
    return country, None


# Fully remote, as opposed to hybrid. "Hybrid in Brno" is the exact case the location gate
# exists to exclude for a Prague subscriber, so any hint of hybrid or on-site disqualifies
# *remote* — and, since 2026-07-29, says "hybrid" positively rather than merely "not remote".
_REMOTE_LOC = re.compile(
    r"\b(remote|anywhere|worldwide|distributed|work from home|home based|wfh"
    r"|vzdalene|prace z domova|telearbeit)\b")
_REMOTE_STRONG = re.compile(
    r"\b(fully remote|100 remote|remote only|remote first|work from anywhere"
    r"|plne vzdalene|zcela vzdalene)\b")
# The two halves of what used to be one `_NOT_REMOTE` pattern. Both still disqualify remote;
# they are separated because they disagree about what the posting *is*, and that difference is
# now stored: "hybrid" is a role someone in Brno cannot take but someone in Prague can do from
# home two days a week, while "no remote" is five days in the office. Searched in the location
# field only, which is why the bare word "hybrid" is safe here and not in body copy.
_HYBRID_LOC = re.compile(r"\b(hybrid|hybridni|hybridne|hybridny)\b")
_ONSITE_LOC = re.compile(
    r"\b(on site|onsite|in office|no remote|not remote"
    r"|remote (work )?(not|un)\w*|bez home office|bez prace z domova)\b")
# Czech/Slovak boards rarely write "hybrid" — they grade the home office instead, and the
# grade is the whole meaning: "moznost obcasne prace z domova" is a Prague office job with a
# perk, not a role someone in Brno can take. The qualifier must sit next to the home-office
# phrase, because the words alone are far too common in body copy to disqualify a posting.
_QUALIFIED_HOME_OFFICE = re.compile(
    r"\b(obcasn\w*|prevazne|castecne|prilezitostn\w*|moznost"
    # Profesia's English UI writes the same grade in English: "Košice, Slovakia (Job with
    # occasional home office)" — 320 further postings, and invisible to a Czech-only stem list.
    r"|occasional\w*|partial\w*|predominant\w*|mostly)\s+(?:\w+\s+){0,2}"
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


def _describes_hybrid(text: str) -> bool:
    """Does this posting's own text describe a part-office, part-home arrangement?

    An explicit, unambiguous remote claim wins: a description may mention a hybrid model in
    passing (often to contrast with itself), and "fully remote" said outright is the stronger
    statement. Everything else here names a specific in-office expectation.
    """
    if _REMOTE_STRONG.search(text):
        return False
    return bool(_QUALIFIED_HOME_OFFICE.search(text) or _HYBRID_SCHEDULE.search(text))


def work_mode(location: Optional[str], description: Optional[str] = None,
              source_signal: Optional[bool] = None) -> Optional[str]:
    """How much of this job happens in an office: ``remote`` | ``hybrid`` | ``onsite`` | None.

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

    **``None`` means unknown, and it is the common answer** — most postings simply never say.
    It is deliberately not folded into ``onsite``: a subscriber who deselects on-site work
    would then lose most of the inventory to a guess. Unknown is kept by the SQL gate and the
    preference is passed to the AI matcher instead, which reads the whole description and can
    tell an office job from a hybrid one far better than a pattern list — the same division of
    labour `resolve_location` uses for an unresolved city. What this classifier is for is the
    part that can be *proved*: `remote` is positively detected, so "fully remote only" is a
    filter that genuinely works, and `hybrid` is what 5 200 postings actually were.
    """
    # Checked against both fields and before every other rule: a posting that describes an
    # in-office expectation is hybrid regardless of how it reached us or what it claims.
    if _describes_hybrid(normalise(f"{location or ''} {(description or '')[:2000]}")):
        return "hybrid"
    if source_signal:
        return "remote"
    loc = normalise(location)
    if _HYBRID_LOC.search(loc):
        return "hybrid"
    if _ONSITE_LOC.search(loc):
        return "onsite"
    if _REMOTE_LOC.search(loc):
        return "remote"
    if _REMOTE_STRONG.search(normalise(description)[:2000]):
        return "remote"
    return None


def is_fully_remote(location: Optional[str], description: Optional[str] = None,
                    source_signal: Optional[bool] = None) -> bool:
    """Would this posting let someone work from another city entirely?

    The boolean face of `work_mode`, and the one `postings.remote_signal` stores. Kept as its
    own function because that column is what exempts a posting from the location gate, and a
    caller asking "may I ignore this posting's city?" should not have to know that `hybrid`
    and `onsite` and unknown all answer no for different reasons.
    """
    return work_mode(location, description, source_signal) == "remote"


# --- how far a remote role reaches ---------------------------------------------
#
# `work_mode` answers "does this job happen in an office". This answers the *other* question a
# remote posting raises and the one subscribers actually read into the word: "and where am I
# allowed to live while doing it". The two are independent, and conflating them is a promise we
# cannot keep — most work-from-home roles are bound to one country because that is where the
# employer runs payroll, a minority are genuinely location-independent, and the difference is
# what decides whether a Prague subscriber can take a US-remote job. They cannot.
#
# Values, widest first:
#   anywhere  names no geographic restriction at all ("Anywhere in the World", "Worldwide")
#   region    bound to a named macro-region, a timezone band, or an explicit set of >= 2
#             countries — you may live abroad, inside that set
#   country   bound to exactly one country: work from *home*, not from another country
#   None      the posting never said. **The majority answer, and kept everywhere** — same rule
#             as `work_mode`, `education_min` and an unresolved city. A display filter is
#             equality so it excludes None; the digest path must never gate on this in SQL.
#
# Only meaningful for a fully-remote posting, and `ingest` stores it only for those: "reach" of
# an on-site Berlin job is a category error, and computing it anyway would fill the column with
# trivially-`country` rows and make coverage look far better than it is.
#
# **The signal is structured data first, prose last.** Measured 2026-08-14: reading description
# prose found an explicit scope on 5.8% of active remote rows, which read as "unprovable, give
# up". That was measuring the wrong layer. The remote boards publish scope as a *field* — WWR's
# `region`, Himalayas' `locationRestrictions` + `timezoneRestrictions`, Jobicy's `jobGeo`,
# Remotive's `candidate_required_location`, Ashby's `secondaryLocations` — and three adapters
# were dropping theirs on the floor. `JobPosting.scope_raw` carries that field verbatim so this
# classifier reads a publisher's own answer instead of guessing at body copy.

REACH_IDS = ("anywhere", "region", "country")
REACH_LABELS = {
    "anywhere": "Work from anywhere",
    "region": "Remote within a region",
    "country": "Remote within one country",
}

# --- and *which* areas it reaches ----------------------------------------------
#
# `remote_reach` measures the *breadth* of a scope. It cannot answer the question `/jobs` actually
# asks, which is "could I, here, hold this job" — `region` covers "Europe", "APAC" and "US or
# Canada" alike, and only one of those is any use to someone in Prague. `reach_areas` is the
# second half: which of the areas we can name does a multi-country scope actually include.
#
# Two areas, because those are the two the product serves (`COUNTRIES` is the EEA plus GB, US and
# CA). They **overlap by design** — a scope reading "North America or Europe" is in both, and that
# overlap is the only set in which someone in the EEA can hold a US-facing role.
#
#   eea   the scope includes at least one EEA country, or a European macro-region or timezone band
#   na    the scope includes the US or Canada, or a North-American macro-region
#
# **Only computed for `anywhere` and `region`.** A single-country scope is already answered by the
# country filter — a Germany-remote job belongs under Germany, not under an international heading —
# so `country` and unknown reaches get an empty list. Empty is also what an unreadable scope gets,
# and it passes every filter rather than being hidden: the same rule as `skills`, `work_mode` and
# `education_min`.
#
# `EEA_COUNTRIES` is used rather than `COUNTRIES` deliberately, and the difference is not cosmetic:
# GB is selectable but is *not* in the EEA, so "Australia, Canada, New Zealand, United Kingdom,
# United States" is `na` and not `eea`. Merging the two sets here would silently tell a Czech
# visitor that a UK-only role is open to them. See the `COUNTRIES` / `EEA_COUNTRIES` rule.

REACH_AREAS = ("eea", "na")
REACH_AREA_LABELS = {"eea": "EU-International", "na": "North America-International"}
#: The `na` area in selectable-country terms. Mexico is North America geographically and is not
#: here, because nothing in `COUNTRIES` can select it and an area nobody can pick grants nothing.
_NA_COUNTRIES = frozenset({"US", "CA"})

# "Europe", in the languages the boards write in. Measured 2026-08-15: this module read the word
# in English and French only, so "Europaweit", "Remote, Europa", "Europees" and "Hela Norden" all
# classified as *no scope at all* — a silent miss on exactly the boards whose inventory the
# EU-International row exists to surface (arbeitnow is German and ran 48% unknown). Country names
# were never the gap: `COUNTRY_ALIASES` already reads "Deutschland" and "Nederland". Only the
# macro-region words were English.
#
# One constant, because it feeds both the "is this a region at all" test and the "does that region
# include the EEA" test, and two copies of a word list drift. Stems are enumerated rather than
# written `europ\w*`: that shorter pattern also matches *Europcar*, and a company name is not a
# scope.
#
# **Excluded under the `georgia` rule**, having been checked rather than assumed:
#   - `norden` — Swedish/Danish/Norwegian for "the Nordics" *and* a town in Lower Saxony *and* the
#     ordinary German word for "the north". `nordisk`/`nordiska`/`pohjoismaat` carry the same
#     meaning with no collision.
#   - `mundial` (es/pt) — "worldwide", and also the football World Cup.
_EUROPE = (r"europe|european|europa|europaweit|europaisch\w*|europeen\w*|europees|europese"
           r"|europejsk\w*|europeo|europea|europei|europee|europeu|europeia"
           r"|evropa|evropsk\w*|eurooppa|eurooppalainen")
#: The Nordics and Iberia in their own languages, for the same reason. Every one of these is
#: inside the EEA, so they belong to both patterns below.
_EUROPE_SUB = r"dach|benelux|nordics|nordic|nordisk\w*|skandinav\w*|pohjoismaat|baltics|iberia"

# Macro-regions and timezone bands that contain the EEA. "EMEA" is Europe + Middle East + Africa,
# so it counts; "MENA" is Middle East + North Africa and does not, which is why it is absent.
_EEA_AREA = re.compile(
    rf"\b({_EUROPE}|eu|eea|emea|{_EUROPE_SUB}|cee|schengen|eurozone)\b")
# European timezones. `bst` and `wet` are deliberately absent: BST is British and GB is not in the
# EEA, and "wet" is an ordinary English word. UTC+0 is Ireland and Portugal, so it counts.
#: Case-insensitive because, unlike every other pattern here, these two run against the **raw**
#: scope text rather than `normalise`d output — the sign in "UTC+2" does not survive normalisation.
#: Boards write "CET", not "cet".
_EEA_TZ = re.compile(r"\b(cet|cest|eet|eest)\b|\b(utc|gmt)\s*\+\s*[0-3]\b", re.I)
# North America. `latam`, "south america" and "central america" are absent on purpose — they name
# the half of the Americas that grants nothing in the US or Canada.
_NA_AREA = re.compile(r"\b(north america|americas|amer|nam)\b")
# Only the offset form. The bare abbreviations (EST/CST/PST/MST) are left out for the reason given
# on `_TIMEZONE_BAND`: "est" is a word in three European languages. The cost is nil — a role bound
# to US timezones names the US, and a named country is read before any band.
_NA_TZ = re.compile(r"\b(utc|gmt)\s*-\s*([4-9]|10)\b", re.I)

# Matched against the scope/location field, never body copy. "global" is safe here and would be
# hopeless there — a description says "a global leader in" and means nothing about eligibility.
_ANYWHERE_SCOPE = re.compile(
    r"\b(anywhere|worldwide|world wide|global|globally|any country"
    r"|location independent|fully distributed globally"
    # The same words on the non-English boards, added 2026-08-15 alongside `_EUROPE`. Held to
    # compounds and set phrases: this pattern is also run over the window after "must be based
    # in", so a bare adjective that doubles as marketing ("mundial", "globale") would read a
    # company's self-description as an eligibility grant — the failure `_ANYWHERE_TEXT`'s hedge
    # guard exists for. `weltweit` and `wereldwijd` are safe because they can only be reached
    # here through that bound window, never through free body copy.
    r"|weltweit|wereldwijd|celosvetove|kdekoli\w*|gdziekolwiek|na calym swiecie"
    r"|en cualquier lugar|en cualquier parte|desde cualquier lugar"
    r"|partout dans le monde|n importe ou dans le monde"
    r"|in tutto il mondo|da qualsiasi luogo|em qualquer lugar"
    r"|var som helst|hvor som helst|overallt)\b")
# The description equivalent, deliberately much narrower: only phrases that state the policy
# outright. A bare "anywhere" in body copy is "anywhere in the org", "anywhere from 3-5 years".
#
# The hedge guard is not hypothetical. Measured 2026-08-14, `anywhere` read out of the location
# field was right 22 times out of 22, and read out of body copy right once in three — both
# failures being sentences that *say* anywhere and then take it back:
#   "work from almost anywhere in the world; IDEXX offers employment contracts in 30 [countries]"
#   "you can work from anywhere in the world as long as your main location is between UTC-5 and
#    UTC+2"
# The first is caught by refusing the hedge, the second by `_ANYWHERE_HEDGED_BY` below, which
# downgrades to `region` when a timezone band sits beside the phrase. Marketing copy promises
# freedom in the same paragraph that limits it, which is exactly why prose is the last resort.
_ANYWHERE_TEXT = re.compile(
    r"\b(?<!almost )(?<!nearly )(?<!virtually )(?<!practically )(?<!just about )"
    r"(work from anywhere|from anywhere in the world|anywhere in the world"
    r"|no location restrictions?|location independent|any country in the world"
    r"|work remotely from anywhere)\b")
#: How far either side of an `_ANYWHERE_TEXT` hit to look for a qualifier that takes it back.
_ANYWHERE_WINDOW = 220
# Named macro-regions. Searched on the *residue* after country names have been blanked out, so
# "South Africa" cannot be read as the continent and "United States of America" cannot be read
# as "Americas". "america" (singular) is deliberately absent: it usually means the USA.
_MACRO_REGION = re.compile(
    rf"\b({_EUROPE}|eu|eea|emea|apac|asia pacific|asiapac|latam|latin america"
    r"|north america|south america|central america|americas|africa|oceania|middle east|mena"
    rf"|asia|anz|{_EUROPE_SUB}|cee|schengen|eurozone"
    # `amer` and `nam` are the North-American members of the AMER/EMEA/APAC trio that large
    # employers write their regions in, and `iberia` of the sub-European set alongside DACH and
    # the Nordics. Added 2026-08-14 after "AMER" and "Remote-Iberia" turned up as scope fields
    # this list could not read — a scope naming a region we cannot parse falls through to
    # unknown, which is safe but loses a row the visitor could have used.
    # (`iberia` now lives in `_EUROPE_SUB`, with the other sub-European regions, because it is
    # one of the ones the EEA test has to agree about.)
    r"|commonwealth|amer|nam)\b")
# A timezone band is a geographic scope too, and a wide one: "CET (+/- 3 hours)" spans some
# thirty countries, so it means "abroad, within this band" rather than any single country.
#
# The American abbreviations (EST/CST/PST/MST) are deliberately absent. "est" is a word in
# French, Spanish and Latin and shows up in normalised body copy constantly, and the cost of
# omitting them is nil: a role bound to US timezones names the US, and the country rule below
# already outranks this one. Bare "overlap" is out for the same reason — it is almost always
# "overlap with our team", and the cases that matter ("EU timezone overlap") say timezone.
_TIMEZONE_BAND = re.compile(r"\b(time ?zones?|utc|gmt|cet|cest|eet|weso?t)\b")
# Right-to-work phrasing, the only prose worth reading on a general employer board. The scope
# classifier is re-run on the window that follows, so "eligible to work in the EU" resolves the
# same way the field "EU" would.
#
# **A bare "based in" is not enough, and that was the first version's mistake.** Every posting
# describes its employer, and "Azumo is based in San Francisco" / "a Munich-based company is
# looking for a founding engineer" then read as a restriction on the candidate — attaching a
# company's head office to a role that may well be open across the EU. Audited on 18 live
# description-derived verdicts, five of them were an employer's own address read as an
# eligibility rule.
#
# So the trigger must either state an obligation outright (`must be based`, `right to work`) or
# name who is being restricted (`candidates based in`, `you must reside in`). "Azumo is based in"
# matches neither. This costs recall — some genuine "based in the greater San Diego area" role
# statements are lost with it — and that is the correct direction: this classifier's only safe
# error is a miss, and 97 of 3 593 verdicts came from prose at all.
_TEXT_BOUND = re.compile(
    r"\b(?:"
    # (a) an obligation, whoever it is aimed at
    r"must (?:be |currently be )?(?:a )?(?:located|based|resident|residing|reside|living)"
    r"|(?:eligible|authori[sz]ed|legally able|permitted|able) to work"
    r"|right to work|work authori[sz]ation|work permit|permanent resident"
    r"|(?:can only|only) (?:hire|employ|consider)"
    # (b) or an explicit subject — the candidate, not the company
    r"|(?:candidates?|applicants?|you|your|talent|new hires?|hires?|employees?"
    r"|team members?|this (?:role|position)|the (?:role|position))"
    r"(?:\s+\w+){0,3}?\s+(?:based|located|residing|reside|living|sits|open)"
    r")"
    r"(?:\s+\w+){0,3}?\s+(?:in|within|from|across)\s+"
    r"(?:the\s+)?(?P<scope>.{0,60})")
# `normalise` flattens HTML to bare words, so a scope window runs straight through the end of its
# sentence and into the next block. That is how "right to work in Germany" picked up arbeitnow's
# site-wide footer link — "…in germany p p find a href https www arbeitnow co uk jobs in united
# kingdom" — and read as a two-country region. Block-level tag remnants are where the sentence
# actually ended, so the window stops at the first one. Inline tags (`strong`, `em`, `span`) are
# deliberately absent: they sit *inside* the list being enumerated, and cutting there would lose
# "candidates based in the <strong>EU, UK or North America</strong>".
_MARKUP_BREAK = re.compile(r"\b(?:p|br|div|li|ul|ol|tr|td|table|href|https|www|h[1-6])\b")


def countries_named(text: Optional[str]) -> tuple[set[str], str]:
    """Every country ``text`` names, plus the text with those names blanked out.

    Reuses this module's own tables — `COUNTRY_ALIASES` for country names, `CITIES` and
    `FOREIGN_CITIES` for the places that imply one — because geography has one definition and a
    second list of country spellings here would drift from `resolve_location` within a month.
    Longest n-gram first and non-overlapping, so "united states" is one hit rather than "united
    states" plus a stray "us".

    **An explicitly named country outranks a country merely implied by a city**, exactly as in
    `resolve_location`, and the whole set of implied ones is discarded when any explicit name is
    present. RemoteOK writes "London, London, Ontario, Canada"; reading the city as Great Britain
    alongside the stated Canada makes a single-city job look open in two countries, which is the
    difference between "work from home in Ontario" and "live wherever you like in two countries".
    Where nothing is named outright, an unambiguous city is the best evidence available and
    "Remote, Bangalore" resolves to India.

    The residue is what makes the macro-region pass safe: "South Africa" leaves nothing behind
    that looks like a continent, so the caller can then trust a bare "africa".
    """
    tokens = normalise(text).split()
    if not tokens:
        return set(), ""
    named: set[str] = set()
    implied: set[str] = set()
    consumed = [False] * len(tokens)
    for start, phrase in _ngrams(tokens):
        size = len(phrase.split())
        if any(consumed[start:start + size]):
            continue
        hit = _COUNTRY_INDEX.get(phrase) or _FOREIGN_INDEX.get(phrase)
        bucket = named
        if not hit:
            cities = _CITY_INDEX.get(phrase)
            # An ambiguous city name never implies a country here, on the `georgia` rule.
            if cities and len(cities) == 1 and phrase not in _AMBIGUOUS:
                hit, bucket = cities[0][0], implied
        if hit:
            bucket.add(hit)
            for i in range(start, start + size):
                consumed[i] = True
    residue = " ".join(t for t, used in zip(tokens, consumed) if not used)
    return (named or implied), residue


#: How a `scope_raw` built from a board's **list** of locations separates one entry from the next.
#: The comma is already spoken for *inside* an entry ("Warsaw, PL"), so the adapters that join a
#: `locations[]` array — teamtailor, lever, recruitee, workable — join it with this instead.
_SCOPE_ENTRY = re.compile(r"\s*;\s*")


def countries_in_scope(scope: Optional[str]) -> tuple[set[str], str]:
    """`countries_named` over a scope that may be a **list** of locations, unioned per entry.

    `countries_named`'s "an explicit name discards every city-implied country" rule is correct
    for one location phrase and wrong for a list of them, and the difference is not academic —
    it was a live miss the moment the ATS adapters began emitting `locations[]`. Printful's
    ``"Warsaw, PL; Kyiv, UA; Bucharest, RO; Tallinn, EE; Barcelona, ES"`` has five cities in five
    countries, of which only ``ua`` is a token this module reads as a country name outright. That
    one hit filled the `named` bucket, the other four were dropped as merely implied, and a role
    open across five countries classified as ``country`` — Ukraine, the one country the posting
    arguably says least about.

    Splitting first keeps both rules intact: within an entry, "London, London, Ontario, Canada"
    is still one phrase and still resolves to Canada alone; across entries, each contributes
    whatever it names or implies. A scope with no separator behaves exactly as before.
    """
    segments = [s for s in _SCOPE_ENTRY.split(scope or "") if s.strip()]
    if len(segments) < 2:
        return countries_named(scope)
    countries: set[str] = set()
    residues: list[str] = []
    for segment in segments:
        found, residue = countries_named(segment)
        countries |= found
        residues.append(residue)
    return countries, " ".join(r for r in residues if r)


def _reach_of_scope(scope: Optional[str]) -> Optional[str]:
    """Classify one scope string — a board's own field, or a window of right-to-work prose.

    **A named country outranks every wider signal**, and that ordering is the whole classifier.
    All three false positives found on live data in the first measurement pass were a wide
    signal winning over a stated country:

    - RemoteOK's "Anywhere in the United States" — a US-only role — read as `anywhere`, which
      would put an unreachable job in front of a Prague subscriber under a filter that promises
      the opposite.
    - Himalayas' `"United States | UTC-10..UTC-5, UTC+14"` read as `region`, for **every one of
      300 sampled postings**: the timezone band is a refinement *inside* the country, not a
      wider grant, and taking it as scope wiped the country/region distinction off the board.
    - "London, London, Ontario, Canada" read as `region` — see `countries_named`.

    So countries are resolved first and answer alone if there are any. A macro-region or
    timezone band only speaks when no country is named, and `anywhere` only when there is no
    narrower reading at all — "Anywhere in Europe" is Europe, not anywhere.
    """
    text = normalise(scope)
    if not text:
        return None
    countries, residue = countries_in_scope(scope)
    if len(countries) >= 2:
        return "region"          # an explicit multi-country set: abroad, within that set
    if len(countries) == 1:
        return "country"
    if _MACRO_REGION.search(residue) or _TIMEZONE_BAND.search(residue):
        return "region"
    if _ANYWHERE_SCOPE.search(text):
        return "anywhere"
    return None


def remote_reach(scope_raw: Optional[str] = None,
                 location: Optional[str] = None,
                 description: Optional[str] = None) -> Optional[str]:
    """How far a fully-remote posting reaches: ``anywhere`` | ``region`` | ``country`` | None.

    Reads the three sources of truth in descending order of trustworthiness, and stops at the
    first that answers: the board's own structured scope field, then the location text, then —
    only through the right-to-work keyhole of `_TEXT_BOUND` and the outright policy statements of
    `_ANYWHERE_TEXT` — the description.

    Precedence is not a tie-break, it is a trust ordering. A publisher who filled in a scope
    field has answered the question; body copy mentioning a country is usually saying something
    else entirely ("our Berlin office", "customers across Europe"), which is why prose is read
    last and through a keyhole.

    Returning None is a real answer and the common one. The only safe error here is a miss:
    claiming a country-bound role is open worldwide sends someone to an application they cannot
    legally take, so every rule is a positive detection and nothing is inferred from silence.
    """
    return _classify_reach(scope_raw, location, description)[0]


def _classify_reach(scope_raw: Optional[str] = None,
                    location: Optional[str] = None,
                    description: Optional[str] = None) -> tuple[Optional[str], str]:
    """``(reach, the normalised text that decided it)``.

    The second half exists so `reach_areas` reads **the same field the verdict came from**. Doing
    it any other way lets the two columns describe different sentences: a posting whose scope field
    says "EMEA" and whose location says "APAC" would be stored `region` (from the scope field, the
    trusted layer) with areas taken from the location — filed under the wrong continent, and no
    test comparing the columns pairwise would look wrong because each is individually defensible.
    """
    for field in (scope_raw, location):
        hit = _reach_of_scope(field)
        if hit:
            # The **raw** field, not the normalised one. `normalise` reduces punctuation to spaces,
            # so "UTC+2" and "UTC-8" both arrive as "utc 2" / "utc 8" with the sign gone — and a
            # signed-offset pattern applied to normalised text is a regex that can never match,
            # which is a silent miss rather than an error. `_areas_of` normalises for the country
            # and macro-region passes and keeps the raw text for the timezone offsets.
            return hit, (field or "")

    text = normalise(description)[:4000]
    if not text:
        return None, ""
    # Narrowest wins among the right-to-work windows, and they are read *before* any
    # work-from-anywhere phrasing: an ad naming both "the EU" and "Germany" is telling you about
    # the entity and the seat, and the seat is the binding half. Same reason "we can only hire in
    # the US and Canada" has to outrank a cheerful "work from anywhere" in the perks list — one
    # of those two sentences is the one that stops an application.
    best: Optional[str] = None
    best_text = ""
    for m in _TEXT_BOUND.finditer(text):
        scope = m.group("scope")
        cut = _MARKUP_BREAK.search(scope)
        window = scope[:cut.start()] if cut else scope
        hit = _reach_of_scope(window)
        if hit is None:
            continue
        if best is None or REACH_IDS.index(hit) > REACH_IDS.index(best):
            best, best_text = hit, window
    if best:
        return best, best_text
    m = _ANYWHERE_TEXT.search(text)
    if not m:
        return None, ""
    # "Anywhere in the world, as long as you are between UTC-5 and UTC+2" is a band, not a grant.
    window = text[max(0, m.start() - _ANYWHERE_WINDOW):m.end() + _ANYWHERE_WINDOW]
    return ("region", window) if _TIMEZONE_BAND.search(window) else ("anywhere", window)


def _areas_of(reach: Optional[str], raw: str) -> list[str]:
    """Which of `REACH_AREAS` a multi-country scope grants. Order follows `REACH_AREAS`.

    ``raw`` is the scope text as the board wrote it. The country and macro-region passes run on the
    normalised form; the timezone-offset patterns run on ``raw``, because normalisation deletes the
    sign and "UTC+2" (Europe) and "UTC-8" (California) are the same string once it has.
    """
    if reach == "anywhere":
        # No restriction stated, so both areas are reached. This is the one place the two
        # options overlap wholesale, and it is why the `anywhere` rows are the good half of
        # both counts.
        return list(REACH_AREAS)
    if reach != "region":
        # `country` is answered by the country filter itself; None means nobody said. Neither
        # belongs under an international heading, and an empty list narrows nothing.
        return []
    countries, residue = countries_in_scope(raw)
    # Union, never either/or. "Canada, Europe, USA" names two countries *and* a macro-region, and
    # stopping at the countries files a role a European may hold as North-America-only — found
    # while measuring, on 26 live postings.
    eea = bool(countries & set(EEA_COUNTRIES)) or bool(
        _EEA_AREA.search(residue) or _EEA_TZ.search(raw))
    na = bool(countries & _NA_COUNTRIES) or bool(
        _NA_AREA.search(residue) or _NA_TZ.search(raw))
    return [area for area, hit in (("eea", eea), ("na", na)) if hit]


def reach_areas(scope_raw: Optional[str] = None,
                location: Optional[str] = None,
                description: Optional[str] = None) -> list[str]:
    """Which named areas a fully-remote posting's scope reaches: ``["eea"]``, ``["eea", "na"]``, …

    Empty means "not a multi-country scope, or we could not read which areas" — and empty must
    narrow nothing, on the same rule as `skills`. A scope naming both Europe and North America
    returns both, which is correct rather than sloppy: it is exactly the set in which someone in
    the EEA can hold a US-facing role.

    Read `reach_areas` and `remote_reach` from `classify_reach` when you need both — computing them
    from separate calls is the same work twice and invites them to drift.
    """
    reach, text = _classify_reach(scope_raw, location, description)
    return _areas_of(reach, text)


def classify_reach(scope_raw: Optional[str] = None,
                   location: Optional[str] = None,
                   description: Optional[str] = None
                   ) -> tuple[Optional[str], list[str]]:
    """``(remote_reach, reach_areas)`` in one pass — what `ingest` and the backfill both store."""
    reach, text = _classify_reach(scope_raw, location, description)
    return reach, _areas_of(reach, text)


def reach_countries(scope_raw: Optional[str] = None,
                    location: Optional[str] = None) -> list[str]:
    """Every country a posting names as a place the job can be held, when it names **two or more**.

    `postings.country_code` is one country, and a posting is routinely open in several: a
    Teamtailor role listing twelve `jobLocation` entries, an Ashby role whose secondaries are
    "Germany, France, Portugal (remote)", a Remotive row whose candidate location is "USA,
    Canada". `resolve_location` has to pick one of those to be *the* country — that is its job,
    and the city has to agree with it — so every other country the posting names was unreachable
    by the country filter. A Printful posting open in twelve countries answered eleven country
    filters with silence.

    **Deliberately not gated on being remote**, unlike `remote_reach` and `reach_areas`. Those
    two answer "where may I *live*", which is a category error for an on-site job. This one
    answers "is there a job for me in country X", and two offices in two countries is a perfectly
    ordinary way for the answer to be yes in both. It is the same question the Country filter
    asks, so it belongs to the same control.

    **Fields only — never the description.** Prose is read through a keyhole for reach
    (`anywhere` from body copy was right one time in three) and minting a country filter entry
    out of it would be a stronger claim on weaker evidence: an employer's head office read as a
    second hiring country. Both arguments here are a board's own structured location list.

    Empty below two countries, on the same rule as `reach_areas`: one country is what
    `country_code` already says, and a column that repeats another one only invites them to
    disagree.
    """
    countries: set[str] = set()
    for field in (scope_raw, location):
        if field:
            countries |= countries_in_scope(field)[0]
    return sorted(countries) if len(countries) >= 2 else []


def clean_remote_reach(value: Any) -> Optional[str]:
    """The stored/queried form of a reach value, or None for anything unrecognised."""
    v = str(value or "").strip().lower()
    return v if v in REACH_IDS else None


def clean_reach_areas(values: Any) -> list[str]:
    """The queried form of an area list: recognised ids only, in `REACH_AREAS` order, deduped.

    The public `/jobs` feed passes these straight from a URL parameter, so an unrecognised id must
    be dropped rather than reaching SQL — and dropping it can only ever *widen*, because an empty
    list means the international filter was not asked for.
    """
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    given = {str(v).strip().lower() for v in values}
    return [area for area in REACH_AREAS if area in given]


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


def clean_work_modes(values: Any) -> list[str]:
    """Normalise a work-setup selection, in `WORK_MODES` order.

    An empty or entirely unrecognised selection becomes **all three**, not none. A subscriber
    who somehow clears every box has expressed no preference, and reading that as "nothing is
    acceptable" would filter their digest down to zero without a single error — the failure
    this codebase keeps meeting. Widening is the safe direction here for the same reason it is
    in `query_shortlist_meta`.
    """
    wanted = {str(v).strip().lower().replace("-", "").replace("_", "") for v in (values or [])}
    # Accept the spellings a hand-written API client is likely to send.
    aliases = {"onpremise": "onsite", "office": "onsite", "inoffice": "onsite",
               "fullyremote": "remote", "wfh": "remote"}
    wanted = {aliases.get(w, w) for w in wanted}
    out = [m for m in WORK_MODES if m in wanted]
    return out or list(DEFAULT_WORK_MODES)


def describe_work_modes(modes: Iterable[str]) -> str:
    """One human phrase for the matcher prompt, e.g. "hybrid or fully remote roles only"."""
    picked = clean_work_modes(modes)
    if len(picked) == len(WORK_MODES):
        return "any work setup (on-site, hybrid or fully remote)"
    names = [WORK_MODE_LABELS[m].lower() for m in picked]
    joined = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"
    return f"{joined} roles only"


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

def work_mode_predicate(profile: dict, alias: str = "p") -> tuple[str, list[Any]]:
    """SQL fragment (plus params) restricting postings to a profile's chosen work setups.

    Two rules, and the second is the important one:

      * A profile that accepts all three modes gets no filter at all — the common case, and
        the state every existing subscriber was migrated into, so nobody's digest narrows
        without them choosing it.
      * **A null `work_mode` always passes.** Most postings never say what their arrangement
        is, so treating unknown as on-site would let one deselected checkbox delete most of
        the inventory. The preference still reaches the AI matcher through
        `describe_work_modes`, which reads the description and can judge what a pattern list
        cannot. Same division of labour as an unresolved city in `location_predicate`.

    So this gate is precise where the data is provable — `remote` is positively detected, so
    "fully remote only" genuinely filters — and defers where it is not.
    """
    modes = clean_work_modes(profile.get("work_modes"))
    if len(modes) == len(WORK_MODES):
        return "true", []
    return f"({alias}.work_mode is null or {alias}.work_mode = any(%s))", [modes]


def location_predicate(profile: dict, alias: str = "p") -> tuple[str, list[Any]]:
    """SQL fragment (plus params) restricting postings to a profile's chosen locations.

    The rule, in one place:

      * **Fully remote** postings are judged only by the remote scope — their city is
        irrelevant, which is the whole reason the two controls are separate.
      * **On-site or hybrid** postings must sit in a selected country, and — only for the
        countries where the subscriber actually named cities — in one of those cities.
        Naming no city for a country means "any city there". Hybrid belongs on this side
        precisely because it is *not* remote: two days a week in a Brno office is still a
        commute to Brno.
      * **Unknowns are kept.** A posting whose country or city we could not resolve passes
        the gate and reaches the AI matcher, which reads the raw location text. Dropping
        them would be a stricter promise than the data supports: `country_code` is null for
        whole sources, and a free-text city the subscriber typed is not in `CITIES` at all,
        so it can never match `postings.city` — for those the matcher is the only enforcer.
        Precision is what a miss costs; recall is never lost.

    `work_mode_predicate` is ANDed in here rather than left to the caller. It is a separate
    question — *how much office*, not *which city* — but every caller wants both, and this
    module's history is of filters that were silently not applied at one of two call sites.
    One composed predicate cannot be half-forgotten.

    Falls back to the coarse `region` gate when a profile has no `countries` — a row that
    predates the migration, or an API client that only sent `regions`.
    """
    mode_sql, mode_params = work_mode_predicate(profile, alias)

    def combine(place_sql: str, place_params: list[Any]) -> tuple[str, list[Any]]:
        if mode_sql == "true":
            return place_sql, place_params
        if place_sql == "true":
            return mode_sql, mode_params
        # Mode first, because psycopg2 binds %s by position in the SQL *text*.
        return f"({mode_sql} and {place_sql})", mode_params + place_params

    countries = clean_countries(profile.get("countries"))
    if not countries:
        regions = [str(r) for r in (profile.get("regions") or [])]
        return combine(f"{alias}.region = any(%s)", [regions]) if regions \
            else combine("true", [])

    cities = clean_cities(profile.get("cities"), countries)
    restricted = sorted({split_city(v)[0] for v in cities} - {None})
    remote = f"coalesce({alias}.remote_signal, false)"

    # `reach_countries` (migration 023) is ORed into the country test on both arms below. A
    # posting whose stored `country_code` is Spain but which lists Poland among its own locations
    # is a job in Poland, and `country_code` can only hold one of the two because `city` has to
    # agree with it. This widens the digest's candidate pool, deliberately: the countries in that
    # column are ones the board named outright, never anything read out of prose.
    #
    # **Two `%s`, so two params, and psycopg2 binds them by position in the SQL text** — which is
    # why `params` is built strictly in the order the fragments are concatenated below rather than
    # appended to wherever the reading is easiest.
    in_country = f"({alias}.country_code = any(%s) or {alias}.reach_countries && %s)"

    params: list[Any] = [countries, countries]
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
              f" or {in_country}) and ({city_gate}))")

    scope = clean_remote_scope(profile.get("remote_scope"))
    if scope == "worldwide":
        remote_gate = "true"
    elif scope == "eu":
        # EEA_COUNTRIES, not COUNTRIES: "Anywhere in the EU or EEA" must keep meaning the EEA
        # even though GB and US are now selectable. A US fully-remote role reaches a subscriber
        # only at `worldwide` scope; picking `eu` must never admit it. `reach_countries` is
        # tested against the same EEA set for the same reason — a role stored under the US that
        # names Germany among its locations is reachable from the EEA; one that names only
        # Canada is not.
        remote_gate = (f"({alias}.country_code = any(%s) or {alias}.reach_countries && %s"
                       f" or {alias}.region in ('cz','eu','worldwide'))")
        params += [list(EEA_COUNTRIES), list(EEA_COUNTRIES)]
    else:
        remote_gate = (f"({in_country}"
                       f" or ({alias}.country_code is null and {alias}.region = any(%s)))")
        params += [countries, countries, regions_for(countries, scope)]

    return combine(f"({onsite} or ({remote} and {remote_gate}))", params)
