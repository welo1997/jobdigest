/**
 * The language a posting is written in, mirroring `service/language.py`.
 *
 * The browser cannot import Python, so this is a hand-kept copy and
 * `service/tests/test_language.py` fails if `LANGUAGE_IDS` / `LANGUAGE_LABEL` stop agreeing with
 * `language.LANGUAGES` / `language.LANGUAGE_LABELS` — the same arrangement as `lib/education.ts`.
 * Drift is not cosmetic: these codes are what the form POSTs, and a code the API does not
 * recognise is rejected by `_check_understood_languages`, so a typo is a page that cannot save.
 *
 * **What this filter does, so the copy does not overpromise.** It drops a posting only when its
 * text was *confidently* detected in a language the subscriber did not tick. English always
 * passes (it is the bulk of inventory and says nothing about what a job requires), and a posting
 * whose language could not be read — most short/title-only ads — always passes too. So it removes
 * the genuinely-unreadable ads (a Russian-only description for someone who does not read Russian)
 * and nothing else.
 */

// Mirrors `language.LANGUAGES`, same canonical order.
export const LANGUAGE_IDS = [
  "en", "cs", "sk", "de", "pl", "fr", "es", "it", "pt", "nl",
  "sv", "no", "da", "fi", "ru", "uk", "ro", "hu", "bg", "el",
  "hr", "sl", "sr", "et", "lv", "lt", "tr",
] as const;
export type LanguageId = (typeof LANGUAGE_IDS)[number];

// English names — keys quoted so this parses as JSON, which is what lets the drift test compare
// it against `language.LANGUAGE_LABELS` directly. Mirrors that dict exactly.
export const LANGUAGE_LABEL: Record<LanguageId, string> = {
  "en": "English", "cs": "Czech", "sk": "Slovak", "de": "German", "pl": "Polish",
  "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese", "nl": "Dutch",
  "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish", "ru": "Russian",
  "uk": "Ukrainian", "ro": "Romanian", "hu": "Hungarian", "bg": "Bulgarian", "el": "Greek",
  "hr": "Croatian", "sl": "Slovenian", "sr": "Serbian", "et": "Estonian", "lv": "Latvian",
  "lt": "Lithuanian", "tr": "Turkish"
};

// Endonyms — each language in its own name — used to render the chips, so the picker reads the
// same in every UI locale and needs no per-locale translation of 27 names. Display only; the
// stored value is always the `LanguageId` code.
export const LANGUAGE_ENDONYM: Record<LanguageId, string> = {
  "en": "English", "cs": "Čeština", "sk": "Slovenčina", "de": "Deutsch", "pl": "Polski",
  "fr": "Français", "es": "Español", "it": "Italiano", "pt": "Português", "nl": "Nederlands",
  "sv": "Svenska", "no": "Norsk", "da": "Dansk", "fi": "Suomi", "ru": "Русский",
  "uk": "Українська", "ro": "Română", "hu": "Magyar", "bg": "Български", "el": "Ελληνικά",
  "hr": "Hrvatski", "sl": "Slovenščina", "sr": "Српски", "et": "Eesti", "lv": "Latviešu",
  "lt": "Lietuvių", "tr": "Türkçe"
};

/** Mirrors `language.clean_languages`: unknown values dropped, order canonical, empty stays
 *  empty (no preference = no filter — unlike education, there is no "everything" member). */
export function cleanUnderstoodLanguages(
  values: readonly string[] | undefined | null,
): LanguageId[] {
  const wanted = new Set((values || []).map((v) => String(v).trim().toLowerCase()));
  return LANGUAGE_IDS.filter((c) => wanted.has(c)) as LanguageId[];
}
