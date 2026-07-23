import Link from "next/link";
import ThemeToggle from "./ThemeToggle";

/** Shared top bar (dawn gradient + brand + theme toggle) used on every page. */
export function Nav() {
  return (
    <>
      <div className="dawnbar" />
      <header className="nav">
        <div className="wrap">
          <Link className="brand" href="/">
            <span className="sun" aria-hidden="true" />
            Job<em>Digest</em>
          </Link>
          <span className="nav-actions">
            <Link className="nav-link" href="/manage">Manage subscription</Link>
            <ThemeToggle />
          </span>
        </div>
      </header>
    </>
  );
}

export function Footer() {
  return (
    <footer className="footer">
      <div className="wrap">
        <span className="brand" style={{ fontSize: "1.05rem" }}>
          <span className="sun" style={{ width: 16, height: 16 }} />
          Job<em>Digest</em>
        </span>
        <span>© 2026 · Made in the EU</span>
        <span className="links">
          <Link href="/manage">Manage subscription</Link>
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms</Link>
        </span>
      </div>
    </footer>
  );
}
