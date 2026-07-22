/**
 * Scheme guard for URLs that come from third-party job feeds we do not control.
 *
 * Parity with the backend's `safe_url` in `service/digest.py`: the digest email already
 * refuses to put a non-http(s) posting URL into an `href`. The /matches page renders the
 * same feed URLs as clickable links, and React does NOT block `javascript:` or `data:`
 * hrefs — it renders them as active links — so a feed serving `javascript:...` would ship
 * an executable link on jobdigest.eu. Keep the two guards in sync.
 *
 * Returns the URL only if it is a plain http(s) link, else "" (falsy) so callers can
 * choose to render no link at all rather than a dead anchor.
 */
export function safeHref(url: string | null | undefined): string {
  const u = (url ?? "").trim();
  const lower = u.toLowerCase();
  return lower.startsWith("http://") || lower.startsWith("https://") ? u : "";
}
