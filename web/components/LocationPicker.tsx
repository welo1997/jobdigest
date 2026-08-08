"use client";

/**
 * Where the subscriber can work: countries, the cities inside each, how much of the week is
 * spent in an office, and how far a *fully remote* role may be.
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
 *   - Work setup is a *third* axis, not a finer grade of the second. Hybrid is gated by city
 *     exactly like on-site work, because two days a week in Brno is still a commute to Brno;
 *     what the control decides is whether those roles are wanted at all.
 */

import { CSSProperties, useState } from "react";
import {
  CITIES, COUNTRIES, REMOTE_SCOPES, RemoteScope,
  WORK_MODES, WorkMode,
  citiesFor, cityLabel, cleanWorkModes, qualify, slugifyCity, splitCity,
} from "@/lib/geo";
import { fmt } from "@/i18n/config";
import { useI18n } from "@/i18n/context";

// Offered as one-tap chips: the markets that actually carry postings for this audience.
// Everything else is one <select> away, so this is a shortcut, not a limit.
const QUICK = ["CZ", "SK", "DE", "AT", "PL", "NL"];

// Inline rather than a class: this component renders inside both the signup wizard and the
// preferences page, which do not share a hint class.
const HINT: CSSProperties = {
  color: "var(--muted)", fontSize: "var(--fs-sm)", marginTop: 6,
};

export interface LocationValue {
  countries: string[];
  cities: string[];        // qualified, e.g. "cz:prague"
  remoteScope: RemoteScope;
  workModes: WorkMode[];   // all three — or none — = no preference
}

export function LocationPicker({
  value, onChange, idPrefix = "loc",
}: {
  value: LocationValue;
  onChange: (next: LocationValue) => void;
  idPrefix?: string;
}) {
  const { t, locale, country } = useI18n();
  const [typed, setTyped] = useState<Record<string, string>>({});
  const { countries, cities, remoteScope } = value;
  // Empty is preserved rather than widened to all three. Both mean "no preference" to every
  // reader (`cleanWorkModes` here, `geo.clean_work_modes` on the server), so this changes no
  // filter — it changes what the chips *look* like on a control nobody has touched. Normalising
  // first drew all three pressed, which is the signup wizard claiming a decision it had not
  // been given; /preferences is unaffected because it cleans the stored value before passing
  // it in, so a subscriber whose row says "all three" still sees all three pressed.
  const workModes = value.workModes.length ? cleanWorkModes(value.workModes) : [];
  const noModePreference = workModes.length === 0 || workModes.length === WORK_MODES.length;
  const remoteExcluded = workModes.length > 0 && !workModes.includes("remote");

  const toggleCountry = (code: string) => {
    if (countries.includes(code)) {
      // Dropping a country drops its cities too — a city preference for a country that is no
      // longer selected is invisible in the UI but would still narrow the filter server-side.
      onChange({
        ...value,
        countries: countries.filter((c) => c !== code),
        cities: cities.filter((v) => splitCity(v)?.country !== code),
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

  // Unticking the last one is not an error and must not be blocked: an empty selection means
  // "no preference" everywhere it is read (see `cleanWorkModes` and `geo.clean_work_modes`),
  // so it widens back to all three rather than matching nothing. It now *looks* empty when it
  // gets there instead of springing back to all-pressed, which is the same state said honestly
  // — the label beside it goes on reading "anything goes".
  const toggleWorkMode = (mode: WorkMode) =>
    onChange({
      ...value,
      workModes: workModes.includes(mode)
        ? workModes.filter((m) => m !== mode)
        : WORK_MODES.filter((m) => m === mode || workModes.includes(m)),
    });

  const addTypedCity = (code: string) => {
    const slug = slugifyCity(typed[code] || "");
    setTyped((t) => ({ ...t, [code]: "" }));
    if (!slug) return;
    const key = qualify(code, slug);
    if (!cities.includes(key)) onChange({ ...value, cities: [...cities, key] });
  };

  // Alphabetical *in the reader's language* — the English sort order puts Germany under G,
  // which is nowhere near where a Czech ("Německo") or Spanish ("Alemania") reader looks.
  const unselected = Object.keys(COUNTRIES)
    .filter((code) => !countries.includes(code))
    .map((code) => ({ code, name: country(code) }))
    .sort((a, b) => a.name.localeCompare(b.name, locale));

  return (
    <>
      <div className="field">
        <label>{t.location.countriesLabel}</label>
        <div className="chips">
          {[...new Set([...QUICK, ...countries])].map((code) => (
            <button key={code} type="button" className="chip"
              aria-pressed={countries.includes(code)} onClick={() => toggleCountry(code)}>
              {country(code)}
            </button>
          ))}
        </div>
        {unselected.length > 0 && (
          <div className="addwrap">
            <select aria-label={t.location.addCountryAria} value=""
              onChange={(e) => e.target.value && toggleCountry(e.target.value)}>
              <option value="">{t.location.addCountryOption}</option>
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
              {fmt(t.location.citiesIn, { country: country(code) })}
              {" — "}
              <span style={{ fontWeight: 400, color: "var(--muted)" }}>
                {picked.length === 0 ? t.location.anyCityNote : t.location.pickedCityNote}
              </span>
            </label>
            <div className="chips">
              <button type="button" className="chip" aria-pressed={picked.length === 0}
                onClick={() => clearCities(code)}>{t.location.anyCity}</button>
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
                placeholder={fmt(t.location.addTownPlaceholder, { country: country(code) })}
                aria-label={fmt(t.location.addCityAria, { country: country(code) })} />
              <button type="button" onClick={() => addTypedCity(code)}>{t.common.add}</button>
            </div>
          </div>
        );
      })}

      <div className="field">
        <label>
          {t.location.workSetup}
          {" — "}
          <span style={{ fontWeight: 400, color: "var(--muted)" }}>
            {noModePreference ? t.location.anythingGoes : t.location.leaveOutRest}
          </span>
        </label>
        <div className="chips">
          {WORK_MODES.map((m) => (
            <button key={m} type="button" className="chip"
              aria-pressed={workModes.includes(m)}
              title={t.geo.workModeHint[m]}
              onClick={() => toggleWorkMode(m)}>
              {t.geo.workModeLabel[m]}
            </button>
          ))}
        </div>
        <p style={HINT}>{t.location.hybridNote}</p>
      </div>

      <div className="field">
        <label htmlFor={`${idPrefix}-remote`}>{t.location.remoteLabel}</label>
        {/* Disabled only when remote was deliberately left out. The old test was the bare
            `!workModes.includes("remote")`, which is correct for every selection except the
            empty one — and empty is now the state this control opens in. An empty set contains
            no "remote" and yet admits fully remote roles, so that test would have greyed the
            question out on an untouched form and then gone on applying the answer behind it. */}
        <select id={`${idPrefix}-remote`} value={remoteScope}
          disabled={remoteExcluded}
          onChange={(e) => onChange({ ...value, remoteScope: e.target.value as RemoteScope })}>
          {REMOTE_SCOPES.map((s) => (
            <option key={s} value={s}>{t.geo.remoteScope[s]}</option>
          ))}
        </select>
        {remoteExcluded && (
          <p style={HINT}>{t.location.remoteDisabledNote}</p>
        )}
      </div>
    </>
  );
}
