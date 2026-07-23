"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";
import { PreviewJobCard, PreviewResponse } from "@/lib/api";
import { safeHref } from "@/lib/url";

function PreviewRow({ j }: { j: PreviewJobCard }) {
  // Feed URLs are untrusted; a non-http(s) scheme renders no link (same rule as /matches).
  const href = safeHref(j.url);
  return (
    <div className="match">
      <div className="bd">
        <h3>{j.title || "Role"} <span>— {j.company || ""}</span></h3>
        <div className="tags">
          {j.tags.map((t, i) => (
            <span key={i} className={`tag${/free|contract/i.test(t) ? " fl" : ""}`}>{t}</span>
          ))}
        </div>
        {j.why && <div className="why">{j.why}</div>}
        {href && (
          <a className="apply" href={href} target="_blank" rel="noopener noreferrer">
            View &amp; apply →
          </a>
        )}
      </div>
    </div>
  );
}

function Inner() {
  const email = useSearchParams().get("email") || "your inbox";
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
          <h1>Check your inbox</h1>
          <p>
            We sent a confirmation link to <b>{email}</b>. Click it and your daily digest starts
            tomorrow morning at 7:00.
            {hasJobs && " In the meantime, here are live jobs matching your search:"}
          </p>
          <p className="label" style={{ marginTop: 14 }}>Double opt-in · GDPR consent</p>
          {!hasJobs && (
            <p style={{ marginTop: 18 }}>
              <Link href="/">← Back to home</Link>
            </p>
          )}
        </div>
      </div>

      {hasJobs && (
        <>
          <div className="wrap" style={{ maxWidth: 720, margin: "0 auto", textAlign: "center" }}>
            <span className="label">Instant preview · keyword match</span>
            <p style={{ fontSize: "var(--fs-sm)", color: "var(--muted)", marginTop: 6 }}>
              A quick keyword match to get you started. Tomorrow&apos;s email is <b>AI-ranked</b> —
              each role scored, with a reason it fits you.
            </p>
          </div>
          <div className="matches">
            {jobs.map((j) => <PreviewRow key={j.posting_id} j={j} />)}
          </div>
          <div className="wrap" style={{ textAlign: "center", marginBottom: 60 }}>
            <Link href="/">← Back to home</Link>
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
        <Suspense fallback={<div className="state-wrap"><div className="state-card"><h1>Check your inbox</h1></div></div>}>
          <Inner />
        </Suspense>
      </main>
      <Footer />
    </>
  );
}
