/**
 * Countries and cities for the location picker — a mirror of `service/geo.py`.
 *
 * The browser cannot import the Python module, so this file restates its two data tables.
 * `service/tests/test_geo.py` fails if they drift, the same guard the role taxonomy has:
 * a city slug that exists here and not there is a preference the SQL filter can never apply.
 *
 * Slugs are what get stored (`profiles.cities`, as `"cz:prague"`), so they must stay stable.
 * Display names carry the diacritics; the alias table that resolves "Praha" or "München" in
 * a posting's location text lives only on the Python side — the browser never needs it.
 */

// ISO-2 -> display name. EU-27, the selectable set.
export const COUNTRIES: Record<string, string> = {
  "AT": "Austria",
  "BE": "Belgium",
  "BG": "Bulgaria",
  "HR": "Croatia",
  "CY": "Cyprus",
  "CZ": "Czechia",
  "DK": "Denmark",
  "EE": "Estonia",
  "FI": "Finland",
  "FR": "France",
  "DE": "Germany",
  "GR": "Greece",
  "HU": "Hungary",
  "IE": "Ireland",
  "IT": "Italy",
  "LV": "Latvia",
  "LT": "Lithuania",
  "LU": "Luxembourg",
  "MT": "Malta",
  "NL": "Netherlands",
  "PL": "Poland",
  "PT": "Portugal",
  "RO": "Romania",
  "SK": "Slovakia",
  "SI": "Slovenia",
  "ES": "Spain",
  "SE": "Sweden"
};

// Main cities per country, in the order the picker offers them: slug -> display name.
export const CITIES: Record<string, Record<string, string>> = {
  "AT": {
    "vienna": "Vienna",
    "graz": "Graz",
    "linz": "Linz",
    "salzburg": "Salzburg",
    "innsbruck": "Innsbruck",
    "klagenfurt": "Klagenfurt"
  },
  "BE": {
    "brussels": "Brussels",
    "antwerp": "Antwerp",
    "ghent": "Ghent",
    "leuven": "Leuven",
    "liege": "Liège",
    "bruges": "Bruges"
  },
  "BG": {
    "sofia": "Sofia",
    "plovdiv": "Plovdiv",
    "varna": "Varna",
    "burgas": "Burgas",
    "ruse": "Ruse"
  },
  "HR": {
    "zagreb": "Zagreb",
    "split": "Split",
    "rijeka": "Rijeka",
    "osijek": "Osijek",
    "zadar": "Zadar"
  },
  "CY": {
    "nicosia": "Nicosia",
    "limassol": "Limassol",
    "larnaca": "Larnaca",
    "paphos": "Paphos"
  },
  "CZ": {
    "prague": "Prague",
    "brno": "Brno",
    "ostrava": "Ostrava",
    "plzen": "Plzeň",
    "olomouc": "Olomouc",
    "liberec": "Liberec",
    "hradec-kralove": "Hradec Králové",
    "pardubice": "Pardubice",
    "ceske-budejovice": "České Budějovice",
    "zlin": "Zlín",
    "usti-nad-labem": "Ústí nad Labem",
    "jihlava": "Jihlava"
  },
  "DK": {
    "copenhagen": "Copenhagen",
    "aarhus": "Aarhus",
    "odense": "Odense",
    "aalborg": "Aalborg"
  },
  "EE": {
    "tallinn": "Tallinn",
    "tartu": "Tartu"
  },
  "FI": {
    "helsinki": "Helsinki",
    "espoo": "Espoo",
    "tampere": "Tampere",
    "turku": "Turku",
    "oulu": "Oulu",
    "vantaa": "Vantaa"
  },
  "FR": {
    "paris": "Paris",
    "lyon": "Lyon",
    "marseille": "Marseille",
    "toulouse": "Toulouse",
    "bordeaux": "Bordeaux",
    "lille": "Lille",
    "nantes": "Nantes",
    "nice": "Nice",
    "strasbourg": "Strasbourg",
    "rennes": "Rennes",
    "grenoble": "Grenoble",
    "montpellier": "Montpellier"
  },
  "DE": {
    "berlin": "Berlin",
    "munich": "Munich",
    "hamburg": "Hamburg",
    "frankfurt": "Frankfurt",
    "cologne": "Cologne",
    "stuttgart": "Stuttgart",
    "dusseldorf": "Düsseldorf",
    "leipzig": "Leipzig",
    "dresden": "Dresden",
    "hannover": "Hannover",
    "nuremberg": "Nuremberg",
    "karlsruhe": "Karlsruhe",
    "mannheim": "Mannheim",
    "essen": "Essen",
    "bremen": "Bremen"
  },
  "GR": {
    "athens": "Athens",
    "thessaloniki": "Thessaloniki",
    "patras": "Patras",
    "heraklion": "Heraklion"
  },
  "HU": {
    "budapest": "Budapest",
    "debrecen": "Debrecen",
    "szeged": "Szeged",
    "gyor": "Győr",
    "pecs": "Pécs"
  },
  "IE": {
    "dublin": "Dublin",
    "cork": "Cork",
    "galway": "Galway",
    "limerick": "Limerick"
  },
  "IT": {
    "milan": "Milan",
    "rome": "Rome",
    "turin": "Turin",
    "bologna": "Bologna",
    "florence": "Florence",
    "naples": "Naples",
    "verona": "Verona",
    "padua": "Padua",
    "genoa": "Genoa",
    "catania": "Catania"
  },
  "LV": {
    "riga": "Riga",
    "daugavpils": "Daugavpils"
  },
  "LT": {
    "vilnius": "Vilnius",
    "kaunas": "Kaunas",
    "klaipeda": "Klaipėda"
  },
  "LU": {
    "luxembourg-city": "Luxembourg City",
    "esch-sur-alzette": "Esch-sur-Alzette"
  },
  "MT": {
    "valletta": "Valletta",
    "sliema": "Sliema",
    "birkirkara": "Birkirkara"
  },
  "NL": {
    "amsterdam": "Amsterdam",
    "rotterdam": "Rotterdam",
    "the-hague": "The Hague",
    "utrecht": "Utrecht",
    "eindhoven": "Eindhoven",
    "groningen": "Groningen",
    "delft": "Delft",
    "tilburg": "Tilburg",
    "nijmegen": "Nijmegen"
  },
  "PL": {
    "warsaw": "Warsaw",
    "krakow": "Kraków",
    "wroclaw": "Wrocław",
    "poznan": "Poznań",
    "gdansk": "Gdańsk",
    "lodz": "Łódź",
    "katowice": "Katowice",
    "szczecin": "Szczecin",
    "lublin": "Lublin",
    "bydgoszcz": "Bydgoszcz",
    "rzeszow": "Rzeszów"
  },
  "PT": {
    "lisbon": "Lisbon",
    "porto": "Porto",
    "braga": "Braga",
    "coimbra": "Coimbra",
    "faro": "Faro",
    "aveiro": "Aveiro"
  },
  "RO": {
    "bucharest": "Bucharest",
    "cluj-napoca": "Cluj-Napoca",
    "timisoara": "Timișoara",
    "iasi": "Iași",
    "brasov": "Brașov",
    "sibiu": "Sibiu",
    "constanta": "Constanța"
  },
  "SK": {
    "bratislava": "Bratislava",
    "kosice": "Košice",
    "zilina": "Žilina",
    "nitra": "Nitra",
    "presov": "Prešov",
    "banska-bystrica": "Banská Bystrica",
    "trnava": "Trnava"
  },
  "SI": {
    "ljubljana": "Ljubljana",
    "maribor": "Maribor",
    "celje": "Celje"
  },
  "ES": {
    "madrid": "Madrid",
    "barcelona": "Barcelona",
    "valencia": "Valencia",
    "seville": "Seville",
    "malaga": "Málaga",
    "bilbao": "Bilbao",
    "zaragoza": "Zaragoza",
    "palma": "Palma",
    "alicante": "Alicante",
    "murcia": "Murcia"
  },
  "SE": {
    "stockholm": "Stockholm",
    "gothenburg": "Gothenburg",
    "malmo": "Malmö",
    "uppsala": "Uppsala",
    "linkoping": "Linköping",
    "lund": "Lund"
  }
};

export const REMOTE_SCOPES = ["country", "eu", "worldwide"] as const;
export type RemoteScope = (typeof REMOTE_SCOPES)[number];

// What each remote scope promises, in the words the form uses.
export const REMOTE_SCOPE_LABEL: Record<RemoteScope, string> = {
  country: "Only in the countries I picked",
  eu: "Anywhere in the EU",
  worldwide: "Anywhere in the world",
};

// How much of the job happens in an office. Mirrors `geo.WORK_MODES`, office-first — the
// drift test in service/tests/test_geo.py fails if the two lists stop agreeing.
export const WORK_MODES = ["onsite", "hybrid", "remote"] as const;
export type WorkMode = (typeof WORK_MODES)[number];

// Keys are quoted so this parses as JSON: that is what lets the drift test in
// service/tests/test_geo.py compare it against `geo.WORK_MODE_LABELS` directly.
export const WORK_MODE_LABEL: Record<WorkMode, string> = {
  "onsite": "On-site",
  "hybrid": "Hybrid",
  "remote": "Fully remote"
};

// The second line each option shows. "Hybrid" is the one people read wrong: it is not a
// weaker kind of remote, it is a job in that city that you do from home part of the week.
export const WORK_MODE_HINT: Record<WorkMode, string> = {
  onsite: "In the office",
  hybrid: "Part office, part home — you still need to be near it",
  remote: "No office at all",
};

/** Mirrors `geo.clean_work_modes`: unknown values dropped, empty means all three.
 *  Unticking everything is "no preference", never "nothing is acceptable" — reading it the
 *  other way would silently empty the digest. */
export function cleanWorkModes(values: readonly string[] | undefined | null): WorkMode[] {
  const wanted = new Set((values || []).map((v) => String(v).trim().toLowerCase()));
  const out = WORK_MODES.filter((m) => wanted.has(m));
  return out.length ? [...out] : [...WORK_MODES];
}

/** Flatten to ASCII the same way `geo.normalise` does — the two must agree, because a city
 *  typed here is compared against slugs resolved there. */
export function normalise(text: string): string {
  return (text || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")   // combining marks NFKD split off
    .replace(/[\u0141\u0142]/g, "l")   // L-stroke, which NFKD leaves intact
    .replace(/[\u00d8\u00f8]/g, "o")   // Nordic O-slash
    .replace(/\u0111/g, "d")            // d-stroke
    .replace(/\u00df/g, "ss")           // eszett
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** "Kolín" -> "kolin". Mirrors `geo.slugify_city`, so a town someone types in the browser
 *  produces the same stored value as one sent straight to the API. */
export function slugifyCity(text: string): string {
  return normalise(text).replace(/\s+/g, "-").slice(0, 40).replace(/^-+|-+$/g, "");
}

/** The stored form of a city preference. */
export function qualify(country: string, slug: string): string {
  return `${country.toLowerCase()}:${slug.toLowerCase()}`;
}

export function splitCity(value: string): { country: string; slug: string } | null {
  const i = (value || "").indexOf(":");
  if (i <= 0) return null;
  const country = value.slice(0, i).toUpperCase();
  const slug = value.slice(i + 1).toLowerCase();
  if (!country || !slug || !COUNTRIES[country]) return null;
  return { country, slug };
}

/** Display label for a city preference — curated cities keep their proper name, a typed one
 *  is title-cased back from its slug. */
export function cityLabel(country: string, slug: string): string {
  return CITIES[country]?.[slug] ??
    slug.split("-").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

/** Countries in alphabetical order by display name, for a <select>. */
export function countryOptions(): { code: string; name: string }[] {
  return Object.entries(COUNTRIES)
    .map(([code, name]) => ({ code, name }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** The cities picked for one country, in the curated order, typed ones last. */
export function citiesFor(country: string, picked: string[]): string[] {
  const slugs = picked
    .map((v) => splitCity(v))
    .filter((p): p is { country: string; slug: string } => !!p && p.country === country)
    .map((p) => p.slug);
  const curated = Object.keys(CITIES[country] || {});
  return [...curated.filter((s) => slugs.includes(s)), ...slugs.filter((s) => !curated.includes(s))];
}
