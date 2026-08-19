"use client";

/**
 * The other half of `/matches`: jobs the subscriber hid because they had already applied or
 * did not want to see them again.
 *
 * Hiding is never a delete — this page is what makes that true. It is the same list, the same
 * cards and the same paging read through the opposite filter (`useMatchList({hidden: true})`),
 * so nothing about a job changes by being hidden except which page it is on.
 */

import { Suspense, useState } from "react";
import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";
import { MatchCard, SelectionBar } from "@/components/MatchCard";
import { setMatchesHidden } from "@/lib/api";
import { useMatchList } from "@/lib/useMatchList";
import { useI18n } from "@/i18n/context";

function Inner() {
  const { t, href, count } = useI18n();
  const list = useMatchList({ hidden: true, path: "/hidden" });
  const { data, jobs } = list;
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  const unhide = async () => {
    const ids = [...picked];
    if (!ids.length || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const res = await setMatchesHidden(ids, false, list.token || undefined);
      list.removeJobs(ids, res);
      setPicked(new Set());
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const matchesHref = list.linkTo("/matches");

  if (list.err) {
    return (
      <div className="state-wrap">
        <div className="state-card warn">
          <div className="ic">!</div>
          <h1>{t.matches.errTitle}</h1>
          <p>{list.err}</p>
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
        <span className="label">{t.hidden.label}</span>
        <h1>{data.count > 0 ? count(data.count, t.hidden.countTitle) : t.hidden.noneTitle}</h1>
        <p>{t.hidden.intro}</p>
      </div>

      {data.count === 0 ? (
        <div className="state-wrap">
          <div className="state-card">
            <h1>{t.hidden.nothingTitle}</h1>
            <p>{t.hidden.nothingBody}</p>
            <p style={{ marginTop: 16 }}>
              <Link href={matchesHref}>{t.hidden.backToMatches}</Link>
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="wrap list-tools">
            <div className="row">
              <button type="button" className="lnk"
                onClick={() => setPicked(new Set(jobs.map((j) => j.posting_id)))}>
                {t.matches.selectAll}
              </button>
              <Link className="hidden-link" href={matchesHref}>{t.hidden.backToMatches}</Link>
            </div>
          </div>

          <div className="matches">
            {jobs.map((j) => (
              <MatchCard
                key={j.posting_id}
                j={j}
                token={list.token || undefined}
                selected={picked.has(j.posting_id)}
                onToggle={toggle}
                emailMinScore={data.email_min_score}
                greatFitScore={data.great_fit_score}
              />
            ))}
          </div>

          <div className="wrap more-wrap">
            {failed && <p className="more-err" role="alert">{t.hidden.unhideFailed}</p>}
            {list.hasMore && (
              <>
                <p className="more-count">
                  {count(data.count, t.hidden.showing, { shown: jobs.length })}
                </p>
                <button type="button" className="btn more-btn"
                  onClick={list.loadMore} disabled={list.loadingMore}>
                  {list.loadingMore ? t.common.loading : t.matches.loadMore}
                </button>
              </>
            )}
            {list.moreErr && (
              <p className="more-err" role="alert">{t.matches.loadMoreFailed}</p>
            )}
          </div>

          <SelectionBar
            n={picked.size}
            busy={busy}
            countLabel={count(picked.size, t.matches.selected)}
            label={t.hidden.unhideSelected}
            busyLabel={t.hidden.unhiding}
            clearLabel={t.matches.clearSelection}
            onConfirm={unhide}
            onClear={() => setPicked(new Set())}
          />
        </>
      )}
    </>
  );
}

export default function HiddenPage() {
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
