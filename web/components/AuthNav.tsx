"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getSession, logout } from "@/lib/api";
import { useI18n } from "@/i18n/context";

/**
 * The auth affordance in the top bar. JobDigest is passwordless, so "logging in" means
 * clicking a magic link — but once you have, a session cookie keeps you signed in, and this
 * reflects that:
 *   - signed out  -> "Log in" (the /manage email → magic-link flow);
 *   - signed in   -> a link to your preferences + a "Log out" button.
 * It resolves the session on mount via GET /session; until then it renders nothing so the bar
 * doesn't flash the wrong control.
 */
export default function AuthNav() {
  const { t, href } = useI18n();
  const [state, setState] = useState<"loading" | "in" | "out">("loading");

  useEffect(() => {
    let alive = true;
    const check = () =>
      getSession()
        .then(() => alive && setState("in"))
        .catch(() => alive && setState("out"));
    check();
    // A page that establishes a session from a magic-link token (or logs out) fires this, so
    // the nav re-checks instead of showing a stale control from its first-load probe.
    const onChange = () => check();
    window.addEventListener("jd-auth-changed", onChange);
    return () => { alive = false; window.removeEventListener("jd-auth-changed", onChange); };
  }, []);

  if (state === "loading") return null;

  if (state === "in") {
    const doLogout = async () => {
      try { await logout(); } catch { /* best-effort cookie clear */ }
      // Back to this locale's home, not "/" — that would bounce through the language gate.
      if (typeof window !== "undefined") window.location.href = href("/");
    };
    return (
      <>
        {/* No token on these: a signed-in browser rides the session cookie, and both pages
            fall back to their own `?token=` only when a magic link supplied one. */}
        <Link className="nav-link" href={href("/matches")}>{t.nav.myMatches}</Link>
        <Link className="nav-link" href={href("/preferences")}>{t.nav.myPreferences}</Link>
        <button type="button" className="nav-link" onClick={doLogout}>{t.nav.logOut}</button>
      </>
    );
  }

  return <Link className="nav-link" href={href("/manage")}>{t.nav.logIn}</Link>;
}
