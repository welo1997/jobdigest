"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";

function Inner() {
  const email = useSearchParams().get("email") || "your inbox";
  return (
    <div className="state-wrap">
      <div className="state-card">
        <div className="ic">✉</div>
        <h1>Check your inbox</h1>
        <p>
          We sent a confirmation link to <b>{email}</b>. Click it and your first digest arrives
          tomorrow morning at 7:00.
        </p>
        <p className="label" style={{ marginTop: 14 }}>Double opt-in · GDPR consent</p>
        <p style={{ marginTop: 18 }}>
          <Link href="/">← Back to home</Link>
        </p>
      </div>
    </div>
  );
}

export default function CheckInbox() {
  return (
    <>
      <Nav />
      <main>
        <Suspense fallback={<div className="state-wrap"><div className="state-card"><h1>Check your inbox</h1></div></div>}>
          <Inner />
        </Suspense>
      </main>
      <Footer />
    </>
  );
}
