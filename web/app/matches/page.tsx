"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";
import { establishSession, getMatches, MatchesResponse, MatchJob } from "@/lib/api";
import { track } from "@/lib/analytics";
import { safeHref } from "@/lib/url";

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function jobTags(j: MatchJob): { text: string; fl?: boolean }[] {
  const out: { text: string; fl?: boolean }[] = [];
  if (j.region) out.push({ text: j.region.toUpperCase() });
  if (j.region === "eu" || j.region === "worldwide") out.push({ text: "Remote" });
  if (j.seniority) out.push({ text: cap(j.seniority) });
  if (j.work_type === "freelance/contract") out.push({ text: "Freelance", fl: true });
  if (j.salary) out.push({ text: j.salary });
  // de-dup by lowercased text
  const seen = new Set<string>();
  return out.filter((t) => {
    const k = t.text.toLowerCase();
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

function MatchRow({ j, token }: { j: MatchJob; token?: string }) {
  const score = j.score ?? 0;
  const strong = score >= 6;
  // Feed URLs are untrusted; a non-http(s) scheme (javascript:, data:) renders no link.
  const href = safeHref(j.url);
  return (
    <div className={`match${strong ? " top" : ""}`}>
      <div className="sc">
        <div className="n">{score}</div>
        <div className="d">/ 10</div>
        <div className="bar"><i style={{ width: `${score * 10}%` }} /></div>
      </div>
      <div className="bd">
        <h3>
          {j.title || "Role"} <span>— {j.company || ""}</span>
          {score >= 8 && <span className="emailed">Top match</span>}
        </h3>
        <div className="tags">
          {jobTags(j).map((t, i) => (
            <span key={i} className={`tag${t.fl ? " fl" : ""}`}>{t.text}</span>
          ))}
        </div>
        {j.summary && <div className="why">{j.summary}</div>}
        {href && (
          <a
            className="apply"
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            // Score only — never the job, company or URL. Tells us whether the ranking is
            // trusted (are low-scored matches ever clicked?) without profiling anyone.
            onClick={() => track("match_clicked", { count: score }, token || undefined)}
          >
            View &amp; apply →
          </a>
        )}
      </div>
    </div>
  );
}

function Inner() {
  const urlToken = useSearchParams().get("token") || "";
  // Same login model as /preferences: trade the one-time token for a session cookie, then
  // ride the cookie. Stays set only in the cookie-refused fallback.
  const tokenRef = useRef<string>(urlToken);
  const [data, setData] = useState<MatchesResponse | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let cancelled = false;
    const done = (d: MatchesResponse) => {
      if (cancelled) return;
      setData(d);
      // How many matches the page actually had — a page that routinely shows 0 or 1 is
      // a product problem, not a UI one.
      track("matches_viewed", { count: d.count }, tokenRef.current || undefined);
    };
    const load = async () => {
      try {
        if (urlToken) {
          try {
            await establishSession(urlToken);
            tokenRef.current = "";
            if (typeof window !== "undefined") {
              window.history.replaceState(null, "", "/matches");
              window.dispatchEvent(new Event("jd-auth-changed"));   // nav: re-check, we're in
            }
            done(await getMatches());
          } catch {
            done(await getMatches(urlToken));
          }
          return;
        }
        done(await getMatches());
      } catch (e) {
        if (cancelled) return;
        setErr(
          urlToken
            ? e instanceof Error ? e.message : "Unknown or expired link."
            : "Open your matches from the link in your email — or use “Manage subscription” to get a fresh one."
        );
      }
    };
    load();
    return () => { cancelled = true; };
  }, [urlToken]);

  const prefsHref = tokenRef.current
    ? `/preferences?token=${encodeURIComponent(tokenRef.current)}`
    : "/preferences";

  if (err) {
    return (
      <div className="state-wrap">
        <div className="state-card warn">
          <div className="ic">!</div>
          <h1>Can&apos;t open your matches</h1>
          <p>{err}</p>
          <p style={{ marginTop: 16 }}><Link href="/">Go to homepage →</Link></p>
        </div>
      </div>
    );
  }
  if (!data) {
    return <div className="state-wrap"><div className="state-card"><h1>Loading…</h1></div></div>;
  }

  return (
    <>
      <div className="wrap page-head">
        <span className="label">Your matches · signed in</span>
        <h1>{data.count > 0 ? `${data.count} matches for you` : "No matches yet"}</h1>
        <p>
          Everything we found for <b>{data.email}</b>, ranked best-first. We email you the
          strongest few each morning — this is the full list.
        </p>
      </div>

      {data.count === 0 ? (
        <div className="state-wrap">
          <div className="state-card">
            <h1>Nothing yet</h1>
            <p>Your first matches are found overnight — check back after tomorrow&apos;s 7:00 digest.</p>
            <p style={{ marginTop: 16 }}>
              <Link href={prefsHref}>Adjust your preferences →</Link>
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="matches">
            {data.jobs.map((j) => <MatchRow key={j.posting_id} j={j} token={tokenRef.current || undefined} />)}
          </div>
          <div className="wrap" style={{ textAlign: "center", marginBottom: 60 }}>
            <Link href={prefsHref}
              style={{ fontSize: "var(--fs-sm)", color: "var(--muted)" }}>
              Not quite right? Adjust your roles &amp; skills →
            </Link>
          </div>
        </>
      )}
    </>
  );
}

export default function MatchesPage() {
  return (
    <>
      <Nav />
      <main>
        <Suspense fallback={<div className="state-wrap"><div className="state-card"><h1>Loading…</h1></div></div>}>
          <Inner />
        </Suspense>
      </main>
      <Footer />
    </>
  );
}
