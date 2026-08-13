import Link from "next/link";
import { legalHref, localeHref } from "@/i18n/config";

/** English privacy — the authoritative text. Its Czech translation is
 *  `PrivacyCs.tsx` and the two must change in the same commit. */
export default function PrivacyEn() {
  return (
    <>
      <div className="wrap page-head">
        <span className="label">Effective 13 August 2026</span>
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
          <li><b>Your search preferences</b> — roles, skills/keywords, the countries and cities
            you want to work in, which work setups you&apos;ll take (on-site, hybrid, fully
            remote), which education requirements you&apos;ll accept and, if you choose to give
            it, what you studied, any industries you tell us you&apos;re interested in, how far
            afield a fully remote role may be, type of work, and
            frequency, as you enter them, and the language you read the site in — so your emails
              arrive in it too.</li>
          <li><b>CV-derived signals</b> — if you upload a CV (optional), see section 4.</li>
          <li><b>Delivery records</b> — which jobs we&apos;ve already emailed you, so we never send
            the same posting twice, plus timestamps of sends and confirmation.</li>
          <li><b>Jobs you hide</b> — if you hide a job from your matches page (because you
            already applied, or it isn&apos;t for you), we record that choice against your
            subscription so we stop listing and emailing it. You can see and undo every one of
            them on your hidden-jobs page.</li>
          <li><b>Privacy-preserving usage statistics</b> — which pages are viewed and which steps of the
            signup form are reached or fail, so we can fix what&apos;s confusing. These do not identify
            you; see section 6.</li>
        </ul>
        <p>We do <b>not</b> collect names, phone numbers, payment details, or track you across other
          websites. We do not buy or sell personal data.</p>

        <h2>3. Legal basis &amp; how we use it</h2>
        <p>We process your data on the basis of your <b>consent</b> — given either via double
          opt-in (you confirm a link we email you) or, if you sign up with Google, by Google
          verifying that you control the address — to send you the daily job digest and the
          transactional emails required to manage it. You can withdraw consent at any time by unsubscribing — one click,
          from any email — which stops all sending immediately.</p>
        <p>The usage statistics in section 6 are processed on the basis of our
          <b> legitimate interest</b> (Art. 6(1)(f) GDPR) in understanding which parts of our own
          website are confusing or broken, so we can improve them. They are designed not to identify
          you — no cookies, no IP addresses and no cross-site tracking — so the impact on your privacy
          is minimal, but you can object at any time (section 8), and we honour your browser&apos;s
          &quot;Do Not Track&quot; and Global Privacy Control settings automatically.</p>

        <h2>4. Your CV — parse &amp; discard</h2>
        <p>If you choose to upload a CV, we read it <b>once, in memory</b>, to detect relevant skills,
          roles and your highest level of education, and then <b>delete the file immediately</b>. We
          never store the document itself, and it is never sent to any third party. Only a short
          derived summary — for example &quot;Detected: data engineering, dbt, Snowflake · ~4 yrs ·
          master&apos;s in Economics&quot; — is saved. The education level we detect is only used to
          fill in the form for you, where you can change it before subscribing.</p>
        <p>That summary is part of your matching profile, so it is processed by our matching provider
          along with your other preferences (section 5). The document itself, and any detail in it we
          did not summarise — employers, dates, contact details, anything you wrote — is gone the
          moment the upload finishes and is never stored or transmitted anywhere.</p>

        <h2>5. Who processes your data</h2>
        <p>We use a small number of service providers (processors) strictly to run the service.
          Your subscription and your email address are stored only in the EU:</p>
        <ul>
          <li><b>Hetzner</b> (Germany) — server hosting and database. This is where your
            subscription lives.</li>
          <li><b>Resend</b> (EU region) — sending our emails.</li>
          <li><b>Cloudflare</b> — DNS, HTTPS, bot protection (Turnstile), and cookieless
            aggregate page-view analytics for the website.</li>
          <li><b>Google</b> (Google Ireland Ltd / Google LLC) — <i>only if</i> you choose
            &quot;Continue with Google&quot;. Google confirms your email address to us so we can
            log you in, or — for a new user — verify your address so you can sign up without a
            separate confirmation email. We <b>request only your email</b>, and we <b>store
            nothing extra</b> from Google — not your name, not a Google account id; we simply
            match the confirmed address to a subscription. If you never use it, Google
            processes nothing.</li>
        </ul>
        <p>Choosing which jobs fit you is done by an AI matching step, and that step runs outside
          the EU. Once a day we send it a file containing, for each subscriber:</p>
        <ul>
          <li>a random identifier — <b>not your email address, and not your name</b>;</li>
          <li>your stated preferences (roles, skills, the countries and cities you chose, the
            work setups you&apos;ll take, the education requirements you&apos;ll accept and what
            you studied if you gave it, any industries you&apos;re interested in, type of work);</li>
          <li>your CV-derived summary line, if you uploaded one (section 4);</li>
          <li>the public job listings we are asking it to rank.</li>
        </ul>
        <p>That file is transferred via <b>Google Drive</b> (Google Ireland Ltd / Google LLC) and read
          by <b>Anthropic</b> (Claude), both of which may process it in the United States. Transfers
          rely on the European Commission&apos;s Standard Contractual Clauses and the EU–US Data
          Privacy Framework. The file deliberately contains <b>no email address and no name</b>, so it
          does not directly identify you. It is <b>pseudonymised, not anonymous</b> — it remains
          personal data, and we treat it as such, which is why the transfer relies on the safeguards
          above; your address itself never leaves our EU database. The results come back as a list of
          job matches against that same random identifier.</p>

        <h2>6. Cookies &amp; usage statistics</h2>
        <p>The site sets <b>no advertising or analytics cookies</b>. It sets one
          <b> strictly necessary cookie</b>, and only if you log in: when you open a link we
          emailed you, we set a session cookie so you stay signed in on that device without
          re-opening the link each time. It contains a random token (no email, no personal
          data), is <b>not used for tracking</b>, and is cleared when you log out. We store only
          a one-way hash of that token, alongside your subscription, for at most <b>30 days</b> of
          inactivity — so a copy of our database never reveals a usable login. Cloudflare Turnstile
          (our no-CAPTCHA bot check) may also set a strictly necessary token to verify you&apos;re
          human when you sign up.</p>
        <p>To improve the site we record a small number of privacy-preserving events — for example
          &quot;landing page viewed&quot;, &quot;signup form started&quot;, or &quot;CV upload
          failed&quot;. This is done <b>without cookies</b>, and specifically:</p>
        <ul>
          <li>We <b>never store your IP address</b> — not in raw form, and not hashed. The only
            location we derive from your connection is your <b>country</b>. (The cities in your
            search preferences are different: you choose those yourself — see section 2.)</li>
          <li>We store a coarse browser family (e.g. &quot;firefox&quot;), never the full
            browser fingerprint or user-agent string.</li>
          <li>Events are grouped by a random identifier that lives only in your browser tab and is
            <b> erased when you close it</b>. It cannot link your visits together or follow you to
            any other site.</li>
          <li>No CV content, email address, or anything you type is ever included.</li>
          <li>If your browser sends &quot;Do Not Track&quot; or Global Privacy Control, we record
            nothing at all.</li>
        </ul>
        <p>We also use <b>Cloudflare Web Analytics</b> for aggregate page-view counts. It is
          cookieless by design and collects no personal data or cross-site identifiers.</p>

        <h2>7. How long we keep it</h2>
        <p>We keep your subscription data for as long as you&apos;re subscribed. When you
          unsubscribe, sending stops <b>immediately</b> and any active login sessions are
          ended. Your profile — preferences, CV summary, match history, and a technical
          record of whether each day&apos;s digest was produced for you (counts only, so we
          can tell when our matching has quietly failed someone) — is then
          <b> deleted within 30 days</b>, and its login sessions with it. We keep only your email address on a
          suppression list, indefinitely: it is the minimum needed to guarantee we never email you
          again, and deleting it would defeat that.</p>
        <p>The 30-day gap is deliberate rather than a delay for our benefit. Some email clients and
          corporate security scanners follow links automatically, which can trigger an unsubscribe
          you never clicked. The window means that is recoverable by mailing us, instead of silently
          destroying your settings. If you would rather we erase everything right away, ask us and
          we will (section 8).</p>
        <p>These usage events are deleted after <b>180 days</b>. Before deletion they
          are reduced to anonymous daily totals — for example &quot;42 signup forms started on 3
          March&quot; — which contain no individual records at all.</p>

        <h2>8. Your rights</h2>
        <p>Under the GDPR you have the right to access, correct, delete, restrict, or port your data,
          and to object to or withdraw consent for processing. The secure link in every email lets
          you edit your preferences or unsubscribe with no password; if you lose it, you can ask us
          to email that link to your address again from the <b>Manage subscription</b> page. For any
          other request, email
          <b> hello@jobdigest.eu</b> and we&apos;ll respond promptly. You also have the right to lodge
          a complaint with your data protection authority (in the Czech Republic, the Úřad pro
          ochranu osobních údajů, uoou.gov.cz).</p>

        <h2>9. Security</h2>
        <p>All traffic is encrypted over HTTPS, management links use unguessable tokens rather than
          passwords, and access to our systems is restricted. We hold no passwords, so there is no
          password database to breach. Database backups are encrypted before they leave the server,
          so an off-site copy is useless without a key we hold separately. No system is perfectly
          secure, but we take reasonable measures to protect your data.</p>

        <h2>10. Changes</h2>
        <p>If we materially change this policy we&apos;ll update the effective date above and, where
          appropriate, notify subscribers by email.</p>

        <p style={{ marginTop: 24 }}><Link href={localeHref("en", "/")}>← Back to home</Link></p>
      </div>
    </>
  );
}
