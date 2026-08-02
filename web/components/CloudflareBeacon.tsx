/**
 * Cloudflare Web Analytics — cookieless, no personal data, no consent banner required.
 *
 * Only rendered when NEXT_PUBLIC_CF_BEACON_TOKEN is set at build time, so local dev and any
 * self-hosted copy stay completely free of third-party requests. Extracted from the old single
 * root layout when the site gained per-locale layouts, so both of them measure the same way.
 */
const CF_BEACON_TOKEN = process.env.NEXT_PUBLIC_CF_BEACON_TOKEN;

export default function CloudflareBeacon() {
  if (!CF_BEACON_TOKEN) return null;
  return (
    <script
      defer
      src="https://static.cloudflareinsights.com/beacon.min.js"
      data-cf-beacon={JSON.stringify({ token: CF_BEACON_TOKEN })}
    />
  );
}
