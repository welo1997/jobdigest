"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";
import { establishSession, getMatches, MatchesResponse, MatchJob } from "@/lib/api";
import { track } from "@/lib/analytics";
import { safeHref } from "@/lib/url";
import { rich, useI18n } from "@/i18n/context";
import type { Messages } from "@/i18n/schema";

function jobTags(j: MatchJob, t: Messages): { text: string; fl?: boolean }[] {
  const out: { text: string; fl?: boolean }[] = [];
  if (j.region) out.push({ text: j.region.toUpperCase() });
  // Hybrid wins over the coarse region-implied "Remote" — see `_tags` in service/digest.py.
  if (j.work_mode === "hybrid") out.push({ text: t.matches.tagHybrid });
  else if (j.work_mode === "remote" || j.region === "eu" || j.region === "worldwide") {
    out.push({ text: t.matches.tagRemote });
  }
  // Seniority is a stored code (junior|mid|senior), so it reads out of the same chip
  // vocabulary the preferences form uses rather than being title-cased in English.
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

function MatchRow({ j, token }: { j: MatchJob; token?: string }) {
  const { t } = useI18n();
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
          {j.title || t.common.roleFallback} <span>— {j.company || ""}</span>
          {score >= 8 && <span className="emailed">{t.matches.topMatch}</span>}
        </h3>
        <div className="tags">
          {jobTags(j, t).map((tag, i) => (
            <span key={i} className={`tag${tag.fl ? " fl" : ""}`}>{tag.text}</span>
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
            {t.common.viewAndApply}
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
  const { t, href, count } = useI18n();
  const [data, setData] = useState<MatchesResponse | null>(null);
  // Pages fetched after the first are appended here, so "load more" grows the list in place
  // instead of replacing it.
  const [more, setMore] = useState<MatchJob[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  // Separate from `err`, which replaces the whole page: a failed "load more" must not throw
  // away the matches already on screen.
  const [moreErr, setMoreErr] = useState(false);
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
              window.history.replaceState(null, "", href("/matches"));
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
            ? e instanceof Error ? e.message : t.matches.errUnknownLink
            : t.matches.errNoLink
        );
      }
    };
    load();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlToken]);

  const prefsHref = tokenRef.current
    ? href(`/preferences?token=${encodeURIComponent(tokenRef.current)}`)
    : href("/preferences");

  // Everything rendered so far: the first page plus whatever "load more" has appended.
  const jobs = data ? [...data.jobs, ...more] : [];
  const hasMore = !!data && jobs.length < data.count;

  const loadMore = async () => {
    if (!data || loadingMore) return;
    setLoadingMore(true);
    setMoreErr(false);
    try {
      // Offset by what is on screen, not by page number — `data.limit` is the server's cap
      // and the client must not assume it stays the same between requests.
      const next = await getMatches(tokenRef.current || undefined, jobs.length);
      setMore((prev) => [...prev, ...next.jobs]);
      // Re-read the total from the fresh response. If a posting went inactive between
      // requests the count shrinks, and an empty page then settles `hasMore` to false on the
      // next render rather than leaving a button that can never finish.
      setData((d) => (d ? { ...d, count: next.count } : d));
    } catch {
      // Deliberately not `setErr`: that swaps the whole page for an error card and would
      // throw away matches the subscriber is already reading. A failed page-2 fetch should
      // cost them page 2, nothing else.
      setMoreErr(true);
    } finally {
      setLoadingMore(false);
    }
  };

  if (err) {
    return (
      <div className="state-wrap">
        <div className="state-card warn">
          <div className="ic">!</div>
          <h1>{t.matches.errTitle}</h1>
          <p>{err}</p>
          <p style={{ marginTop: 16 }}><Link href={href("/")}>{t.common.goHome}</Link></p>
        </div>
      </div>
    );
  }
  if (!data) {
    return <div className="state-wrap"><div className="state-card"><h1>{t.common.loading}</h1></div></div>;
  }

  return (
    <>
      <div className="wrap page-head">
        <span className="label">{t.matches.label}</span>
        <h1>{data.count > 0 ? count(data.count, t.matches.countTitle) : t.matches.noneTitle}</h1>
        <p>{rich(t.matches.intro, [<b key="e">{data.email}</b>])}</p>
      </div>

      {data.count === 0 ? (
        <div className="state-wrap">
          <div className="state-card">
            <h1>{t.matches.nothingTitle}</h1>
            <p>{t.matches.nothingBody}</p>
            <p style={{ marginTop: 16 }}>
              <Link href={prefsHref}>{t.matches.adjustPrefs}</Link>
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="matches">
            {jobs.map((j) => <MatchRow key={j.posting_id} j={j} token={tokenRef.current || undefined} />)}
          </div>
          <div className="wrap more-wrap">
            {hasMore && (
              <>
                {/* Stated before the button, so "load more" is a decision rather than a
                    guess at how much is left. */}
                <p className="more-count">
                  {count(data.count, t.matches.showing, { shown: jobs.length })}
                </p>
                <button type="button" className="btn more-btn"
                  onClick={loadMore} disabled={loadingMore}>
                  {loadingMore ? t.common.loading : t.matches.loadMore}
                </button>
              </>
            )}
            {moreErr && (
              <p className="more-err" role="alert">{t.matches.loadMoreFailed}</p>
            )}
            <p style={{ marginTop: 28 }}>
              <Link href={prefsHref}
                style={{ fontSize: "var(--fs-sm)", color: "var(--muted)" }}>
                {t.matches.notQuiteRight}
              </Link>
            </p>
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
  return <div className="state-wrap"><div className="state-card"><h1>{t.common.loading}</h1></div></div>;
}
