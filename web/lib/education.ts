/**
 * Education requirement levels, mirroring `service/education.py`.
 *
 * The browser cannot import Python, so this is a hand-kept copy and
 * `service/tests/test_education.py` fails if the two stop agreeing — the same arrangement as
 * `lib/geo.ts`. Drift here is not cosmetic: these values are what the form POSTs, and a level
 * the API does not recognise is rejected outright by `_check_education_levels`, so a typo is a
 * preferences page that cannot be saved.
 *
 * **What this filter can actually do, so the copy does not overpromise.** Measured against
 * production on 2026-08-02: only ~3.2% of active postings state a binding education
 * requirement, because ~70% of the corpus carries no description to read at all — the Czech and
 * Slovak sources (jobs.cz, Profesia, Cocuma; together about two thirds of inventory) ship an
 * empty description or a 30-character scrap. A posting whose requirement is unknown is always
 * shown. So this narrows a real but small slice of mostly English-language postings, and the
 * hint string under the chips says exactly that rather than implying a hard guarantee.
 */

// Ordered lowest-first — the order the checkboxes render in. Mirrors `education.LEVELS`.
export const EDUCATION_LEVELS = [
  "secondary",
  "vocational",
  "bachelor",
  "master",
  "doctorate",
] as const;
export type EducationLevel = (typeof EDUCATION_LEVELS)[number];

// Keys are quoted so this parses as JSON: that is what lets the drift test compare it against
// `education.LEVEL_LABELS` directly.
export const EDUCATION_LABEL: Record<EducationLevel, string> = {
  "secondary": "High school",
  "vocational": "Vocational / apprenticeship",
  "bachelor": "Bachelor's",
  "master": "Master's",
  "doctorate": "Doctorate / PhD"
};

/** Max length of the free-text field of study. Mirrors `education.MAX_FIELD_LEN`. */
export const MAX_FIELD_LEN = 120;

/** Mirrors `education.clean_levels`: unknown values dropped, empty means all five.
 *  Unticking everything is "no preference", never "nothing is acceptable" — reading it the
 *  other way would silently empty the digest. */
export function cleanEducationLevels(
  values: readonly string[] | undefined | null,
): EducationLevel[] {
  const wanted = new Set((values || []).map((v) => String(v).trim().toLowerCase()));
  const out = EDUCATION_LEVELS.filter((l) => wanted.has(l));
  return out.length ? [...out] : [...EDUCATION_LEVELS];
}

/** Mirrors `education.clean_field`: collapse whitespace, cap length, empty becomes null. */
export function cleanEducationField(value: string | undefined | null): string {
  return (value || "").split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_FIELD_LEN);
}

/**
 * Every level up to and including `highest` — what somebody who holds that qualification can
 * apply for. Used only to pre-tick the boxes after a CV upload, never to save silently:
 * `cvparse.merge_into_profile` deliberately does not set `education_levels` server-side,
 * because narrowing a digest from a regex over a CV is a filter nobody chose.
 */
export function levelsUpTo(highest: string | null | undefined): EducationLevel[] {
  const idx = EDUCATION_LEVELS.indexOf(String(highest || "") as EducationLevel);
  return idx < 0 ? [...EDUCATION_LEVELS] : EDUCATION_LEVELS.slice(0, idx + 1);
}
