import { DEFAULT_LOCALE, type Locale } from "./config";
import type { Messages } from "./schema";
import en from "./messages/en";
import cs from "./messages/cs";
import de from "./messages/de";
import sk from "./messages/sk";
import pl from "./messages/pl";
import es from "./messages/es";
import fr from "./messages/fr";
import it from "./messages/it";

const CATALOGUES: Record<Locale, Messages> = { en, cs, de, sk, pl, es, fr, it };

/**
 * The catalogue for a locale.
 *
 * Called from the `[locale]` layout, which is a server component, so only the requested
 * language ends up in that page's payload — the other seven are tree-shaken out of the client
 * bundle rather than shipped to every visitor.
 */
export function getMessages(locale: Locale): Messages {
  return CATALOGUES[locale] ?? CATALOGUES[DEFAULT_LOCALE];
}

export type { Messages };
