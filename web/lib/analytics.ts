/**
 * First-party, cookieless product analytics.
 *
 * Exists to answer one question: where does the signup flow lose people. It is
 * deliberately minimal so the site needs no consent banner:
 *
 *   - No cookies. The session id lives in sessionStorage, so it dies when the tab closes
 *     and cannot follow anyone across visits or sites.
 *   - No IP, no user-agent, no free text ever leaves the browser. The server derives
 *     country (Cloudflare header) and a coarse browser family; see service/webapp.py.
 *   - Respects Do Not Track and Global Privacy Control: if either is set, nothing is sent.
 *   - Fire-and-forget. Failures are swallowed — analytics must never break a user flow.
 */

import { API_URL } from "./api";

const SESSION_KEY = "jd_sid";

/** Opt out if the browser asks us to. Cheap to honour, and the right default. */
function optedOut(): boolean {
  if (typeof navigator === "undefined") return true;
  const nav = navigator as Navigator & { globalPrivacyControl?: boolean; msDoNotTrack?: string };
  return nav.doNotTrack === "1" || nav.msDoNotTrack === "1" || nav.globalPrivacyControl === true;
}

/**
 * Random per-tab id used only to reconstruct a funnel within one visit
 * ("did this session start the form and then leave?"). Not a persistent identifier.
 */
function sessionId(): string | undefined {
  try {
    let sid = sessionStorage.getItem(SESSION_KEY);
    if (!sid) {
      sid =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : Math.random().toString(36).slice(2) + Date.now().toString(36);
      sessionStorage.setItem(SESSION_KEY, sid);
    }
    return sid;
  } catch {
    return undefined; // private mode / storage blocked — event still counts, just unlinked
  }
}

export type EventProps = {
  reason?: string;
  status?: number;
  skills?: number;
  count?: number;
  variant?: string;
  step?: string;
};

/**
 * Record an event. Never throws, never blocks — safe to call anywhere, including in a
 * handler that is about to navigate away.
 */
export function track(name: string, props?: EventProps, token?: string): void {
  if (typeof window === "undefined" || optedOut()) return;

  const body = JSON.stringify({
    name,
    session_id: sessionId(),
    path: window.location.pathname,
    props: props ?? {},
    token,
  });

  try {
    // sendBeacon survives the page unloading, which matters for drop-off events — the
    // ones we most need are fired precisely as someone leaves.
    if (navigator.sendBeacon) {
      navigator.sendBeacon(`${API_URL}/event`, new Blob([body], { type: "application/json" }));
      return;
    }
    void fetch(`${API_URL}/event`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    }).catch(() => {});
  } catch {
    /* analytics must never surface an error to the user */
  }
}
