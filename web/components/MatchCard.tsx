"use client";

import { MatchJob } from "@/lib/api";
import { track } from "@/lib/analytics";
import { safeHref } from "@/lib/url";
import { useI18n } from "@/i18n/context";
import type { Messages } from "@/i18n/schema";

/**
 * One job card, shared by `/matches` and `/hidden` so a hidden job reads exactly as it did
 * before it was hidden — same score, same tags, same apply link. The only difference between
 * the two pages is which action the tick box performs.
 */

export function jobTags(j: MatchJob, t: Messages): { text: string; fl?: boolean }[] {
  const out: { text: string; fl?: boolean }[] = [];
  if (j.region) out.push({ text: j.region.toUpperCase() });
  // Hybrid wins over the coarse region-implied "Remote" — see `_tags` in service/digest.py.
  if (j.work_mode === "hybrid") out.push({ text: t.matches.tagHybrid });
  else if (j.work_mode === "remote" || j.region === "eu" || j.region === "worldwide") {
    out.push({ text: t.matches.tagRemote });
  }
  // Seniority is a stored code (`SENIORITY_IDS`), so it reads out of the same chip vocabulary
  // the preferences form uses rather than being title-cased in English — which is also what
  // keeps `entry_level` from rendering as "Entry_level". A posting whose title named no level
  // is NULL and gets no tag at all: an absence is not a fact about the job.
  if (j.seniority) out.push({ text: t.seniorities[j.seniority] ?? j.seniority });
  if (j.work_type === "freelance/contract") out.push({ text: t.matches.tagFreelance, fl: true });
  if (j.salary) out.push({ text: j.salary });
  // de-dup by lowercased text
  const seen = new Set<string>();
  return out.filter((tag) => {
    const k = tag.text.toLowerCase();
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

export function MatchCard(
  { j, token, selected, onToggle }: {
    j: MatchJob;
    token?: string;
    selected: boolean;
    onToggle: (id: string) => void;
  }
) {
  const { t, count } = useI18n();
  const score = j.score ?? 0;
  const strong = score >= 6;
  // Feed URLs are untrusted; a non-http(s) scheme (javascript:, data:) renders no link.
  const href = safeHref(j.url);
  return (
    <div className={`match${strong ? " top" : ""}${selected ? " picked" : ""}`}>
      {/* A real checkbox in a label, not a div with an onClick: it is what gives the card a
          keyboard-reachable, screen-reader-announced control for free. The label wraps only
          the box, so ticking one can never be confused with opening the job. */}
      <label className="pick">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggle(j.posting_id)}
          aria-label={`${t.matches.selectAria}: ${j.title || t.common.roleFallback}`}
        />
      </label>
      <div className="sc">
        <div className="n">{score}</div>
        <div className="d">/ 10</div>
        <div className="bar"><i style={{ width: `${score * 10}%` }} /></div>
      </div>
      <div className="bd">
        <h3>
          {j.title || t.common.roleFallback} <span>— {j.company || ""}</span>
          {score >= 8 && <span className="emailed">{t.matches.topMatch}</span>}
        </h3>
        <div className="tags">
          {jobTags(j, t).map((tag, i) => (
            <span key={i} className={`tag${tag.fl ? " fl" : ""}`}>{tag.text}</span>
          ))}
          {/* Rendered here rather than in jobTags because it pluralises: "{n}+ years" needs
              the locale's plural rules (count), which jobTags' (job, messages) signature does
              not carry. Absent when the ad stated nothing — an absence is not a fact. */}
          {j.experience_min != null && (
            <span className="tag">{count(j.experience_min, t.matches.tagExperience)}</span>
          )}
        </div>
        {/* Skill chips: canonical tool names (data, not translated copy), read-only. Capped so
            a keyword-stuffed ad cannot blow out the card. */}
        {j.skills && j.skills.length > 0 && (
          <div className="skills">
            {j.skills.slice(0, 10).map((s) => (
              <span key={s} className="skill">{s}</span>
            ))}
          </div>
        )}
        {j.summary && <div className="why">{j.summary}</div>}
        {href && (
          <a
            className="apply"
            href={href}
            target="_blank"
            // `noopener` only. `noreferrer` was also stripping the Referer header, which is
            // how a source sees that we sent the visit — Remote OK's API terms ask for a
            // followed link and the traffic back, and several boards treat referred traffic as
            // the whole reason to tolerate an aggregator. Nothing sensitive leaks: the token is
            // removed from the URL once it is traded for a session, so the referrer is the bare
            // /matches path. `noopener` still blocks the reverse-tabnabbing hole, which is the
            // part that was ever a security property.
            rel="noopener"
            // Score only — never the job, company or URL. Tells us whether the ranking is
            // trusted (are low-scored matches ever clicked?) without profiling anyone.
            onClick={() => track("match_clicked", { count: score }, token || undefined)}
          >
            {t.common.viewAndApply}
          </a>
        )}
      </div>
    </div>
  );
}

/**
 * The bar that appears once something is ticked. Fixed to the bottom of the viewport, because
 * a selection made at the top of a 25-row list and confirmed at the bottom is a selection
 * whose button must not have scrolled away.
 */
export function SelectionBar(
  { n, busy, label, busyLabel, onConfirm, onClear, clearLabel, countLabel }: {
    n: number;
    busy: boolean;
    label: string;
    busyLabel: string;
    countLabel: string;
    clearLabel: string;
    onConfirm: () => void;
    onClear: () => void;
  }
) {
  if (n === 0) return null;
  return (
    <div className="selbar" role="region" aria-live="polite">
      <span className="n">{countLabel}</span>
      <button type="button" className="btn" onClick={onConfirm} disabled={busy}>
        {busy ? busyLabel : label}
      </button>
      <button type="button" className="lnk" onClick={onClear} disabled={busy}>
        {clearLabel}
      </button>
    </div>
  );
}
