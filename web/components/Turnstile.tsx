"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

// Cloudflare Turnstile widget (explicit render). When NEXT_PUBLIC_TURNSTILE_SITE_KEY is
// empty (local dev), it renders nothing and reports an empty token — the API skips
// verification when TURNSTILE_SECRET is unset, so the flow still works end-to-end.
//
// Exposes reset() via ref: Turnstile tokens are single-use, so after a failed submit the
// widget must be reset to mint a fresh token (otherwise the reused token fails siteverify
// as "timeout-or-duplicate").
//
// Local testing uses Cloudflare's always-pass test keys:
//   site key   1x00000000000000000000AA
//   secret     1x0000000000000000000000000000000AA

declare global {
  interface Window {
    turnstile?: {
      render: (el: HTMLElement, opts: Record<string, unknown>) => string;
      remove: (id: string) => void;
      reset: (id?: string) => void;
    };
  }
}

const SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY || "";
export const turnstileEnabled = !!SITE_KEY;

export interface TurnstileHandle {
  reset: () => void;
}

let scriptPromise: Promise<void> | null = null;
function loadScript(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.turnstile) return Promise.resolve();
  if (!scriptPromise) {
    scriptPromise = new Promise<void>((resolve) => {
      const s = document.createElement("script");
      s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      s.async = true;
      s.defer = true;
      s.onload = () => resolve();
      document.head.appendChild(s);
    });
  }
  return scriptPromise;
}

const Turnstile = forwardRef<TurnstileHandle, { onVerify: (token: string) => void }>(
  function Turnstile({ onVerify }, ref) {
    const boxRef = useRef<HTMLDivElement>(null);
    const widgetId = useRef<string | null>(null);
    // keep the latest callback without re-running the render effect
    const cb = useRef(onVerify);
    cb.current = onVerify;

    useImperativeHandle(ref, () => ({
      reset() {
        cb.current(""); // clear the stale token immediately
        if (widgetId.current && window.turnstile) {
          try {
            window.turnstile.reset(widgetId.current); // re-runs challenge -> fresh token
          } catch {}
        }
      },
    }));

    useEffect(() => {
      if (!SITE_KEY) {
        cb.current(""); // disabled in dev — unblock the flow
        return;
      }
      let cancelled = false;
      loadScript().then(() => {
        if (cancelled || !boxRef.current || !window.turnstile) return;
        widgetId.current = window.turnstile.render(boxRef.current, {
          sitekey: SITE_KEY,
          callback: (token: string) => cb.current(token),
          "expired-callback": () => cb.current(""),
          "error-callback": () => cb.current(""),
        });
      });
      return () => {
        cancelled = true;
        if (widgetId.current && window.turnstile) {
          try {
            window.turnstile.remove(widgetId.current);
          } catch {}
        }
      };
    }, []);

    if (!SITE_KEY) return null;
    return <div ref={boxRef} style={{ marginTop: 12 }} />;
  }
);

export default Turnstile;
