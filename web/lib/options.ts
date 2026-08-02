/**
 * The vocabularies the signup wizard and /preferences offer, keyed by stable ids.
 *
 * Before the site spoke more than one language these lists *were* their labels: `roles` held
 * the string "Data Analyst", and `ROLE_CAT["Data Analyst"]` turned it into a category on
 * submit. Translating a label under that scheme silently breaks the payload — "Datový
 * analytik" misses the lookup, falls through the `role_category` path and lands in `stack` as
 * a keyword, so the subscriber gets a filter they never chose and nothing errors anywhere.
 * That is the exact failure mode CLAUDE.md's taxonomy rules are about, so the ids come first
 * and the label is only ever a rendering of them.
 *
 * The load-bearing rule: **what is stored never depends on the display language.** A Czech and
 * an English visitor who tap the same chip must produce byte-identical subscriptions. So
 * `keyword` below is the English word, not the rendered label, and it is what reaches
 * `profiles.stack` — the shortlist full-text query runs against posting text, which is not in
 * the visitor's UI language either.
 */

export interface RoleOption {
  id: string;
  /** `role_category` this chip maps to, or null when no category models it. */
  category: string | null;
  /** Search keyword used when there is no category — always English, never the rendered label. */
  keyword: string;
}

// Order is the order the chips are offered in. Mirrors service/taxonomy.py's categories; a
// chip with `category: null` is admitted as a keyword instead of being dropped, which is the
// rule that stops a role the taxonomy does not model from vanishing.
export const ROLE_OPTIONS: RoleOption[] = [
  { id: "product_manager", category: "product", keyword: "product manager" },
  { id: "marketing", category: "other_tech_function", keyword: "marketing" },
  { id: "social_media", category: "social_media", keyword: "social media" },
  { id: "data_analyst", category: "data_analysis", keyword: "data analyst" },
  { id: "designer", category: "design", keyword: "designer" },
  { id: "software_engineer", category: "software_engineering", keyword: "software engineer" },
  { id: "data_engineer", category: "data_engineering", keyword: "data engineer" },
  { id: "devops", category: "devops_platform", keyword: "devops" },
  { id: "finance", category: "other_tech_function", keyword: "finance" },
  // Offered only when a CV asks for it (never in the default chip row), and modelled by no
  // category — so it rides the keyword path, exactly as the old "ML Engineer" label did.
  { id: "ml_engineer", category: null, keyword: "ml engineer" },
];

export const DEFAULT_ROLE_IDS = ROLE_OPTIONS.slice(0, 9).map((r) => r.id);

const BY_ID = new Map(ROLE_OPTIONS.map((r) => [r.id, r]));

export const roleOption = (id: string): RoleOption | undefined => BY_ID.get(id);

/** `role_category` for a chip, or null for a typed one and for the modelled-by-nothing ids. */
export const roleCategory = (id: string): string | null => BY_ID.get(id)?.category ?? null;

/** What a category-less chip contributes to `profiles.stack`. A typed chip is its own text. */
export const roleKeyword = (id: string): string => BY_ID.get(id)?.keyword ?? id;

/** `cv_signals.role_categories` -> the chip to preselect. */
export const CV_ROLE_ID: Record<string, string> = {
  data_engineering: "data_engineer",
  data_analysis: "data_analyst",
  machine_learning: "ml_engineer",
  software_engineering: "software_engineer",
  devops_platform: "devops",
  product: "product_manager",
  design: "designer",
  social_media: "social_media",
};

/**
 * Stored `role_categories` -> a representative chip, for reopening /preferences on what the
 * filter is actually doing. `other_tech_function` is a bucket (Marketing and Finance both map
 * into it); we show Marketing and accept the lossiness, as the label-keyed version did.
 */
export const ROLE_ID_FOR_CATEGORY: Record<string, string> = {
  data_engineering: "data_engineer",
  data_analysis: "data_analyst",
  software_engineering: "software_engineer",
  devops_platform: "devops",
  product: "product_manager",
  design: "designer",
  social_media: "social_media",
  other_tech_function: "marketing",
};

/** A category no chip models — shown as a readable chip that round-trips back to the same
 *  keyword on save, rather than disappearing from the form. */
export const prettifyCategory = (slug: string): string =>
  slug.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());

// ---------------------------------------------------------------- employment type ----

export const WORK_TYPE_IDS = ["fulltime", "freelance", "parttime"] as const;
export type WorkTypeId = (typeof WORK_TYPE_IDS)[number];

// ------------------------------------------------------------------- seniority ------

// These *are* the stored codes (`profiles.seniorities`, `postings.seniority`), so there is no
// mapping step and nothing to keep in sync — the chip id is the value.
export const SENIORITY_IDS = ["junior", "mid", "senior"] as const;
export type SeniorityId = (typeof SENIORITY_IDS)[number];

// ------------------------------------------------------------------ skills ----------

// Not translated, and not id-keyed: a skill chip *is* its keyword. "SQL", "Figma" and "dbt"
// are the same word in every language this site speaks, and they are stored verbatim in
// `profiles.stack` to be matched against posting text.
export const SKILL_OPTS = [
  "SQL", "Figma", "Analytics", "Excel", "Python", "SEO", "Looker", "Roadmapping", "Power BI", "dbt",
];
