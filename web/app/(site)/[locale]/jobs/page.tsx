"use client";

/**
 * `/jobs` — the public job feed.
 *
 * The free tier's front door (`notes/scaling/PLAN.md`): no account, no model call, no tokens.
 * It reads `GET /jobs`, which is unauthenticated and writes nothing — including nothing about
 * what was searched for.
 *
 * Four decisions worth knowing before editing:
 *
 *   - **Nothing is listed until something is asked for.** With no term and no filter the page
 *     fetches the filter menus and shows no jobs. A feed that opens full of listings reads as
 *     a recommendation, and this page makes no per-job judgement — that is the digest's, and
 *     the difference between the tiers. The newest twenty rows in the corpus are nobody's
 *     search, and presenting them as an answer overstates what an unauthenticated,
 *     model-free SQL query knows about the visitor.
 *   - **The search lives in the URL.** `?q=&country=&category=` is built by `searchQuery` in
 *     `lib/api.ts`, the same function that builds the request, so the link and the fetch can
 *     never disagree about what was asked for. A search worth running is a search worth
 *     sharing, and it also means the back button works.
 *   - **Facets arrive with page 1 and are kept.** The API omits them on later pages (absent,
 *     not empty), so the menus must be held in state rather than re-read from the last
 *     response — reading them straight off `data` would blank the filter row on "load more".
 *   - **Category labels come from `lib/options.ts`**, which derives them from `ROLE_OPTIONS`,
 *     which is the same list the signup chips render from. There is no second table of
 *     category names to drift.
 */

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Nav, Footer } from "@/components/SiteChrome";
import { searchJobs, searchQuery, type SearchFacet, type SearchJob, type SearchResponse } from "@/lib/api";
import { categoryLabel } from "@/lib/options";
import { SENIORITY_IDS } from "@/lib/options";
import { safeHref } from "@/lib/url";
import { track } from "@/lib/analytics";
import { useI18n } from "@/i18n/context";
import type { Messages } from "@/i18n/schema";

const PAGE = 20;

/** Tags for one public card. Deliberately the same precedence as `MatchCard.jobTags` and the
 *  digest's `_tags`: hybrid is never also "Remote". Not imported from there because that one
 *  takes a `MatchJob` (which carries a score and a summary this page has neither of). */
function jobTags(j: SearchJob, t: Messages): { text: string; fl?: boolean }[] {
  const out: { text: string; fl?: boolean }[] = [];
  if (j.work_mode === "hybrid") out.push({ text: t.matches.tagHybrid });
  else if (j.remote_signal || j.work_mode === "remote") out.push({ text: t.matches.tagRemote });
  if (j.seniority) out.push({ text: t.seniorities[j.seniority] ?? j.seniority });
  if (j.work_type === "freelance/contract") out.push({ text: t.matches.tagFreelance, fl: true });
  if (j.salary) out.push({ text: j.salary });
  const seen = new Set<string>();
  return out.filter((tag) => {
    const k = tag.text.toLowerCase();
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

function JobCard({ j }: { j: SearchJob }) {
  const { t, country } = useI18n();
  // Feed URLs are untrusted; a non-http(s) scheme (javascript:, data:) renders no link.
  const href = safeHref(j.url);
  const where = j.location || (j.country_code ? country(j.country_code) : "");
  return (
    <div className="match feed">
      <div className="bd">
        <h3>
          {j.title || t.common.roleFallback} <span>— {j.company || ""}</span>
        </h3>
        {where && <div className="feed-where">{where}</div>}
        <div className="tags">
          {jobTags(j, t).map((tag, i) => (
            <span key={i} className={`tag${tag.fl ? " fl" : ""}`}>{tag.text}</span>
          ))}
        </div>
        {j.skills.length > 0 && (
          <div className="skills">
            {j.skills.slice(0, 10).map((s) => (
              <span key={s} className="skill">{s}</span>
            ))}
          </div>
        )}
        {href && (
          <a
            className="apply"
            href={href}
            target="_blank"
            // `noopener` only, never `noreferrer` — the same call `MatchCard` documents. The
            // Referer is how a source sees that we sent the visit, and Remote OK's API terms
            // ask for a followed link and the traffic back. Nothing sensitive can leak from
            // this page: it is public, unauthenticated, and carries no token in its URL.
            rel="noopener"
            onClick={() => track("feed_job_clicked")}
          >
            {t.common.viewAndApply}
          </a>
        )}
      </div>
    </div>
  );
}

/** A tick-box dropdown of facet values with counts. Same shape as the skill menu on
 *  `/matches`, so the two filter rows behave identically. */
function FacetMenu({
  label,
  facets,
  selected,
  render,
  onToggle,
}: {
  label: string;
  facets: SearchFacet[];
  selected: string[];
  render: (value: string) => string;
  onToggle: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!facets.length && !selected.length) return null;
  const n = selected.length;
  return (
    <div className="skill-menu" ref={ref}>
      <button
        type="button"
        className={`skill-menu-btn${n ? " on" : ""}`}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {label}
        {n > 0 && <span className="n">{n}</span>}
        <span className="caret" aria-hidden>▾</span>
      </button>
      {open && (
        <div className="skill-menu-pop">
          <div className="skill-menu-list" role="group">
            {facets.map((f) => {
              const on = selected.includes(f.value);
              return (
                <button
                  type="button"
                  key={f.value}
                  className={`skill-opt${on ? " on" : ""}`}
                  aria-pressed={on}
                  // Named explicitly rather than left to the contents. The tick box is
                  // `aria-hidden` and the count is a bare number, so the computed name was
                  // coming out empty in the accessibility tree — a row of unlabelled buttons
                  // on a page with no login in front of it.
                  aria-label={render(f.value)}
                  onClick={() => onToggle(f.value)}
                >
                  <span className="box" aria-hidden>{on ? "✓" : ""}</span>
                  <span className="nm">{render(f.value)}</span>
                  <span className="c">{f.count}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function Inner() {
  const { t, href, count, country } = useI18n();
  const router = useRouter();
  const params = useSearchParams();

  // The URL is the source of truth for the search, so a shared link, a reload and the back
  // button all reproduce the same page. State below is only what the URL does not hold.
  const q = params.get("q") ?? "";
  const countries = params.getAll("country");
  const categories = params.getAll("category");
  const seniorities = params.getAll("seniority");
  const remote = params.get("remote") === "true";

  const [text, setText] = useState(q);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [jobs, setJobs] = useState<SearchJob[]>([]);
  const [facets, setFacets] = useState<SearchResponse["facets"] | null>(null);
  const [err, setErr] = useState(false);
  const [moreErr, setMoreErr] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  // `useSearchParams` returns a new object each render, so the raw arrays cannot be effect
  // dependencies without refetching forever. The serialised query is stable for an unchanged
  // search, which is exactly the identity we want.
  const key = useMemo(
    () => searchQuery({ q, countries, categories, seniorities, remote }).toString(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [params.toString()]
  );

  // Whether anything has actually been asked for. Read before the fetch, not after it: it is
  // what decides whether this page answers at all.
  const filtering =
    Boolean(q) || countries.length > 0 || categories.length > 0 ||
    seniorities.length > 0 || remote;

  useEffect(() => {
    let cancelled = false;
    setErr(false);
    setData(null);
    // **A visitor who has asked for nothing is shown no jobs.** Landing on a page already
    // full of listings reads as a recommendation — it is not one; it was the newest twenty
    // rows in the corpus, which is nobody's search. So with no term and no filter this call
    // fetches the filter menus and nothing else, and the first thing on screen is the
    // question rather than an answer to one nobody asked.
    //
    // `limit: 1` rather than a facets-only mode: the API clamps `limit` to a minimum of one
    // (`SEARCH_LIMIT_MAX` guards the other end), so one row is the cheapest legal request and
    // it is discarded below. Facets are computed on `offset == 0` regardless, which is the
    // part we are actually here for.
    searchJobs({ q, countries, categories, seniorities, remote, limit: filtering ? PAGE : 1 })
      .then((d) => {
        if (cancelled) return;
        // Kept rather than read from `data` on every render: later pages omit `facets`, and
        // reading it off the newest response would empty the menu the visitor is using.
        if (d.facets) setFacets(d.facets);
        if (!filtering) {
          // No jobs, no count, and no `job_search` event — nothing was searched for.
          setJobs([]);
          return;
        }
        setData(d);
        setJobs(d.jobs);
        track("job_search", { count: d.count });
      })
      .catch(() => {
        if (!cancelled) setErr(true);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  /** Rewrite the URL, which is what actually triggers a refetch. `scroll: false` because a
   *  filter change should leave the reader where they were. */
  const apply = useCallback(
    (next: Partial<{ q: string; countries: string[]; categories: string[]; seniorities: string[]; remote: boolean }>) => {
      const s = searchQuery({
        q: next.q ?? q,
        countries: next.countries ?? countries,
        categories: next.categories ?? categories,
        seniorities: next.seniorities ?? seniorities,
        remote: next.remote ?? remote,
      }).toString();
      router.replace(`?${s}`, { scroll: false });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [router, params.toString()]
  );

  const toggle = (list: string[], v: string) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v];

  const loadMore = async () => {
    if (loadingMore || !data) return;
    setLoadingMore(true);
    setMoreErr(false);
    try {
      const d = await searchJobs({
        q, countries, categories, seniorities, remote,
        limit: PAGE, offset: jobs.length,
      });
      setJobs((prev) => [...prev, ...d.jobs]);
    } catch {
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
          <h1>{t.jobs.errTitle}</h1>
          <p>{t.jobs.errBody}</p>
          <p style={{ marginTop: 16 }}><Link href={href("/")}>{t.common.goHome}</Link></p>
        </div>
      </div>
    );
  }

  // The count is capped server-side; rendering the cap as an exact figure would state a
  // ceiling as a fact, so the capped form carries the "+".
  const headline = data
    ? data.count_capped
      ? t.jobs.countCapped.replace("{n}", String(data.count))
      : count(data.count, t.jobs.countTitle)
    : t.common.loading;

  return (
    <>
      <div className="wrap page-head">
        <span className="label">{t.jobs.label}</span>
        <h1>{t.jobs.title}</h1>
        <p>{t.jobs.intro}</p>
      </div>

      <div className="wrap feed-search">
        <form
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            apply({ q: text });
          }}
        >
          <label className="sr-only" htmlFor="feed-q">{t.jobs.searchLabel}</label>
          <input
            id="feed-q"
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={t.jobs.searchPlaceholder}
            autoComplete="off"
          />
          <button type="submit" className="btn">{t.jobs.searchButton}</button>
        </form>
      </div>

      <div className="wrap skill-filter feed-filters">
        <FacetMenu
          label={t.jobs.filterCategory}
          facets={facets?.categories ?? []}
          selected={categories}
          render={(v) => categoryLabel(v, t.roles, t.jobs.categories)}
          onToggle={(v) => apply({ categories: toggle(categories, v) })}
        />
        <FacetMenu
          label={t.jobs.filterCountry}
          facets={facets?.countries ?? []}
          selected={countries}
          render={(v) => country(v)}
          onToggle={(v) => apply({ countries: toggle(countries, v) })}
        />
        <FacetMenu
          label={t.jobs.filterSeniority}
          // Seniority has a fixed, tiny vocabulary, so it is offered in full rather than
          // faceted — a level with no results today is still a level someone means to pick.
          facets={SENIORITY_IDS.map((id) => ({ value: id, count: 0 }))}
          selected={seniorities}
          render={(v) => t.seniorities[v] ?? v}
          onToggle={(v) => apply({ seniorities: toggle(seniorities, v) })}
        />
        <button
          type="button"
          className={`skill-menu-btn${remote ? " on" : ""}`}
          aria-pressed={remote}
          onClick={() => apply({ remote: !remote })}
        >
          {t.jobs.remoteOnly}
        </button>
        {filtering && (
          <button
            type="button"
            className="lnk"
            onClick={() => {
              setText("");
              router.replace("?", { scroll: false });
            }}
          >
            {t.jobs.clearFilters}
          </button>
        )}
      </div>

      {filtering && (
        <div className="wrap list-tools">
          <p className="hint">{headline}</p>
        </div>
      )}

      {!filtering ? (
        // The idle state. Not an empty-results card: nothing was searched for, so saying
        // "nothing matches" would be answering a question that was never asked.
        <div className="state-wrap">
          <div className="state-card">
            <h1>{t.jobs.startTitle}</h1>
            <p>{t.jobs.startBody}</p>
          </div>
        </div>
      ) : data && data.count === 0 ? (
        <div className="state-wrap">
          <div className="state-card">
            <h1>{t.jobs.noneTitle}</h1>
            <p>{t.jobs.noneBody}</p>
          </div>
        </div>
      ) : (
        <>
          <div className="matches">
            {jobs.map((j) => (
              <JobCard key={j.posting_id} j={j} />
            ))}
          </div>

          <div className="wrap more-wrap">
            {data && jobs.length < data.count && (
              <>
                <p className="more-count">
                  {count(data.count, t.jobs.showing, { shown: jobs.length })}
                </p>
                <button
                  type="button"
                  className="btn more-btn"
                  onClick={loadMore}
                  disabled={loadingMore}
                >
                  {loadingMore ? t.common.loading : t.jobs.loadMore}
                </button>
              </>
            )}
            {moreErr && <p className="more-err" role="alert">{t.jobs.loadMoreFailed}</p>}
          </div>
        </>
      )}

      {/* The upgrade path. This page is the hook; the digest is the product, and the
          difference stated here is the honest one — we read every job and say why. */}
      <div className="wrap feed-cta">
        <div className="state-card">
          <h2>{t.jobs.ctaTitle}</h2>
          <p>{t.jobs.ctaBody}</p>
          <p style={{ marginTop: 16 }}>
            <Link className="btn" href={href("/")}>{t.jobs.ctaButton}</Link>
          </p>
        </div>
      </div>
    </>
  );
}

export default function JobsPage() {
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
