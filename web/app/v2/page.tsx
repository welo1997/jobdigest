"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";
import Turnstile, { turnstileEnabled, TurnstileHandle } from "@/components/Turnstile";
import { useToast } from "@/components/useToast";
import { cap } from "@/lib/preview";
import { CVSignals, parseCV, subscribe, SubscribePayload } from "@/lib/api";

const ROLE_OPTS = ["Product Manager", "Marketing", "Social Media", "Data Analyst", "Designer", "Software Engineer", "Data Engineer", "DevOps", "Finance"];
const SKILL_OPTS = ["SQL", "Figma", "Analytics", "Excel", "Python", "SEO", "Looker", "Roadmapping", "Power BI", "dbt"];
const REGION_OPTS = ["Czechia", "EU remote", "Worldwide", "Hybrid Prague"];
const WORK_OPTS = ["Full-time", "Freelance", "Part-time"];

// display role -> role_category (mirrors service.ingest role rules)
const ROLE_CAT: Record<string, string> = {
  "Data Engineer": "data_engineering", "Data Analyst": "data_analysis",
  "Software Engineer": "software_engineering", "DevOps": "devops_platform",
  "Product Manager": "product", "Designer": "design",
  "Social Media": "social_media",
  "Marketing": "other_tech_function", "Finance": "other_tech_function",
};
const REGION_CODES: Record<string, string[]> = {
  "Czechia": ["cz"], "EU remote": ["cz", "eu"], "Worldwide": ["cz", "eu", "worldwide"],
  "Hybrid Prague": ["cz"],
};
const CV_ROLE_LABEL: Record<string, string> = {
  data_engineering: "Data Engineer", data_analysis: "Data Analyst",
  machine_learning: "ML Engineer", software_engineering: "Software Engineer",
  devops_platform: "DevOps", product: "Product Manager", design: "Designer",
};

const LAST = 3;

function Chip({ label, on, toggle }: { label: string; on: boolean; toggle: () => void }) {
  return (
    <button type="button" className="chip" aria-pressed={on} onClick={toggle}>
      {label}
    </button>
  );
}

export default function Landing() {
  const router = useRouter();
  const { show, element: toast } = useToast();

  const [step, setStep] = useState(0);
  const [roleOpts, setRoleOpts] = useState(ROLE_OPTS);
  const [skillOpts, setSkillOpts] = useState(SKILL_OPTS);
  const [roles, setRoles] = useState<Set<string>>(new Set(["Product Manager", "Marketing", "Data Analyst", "Designer"]));
  const [skills, setSkills] = useState<Set<string>>(new Set(["SQL", "Figma", "Analytics"]));
  const [region, setRegion] = useState("EU remote");
  const [work, setWork] = useState<Set<string>>(new Set(["Full-time", "Freelance"]));
  const [email, setEmail] = useState("");
  const [consent, setConsent] = useState(true);
  const [addRole, setAddRole] = useState("");
  const [addSkill, setAddSkill] = useState("");

  const [cvSignals, setCvSignals] = useState<CVSignals | null>(null);
  const [cvName, setCvName] = useState("");
  const [cvBusy, setCvBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [tsToken, setTsToken] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const tsRef = useRef<TurnstileHandle>(null);

  // --- set helpers (immutable so React re-renders) ---
  const toggleIn = (set: Set<string>, v: string, setter: (s: Set<string>) => void) => {
    const next = new Set(set);
    next.has(v) ? next.delete(v) : next.add(v);
    setter(next);
  };
  const addTo = (set: Set<string>, v: string, setter: (s: Set<string>) => void) => {
    const next = new Set(set);
    next.add(v);
    setter(next);
  };

  const addCustomRole = () => {
    const v = addRole.trim();
    if (!v) return;
    if (!roleOpts.includes(v)) setRoleOpts([...roleOpts, v]);
    addTo(roles, v, setRoles);
    setAddRole("");
  };
  const addCustomSkill = () => {
    const v = addSkill.trim();
    if (!v) return;
    if (!skillOpts.includes(v)) setSkillOpts([...skillOpts, v]);
    addTo(skills, v, setSkills);
    setAddSkill("");
  };

  // --- CV upload -> POST /cv/parse -> prefill ---
  const onCVFile = async (file?: File | null) => {
    if (!file) return;
    if (!/\.(pdf|docx)$/i.test(file.name)) return show("Please upload a PDF or DOCX file");
    if (file.size > 8 * 1024 * 1024) return show("That file is too large (max 8 MB)");
    setCvBusy(true);
    try {
      const sig = await parseCV(file, tsToken || undefined);
      // prefill role chips
      const roleLabels = sig.role_categories.map((c) => CV_ROLE_LABEL[c]).filter(Boolean);
      const nextRoleOpts = [...roleOpts];
      const nextRoles = new Set(roles);
      roleLabels.forEach((r) => {
        if (!nextRoleOpts.includes(r)) nextRoleOpts.push(r);
        nextRoles.add(r);
      });
      // prefill skill chips
      const skillLabels = sig.skills.map((s) => cap(s));
      const nextSkillOpts = [...skillOpts];
      const nextSkills = new Set(skills);
      skillLabels.forEach((s) => {
        if (!nextSkillOpts.includes(s)) nextSkillOpts.push(s);
        nextSkills.add(s);
      });
      setRoleOpts(nextRoleOpts);
      setRoles(nextRoles);
      setSkillOpts(nextSkillOpts);
      setSkills(nextSkills);
      setCvSignals(sig);
      setCvName(file.name);
      show("CV read — we prefilled your profile");
    } catch (e) {
      show(e instanceof Error ? e.message : "Couldn't read that file");
    } finally {
      setCvBusy(false);
    }
  };

  const clearCV = () => {
    setCvSignals(null);
    setCvName("");
    if (fileRef.current) fileRef.current.value = "";
  };

  // --- build payload + submit ---
  function buildPayload(): SubscribePayload {
    const workTypes: string[] = [];
    if (work.has("Full-time")) workTypes.push("permanent");
    if (work.has("Freelance")) workTypes.push("freelance/contract");
    return {
      email: email.trim(),
      label: "My digest",
      stack: [...skills].map((s) => s.toLowerCase()),
      role_categories: [...roles].map((r) => ROLE_CAT[r]).filter(Boolean),
      regions: REGION_CODES[region] || ["cz", "eu", "worldwide"],
      work_types: workTypes.length ? workTypes : ["permanent", "freelance/contract"],
      part_time_only: work.has("Part-time"),
      sectors: cvSignals?.sectors || [],
      seniorities: cvSignals?.seniorities || ["junior", "mid"],
      min_score: 6,
      frequency: "daily",
      cv_signals: cvSignals,
      cf_turnstile_token: tsToken || null,
    };
  }

  const submit = async () => {
    if (!consent) return show("Please accept the privacy policy first");
    if (!email.trim().includes("@")) return show("Enter a valid email");
    if (turnstileEnabled && !tsToken) return show("Please complete the verification");
    setSubmitting(true);
    try {
      await subscribe(buildPayload());
      router.push(`/check-inbox/?email=${encodeURIComponent(email.trim())}`);
    } catch (e) {
      // Turnstile tokens are single-use — mint a fresh one so the retry isn't rejected as duplicate.
      tsRef.current?.reset();
      show(e instanceof Error ? e.message : "Something went wrong — please retry");
      setSubmitting(false);
    }
  };

  const progress = `${(step + 1) * 25}%`;

  return (
    <>
      <Nav />
      <main>
        <section>
          <div className="wrap hero">

            <div className="build solo">
              {/* WIZARD */}
              <div className="wizard big" id="wizard" aria-label="Build your digest">
                <div className="wz-top">
                  <span className="wz-step">Step {step + 1} of 4</span>
                  <span className="wz-prog">
                    <i style={{ width: progress }} />
                  </span>
                </div>

                {/* step 1: roles + CV fast-path */}
                {step === 0 && (
                  <div className="wz-panel">
                    <div className="wz-q">Start searching now</div>

                    {!cvSignals ? (
                      <div
                        className={`cvzone${drag ? " drag" : ""}${cvBusy ? " busy" : ""}`}
                        role="button"
                        tabIndex={0}
                        onClick={() => fileRef.current?.click()}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            fileRef.current?.click();
                          }
                        }}
                        onDragEnter={(e) => { e.preventDefault(); setDrag(true); }}
                        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
                        onDragLeave={(e) => { e.preventDefault(); setDrag(false); }}
                        onDrop={(e) => { e.preventDefault(); setDrag(false); onCVFile(e.dataTransfer.files?.[0]); }}
                      >
                        <div className="cvic" aria-hidden="true">
                          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                            <path d="m7 10 5 5 5-5" />
                            <path d="M12 15V3" />
                          </svg>
                        </div>
                        <div className="cvtxt">
                          <b>{cvBusy ? "Reading your CV…" : "Drop your CV — we'll fill this in"}</b>
                          <span>PDF or DOCX · We read it to set up your matches, then delete the file. Never shared.</span>
                        </div>
                        <span className="cvbtn">Choose file</span>
                        <input
                          ref={fileRef}
                          type="file"
                          accept=".pdf,.docx"
                          hidden
                          onChange={(e) => onCVFile(e.target.files?.[0])}
                        />
                      </div>
                    ) : (
                      <div className="cvbanner">
                        <span className="bic">✓</span>
                        <div className="btxt">
                          <b>CV read — prefilled</b>
                          <span>{cvSignals.summary} — {cvName}</span>
                        </div>
                        <button type="button" className="bx" aria-label="Remove CV" onClick={clearCV}>
                          ×
                        </button>
                      </div>
                    )}
                    <div className="cvor">or pick manually</div>

                    <div className="chips">
                      {roleOpts.map((o) => (
                        <Chip key={o} label={o} on={roles.has(o)} toggle={() => toggleIn(roles, o, setRoles)} />
                      ))}
                    </div>
                    <div className="addwrap">
                      <input
                        type="text"
                        value={addRole}
                        onChange={(e) => setAddRole(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCustomRole(); } }}
                        placeholder="Add another role…"
                        aria-label="Add a role"
                      />
                      <button type="button" onClick={addCustomRole}>Add</button>
                    </div>
                  </div>
                )}

                {/* step 2: skills */}
                {step === 1 && (
                  <div className="wz-panel">
                    <div className="wz-q">What are you good at?</div>
                    <p className="wz-hint">These are what we match jobs on. Tap all that apply.</p>
                    <div className="chips">
                      {skillOpts.map((o) => (
                        <Chip key={o} label={o} on={skills.has(o)} toggle={() => toggleIn(skills, o, setSkills)} />
                      ))}
                    </div>
                    <div className="addwrap">
                      <input
                        type="text"
                        value={addSkill}
                        onChange={(e) => setAddSkill(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCustomSkill(); } }}
                        placeholder="Add a skill…"
                        aria-label="Add a skill"
                      />
                      <button type="button" onClick={addCustomSkill}>Add</button>
                    </div>
                  </div>
                )}

                {/* step 3: where & how */}
                {step === 2 && (
                  <div className="wz-panel">
                    <div className="wz-q">Where &amp; how?</div>
                    <p className="wz-hint">Location first.</p>
                    <div className="chips">
                      {REGION_OPTS.map((o) => (
                        <Chip key={o} label={o} on={region === o} toggle={() => setRegion(o)} />
                      ))}
                    </div>
                    <p className="wz-hint" style={{ marginTop: 16 }}>Type of work — tap all that fit.</p>
                    <div className="chips">
                      {WORK_OPTS.map((o) => (
                        <Chip key={o} label={o} on={work.has(o)} toggle={() => toggleIn(work, o, setWork)} />
                      ))}
                    </div>
                  </div>
                )}

                {/* step 4: email */}
                {step === 3 && (
                  <div className="wz-panel">
                    <div className="wz-q">Where do we send it?</div>
                    <p className="wz-hint">One confirmation email first — then your daily digest at 7:00.</p>
                    <input
                      type="email"
                      className="emailin"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@example.com"
                      aria-label="Your email"
                    />
                    <label className="consent">
                      <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                      I agree to the <Link href="/privacy">privacy policy</Link> and to the daily digest.
                    </label>
                    <Turnstile ref={tsRef} onVerify={setTsToken} />
                    <div className="turnstile">
                      <span className="box">✓</span> Protected by Cloudflare Turnstile — no CAPTCHA
                    </div>
                  </div>
                )}

                {/* nav */}
                <div className="wz-nav">
                  {step > 0 && (
                    <button className="btn ghost" onClick={() => setStep((s) => Math.max(0, s - 1))}>
                      ← Back
                    </button>
                  )}
                  <span className="spacer" />
                  {step < LAST ? (
                    <button className="btn" onClick={() => setStep((s) => Math.min(LAST, s + 1))}>
                      Next →
                    </button>
                  ) : (
                    <button className="btn" onClick={submit} disabled={submitting}>
                      {submitting ? "Sending…" : "Start my digest →"}
                    </button>
                  )}
                </div>
              </div>
            </div>

            <div className="microtrust">
              <span><Check /> One email a day</span>
              <span><Check /> Unsubscribe in one click</span>
              <span><Check /> Free to start · 13 sources scanned nightly</span>
            </div>
          </div>

          <div className="wrap section">
            <span className="label">How it works</span>
            <div className="steps">
              <div className="step"><div className="num">01</div><h3>Tap your profile</h3><p>Roles, skills, where you can work. Twenty seconds, mostly tapping.</p></div>
              <div className="step"><div className="num">02</div><h3>We match overnight</h3><p>Fresh postings from dozens of sources, ranked to you, duplicates dropped.</p></div>
              <div className="step"><div className="num">03</div><h3>Read one email</h3><p>A short ranked shortlist with a reason and an apply link for each.</p></div>
            </div>
          </div>
        </section>
      </main>
      {/* Mobile-only sticky call-to-action — always-visible path to the sign-up wizard, since
          the live preview pushes it well down the page on phones. */}
      <a href="#wizard" className="mcta" aria-label="Jump to sign-up">
        Get my digest <span aria-hidden="true">→</span>
      </a>
      <Footer />
      {toast}
    </>
  );
}

function Check() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}
