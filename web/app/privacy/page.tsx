import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";

export const metadata = { title: "Privacy Policy — JobDigest" };

export default function Privacy() {
  return (
    <>
      <Nav />
      <main>
        <div className="wrap page-head">
          <span className="label">Effective 18 July 2026</span>
          <h1>Privacy Policy</h1>
          <p>How JobDigest collects, uses, and protects your data. Plain language, no surprises.</p>
        </div>
        <div className="wrap prose">
          <h2>1. Who we are</h2>
          <p>JobDigest (&quot;we&quot;, &quot;us&quot;) is a daily job-digest email service operated by
            Vojtěch Cizinský, based in the Czech Republic (EU). We are the data controller for the
            personal data described here. Contact us any time at <b>hello@jobdigest.eu</b>; our postal
            address is available on request.</p>

          <h2>2. What we collect</h2>
          <p>We deliberately collect as little as possible:</p>
          <ul>
            <li><b>Your email address</b> — so we can send your digest and confirmation.</li>
            <li><b>Your search preferences</b> — roles, skills/keywords, region, type of work, and
              frequency, as you enter them.</li>
            <li><b>CV-derived signals</b> — if you upload a CV (optional), see section 4.</li>
            <li><b>Delivery records</b> — which jobs we&apos;ve already emailed you, so we never send
              the same posting twice, plus timestamps of sends and confirmation.</li>
          </ul>
          <p>We do <b>not</b> collect names, phone numbers, payment details, or track you across other
            websites. We do not buy or sell personal data.</p>

          <h2>3. Legal basis &amp; how we use it</h2>
          <p>We process your data on the basis of your <b>consent</b> (given via double opt-in when you
            confirm your subscription) to send you the daily job digest and the transactional emails
            required to manage it. You can withdraw consent at any time by unsubscribing — one click,
            from any email — which stops all sending immediately.</p>

          <h2>4. Your CV — parse &amp; discard</h2>
          <p>If you choose to upload a CV, we read it <b>once, in memory</b>, to detect relevant skills
            and roles, and then <b>delete the file immediately</b>. We never store the document itself.
            Only the derived signals — for example &quot;knows dbt, Snowflake; ~4 years&apos;
            experience&quot; — are saved, and only to set up better matches. Your CV content is never
            shared with anyone.</p>

          <h2>5. Who processes your data</h2>
          <p>We use a small number of EU-based service providers (processors) strictly to run the
            service. Your data stays within the EU:</p>
          <ul>
            <li><b>Hetzner</b> (Germany) — server hosting and database.</li>
            <li><b>Resend</b> (EU region) — sending our emails.</li>
            <li><b>Cloudflare</b> — DNS, HTTPS, and bot protection (Turnstile) for the website.</li>
          </ul>

          <h2>6. Cookies &amp; similar</h2>
          <p>The site sets no advertising or analytics cookies. Cloudflare Turnstile (our
            no-CAPTCHA bot check) may set a strictly necessary token to verify you&apos;re human when
            you sign up. That&apos;s it.</p>

          <h2>7. How long we keep it</h2>
          <p>We keep your subscription data for as long as you&apos;re subscribed. When you
            unsubscribe, we remove your active profile and retain only your email address on a
            suppression list — the minimum needed to make sure we never email you again.</p>

          <h2>8. Your rights</h2>
          <p>Under the GDPR you have the right to access, correct, delete, restrict, or port your data,
            and to object to or withdraw consent for processing. The secure link in every email lets
            you edit your preferences or unsubscribe with no password. For any other request, email
            <b> hello@jobdigest.eu</b> and we&apos;ll respond promptly. You also have the right to lodge
            a complaint with your data protection authority (in the Czech Republic, the Úřad pro
            ochranu osobních údajů, uoou.gov.cz).</p>

          <h2>9. Security</h2>
          <p>All traffic is encrypted over HTTPS, management links use unguessable tokens rather than
            passwords, and access to our systems is restricted. No system is perfectly secure, but we
            take reasonable measures to protect your data.</p>

          <h2>10. Changes</h2>
          <p>If we materially change this policy we&apos;ll update the effective date above and, where
            appropriate, notify subscribers by email.</p>

          <p style={{ marginTop: 24 }}><Link href="/">← Back to home</Link></p>
        </div>
      </main>
      <Footer />
    </>
  );
}
