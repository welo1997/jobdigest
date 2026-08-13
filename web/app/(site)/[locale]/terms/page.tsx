import type { Metadata } from "next";
import { Nav, Footer } from "@/components/SiteChrome";
import TermsEn from "@/components/legal/TermsEn";
import TermsCs from "@/components/legal/TermsCs";
import { LEGAL_LOCALES, legalLocale, isLocale } from "@/i18n/config";

/** Terms of service. Same en+cs-only rule as the privacy policy — see that file. */
export function generateStaticParams() {
  return LEGAL_LOCALES.map((locale) => ({ locale }));
}

const TITLE: Record<string, string> = {
  en: "Terms of Service — JobDigest",
  cs: "Podmínky služby — JobDigest",
};

export async function generateMetadata(
  { params }: { params: Promise<{ locale: string }> }
): Promise<Metadata> {
  const { locale } = await params;
  return {
    title: TITLE[locale] ?? TITLE.en,
    // Self-referencing — same reasoning as the privacy page. en+cs only.
    alternates: {
      canonical: `/${locale}/terms/`,
      languages: {
        ...Object.fromEntries(LEGAL_LOCALES.map((l) => [l, `/${l}/terms/`])),
        "x-default": "/en/terms/",
      },
    },
  };
}

export default async function TermsPage(
  { params }: { params: Promise<{ locale: string }> }
) {
  const { locale } = await params;
  const lang = legalLocale(isLocale(locale) ? locale : "en");
  return (
    <>
      <Nav />
      <main>{lang === "cs" ? <TermsCs /> : <TermsEn />}</main>
      <Footer />
    </>
  );
}
