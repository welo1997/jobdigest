"use client";

import { cap, pickJobs, PreviewJob } from "@/lib/preview";

interface Props {
  roles: Set<string>;
  skills: Set<string>;
  work: Set<string>;
  levels: Set<string>; // seniority codes (junior|mid|senior) the visitor picked
  email: string;
  limit?: number;
}

function reason(job: PreviewJob, skills: Set<string>) {
  const us = new Set([...skills].map((s) => s.toLowerCase()));
  const matched = job.skills.filter((s) => us.has(s.toLowerCase()));
  return matched.length ? (
    <>
      Matches{" "}
      {matched.map((m, i) => (
        <span key={m}>
          {i > 0 ? ", " : ""}
          <mark>{cap(m)}</mark>
        </span>
      ))}
      . In {job.sector}.
    </>
  ) : (
    <>
      Strong {job.sector} role in your region. In {job.sector}.
    </>
  );
}

export default function EmailPreview({ roles, skills, work, levels, email, limit = 3 }: Props) {
  // One role → name it ("3 new Product Manager roles"); several → stay generic ("3 new roles").
  const role = roles.size === 1 ? `${[...roles][0]} ` : "";
  const to = email || "you@example.com";
  const jobs = pickJobs(skills, work, levels, limit);
  const cnt = jobs.length;
  const dateStr = new Date().toLocaleDateString("en-GB", { day: "numeric", month: "short" });

  return (
    <div className="mail-frame">
      <div className="mail-chrome">
        <div className="tl">
          <i /><i /><i />
        </div>
        <div className="addr">inbox — {to}</div>
      </div>
      <div className="mail-meta">
        <div className="mail-from">
          <div className="avatar" />
          <div>
            <div className="n">JobDigest</div>
            <div className="e">hello@jobdigest.eu</div>
          </div>
          <div className="time">7:00 AM</div>
        </div>
        <div className="mail-subject">
          {cnt} new {role}{cnt === 1 ? "role" : "roles"} for you — {dateStr}
        </div>
      </div>
      <div className="mail-body">
        {roles.size === 0 ? (
          <div className="mail-empty">Pick a role to see your matches →</div>
        ) : cnt === 0 ? (
          <div className="mail-empty">No matches for this combo — widen your levels or work type →</div>
        ) : (
          <>
            <p className="greet">
              Good morning. <b>{cnt} fresh {cnt === 1 ? "match" : "matches"}</b> today, from 214
              postings scanned overnight.
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
                    {job.tags.map((t) => (
                      <span key={t} className={`tag${/free|contract/i.test(t) ? " fl" : ""}`}>
                        {t}
                      </span>
                    ))}
                  </div>
                  <div className="reason">{reason(job, skills)}</div>
                  <span className="apply" style={{ color: "var(--brand)" }}>
                    View &amp; apply →
                  </span>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
      <div className="mail-foot">
        <div className="actions">
          <span style={{ color: "var(--brand)" }}>Refine preferences</span> ·{" "}
          <span style={{ color: "var(--brand)" }}>Pause 2 weeks</span> ·{" "}
          <span style={{ color: "var(--brand)" }}>Unsubscribe</span>
        </div>
        <div className="fine">
          You&apos;re getting this because you signed up at jobdigest.eu · One email a day.
        </div>
      </div>
    </div>
  );
}
