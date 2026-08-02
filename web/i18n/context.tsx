"use client";

/**
 * How a client component reaches the current language.
 *
 * The catalogue is handed down from the `[locale]` server layout as a plain prop, not imported
 * here — importing all eight would put every language in every visitor's JavaScript bundle to
 * render one of them. `Messages` is nothing but nested strings, so it crosses the server/client
 * boundary as ordinary serialisable data.
 */

import { createContext, Fragment, useContext, type ReactNode } from "react";
import { COUNTRIES } from "@/lib/geo";
import { DEFAULT_LOCALE, fmt, localeHref, plural, type Locale, type PluralForms } from "./config";
import type { Messages } from "./schema";
import en from "./messages/en";

interface I18n {
  locale: Locale;
  /** The catalogue. Read it directly: `t.landing.q1`. */
  t: Messages;
  /** Locale-prefixed internal link: `href("/preferences")` -> "/cs/preferences/". */
  href: (path?: string) => string;
  /** Plural-correct count, with `{n}` already substituted. */
  count: (n: number, forms: PluralForms, vars?: Record<string, string | number>) => string;
  /** Localised country name, falling back to the English one in lib/geo.ts. */
  country: (code: string) => string;
}

// The default exists only so a component rendered outside the provider (a test, a stray
// import) degrades to English instead of throwing. Nothing in the app relies on it.
const Ctx = createContext<I18n>(build(DEFAULT_LOCALE, en));

function build(locale: Locale, t: Messages): I18n {
  return {
    locale,
    t,
    href: (path = "/") => localeHref(locale, path),
    count: (n, forms, vars) => fmt(plural(locale, n, forms), { n, ...vars }),
    country: (code) => t.geo.countries[code] ?? COUNTRIES[code] ?? code,
  };
}

export function I18nProvider(
  { locale, messages, children }: { locale: Locale; messages: Messages; children: ReactNode }
) {
  return <Ctx.Provider value={build(locale, messages)}>{children}</Ctx.Provider>;
}

export function useI18n(): I18n {
  return useContext(Ctx);
}

/**
 * Substitute React nodes into a translated sentence at its `{0}`, `{1}`, … holes.
 *
 * This is what lets a message that wraps something in markup — a bold address, a link to the
 * privacy policy — stay a single string in the catalogue. The alternative, a `before`/`after`
 * pair, silently pins every language to English word order, and several of the languages here
 * do not put the inserted element where English does.
 */
export function rich(template: string, nodes: ReactNode[]): ReactNode {
  return template.split(/(\{\d\})/).map((chunk, i) => {
    const hole = /^\{(\d)\}$/.exec(chunk);
    return <Fragment key={i}>{hole ? nodes[Number(hole[1])] : chunk}</Fragment>;
  });
}
