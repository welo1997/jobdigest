"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";
import { PreviewJobCard, PreviewResponse } from "@/lib/api";
import { safeHref } from "@/lib/url";
import { rich, useI18n } from "@/i18n/context";

function PreviewRow({ j }: { j: PreviewJobCard }) {
  const { t } = useI18n();
  // Feed URLs are untrusted; a non-http(s) scheme renders no link (same rule as /matches).
  const href = safeHref(j.url);
  return (
    <div className="match">
      <div className="bd">
        <h3>{j.title || t.common.roleFallback} <span>— {j.company || ""}</span></h3>
        <div className="tags">
          {j.tags.map((tag, i) => (
            <span key={i} className={`tag${/free|contract/i.test(tag) ? " fl" : ""}`}>{tag}</span>
          ))}
        </div>
        {/* Skill chips: canonical tool names (data, not translated copy), read-only. */}
        {j.skills && j.skills.length > 0 && (
          <div className="skills">
            {j.skills.slice(0, 10).map((s) => (
              <span key={s} className="skill">{s}</span>
            ))}
          </div>
        )}
        {j.why && <div className="why">{j.why}</div>}
        {href && (
          <a className="apply" href={href} target="_blank" rel="noopener noreferrer">
            {t.common.viewAndApply}
          </a>
        )}
      </div>
    </div>
  );
}

function Inner() {
  const { t, href } = useI18n();
  const email = useSearchParams().get("email") || t.checkInbox.yourInbox;
  const [jobs, setJobs] = useState<PreviewJobCard[]>([]);

  // Read the instant preview stashed by the signup form. One-shot: removed after reading so a
  // refresh or a later visit doesn't show a stale list. Runs post-hydration (no SSR mismatch).
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem("jd_preview");
      if (raw) {
        setJobs((JSON.parse(raw) as PreviewResponse).jobs || []);
        sessionStorage.removeItem("jd_preview");
      }
    } catch {
      /* ignore — the confirmation flow does not depend on the preview */
    }
  }, []);

  const hasJobs = jobs.length > 0;

  return (
    <>
      <div className="state-wrap">
        <div className="state-card">
          <div className="ic">✉</div>
          <h1>{t.checkInbox.title}</h1>
          <p>
            {rich(t.checkInbox.body, [<b key="e">{email}</b>])}
            {hasJobs && t.checkInbox.inMeantime}
          </p>
          <p className="label" style={{ marginTop: 14 }}>{t.checkInbox.doubleOptIn}</p>
          {!hasJobs && (
            <p style={{ marginTop: 18 }}>
              <Link href={href("/")}>{t.common.backToHome}</Link>
            </p>
          )}
        </div>
      </div>

      {hasJobs && (
        <>
          <div className="wrap" style={{ maxWidth: 720, margin: "0 auto", textAlign: "center" }}>
            <span className="label">{t.checkInbox.instantPreview}</span>
            <p style={{ fontSize: "var(--fs-sm)", color: "var(--muted)", marginTop: 6 }}>
              {rich(t.checkInbox.instantNote, [
                <b key="ai">{t.checkInbox.instantNoteEmphasis}</b>,
              ])}
            </p>
          </div>
          <div className="matches">
            {jobs.map((j) => <PreviewRow key={j.posting_id} j={j} />)}
          </div>
          <div className="wrap" style={{ textAlign: "center", marginBottom: 60 }}>
            <Link href={href("/")}>{t.common.backToHome}</Link>
          </div>
        </>
      )}
    </>
  );
}

export default function CheckInbox() {
  return (
    <>
      <Nav />
      <main>
        <Suspense fallback={<Loading />}>
          <Inner />
        </Suspense>
      </main>
      <Footer />
    </>
  );
}

function Loading() {
  const { t } = useI18n();
  return <div className="state-wrap"><div className="state-card"><h1>{t.checkInbox.title}</h1></div></div>;
}
