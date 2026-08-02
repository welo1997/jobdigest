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
  seniority: string; // junior | mid | senior — mirrors the real hard seniority filter
}

// A deliberately mixed set (product, marketing, data, design, engineering) so the preview
// reads as "jobs for everyone at tech companies", not just data/engineering roles. Seniority
// is spread across all three levels so toggling a level visibly changes the preview.
export const JOBS: PreviewJob[] = [
  { score: 9, title: "Product Manager", co: "Notion", tags: ["EU", "Remote", "Perm"], skills: ["roadmapping", "sql", "figma"], sector: "SaaS", fl: false, seniority: "mid" },
  { score: 8, title: "Marketing Lead", co: "Revolut", tags: ["EU", "Remote", "Perm"], skills: ["seo", "analytics", "content"], sector: "fintech", fl: false, seniority: "senior" },
  { score: 7, title: "Data Analyst", co: "Mollie", tags: ["EU", "Remote", "Perm"], skills: ["sql", "looker", "excel"], sector: "fintech", fl: false, seniority: "mid" },
  { score: 7, title: "Junior Product Designer", co: "Pitch", tags: ["EU", "Remote", "Perm"], skills: ["figma", "research"], sector: "SaaS", fl: false, seniority: "junior" },
  { score: 6, title: "Junior Data Engineer", co: "Bitpanda", tags: ["EU", "Remote", "Perm"], skills: ["snowflake", "python", "dbt"], sector: "trading", fl: false, seniority: "junior" },
  { score: 6, title: "Senior Growth Marketer", co: "Productboard", tags: ["Remote", "Freelance"], skills: ["ads", "analytics"], sector: "SaaS", fl: true, seniority: "senior" },
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
  // reflects what the subscriber would receive. `levels` holds codes (junior|mid|senior).
  if (levels.size) {
    jobs = jobs.filter((j) => levels.has(j.seniority));
  }
  const us = new Set([...skills].map((s) => s.toLowerCase()));
  const overlap = (j: PreviewJob) => j.skills.filter((s) => us.has(s.toLowerCase())).length;
  jobs.sort((a, b) => overlap(b) - overlap(a) || b.score - a.score);
  return jobs.slice(0, limit);
}
