// Sample postings + ranking for the live email preview on the landing page.
// Mirrors design/prototype.html so the preview matches the signed-off design. This is
// illustrative only — the real ranking runs server-side in service/digest.py.

export interface PreviewJob {
  score: number;
  title: string;
  co: string;
  tags: string[];
  skills: string[];
  sector: string;
  fl: boolean;
  seniority: string; // one of SENIORITY_IDS in lib/options.ts
}

// A deliberately mixed set (product, marketing, data, design, engineering) so the preview
// reads as "jobs for everyone at tech companies", not just data/engineering roles.
//
// **Every id in `SENIORITY_IDS` must appear at least once here.** `pickJobs` hard-filters on
// level, so a rung with no sample renders an empty preview the moment someone ticks it — and
// the empty state reads as "there are no jobs like that", on the landing page, in the middle
// of signup. The six rungs arrived on 2026-08-12; intern, entry_level and lead were added in
// the same commit for exactly this reason.
export const JOBS: PreviewJob[] = [
  { score: 9, title: "Product Manager", co: "Notion", tags: ["EU", "Remote", "Perm"], skills: ["roadmapping", "sql", "figma"], sector: "SaaS", fl: false, seniority: "mid" },
  { score: 8, title: "Head of Data", co: "Revolut", tags: ["EU", "Remote", "Perm"], skills: ["sql", "analytics", "dbt"], sector: "fintech", fl: false, seniority: "lead" },
  { score: 8, title: "Marketing Lead", co: "Productboard", tags: ["EU", "Remote", "Perm"], skills: ["seo", "analytics", "content"], sector: "SaaS", fl: false, seniority: "lead" },
  { score: 7, title: "Data Analyst", co: "Mollie", tags: ["EU", "Remote", "Perm"], skills: ["sql", "looker", "excel"], sector: "fintech", fl: false, seniority: "mid" },
  { score: 7, title: "Junior Product Designer", co: "Pitch", tags: ["EU", "Remote", "Perm"], skills: ["figma", "research"], sector: "SaaS", fl: false, seniority: "junior" },
  { score: 6, title: "Junior Data Engineer", co: "Bitpanda", tags: ["EU", "Remote", "Perm"], skills: ["snowflake", "python", "dbt"], sector: "trading", fl: false, seniority: "junior" },
  { score: 6, title: "Senior Growth Marketer", co: "Productboard", tags: ["Remote", "Freelance"], skills: ["ads", "analytics"], sector: "SaaS", fl: true, seniority: "senior" },
  { score: 6, title: "Senior Backend Engineer", co: "Mews", tags: ["EU", "Remote", "Perm"], skills: ["python", "aws", "sql"], sector: "SaaS", fl: false, seniority: "senior" },
  { score: 5, title: "Graduate Data Analyst", co: "Rohlik", tags: ["EU", "Perm"], skills: ["sql", "excel", "analytics"], sector: "ecommerce", fl: false, seniority: "entry_level" },
  { score: 5, title: "Werkstudent Analytics", co: "Zalando", tags: ["EU", "Part-time"], skills: ["sql", "excel"], sector: "ecommerce", fl: false, seniority: "intern" },
];

const CAP: Record<string, string> = {
  dbt: "dbt", sql: "SQL", snowflake: "Snowflake", python: "Python", airflow: "Airflow",
  looker: "Looker", django: "Django", pandas: "pandas", spark: "Spark", aws: "AWS",
  figma: "Figma", roadmapping: "Roadmapping", seo: "SEO", analytics: "Analytics",
  content: "Content", excel: "Excel", research: "Research", ads: "Ads", notion: "Notion",
  "power bi": "Power BI",
};
export const cap = (s: string) => CAP[s.toLowerCase()] || s;

/**
 * `work` and `levels` carry the stable ids from `lib/options.ts` (`freelance`, `junior`), not
 * display labels. They used to be the English labels, which meant the preview quietly stopped
 * filtering the moment a label was translated — the chips would visibly change and the list
 * below them would not.
 */
export function pickJobs(
  skills: Set<string>,
  work: Set<string>,
  levels: Set<string>,
  limit: number
): PreviewJob[] {
  let jobs = JOBS.slice();
  if (work.has("freelance") && !work.has("fulltime") && !work.has("parttime")) {
    jobs = jobs.filter((j) => j.fl);
  }
  // Seniority is a hard filter in the real matcher; mirror that here so the preview honestly
  // reflects what the subscriber would receive. `levels` holds `SENIORITY_IDS` codes.
  if (levels.size) {
    jobs = jobs.filter((j) => levels.has(j.seniority));
  }
  const us = new Set([...skills].map((s) => s.toLowerCase()));
  const overlap = (j: PreviewJob) => j.skills.filter((s) => us.has(s.toLowerCase())).length;
  jobs.sort((a, b) => overlap(b) - overlap(a) || b.score - a.score);
  return jobs.slice(0, limit);
}
