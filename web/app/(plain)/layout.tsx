import type { Metadata } from "next";
import "../globals.css";
import CloudflareBeacon from "@/components/CloudflareBeacon";

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

export const metadata: Metadata = {
  title: "JobDigest — jobs that fit you, every morning",
  description:
    "One short email a day with a curated, ranked shortlist of jobs that fit you. Tech/CZ+EU first. Free to start.",
};

export default function PlainLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
        <CloudflareBeacon />
      </body>
    </html>
  );
}
