"use client";

/**
 * Where the subscriber can work: countries, the cities inside each, and how far a *fully
 * remote* role may be.
 *
 * Shared by the signup wizard and the preferences page on purpose. The old three-option
 * "Where" control was copy-pasted into `app/page.tsx`, `app/v2/page.tsx` and
 * `app/preferences/page.tsx`, and they had already drifted (one offered a "Hybrid Prague"
 * chip that mapped to plain `["cz"]` and therefore did nothing). One component, three
 * callers.
 *
 * Two ideas the UI has to get across:
 *   - Naming no city for a country means "any city there". That is why every country shows
 *     an "Any city" chip that is pressed until a specific city is picked — an empty city row
 *     would otherwise read as "nothing selected, so nothing matches".
 *   - Remote is a separate axis. "On-site only in Prague, but remote from anywhere in the
 *     EU" is the common case, and a single control cannot say it.
 */

import { useState } from "react";
import {
  CITIES, COUNTRIES, REMOTE_SCOPES, REMOTE_SCOPE_LABEL, RemoteScope,
  citiesFor, cityLabel, countryOptions, qualify, slugifyCity, splitCity,
} from "@/lib/geo";

// Offered as one-tap chips: the markets that actually carry postings for this audience.
// Everything else is one <select> away, so this is a shortcut, not a limit.
const QUICK = ["CZ", "SK", "DE", "AT", "PL", "NL"];

export interface LocationValue {
  countries: string[];
  cities: string[];        // qualified, e.g. "cz:prague"
  remoteScope: RemoteScope;
}

export function LocationPicker({
  value, onChange, idPrefix = "loc",
}: {
  value: LocationValue;
  onChange: (next: LocationValue) => void;
  idPrefix?: string;
}) {
  const [typed, setTyped] = useState<Record<string, string>>({});
  const { countries, cities, remoteScope } = value;

  const toggleCountry = (code: string) => {
    if (countries.includes(code)) {
      // Dropping a country drops its cities too — a city preference for a country that is no
      // longer selected is invisible in the UI but would still narrow the filter server-side.
      onChange({
        countries: countries.filter((c) => c !== code),
        cities: cities.filter((v) => splitCity(v)?.country !== code),
        remoteScope,
      });
    } else {
      onChange({ ...value, countries: [...countries, code] });
    }
  };

  const toggleCity = (code: string, slug: string) => {
    const key = qualify(code, slug);
    onChange({
      ...value,
      cities: cities.includes(key) ? cities.filter((v) => v !== key) : [...cities, key],
    });
  };

  const clearCities = (code: string) =>
    onChange({ ...value, cities: cities.filter((v) => splitCity(v)?.country !== code) });

  const addTypedCity = (code: string) => {
    const slug = slugifyCity(typed[code] || "");
    setTyped((t) => ({ ...t, [code]: "" }));
    if (!slug) return;
    const key = qualify(code, slug);
    if (!cities.includes(key)) onChange({ ...value, cities: [...cities, key] });
  };

  const unselected = countryOptions().filter((c) => !countries.includes(c.code));

  return (
    <>
      <div className="field">
        <label>Countries you can work in</label>
        <div className="chips">
          {[...new Set([...QUICK, ...countries])].map((code) => (
            <button key={code} type="button" className="chip"
              aria-pressed={countries.includes(code)} onClick={() => toggleCountry(code)}>
              {COUNTRIES[code] || code}
            </button>
          ))}
        </div>
        {unselected.length > 0 && (
          <div className="addwrap">
            <select aria-label="Add another country" value=""
              onChange={(e) => e.target.value && toggleCountry(e.target.value)}>
              <option value="">Add another country…</option>
              {unselected.map((c) => (
                <option key={c.code} value={c.code}>{c.name}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      {countries.map((code) => {
        const picked = citiesFor(code, cities);
        const curated = Object.keys(CITIES[code] || {});
        const shown = [...new Set([...curated, ...picked])];
        return (
          <div className="field" key={code}>
            <label htmlFor={`${idPrefix}-city-${code}`}>
              Cities in {COUNTRIES[code] || code}
              {" — "}
              <span style={{ fontWeight: 400, color: "var(--muted)" }}>
                {picked.length === 0
                  ? "any city (on-site roles anywhere in the country)"
                  : "on-site roles only in the cities you pick"}
              </span>
            </label>
            <div className="chips">
              <button type="button" className="chip" aria-pressed={picked.length === 0}
                onClick={() => clearCities(code)}>Any city</button>
              {shown.map((slug) => (
                <button key={slug} type="button" className="chip"
                  aria-pressed={picked.includes(slug)}
                  onClick={() => toggleCity(code, slug)}>
                  {cityLabel(code, slug)}
                </button>
              ))}
            </div>
            <div className="addwrap">
              <input id={`${idPrefix}-city-${code}`} type="text"
                value={typed[code] || ""}
                onChange={(e) => setTyped((t) => ({ ...t, [code]: e.target.value }))}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); addTypedCity(code); }
                }}
                placeholder={`Add another town in ${COUNTRIES[code] || code}…`}
                aria-label={`Add a city in ${COUNTRIES[code] || code}`} />
              <button type="button" onClick={() => addTypedCity(code)}>Add</button>
            </div>
          </div>
        );
      })}

      <div className="field">
        <label htmlFor={`${idPrefix}-remote`}>Fully remote roles — how far afield?</label>
        <select id={`${idPrefix}-remote`} value={remoteScope}
          onChange={(e) => onChange({ ...value, remoteScope: e.target.value as RemoteScope })}>
          {REMOTE_SCOPES.map((s) => (
            <option key={s} value={s}>{REMOTE_SCOPE_LABEL[s]}</option>
          ))}
        </select>
      </div>
    </>
  );
}

/** One-line summary of a selection, for a review step or a collapsed row. */
export function describeLocation(v: LocationValue): string {
  if (v.countries.length === 0) return "—";
  const parts = v.countries.map((code) => {
    const picked = citiesFor(code, v.cities);
    const name = COUNTRIES[code] || code;
    return picked.length
      ? `${name} (${picked.map((s) => cityLabel(code, s)).join(", ")})`
      : name;
  });
  const remote = v.remoteScope === "worldwide" ? "remote worldwide"
    : v.remoteScope === "eu" ? "remote in the EU"
      : "remote in those countries";
  return `${parts.join(", ")} · ${remote}`;
}
