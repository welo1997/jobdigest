import type { Metadata } from "next";
import { LOCALES } from "@/i18n/config";

/**
 * A segment layout that exists only to give `/[locale]/jobs/` its own canonical.
 *
 * `jobs/page.tsx` is a client component (`"use client"`), so it cannot export `generateMetadata`
 * itself. Without this, the page inherited the parent layout's alternates and — before that
 * blanket canonical was removed — claimed to be a duplicate of the locale root. A self-referencing
 * canonical also collapses the `?q=…&country=…` query-string variants the search builds into one
 * indexable URL, instead of letting each search look like a separate page to a crawler.
 */
export async function generateMetadata(
  { params }: { params: Promise<{ locale: string }> }
): Promise<Metadata> {
  const { locale } = await params;
  return {
    alternates: {
      canonical: `/${locale}/jobs/`,
      languages: {
        ...Object.fromEntries(LOCALES.map((l) => [l, `/${l}/jobs/`])),
        "x-default": "/en/jobs/",
      },
    },
  };
}

export default function JobsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
