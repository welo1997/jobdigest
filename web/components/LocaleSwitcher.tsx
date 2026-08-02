"use client";

import { usePathname } from "next/navigation";
import {
  LEGAL_LOCALES, LOCALES, LOCALE_NAME, isLocale, localeFromPath, swapLocale,
} from "@/i18n/config";
import { useI18n } from "@/i18n/context";

/**
 * Language picker in the top bar.
 *
 * Switches to the *same page* in the new language rather than sending everyone home — someone
 * reading their matches who wants them in Czech should stay on their matches. `swapLocale`
 * rewrites only the first path segment.
 *
 * A full navigation, not `router.push`: the two halves of the site have separate root layouts
 * and each locale is its own exported document, so the `<html lang>` has to be re-fetched, not
 * swapped in place. The choice is remembered so the bare `/` honours it next time.
 */
export default function LocaleSwitcher() {
  const { locale, t } = useI18n();
  const pathname = usePathname() || "/";

  // The same top bar is used by the untranslated pages (/privacy, /terms, /v2). Offering a
  // language menu there would build a URL like /cs/privacy/ that was never exported — a 404
  // reached by using the control as intended. No locale segment, no switcher.
  if (!localeFromPath(pathname)) return null;

  // The legal pages exist in fewer languages than the rest of the site, so on those routes the
  // menu offers only the languages that were actually exported. Listing all eight would make
  // the control itself the way to reach a 404 — the same trap as showing it on an untranslated
  // page, one level down.
  const page = pathname.split("/").filter(Boolean)[1];
  const isLegal = page === "privacy" || page === "terms";
  const offered = isLegal ? LEGAL_LOCALES : LOCALES;

  const onChange = (next: string) => {
    if (!isLocale(next) || next === locale) return;
    try { localStorage.setItem("jd_lang", next); } catch { /* private mode — the URL still wins */ }
    window.location.href = swapLocale(pathname, next);
  };

  return (
    <select
      className="langsel"
      aria-label={t.nav.languageAria}
      value={locale}
      onChange={(e) => onChange(e.target.value)}
    >
      {offered.map((l) => (
        <option key={l} value={l} lang={l}>
          {LOCALE_NAME[l]}
        </option>
      ))}
    </select>
  );
}
