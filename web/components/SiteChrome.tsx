"use client";

import Link from "next/link";
import ThemeToggle from "./ThemeToggle";
import AuthNav from "./AuthNav";
import LocaleSwitcher from "./LocaleSwitcher";
import { useI18n } from "@/i18n/context";
import { legalHref } from "@/i18n/config";
import { useNavActive } from "@/lib/useNavActive";

/**
 * Shared top bar (dawn gradient + brand + language + theme toggle) used on every translated
 * page. Now a client component, because the language it renders in comes from the I18n context
 * rather than from the module scope.
 *
 * The brand link goes to the locale root, not `/` — sending someone back through the language
 * negotiator from inside a translated page would be a redirect they did not ask for.
 */
export function Nav() {
  const { t, href } = useI18n();
  const active = useNavActive();
  return (
    <>
      <div className="dawnbar" />
      <header className="nav">
        <div className="wrap">
          <Link className="brand" href={href("/")}>
            <span className="sun" aria-hidden="true" />
            Job<em>Digest</em>
          </Link>
          <span className="nav-actions">
            {/* The public feed, offered to everyone including signed-out visitors — it is the
                free tier's front door, so it sits before the account links rather than among
                them. */}
            <Link className={`nav-link${active(href("/jobs"))}`}
              aria-current={active(href("/jobs")) ? "page" : undefined}
              href={href("/jobs")}>
              {t.jobs.navLink}
            </Link>
            <AuthNav />
            <LocaleSwitcher />
            <ThemeToggle />
          </span>
        </div>
      </header>
    </>
  );
}

export function Footer() {
  const { t, href, locale } = useI18n();
  return (
    <footer className="footer">
      <div className="wrap">
        <span className="brand" style={{ fontSize: "1.05rem" }}>
          <span className="sun" style={{ width: 16, height: 16 }} />
          Job<em>Digest</em>
        </span>
        <span>© 2026 · {t.footer.madeInEu}</span>
        <span className="links">
          <Link href={href("/manage")}>{t.footer.manage}</Link>
          {/* The legal pages exist in fewer languages than the site does, so these go through
              `legalHref`, which falls back to English rather than linking a reader of one of
              the other six at a page that was never exported. */}
          <Link href={legalHref(locale, "privacy")}>{t.footer.privacy}</Link>
          <Link href={legalHref(locale, "terms")}>{t.footer.terms}</Link>
        </span>
      </div>
    </footer>
  );
}
