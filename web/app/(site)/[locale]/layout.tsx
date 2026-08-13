import type { Metadata } from "next";
import "../../globals.css";
import CloudflareBeacon from "@/components/CloudflareBeacon";
import { I18nProvider } from "@/i18n/context";
import { LOCALES, isLocale, type Locale } from "@/i18n/config";
import { getMessages } from "@/i18n";

/**
 * Root layout for every translated page.
 *
 * This file — not `app/layout.tsx`, which no longer exists — is what renders `<html>` for the
 * localised half of the site. That is the point of the split: `lang` is a real attribute in the
 * exported HTML rather than something JavaScript patches on after hydration, so a screen reader
 * gets the right pronunciation on first paint and a crawler gets the right language without
 * running the page. `app/(plain)/layout.tsx` is the second root layout, for the routes that
 * stay English.
 *
 * The catalogue is loaded here, on the server, and handed to the provider as a prop. Only the
 * requested language reaches the browser; the other seven never enter the bundle.
 */

export function generateStaticParams() {
  return LOCALES.map((locale) => ({ locale }));
}

export async function generateMetadata(
  { params }: { params: Promise<{ locale: string }> }
): Promise<Metadata> {
  const { locale } = await params;
  const t = getMessages(isLocale(locale) ? locale : "en");
  return {
    title: t.meta.title,
    description: t.meta.description,
    alternates: {
      // Deliberately NO blanket `canonical` here. This layout wraps every page under `/[locale]/`,
      // so a canonical set here is inherited by `/jobs/`, `/privacy/`, `/matches/` and the rest —
      // which told Google every page was a duplicate of the locale root and stopped them being
      // indexed on their own. Each page owns its canonical instead: the home self-canonicalises
      // (no tag needed), and `jobs/layout.tsx`, `privacy/page.tsx`, `terms/page.tsx` set theirs
      // explicitly. Only the language alternates belong at this level.
      //
      // Every language points at every other, plus an x-default at the negotiating root. Without
      // these a search engine treats the eight copies as duplicates and picks one on its own.
      languages: {
        ...Object.fromEntries(LOCALES.map((l) => [l, `/${l}/`])),
        "x-default": "/",
      },
    },
  };
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale: raw } = await params;
  const locale: Locale = isLocale(raw) ? raw : "en";
  const messages = getMessages(locale);

  return (
    <html lang={locale}>
      <body>
        <I18nProvider locale={locale} messages={messages}>
          {children}
        </I18nProvider>
        <CloudflareBeacon />
      </body>
    </html>
  );
}
