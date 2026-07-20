import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "JobDigest — jobs that fit you, every morning",
  description:
    "One short email a day with a curated, ranked shortlist of jobs that fit you. Tech/CZ+EU first. Free to start.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
