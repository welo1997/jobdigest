"use client";

import { useEffect, useState } from "react";
import { cap, pickJobs, PreviewJob } from "@/lib/preview";
import { fmt } from "@/i18n/config";
import { rich, useI18n } from "@/i18n/context";

interface Props {
  /** Role *ids* (see lib/options.ts), not labels — the label is looked up per language here. */
  roles: Set<string>;
  skills: Set<string>;
  /** Employment-type ids: fulltime | freelance | parttime. */
  work: Set<string>;
  /** Seniority codes the visitor picked — the `SENIORITY_IDS` in lib/options.ts. */
  levels: Set<string>;
  email: string;
  limit?: number;
}

export default function EmailPreview({ roles, skills, work, levels, email, limit = 3 }: Props) {
  const { t, locale, count } = useI18n();

  const reason = (job: PreviewJob) => {
    const us = new Set([...skills].map((s) => s.toLowerCase()));
    const matched = job.skills.filter((s) => us.has(s.toLowerCase()));
    if (!matched.length) return fmt(t.preview.reasonGeneric, { sector: job.sector });
    const marks = matched.map((m, i) => (
      <span key={m}>
        {i > 0 ? ", " : ""}
        <mark>{cap(m)}</mark>
      </span>
    ));
    return rich(fmt(t.preview.reasonMatches, { sector: job.sector }), [marks]);
  };

  const to = email || t.landing.emailPlaceholder;
  const jobs = pickJobs(skills, work, levels, limit);
  const cnt = jobs.length;
  // Today's date, but computed only *after* mount. In the static export this line also runs at
  // build time, baking the build-day date into the prerendered HTML; the client then renders the
  // real "today", and the text mismatch is React hydration error #418 (thrown away and re-rendered
  // on every visit). Deferring it to an effect makes the server and first client render agree —
  // no date yet — then fills in the real one. Locale-formatted, so a German reader sees "1. Aug.".
  const [dateStr, setDateStr] = useState("");
  useEffect(() => {
    setDateStr(new Date().toLocaleDateString(locale, { day: "numeric", month: "short" }));
  }, [locale]);

  // One role → name it ("3 new Product Manager roles"); several → stay generic. The named form
  // is its own plural set rather than a hole punched into the generic one, because several of
  // these languages do not put the role name where English does.
  const only = roles.size === 1 ? [...roles][0] : null;
  const headline = only
    ? count(cnt, t.preview.roleCountNamed, { role: t.roles[only] ?? only })
    : count(cnt, t.preview.roleCount);

  // On the pre-mount frame `dateStr` is still empty; trim the dangling separator so the subject
  // reads "3 new roles for you" rather than "… for you —" until the effect fills the date in.
  const subject = dateStr
    ? fmt(t.preview.subject, { count: headline, date: dateStr })
    : fmt(t.preview.subject, { count: headline, date: "" }).replace(/[\s—–-]+$/, "");

  return (
    <div className="mail-frame">
      <div className="mail-chrome">
        <div className="tl">
          <i /><i /><i />
        </div>
        <div className="addr">{fmt(t.preview.inbox, { email: to })}</div>
      </div>
      <div className="mail-meta">
        <div className="mail-from">
          <div className="avatar" />
          <div>
            <div className="n">JobDigest</div>
            <div className="e">hello@jobdigest.eu</div>
          </div>
          <div className="time">{t.preview.time}</div>
        </div>
        <div className="mail-subject">{subject}</div>
      </div>
      <div className="mail-body">
        {roles.size === 0 ? (
          <div className="mail-empty">{t.preview.pickRole}</div>
        ) : cnt === 0 ? (
          <div className="mail-empty">{t.preview.noCombo}</div>
        ) : (
          <>
            <p className="greet">
              {rich(t.preview.greeting, [<b key="n">{count(cnt, t.preview.freshMatches)}</b>])}
            </p>
            {jobs.map((job) => (
              <div className="job" key={job.title + job.co}>
                <div className="score">
                  <div className="n">{job.score}</div>
                  <div className="d">/ 10</div>
                  <div className="bar">
                    <i style={{ width: `${job.score * 10}%` }} />
                  </div>
                </div>
                <div>
                  <div className="ttl">
                    {job.title} <span className="co">— {job.co}</span>
                  </div>
                  <div className="tags">
                    {job.tags.map((t2) => (
                      <span key={t2} className={`tag${/free|contract/i.test(t2) ? " fl" : ""}`}>
                        {t2}
                      </span>
                    ))}
                  </div>
                  <div className="reason">{reason(job)}</div>
                  <span className="apply" style={{ color: "var(--brand)" }}>
                    {t.common.viewAndApply}
                  </span>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
      <div className="mail-foot">
        <div className="actions">
          <span style={{ color: "var(--brand)" }}>{t.preview.refine}</span> ·{" "}
          <span style={{ color: "var(--brand)" }}>{t.preview.pause}</span> ·{" "}
          <span style={{ color: "var(--brand)" }}>{t.preview.unsubscribe}</span>
        </div>
        <div className="fine">{t.preview.fine}</div>
      </div>
    </div>
  );
}
