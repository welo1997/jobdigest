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
  { id: "marketing", category: "marketing", keyword: "marketing" },
  { id: "social_media", category: "social_media", keyword: "social media" },
  { id: "data_analyst", category: "data_analysis", keyword: "data analyst" },
  { id: "designer", category: "design", keyword: "designer" },
  { id: "software_engineer", category: "software_engineering", keyword: "software engineer" },
  { id: "data_engineer", category: "data_engineering", keyword: "data engineer" },
  { id: "devops", category: "devops_platform", keyword: "devops" },
  { id: "finance", category: "finance_accounting", keyword: "finance" },
  // Added 2026-08-09 when the taxonomy stopped being tech-only. Appended deliberately:
  // DEFAULT_ROLE_IDS is ROLE_OPTIONS.slice(0, 9), so the default chip row is unchanged and
  // these are opt-in rather than a reshuffled signup form.
  { id: "sales", category: "sales", keyword: "sales" },
  { id: "hr", category: "hr_recruiting", keyword: "recruiter" },
  { id: "legal", category: "legal", keyword: "legal counsel" },
  { id: "customer_support", category: "customer_support", keyword: "customer support" },
  { id: "operations", category: "operations", keyword: "operations" },
  { id: "healthcare", category: "healthcare", keyword: "nurse" },
  { id: "education", category: "education", keyword: "teacher" },
  { id: "hospitality", category: "hospitality", keyword: "chef" },
  { id: "skilled_trades", category: "skilled_trades", keyword: "electrician" },
  { id: "construction", category: "construction", keyword: "construction" },
  { id: "logistics", category: "logistics_transport", keyword: "warehouse" },
  { id: "manufacturing", category: "manufacturing_production", keyword: "production operator" },
  // Offered only when a CV asks for it (never in the default chip row), and modelled by no
  // category — so it rides the keyword path, exactly as the old "ML Engineer" label did.
  { id: "ml_engineer", category: null, keyword: "ml engineer" },
];

export const DEFAULT_ROLE_IDS = ROLE_OPTIONS.slice(0, 9).map((r) => r.id);

// The landing wizard offers a short example row instead of the full nine — enough to show what
// belongs in the box, not a catalogue to read. Deliberately NOT a change to DEFAULT_ROLE_IDS:
// /preferences renders from that same constant, and trimming it there would take six role
// chips away from subscribers managing a live subscription, who are choosing rather than being
// introduced. One spread across three different categories (engineering, data, non-engineering)
// so the row reads as "any of this sort of thing" rather than "we are a data-jobs site".
export const EXAMPLE_ROLE_IDS = ["software_engineer", "data_analyst", "product_manager"];
export const EXAMPLE_SKILLS = ["SQL", "Python", "Figma"];

const BY_ID = new Map(ROLE_OPTIONS.map((r) => [r.id, r]));

export const roleOption = (id: string): RoleOption | undefined => BY_ID.get(id);

/**
 * Fold a typed role or skill down to something comparable: case, accents, punctuation and
 * runs of whitespace all removed. "Datový analytik", "datovy analytik" and "Data  Analyst"
 * must not be three different words, and `NFD` + stripping the combining range is what makes
 * the Czech, Slovak and Polish labels comparable without a per-language table.
 */
export const normalizeRoleText = (text: string): string =>
  text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[_\-./]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

/**
 * The role **id** a typed string names, or null when nothing models it.
 *
 * This exists because "Data Engineer" typed into the box is not a role the taxonomy fails to
 * model — it is `data_engineer`, spelled out. Adding the raw string instead of the id was
 * wrong twice over and silently: `SKILLS_BY_ROLE["Data Engineer"]` misses, so the role
 * suggested no skills, and `roleCategory("Data Engineer")` returns null, so it took the
 * keyword path and contributed **no `role_categories` entry at all** — a weaker filter than
 * the identical chip one tap away, with nothing to show anything had been downgraded.
 *
 * `labels` is the caller's `t.roles`, so the visitor's own language resolves: a Czech visitor
 * typing "Datový analytik" gets `data_analyst`, byte-identical to what an English visitor
 * tapping the chip stores. Passing the catalogue in rather than importing it keeps this
 * function pure and keeps `lib/` from depending on `i18n/` — and it is the same rule the rest
 * of this file is built on, that **what is stored never depends on the display language**.
 *
 * Three spellings are accepted, and the `keyword` one matters most: it is the English word
 * regardless of what the UI is showing, so "data engineer" resolves on the Polish site too.
 *
 * Returning null is a normal answer, not a failure — Sales, Cybersecurity and IT Support are
 * genuinely modelled by nothing, and the caller must go on routing those to `stack` as a
 * keyword. This narrows *what counts as* unmodelled; it does not change what happens to it.
 */
export function resolveRoleId(
  text: string,
  labels: Record<string, string> = {},
): string | null {
  const needle = normalizeRoleText(text);
  if (!needle) return null;
  for (const opt of ROLE_OPTIONS) {
    const spellings = [opt.id, opt.keyword, labels[opt.id] ?? ""];
    if (spellings.some((s) => s && normalizeRoleText(s) === needle)) return opt.id;
  }
  return null;
}

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

/**
 * What the skill column offers once a role is picked, keyed by **role chip id**.
 *
 * A fixed row of ten was a poor question: it showed Figma to a DevOps engineer and Kubernetes
 * to nobody, so the useful words were the ones a visitor had to type. Keying on the chip id
 * rather than on `category` is deliberate — several ids share a category (Marketing and Finance
 * are both `other_tech_function`) and their skills have nothing in common, so the coarser key
 * would suggest SEO to an accountant.
 *
 * Three rules these lists have to obey, and the first two are not style:
 *
 *   - **English, and no punctuation.** A chip *is* what gets stored in `profiles.stack`, and
 *     `store._shortlist_terms` feeds that straight to `plainto_tsquery('simple', …)` against
 *     posting text — which is not in the visitor's UI language either. "CI/CD" and "A/B
 *     testing" are deliberately absent: how that parser splits a slash is not something this
 *     list should be betting on.
 *   - **A word a posting would actually contain.** These are retrieval terms, not a
 *     self-description. "Stakeholder management" earns its place because ads write it;
 *     "attention to detail" would match half the corpus and steer nothing.
 *   - **Suggestions only.** Nothing here narrows anything by itself — an unticked chip is not a
 *     filter, and a role whose list is missing simply contributes none, the same
 *     hand-off a typed role gets. There is no closed vocabulary to fall foul of.
 */
export const SKILLS_BY_ROLE: Record<string, string[]> = {
  product_manager: ["Roadmapping", "Jira", "Analytics", "SQL", "Discovery", "Stakeholder management"],
  marketing: ["SEO", "Google Ads", "HubSpot", "Content marketing", "Analytics", "CRM"],
  social_media: ["Instagram", "TikTok", "Copywriting", "Canva", "Content creation", "Community management"],
  data_analyst: ["SQL", "Excel", "Power BI", "Tableau", "Python", "Looker"],
  designer: ["Figma", "UX research", "Prototyping", "Design systems", "Wireframing", "Adobe Illustrator"],
  software_engineer: ["Python", "TypeScript", "React", "Java", "PostgreSQL", "Docker"],
  data_engineer: ["SQL", "Python", "dbt", "Airflow", "Spark", "Snowflake"],
  devops: ["Kubernetes", "Docker", "Terraform", "AWS", "Linux", "Ansible"],
  finance: ["Excel", "Controlling", "SAP", "IFRS", "Forecasting", "Power BI"],
  ml_engineer: ["Python", "PyTorch", "TensorFlow", "MLOps", "NLP", "scikit-learn"],
};

// Three roles at six skills each is eighteen chips above an input box — a wall to read rather
// than a prompt. The cap is what keeps the column scannable; `suggestedSkills` spends it
// breadth-first so the cut falls on each role's least important word, never on a whole role.
export const SKILL_SUGGESTION_LIMIT = 12;

/**
 * The skill chips to offer for a set of picked roles.
 *
 * Round-robin, not concatenated: one skill from each role, then the second from each, until the
 * limit. Concatenating would spend the whole budget on whichever role happened to come first,
 * so a visitor who picked Designer *and* DevOps would see six Figma-shaped words and no
 * Kubernetes — the column would look like it had ignored half their answer.
 *
 * Ordered by `ROLE_OPTIONS`, not by the order the roles were tapped, so adding a third role
 * does not reshuffle the two rows already on screen.
 *
 * With no *known* role picked — nothing yet, or only typed ones the vocabulary does not
 * model — this falls back to the neutral example row rather than emptying the column. An empty
 * chip row next to a full one reads as a broken screen, and a typed role legitimately has
 * nothing to say here: guessing skills from free text is the closed-vocabulary mistake
 * `roleCategory` already refuses to make.
 */
export function suggestedSkills(roleIds: Iterable<string>): string[] {
  const picked = new Set(roleIds);
  const lists = ROLE_OPTIONS
    .filter((r) => picked.has(r.id) && SKILLS_BY_ROLE[r.id]?.length)
    .map((r) => SKILLS_BY_ROLE[r.id]);
  if (!lists.length) return [...EXAMPLE_SKILLS];

  const out: string[] = [];
  const seen = new Set<string>();
  const deepest = Math.max(...lists.map((l) => l.length));
  for (let i = 0; i < deepest && out.length < SKILL_SUGGESTION_LIMIT; i++) {
    for (const list of lists) {
      if (out.length >= SKILL_SUGGESTION_LIMIT) break;
      const skill = list[i];
      if (!skill || seen.has(skill.toLowerCase())) continue;
      seen.add(skill.toLowerCase());
      out.push(skill);
    }
  }
  return out;
}

/**
 * Merge chip sources into the row to render, first occurrence winning, compared case-insensitively.
 *
 * The order of the arguments is the guarantee: whatever the visitor typed, uploaded or ticked is
 * appended to the suggestions rather than replacing them, so **changing a role can never remove
 * a chip they had already chosen**. Untick a role and the skill you ticked because of it stays
 * on screen and stays pressed — it is in `stack` either way, and a chip that vanishes while
 * still being submitted is the silent-filter failure this file exists to prevent.
 */
export function mergeSkillOptions(...groups: Iterable<string>[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const group of groups) {
    for (const raw of group) {
      const skill = raw.trim();
      const key = skill.toLowerCase();
      if (!skill || seen.has(key)) continue;
      seen.add(key);
      out.push(skill);
    }
  }
  return out;
}
