"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";
import GoogleButton from "@/components/GoogleButton";
import { GOOGLE_AUTH_ENABLED, requestManageLink } from "@/lib/api";

// "Lost your link?" — the passwordless way back into an existing subscription. We ask only for
// the email, then POST /manage-link, which mails the private settings link to that address if
// (and only if) it's subscribed. The API returns the SAME message either way, so this page can
// never be used to check whether an address is a subscriber — we just show the generic result.

export default function ManagePage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A Google sign-in that couldn't log you in redirects back here with ?google=nosub|error.
  // Read it from the URL directly (not useSearchParams) to avoid a Suspense boundary in this
  // otherwise-static page.
  const [googleNote, setGoogleNote] = useState<string | null>(null);
  useEffect(() => {
    const g = new URLSearchParams(window.location.search).get("google");
    if (g === "nosub")
      setGoogleNote("That Google account isn't subscribed yet. Sign up first, then you can sign in with Google.");
    else if (g === "suppressed")
      setGoogleNote("That address previously unsubscribed, so we can't re-subscribe it automatically. Contact us if you'd like to return.");
    else if (g === "error")
      setGoogleNote("Google sign-in didn't complete. Please try again, or use your email link below.");
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await requestManageLink(email.trim());
      setSent(true); // generic success regardless of whether the address exists
    } catch {
      // Only network/500-class failures land here — never "not subscribed" (that's a success).
      setError("Something went wrong. Please try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Nav />
      <main>
        <div className="state-wrap">
          <div className="state-card">
            {sent ? (
              <>
                <div className="ic">✉</div>
                <h1>Check your inbox</h1>
                <p>
                  If <b>{email.trim()}</b> has a JobDigest subscription, we&apos;ve just emailed
                  its private settings link. Open it to edit your preferences, pause, or
                  unsubscribe — no password needed.
                </p>
                <p className="label" style={{ marginTop: 14 }}>
                  Didn&apos;t get it? Check spam, or try again in a few minutes.
                </p>
                <p style={{ marginTop: 18 }}>
                  <Link href="/">← Back to home</Link>
                </p>
              </>
            ) : (
              <>
                <div className="ic">🔑</div>
                <h1>Log in to JobDigest</h1>
                <p style={{ marginBottom: 20 }}>
                  No passwords here. Enter your address and we&apos;ll email you a secure link —
                  click it and you&apos;re signed in, and you&apos;ll stay signed in on this device.
                </p>
                {googleNote && (
                  <p style={{ color: "var(--danger, #c0392b)", fontSize: "var(--fs-sm)", marginBottom: 16, textAlign: "left" }}>
                    {googleNote}
                  </p>
                )}
                {GOOGLE_AUTH_ENABLED && (
                  <>
                    <GoogleButton label="Sign in with Google" />
                    <div className="or-divider"><span>or</span></div>
                  </>
                )}
                <form onSubmit={submit} style={{ textAlign: "left" }}>
                  <div className="field">
                    <label htmlFor="jd-email">Your email address</label>
                    <input
                      id="jd-email"
                      type="email"
                      required
                      autoComplete="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@example.com"
                    />
                  </div>
                  {error && (
                    <p style={{ color: "var(--danger, #c0392b)", fontSize: "var(--fs-sm)", marginBottom: 10 }}>
                      {error}
                    </p>
                  )}
                  <button className="btn block" type="submit" disabled={busy || !email.trim()}>
                    {busy ? "Sending…" : "Email me my settings link"}
                  </button>
                </form>
                <p className="label" style={{ marginTop: 16 }}>
                  We&apos;ll only ever send the link to your own inbox.
                </p>
              </>
            )}
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
