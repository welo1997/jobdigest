import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";

export const metadata = { title: "Terms of Service — JobDigest" };

export default function Terms() {
  return (
    <>
      <Nav />
      <main>
        <div className="wrap page-head">
          <span className="label">Effective 20 July 2026</span>
          <h1>Terms of Service</h1>
          <p>The agreement between you and JobDigest. Please read it before subscribing.</p>
        </div>
        <div className="wrap prose">
          <h2>1. The service</h2>
          <p>JobDigest sends you a once-daily email with a curated, ranked shortlist of job postings
            based on the preferences you provide. The service is operated by Vojtěch Cizinský
            (Czech Republic, EU). By subscribing you agree to these Terms and to our{" "}
            <Link href="/privacy">Privacy Policy</Link>.</p>

          <h2>2. Eligibility</h2>
          <p>You must be at least 16 years old and provide an email address you control. You subscribe
            using double opt-in — no account or password is required; you manage everything through the
            secure link in each email.</p>

          <h2>3. Where the jobs come from</h2>
          <p>Postings are gathered from public sources and third-party job boards and are provided for
            your convenience. We rank them for relevance, but we do <b>not</b> guarantee that any
            listing is accurate, current, still open, or free of errors, and we are not the employer or
            a recruiter. <b>Always verify the details on the employer&apos;s own site before
            applying.</b> JobDigest is not a party to, and takes no responsibility for, any application,
            interview, or hiring decision.</p>

          <h2>4. Acceptable use</h2>
          <p>The digest is for your personal job search. You agree not to scrape, resell, republish, or
            redistribute its contents, not to use the service unlawfully, and not to attempt to disrupt
            or gain unauthorised access to it. We may pause or end a subscription that abuses the
            service or these Terms.</p>

          <h2>4a. Improving the service</h2>
          <p>We record anonymous, cookieless statistics about how the website is used — for example
            which pages are viewed and where the signup form is abandoned — purely to find and fix
            what&apos;s confusing or broken. This never includes your IP address, your CV content, or
            anything that identifies you personally, and we honour your browser&apos;s
            &quot;Do Not Track&quot; setting. Full detail, and how to object, is in our{" "}
            <Link href="/privacy/">Privacy Policy</Link>.</p>

          <h2>5. Free service</h2>
          <p>JobDigest is currently free to use. We may introduce paid features in the future; if we
            do, your existing free digest and preferences continue unless you choose to upgrade. We may
            change, suspend, or discontinue any part of the service at any time.</p>

          <h2>6. No warranties</h2>
          <p>The service is provided &quot;as is&quot; and &quot;as available&quot;, without warranties
            of any kind, whether express or implied, including fitness for a particular purpose. We do
            not warrant that the service will be uninterrupted, timely, or error-free.</p>

          <h2>7. Limitation of liability</h2>
          <p>To the fullest extent permitted by law, JobDigest and its operator will not be liable for
            any indirect, incidental, or consequential damages, or for any loss arising from your use
            of — or inability to use — the service, including reliance on any job listing. Nothing in
            these Terms limits liability that cannot be limited under applicable law.</p>

          <h2>8. Changes to these Terms</h2>
          <p>We may update these Terms from time to time. If we make material changes we&apos;ll update
            the effective date above and, where appropriate, notify subscribers by email. Continued use
            after a change means you accept the updated Terms.</p>

          <h2>9. Governing law</h2>
          <p>These Terms are governed by the laws of the Czech Republic and the European Union.
            Questions? Email <b>hello@jobdigest.eu</b>.</p>

          <p style={{ marginTop: 24 }}><Link href="/">← Back to home</Link></p>
        </div>
      </main>
      <Footer />
    </>
  );
}
