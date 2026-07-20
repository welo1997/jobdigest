import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "JobDigest — jobs that fit you, every morning",
  description:
    "One short email a day with a curated, ranked shortlist of jobs that fit you. Tech/CZ+EU first. Free to start.",
};

// Cloudflare Web Analytics — cookieless, no personal data, no consent banner required.
// Only rendered when NEXT_PUBLIC_CF_BEACON_TOKEN is set at build time, so local dev and
// any self-hosted copy stay completely free of third-party requests.
const CF_BEACON_TOKEN = process.env.NEXT_PUBLIC_CF_BEACON_TOKEN;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
        {CF_BEACON_TOKEN ? (
          <script
            defer
            src="https://static.cloudflareinsights.com/beacon.min.js"
            data-cf-beacon={JSON.stringify({ token: CF_BEACON_TOKEN })}
          />
        ) : null}
      </body>
    </html>
  );
}
