"use client";

import { useEffect } from "react";
import { DEFAULT_LOCALE, LOCALES, LOCALE_NAME, isLocale, localeHref, negotiate } from "@/i18n/config";
import en from "@/i18n/messages/en";

/**
 * The bare `/`, which sends a visitor to their language.
 *
 * Deliberately not a blank redirect shim. It renders the full list of languages as real links,
 * which is what a crawler follows to discover all eight locales and what a visitor with
 * JavaScript off gets to click. The redirect is an enhancement on top of working markup, not
 * the only way through.
 *
 * `location.replace` rather than `push`: a redirect left in the history stack means the back
 * button lands here and immediately bounces forward again, trapping the visitor on the site.
 */
export default function LanguageGate() {
  useEffect(() => {
    let target = DEFAULT_LOCALE;
    try {
      // An explicit choice from the switcher outranks the browser's list — someone who picked
      // English on a German laptop meant it, and should not be re-negotiated every visit.
      const saved = localStorage.getItem("jd_lang");
      target = isLocale(saved) ? saved : negotiate(navigator.languages ?? [navigator.language]);
    } catch {
      target = negotiate([navigator.language]);
    }
    // Carry the query and fragment across. The OAuth callback lands here as
    // `/?google=signup`, and dropping that flag would leave the wizard in ordinary signup
    // mode with the visitor's pre-OAuth picks stranded in sessionStorage — a silent failure
    // of the whole Google path, on a page whose only job is to forward.
    const { search, hash } = window.location;
    window.location.replace(localeHref(target, `/${search}${hash}`));
  }, []);

  return (
    <div className="state-wrap">
      <div className="state-card">
        <h1>{en.root.title}</h1>
        <p>{en.root.body}</p>
        <p style={{ marginTop: 20, display: "flex", flexWrap: "wrap", gap: 12, justifyContent: "center" }}>
          {LOCALES.map((l) => (
            <a key={l} href={localeHref(l, "/")} hrefLang={l} lang={l}>
              {LOCALE_NAME[l]}
            </a>
          ))}
        </p>
      </div>
    </div>
  );
}
