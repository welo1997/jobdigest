import type { Metadata } from "next";
import "../globals.css";
import CloudflareBeacon from "@/components/CloudflareBeacon";
import { DEFAULT_LOCALE, LOCALES } from "@/i18n/config";

/**
 * Root layout for the routes that are not translated: the privacy policy, the terms, the
 * unlinked `/v2` wizard, and the `/` language negotiator.
 *
 * Privacy and terms stay in one language on purpose. CLAUDE.md treats the privacy policy as a
 * specification — it makes concrete promises the code has to keep — and a translated copy is a
 * second specification that can drift out of step with the first without anything failing.
 * Translating them means committing to change eight files every time the product changes what
 * it does with data, and that commitment should be made deliberately, not as a side effect of
 * adding languages to the marketing pages.
 *
 * `app/(site)/[locale]/layout.tsx` is the other root layout. Neither is `app/layout.tsx`, which
 * no longer exists — that is what lets each half declare its own `<html lang>`.
 */

/**
 * The `/` redirect, inlined so it runs *before* the language gate is painted.
 *
 * `app/(plain)/page.tsx` does the same negotiation in a `useEffect`, which cannot fire until
 * React has hydrated — so the whole JS bundle had to download and execute before anyone was
 * sent anywhere, and "Choose your language" flashed on screen for about a second on every
 * visit to the bare domain. A synchronous script placed before the markup redirects during
 * parsing: the card below it is never reached, so there is nothing to flash.
 *
 * The effect in `page.tsx` stays as the fallback for the case where this throws. Both are
 * enhancements on top of eight real `<a>` links, which is what a crawler follows and what a
 * visitor with JavaScript off clicks — that has not changed.
 *
 * **The locale list is interpolated from `i18n/config`, never typed out here.** A ninth
 * language must not need this file edited too. Only the matching rule is restated, and it is
 * the same one `negotiate` documents: match the primary subtag, so `de-AT` finds `de` rather
 * than falling through to English.
 *
 * Guarded on the exact path because this layout also serves /privacy, /terms and /v2.
 */
const LOCALE_REDIRECT = `(function(){try{
if(location.pathname!=="/")return;
var L=${JSON.stringify(LOCALES)},t=null;
try{var s=localStorage.getItem("jd_lang");if(L.indexOf(s)>-1)t=s}catch(e){}
if(!t){var n=navigator,g=(n.languages&&n.languages.length?n.languages:[n.language])||[];
for(var i=0;i<g.length;i++){var p=String(g[i]||"").toLowerCase().split("-")[0];
if(L.indexOf(p)>-1){t=p;break}}}
location.replace("/"+(t||${JSON.stringify(DEFAULT_LOCALE)})+"/"+location.search+location.hash);
}catch(e){}})();`;

export const metadata: Metadata = {
  title: "JobDigest — jobs that fit you, every morning",
  description:
    "One short email a day with a curated, ranked shortlist of jobs that fit you. Tech/CZ+EU first. Free to start.",
};

export default function PlainLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* First child on purpose: it runs while the parser is still above the markup it is
            meant to replace, which is the whole reason there is no flash. */}
        <script dangerouslySetInnerHTML={{ __html: LOCALE_REDIRECT }} />
        {children}
        <CloudflareBeacon />
      </body>
    </html>
  );
}
