/**
 * Which languages the site speaks, and the pure helpers around them.
 *
 * Locale lives in the URL (`/cs/preferences/`), never in a cookie or in `localStorage` alone:
 * a language a visitor cannot link to or share is a language search engines cannot index
 * either. `localStorage` only remembers the *choice* so the bare `/` redirector can honour it
 * on the next visit — it is never the source of truth for what is rendered.
 *
 * No dependency on React here on purpose, so the static `[locale]` layout, the client
 * components and the tests can all import it.
 */

export const LOCALES = ["en", "cs", "de", "sk", "pl", "es", "fr", "it"] as const;
export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = "en";

/** Endonyms — a language picker that names languages in English is useless to the person
 *  who needs it. Shown in the switcher exactly as a native speaker would write it. */
export const LOCALE_NAME: Record<Locale, string> = {
  en: "English",
  cs: "Čeština",
  de: "Deutsch",
  sk: "Slovenčina",
  pl: "Polski",
  es: "Español",
  fr: "Français",
  it: "Italiano",
};

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

/**
 * Languages the privacy policy and terms exist in.
 *
 * Deliberately a shorter list than `LOCALES`. Those two pages are a specification — the
 * privacy policy makes promises the code has to keep — so every extra copy is another
 * document that must change in the same commit as any change to what the product does with
 * data. Adding a language here is a commitment to maintain it, not a translation task.
 *
 * A reader whose language is not on the list gets the English text rather than a 404.
 */
export const LEGAL_LOCALES = ["en", "cs"] as const;
export type LegalLocale = (typeof LEGAL_LOCALES)[number];

export function isLegalLocale(value: unknown): value is LegalLocale {
  return typeof value === "string" && (LEGAL_LOCALES as readonly string[]).includes(value);
}

// Compile-time check that every legal locale is a locale the site actually exports — a typo
// here would otherwise produce links to a language that does not exist.
const _legalAreLocales: readonly Locale[] = LEGAL_LOCALES;
void _legalAreLocales;

/** The legal language to show a reader of `locale`, falling back to the first legal locale. */
export function legalLocale(locale: Locale): LegalLocale {
  // Not `DEFAULT_LOCALE`: that is typed as the whole `Locale` union, so it would compile only
  // by accident of English currently being in both lists. The fallback has to come from
  // LEGAL_LOCALES itself or it stops being guaranteed to exist.
  return isLegalLocale(locale) ? locale : LEGAL_LOCALES[0];
}

/** Link to a legal page from anywhere, in the nearest language that has one. */
export function legalHref(locale: Locale, page: "privacy" | "terms"): string {
  return localeHref(legalLocale(locale), `/${page}`);
}

/**
 * Prefix an app path with a locale. Pass a path that starts with "/" and carries no locale.
 *
 * `trailingSlash: true` in next.config.mjs means every exported route is a directory, so the
 * hrefs have to end in "/" too — otherwise every internal link eats a 301 on the way.
 * Query strings and fragments are kept after the slash.
 */
export function localeHref(locale: Locale, path = "/"): string {
  const [base, rest] = splitQuery(path.startsWith("/") ? path : `/${path}`);
  const clean = base.endsWith("/") ? base : `${base}/`;
  return `/${locale}${clean === "/" ? "/" : clean}${rest}`;
}

function splitQuery(path: string): [string, string] {
  const i = path.search(/[?#]/);
  return i < 0 ? [path, ""] : [path.slice(0, i), path.slice(i)];
}

/** The locale a pathname is under, or null for the unlocalised routes (/privacy, /terms). */
export function localeFromPath(pathname: string): Locale | null {
  const first = pathname.split("/").filter(Boolean)[0];
  return isLocale(first) ? first : null;
}

/** Same page, different language. Used by the switcher, which must not send anyone home. */
export function swapLocale(pathname: string, next: Locale): string {
  const segments = pathname.split("/").filter(Boolean);
  if (isLocale(segments[0])) segments.shift();
  return localeHref(next, `/${segments.join("/")}`);
}

/**
 * Best supported match for a browser's `navigator.languages`.
 *
 * Matches on the primary subtag, so `de-AT` finds `de` — narrowing on the region would send
 * an Austrian visitor to English, which is the one answer that is wrong for everyone.
 */
export function negotiate(preferred: readonly string[]): Locale {
  for (const tag of preferred) {
    const primary = tag.toLowerCase().split("-")[0];
    if (isLocale(primary)) return primary;
  }
  return DEFAULT_LOCALE;
}

/** `fmt("Step {n} of 4", { n: 2 })`. Unknown placeholders are left alone rather than blanked,
 *  so a typo in a catalogue shows up in the UI instead of silently deleting a number. */
export function fmt(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (whole, key: string) =>
    key in vars ? String(vars[key]) : whole
  );
}

/**
 * CLDR plural category for `n` in `locale`, via `Intl.PluralRules`.
 *
 * Hand-rolled `n === 1 ? a : b` is wrong in five of these eight languages — Czech, Slovak and
 * Polish all distinguish 2–4 from 5+ ("2 nabídky" vs "5 nabídek"). Catalogues therefore supply
 * a `PluralForms` object and this picks the key; a language that lacks a form falls back to
 * `other`, which every catalogue must define.
 */
export type PluralForms = Partial<Record<Intl.LDMLPluralRule, string>> & { other: string };

export function plural(locale: Locale, n: number, forms: PluralForms): string {
  const category = new Intl.PluralRules(locale).select(n);
  return forms[category] ?? forms.other;
}
