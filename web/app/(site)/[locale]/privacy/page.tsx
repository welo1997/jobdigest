import type { Metadata } from "next";
import { Nav, Footer } from "@/components/SiteChrome";
import PrivacyEn from "@/components/legal/PrivacyEn";
import PrivacyCs from "@/components/legal/PrivacyCs";
import { LEGAL_LOCALES, legalLocale, isLocale } from "@/i18n/config";

/**
 * The privacy policy, in the languages it actually exists in.
 *
 * `generateStaticParams` returns a *subset* of the locales the parent layout builds: only
 * `/en/privacy/` and `/cs/privacy/` are exported. That is deliberate — see `LEGAL_LOCALES`.
 * Readers of the other six reach English through `legalHref()`, which every link to this page
 * goes through, so no locale can produce a link to a page that was never built.
 */
export function generateStaticParams() {
  return LEGAL_LOCALES.map((locale) => ({ locale }));
}

const TITLE: Record<string, string> = {
  en: "Privacy Policy — JobDigest",
  cs: "Zásady ochrany osobních údajů — JobDigest",
};

export async function generateMetadata(
  { params }: { params: Promise<{ locale: string }> }
): Promise<Metadata> {
  const { locale } = await params;
  return {
    title: TITLE[locale] ?? TITLE.en,
    // Self-referencing, or this page inherits the layout's language alternates and canonicalises
    // to the locale root. The policy exists in en+cs only (LEGAL_LOCALES), so those are the only
    // hreflang alternates that resolve to a real page.
    alternates: {
      canonical: `/${locale}/privacy/`,
      languages: {
        ...Object.fromEntries(LEGAL_LOCALES.map((l) => [l, `/${l}/privacy/`])),
        "x-default": "/en/privacy/",
      },
    },
  };
}

export default async function PrivacyPage(
  { params }: { params: Promise<{ locale: string }> }
) {
  const { locale } = await params;
  const lang = legalLocale(isLocale(locale) ? locale : "en");
  return (
    <>
      <Nav />
      <main>{lang === "cs" ? <PrivacyCs /> : <PrivacyEn />}</main>
      <Footer />
    </>
  );
}
