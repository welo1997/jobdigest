"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";
import GoogleButton from "@/components/GoogleButton";
import { GOOGLE_AUTH_ENABLED, requestManageLink } from "@/lib/api";
import { rich, useI18n } from "@/i18n/context";

// "Lost your link?" — the passwordless way back into an existing subscription. We ask only for
// the email, then POST /manage-link, which mails the private settings link to that address if
// (and only if) it's subscribed. The API returns the SAME message either way, so this page can
// never be used to check whether an address is a subscriber — we just show the generic result.

export default function ManagePage() {
  const { t, href } = useI18n();
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
    if (g === "nosub") setGoogleNote(t.manage.googleNoSub);
    else if (g === "suppressed") setGoogleNote(t.manage.googleSuppressed);
    else if (g === "error") setGoogleNote(t.manage.googleError);
  }, [t]);

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
      setError(t.manage.error);
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
                <h1>{t.manage.sentTitle}</h1>
                <p>{rich(t.manage.sentBody, [<b key="e">{email.trim()}</b>])}</p>
                <p className="label" style={{ marginTop: 14 }}>{t.manage.didntGet}</p>
                <p style={{ marginTop: 18 }}>
                  <Link href={href("/")}>{t.common.backToHome}</Link>
                </p>
              </>
            ) : (
              <>
                <div className="ic">🔑</div>
                <h1>{t.manage.title}</h1>
                <p style={{ marginBottom: 20 }}>{t.manage.body}</p>
                {googleNote && (
                  <p style={{ color: "var(--danger, #c0392b)", fontSize: "var(--fs-sm)", marginBottom: 16, textAlign: "left" }}>
                    {googleNote}
                  </p>
                )}
                {GOOGLE_AUTH_ENABLED && (
                  <>
                    <GoogleButton label={t.manage.googleSignin} />
                    <div className="or-divider"><span>{t.common.or}</span></div>
                  </>
                )}
                <form onSubmit={submit} style={{ textAlign: "left" }}>
                  <div className="field">
                    <label htmlFor="jd-email">{t.manage.emailLabel}</label>
                    <input
                      id="jd-email"
                      type="email"
                      required
                      autoComplete="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder={t.landing.emailPlaceholder}
                    />
                  </div>
                  {error && (
                    <p style={{ color: "var(--danger, #c0392b)", fontSize: "var(--fs-sm)", marginBottom: 10 }}>
                      {error}
                    </p>
                  )}
                  <button className="btn block" type="submit" disabled={busy || !email.trim()}>
                    {busy ? t.manage.sending : t.manage.submit}
                  </button>
                </form>
                <p className="label" style={{ marginTop: 16 }}>{t.manage.onlyOwnInbox}</p>
              </>
            )}
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
