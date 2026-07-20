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
}

// A deliberately mixed set (product, marketing, data, design, engineering) so the preview
// reads as "jobs for everyone at tech companies", not just data/engineering roles.
export const JOBS: PreviewJob[] = [
  { score: 9, title: "Product Manager", co: "Notion", tags: ["EU", "Remote", "Perm"], skills: ["roadmapping", "sql", "figma"], sector: "SaaS", fl: false },
  { score: 8, title: "Marketing Lead", co: "Revolut", tags: ["EU", "Remote", "Perm"], skills: ["seo", "analytics", "content"], sector: "fintech", fl: false },
  { score: 7, title: "Data Analyst", co: "Mollie", tags: ["EU", "Remote", "Perm"], skills: ["sql", "looker", "excel"], sector: "fintech", fl: false },
  { score: 7, title: "Product Designer", co: "Pitch", tags: ["EU", "Remote", "Perm"], skills: ["figma", "research"], sector: "SaaS", fl: false },
  { score: 6, title: "Data Engineer", co: "Bitpanda", tags: ["EU", "Remote", "Perm"], skills: ["snowflake", "python", "dbt"], sector: "trading", fl: false },
  { score: 6, title: "Growth Marketer", co: "Productboard", tags: ["Remote", "Freelance"], skills: ["ads", "analytics"], sector: "SaaS", fl: true },
];

const CAP: Record<string, string> = {
  dbt: "dbt", sql: "SQL", snowflake: "Snowflake", python: "Python", airflow: "Airflow",
  looker: "Looker", django: "Django", pandas: "pandas", spark: "Spark", aws: "AWS",
  figma: "Figma", roadmapping: "Roadmapping", seo: "SEO", analytics: "Analytics",
  content: "Content", excel: "Excel", research: "Research", ads: "Ads", notion: "Notion",
};
export const cap = (s: string) => CAP[s.toLowerCase()] || s;

export function pickJobs(skills: Set<string>, work: Set<string>, limit: number): PreviewJob[] {
  let jobs = JOBS.slice();
  if (work.has("Freelance") && !work.has("Full-time") && !work.has("Part-time")) {
    jobs = jobs.filter((j) => j.fl);
  }
  const us = new Set([...skills].map((s) => s.toLowerCase()));
  const overlap = (j: PreviewJob) => j.skills.filter((s) => us.has(s.toLowerCase())).length;
  jobs.sort((a, b) => overlap(b) - overlap(a) || b.score - a.score);
  return jobs.slice(0, limit);
}
