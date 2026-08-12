"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { establishSession, getMatches, MatchesResponse, MatchJob } from "./api";
import { useI18n } from "@/i18n/context";

/**
 * The shared machinery behind `/matches` and `/hidden`: log in, load a page, page through
 * the rest, and drop rows the subscriber just hid or unhid.
 *
 * The two pages are the same list read through opposite filters, so this exists to stop them
 * being two implementations of paging that can disagree. The subtle half is `removeJobs`:
 * hiding a job removes it from the *server's* list too, so the rows on screen stay exactly
 * the first `jobs.length` of the shrunken list and `loadMore` can keep asking for
 * `offset = jobs.length`. Refetching from zero instead would be correct but would throw away
 * every page already loaded, and re-paging with the old offsets would skip as many jobs as
 * were hidden.
 */
export interface MatchList {
  /** The most recent response — `count` is this view's total, `hidden_count` the other's. */
  data: MatchesResponse | null;
  /** Everything loaded so far: the first page plus whatever `loadMore` appended. */
  jobs: MatchJob[];
  /** Set only for a failure that leaves nothing to render; replaces the whole page. */
  err: string;
  hasMore: boolean;
  loadingMore: boolean;
  /** A failed *extra* page. Deliberately separate from `err`: losing page 2 must not throw
   *  away the matches already on screen. */
  moreErr: boolean;
  loadMore: () => Promise<void>;
  /** The magic-link token, or "" once it has been traded for a session cookie. Pass it on
   *  every call and every internal link, or a cookie-blocked browser loses its login. */
  token: string;
  /** Locale-prefixed href that carries the token when there is one. */
  linkTo: (path: string) => string;
  removeJobs: (ids: string[], counts: { visible_count: number; hidden_count: number }) => void;
}

export function useMatchList(
  /** Which half to read, and the page it is rendered on — the path is where the URL is
   *  rewritten to after the one-time `?token=` is spent. */
  { hidden, path, skills = [], workModes = [], greatFits = false, onLoaded }: {
    hidden: boolean;
    path: string;
    /** The active skill filter. Changing it refetches from page 0 (paging cannot be carried
     *  across a different filter) — see the effect deps. */
    skills?: string[];
    /** The active work-setup filter, and the "great fits only" toggle. Both behave exactly
     *  like `skills`: changing either restarts the list from page 0, because an offset is
     *  only meaningful against the filter it was counted under. */
    workModes?: string[];
    greatFits?: boolean;
    /** Called once the first page lands. The token is passed along because it is only
     *  settled here — by the time the caller renders, it may already have been spent. */
    onLoaded?: (d: MatchesResponse, token: string) => void;
  }
): MatchList {
  // A stable primitive dep for the effect: two arrays with the same members must not refetch.
  const skillsKey = [...skills].sort().join(",");
  const modesKey = [...workModes].sort().join(",");
  // Passed on every request, so it has to be one object the calls can share.
  const filters = { workModes, greatFits };
  const urlToken = useSearchParams().get("token") || "";
  // Same login model on both pages: trade the one-time token for a session cookie, then ride
  // the cookie. Stays set only in the cookie-refused fallback.
  const tokenRef = useRef<string>(urlToken);
  const { t, href } = useI18n();
  const [data, setData] = useState<MatchesResponse | null>(null);
  const [more, setMore] = useState<MatchJob[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreErr, setMoreErr] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    let cancelled = false;
    const done = (d: MatchesResponse) => {
      if (cancelled) return;
      setData(d);
      onLoaded?.(d, tokenRef.current);
    };
    // A new filter (or view) starts a fresh list: drop any extra pages already appended, or
    // they would sit below the first page under the *new* filter and show stale rows.
    setMore([]);
    setMoreErr(false);
    const load = async () => {
      try {
        if (urlToken) {
          try {
            await establishSession(urlToken);
            tokenRef.current = "";
            if (typeof window !== "undefined") {
              window.history.replaceState(null, "", href(path));
              window.dispatchEvent(new Event("jd-auth-changed"));   // nav: re-check, we're in
            }
            done(await getMatches(undefined, 0, hidden, skills, filters));
          } catch {
            done(await getMatches(urlToken, 0, hidden, skills, filters));
          }
          return;
        }
        done(await getMatches(undefined, 0, hidden, skills, filters));
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
  }, [urlToken, hidden, skillsKey, modesKey, greatFits]);

  const jobs = data ? [...data.jobs, ...more] : [];
  const hasMore = !!data && jobs.length < data.count;

  const loadMore = async () => {
    if (!data || loadingMore) return;
    setLoadingMore(true);
    setMoreErr(false);
    try {
      // Offset by what is on screen, not by page number — `data.limit` is the server's cap
      // and the client must not assume it stays the same between requests.
      const next = await getMatches(tokenRef.current || undefined, jobs.length, hidden,
                                    skills, filters);
      setMore((prev) => [...prev, ...next.jobs]);
      // Re-read the totals from the fresh response. If a posting went inactive between
      // requests the count shrinks, and an empty page then settles `hasMore` to false on the
      // next render rather than leaving a button that can never finish.
      setData((d) => (d ? { ...d, count: next.count, hidden_count: next.hidden_count } : d));
    } catch {
      setMoreErr(true);
    } finally {
      setLoadingMore(false);
    }
  };

  const removeJobs = useCallback(
    (ids: string[], counts: { visible_count: number; hidden_count: number }) => {
      const gone = new Set(ids);
      const keep = (j: MatchJob) => !gone.has(j.posting_id);
      setMore((prev) => prev.filter(keep));
      setData((d) =>
        d
          ? {
              ...d,
              jobs: d.jobs.filter(keep),
              // Both totals come from the server's own count, never from `length - ids`:
              // an id that changed nothing (already hidden, posting gone inactive) would
              // make arithmetic drift, and the header is what the subscriber reads.
              count: hidden ? counts.hidden_count : counts.visible_count,
              hidden_count: counts.hidden_count,
            }
          : d
      );
    },
    [hidden]
  );

  const token = tokenRef.current;
  const linkTo = (to: string) =>
    token ? href(`${to}?token=${encodeURIComponent(token)}`) : href(to);

  return { data, jobs, err, hasMore, loadingMore, moreErr, loadMore, token, linkTo, removeJobs };
}
