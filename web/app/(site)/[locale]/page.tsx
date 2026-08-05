"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Nav, Footer } from "@/components/SiteChrome";
import EmailPreview from "@/components/EmailPreview";
import Turnstile, { turnstileEnabled, TurnstileHandle } from "@/components/Turnstile";
import GoogleButton from "@/components/GoogleButton";
import { useToast } from "@/components/useToast";
import { cap } from "@/lib/preview";
import {
  CVSignals, GOOGLE_AUTH_ENABLED, googleAuthUrl, googleSignupPending, parseCV, preview,
  subscribe, subscribeGoogle, SubscribePayload,
} from "@/lib/api";
import { track } from "@/lib/analytics";
import { LocationPicker, LocationValue } from "@/components/LocationPicker";
import { EducationPicker, EducationValue } from "@/components/EducationPicker";
import { WORK_MODES } from "@/lib/geo";
import {
  EDUCATION_LEVELS, cleanEducationField, cleanEducationLevels, levelsUpTo,
} from "@/lib/education";
import {
  CV_ROLE_ID, DEFAULT_ROLE_IDS, SENIORITY_IDS, SKILL_OPTS, WORK_TYPE_IDS,
  roleCategory, roleKeyword,
} from "@/lib/options";
import Link from "next/link";
import { fmt, legalHref } from "@/i18n/config";
import { rich, useI18n } from "@/i18n/context";

// Chip state holds ids, never labels — see lib/options.ts for why that distinction became
// load-bearing once the same chip renders differently in eight languages.
const DEFAULT_ROLES = ["product_manager", "marketing", "data_analyst", "designer"];
const DEFAULT_SKILLS = ["SQL", "Figma", "Analytics"];
const DEFAULT_WORK = ["fulltime", "freelance"];
const DEFAULT_LEVELS = ["junior", "mid"];

// Opens on the home market with no city restriction: "Czechia, any city, plus remote from
// anywhere in the EU" — the widest sensible default, so a subscriber who skips this step is
// never narrowed by a choice they did not make.
const DEFAULT_LOCATION: LocationValue = {
  countries: ["CZ"], cities: [], remoteScope: "eu", workModes: [...WORK_MODES],
};

// Every level ticked, i.e. no education filter at all. Same principle as the location default:
// a visitor who skips this step must not be narrowed by a choice they did not make, and this
// axis can only ever exclude postings, never add any.
const DEFAULT_EDUCATION: EducationValue = {
  levels: [...EDUCATION_LEVELS], field: "",
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
  const { t, href, locale } = useI18n();
  const { show, element: toast } = useToast();

  // A typed role has no catalogue entry, so its id *is* what to display.
  const roleLabel = (id: string) => t.roles[id] ?? id;

  const [step, setStep] = useState(0);
  const [roleOpts, setRoleOpts] = useState<string[]>(DEFAULT_ROLE_IDS);
  const [skillOpts, setSkillOpts] = useState(SKILL_OPTS);
  const [roles, setRoles] = useState<Set<string>>(new Set(DEFAULT_ROLES));
  const [skills, setSkills] = useState<Set<string>>(new Set(DEFAULT_SKILLS));
  const [loc, setLoc] = useState<LocationValue>(DEFAULT_LOCATION);
  const [edu, setEdu] = useState<EducationValue>(DEFAULT_EDUCATION);
  const [work, setWork] = useState<Set<string>>(new Set(DEFAULT_WORK));
  const [levels, setLevels] = useState<Set<string>>(new Set(DEFAULT_LEVELS));
  const [email, setEmail] = useState("");
  // Unticked, and it must stay unticked. Consent under GDPR is an affirmative act, so a box
  // that arrives already ticked is not consent at all (CJEU C-673/17, Planet49) — and this is
  // the box that authorises storing a real person's address and mailing them daily. The
  // privacy policy is treated as a specification here (security rule 4); a pre-ticked box
  // would make its "you agreed" claim false for every subscriber who never touched it.
  const [consent, setConsent] = useState(false);
  const [addRole, setAddRole] = useState("");
  const [addSkill, setAddSkill] = useState("");

  const [cvSignals, setCvSignals] = useState<CVSignals | null>(null);
  const [cvName, setCvName] = useState("");
  const [cvBusy, setCvBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [tsToken, setTsToken] = useState("");
  // Google-verified signup mode: entered after the OAuth round-trip for a new user. The email
  // is fixed (Google-verified), so we skip the email box + Turnstile and submit to /subscribe/google.
  const [googleMode, setGoogleMode] = useState(false);
  const [googleEmail, setGoogleEmail] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const tsRef = useRef<TurnstileHandle>(null);
  const startedRef = useRef(false);

  // --- analytics: funnel entry + first real interaction ---
  // landing_view fires once per mount; form_started fires the first time someone moves past
  // step 0. The gap between the two is the "looked but didn't engage" drop-off.
  useEffect(() => {
    track("landing_view");
  }, []);

  useEffect(() => {
    if (step > 0 && !startedRef.current) {
      startedRef.current = true;
      track("form_started", { step: String(step) });
    }
  }, [step]);

  // --- Google-verified signup: survive the full-page OAuth redirect ---
  // Clicking "Sign up with Google" leaves the SPA entirely, so stash the picks first and
  // restore them when Google sends the user back to /<locale>/?google=signup.
  const saveWizardState = () => {
    try {
      sessionStorage.setItem("jd_google_wiz", JSON.stringify({
        roleOpts, skillOpts, roles: [...roles], skills: [...skills], loc,
        work: [...work], levels: [...levels], cvSignals, step, consent, edu,
      }));
    } catch {}
  };
  const googleStart = () => {
    saveWizardState();
    window.location.href = googleAuthUrl();
  };

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("google") !== "signup") return;
    try {
      const s = JSON.parse(sessionStorage.getItem("jd_google_wiz") || "null");
      if (s) {
        setRoleOpts(s.roleOpts || DEFAULT_ROLE_IDS);
        setSkillOpts(s.skillOpts || SKILL_OPTS);
        setRoles(new Set<string>(s.roles || []));
        setSkills(new Set<string>(s.skills || []));
        setLoc(s.loc && s.loc.countries?.length ? s.loc : DEFAULT_LOCATION);
        setWork(new Set<string>(s.work || []));
        setLevels(new Set<string>(s.levels || []));
        setEdu(s.edu && s.edu.levels?.length ? s.edu : DEFAULT_EDUCATION);
        setCvSignals(s.cvSignals || null);
        // Carried so someone who ticked the box before the OAuth hop is not asked twice.
        // `=== true` because anything else — absent key, older stashed state — must read as
        // "never consented", the same default a fresh visitor gets.
        setConsent(s.consent === true);
      }
    } catch {}
    // Confirm the intent is still live and learn which verified address it's for.
    googleSignupPending()
      .then((r) => {
        setGoogleEmail(r.email);
        setEmail(r.email);
        setGoogleMode(true);
        setStep(LAST);
        try { sessionStorage.removeItem("jd_google_wiz"); } catch {}
      })
      .catch(() => {
        show(t.landing.toast.googleExpired);
        window.history.replaceState(null, "", href("/"));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    track("cv_upload_attempted");
    // Client-side rejections are counted separately from server parse failures — a spike in
    // "wrong_type" means the upload affordance is unclear, not that parsing is broken.
    if (!/\.(pdf|docx)$/i.test(file.name)) {
      track("cv_parse_failed", { reason: "wrong_type" });
      return show(t.landing.toast.cvWrongType);
    }
    if (file.size > 8 * 1024 * 1024) {
      track("cv_parse_failed", { reason: "too_large" });
      return show(t.landing.toast.cvTooLarge);
    }
    setCvBusy(true);
    try {
      const sig = await parseCV(file, tsToken || undefined);
      // prefill role chips
      const roleIds = sig.role_categories.map((c) => CV_ROLE_ID[c]).filter(Boolean);
      const nextRoleOpts = [...roleOpts];
      const nextRoles = new Set(roles);
      roleIds.forEach((r) => {
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
      // Prefill seniority from the CV's detected level(s) — the user can still override before
      // subscribing. `sig.seniorities` already holds the stored codes, which is exactly what
      // the chips are keyed on now, so there is no label round-trip left to get wrong.
      const cvLevels = (sig.seniorities || [])
        .filter((c) => (SENIORITY_IDS as readonly string[]).includes(c));
      if (cvLevels.length) setLevels(new Set(cvLevels));
      // Prefill education the same way — visibly, in the form, where it can be corrected.
      // `levelsUpTo` turns "we detected a bachelor's" into the levels such a person can apply
      // for. Deliberately a *UI* prefill only: the server never derives this from a CV, because
      // a CV that failed to mention a master's would otherwise silently delete every
      // master-requiring role from the digest. See `cvparse.merge_into_profile`.
      if (sig.education || sig.education_field) {
        setEdu({
          levels: sig.education ? levelsUpTo(sig.education) : [...EDUCATION_LEVELS],
          field: sig.education_field || "",
        });
      }
      setCvSignals(sig);
      setCvName(file.name);
      // Skill count, not the skills themselves — "parsed but found nothing" is a distinct
      // and important failure mode (e.g. scanned PDFs) that still returns HTTP 200.
      track("cv_parse_ok", { skills: sig.skills.length });
      show(t.landing.toast.cvOk);
    } catch (e) {
      track("cv_parse_failed", { reason: "server_rejected" });
      show(e instanceof Error ? e.message : t.landing.toast.cvUnreadable);
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
    if (work.has("fulltime")) workTypes.push("permanent");
    if (work.has("freelance")) workTypes.push("freelance/contract");
    // "Type of work — tap all that fit" is an inclusive multi-select, and Full-time ships
    // pre-selected. Tapping Part-time therefore means "part-time fits me too", never "only
    // part-time" — so it is only "only" when Full-time is not also selected. Sending the bare
    // `work.has("parttime")` recorded part_time_only on subscribers who had Full-time visibly
    // ticked, and the matcher then penalised every full-time role it showed them.
    const partTimeOnly = work.has("parttime") && !work.has("fulltime");
    const seniorities = [...levels];
    // "Add another role…" lets someone type a role no category models — Sales, Cybersecurity,
    // IT Support. `.filter(Boolean)` alone dropped those on the floor: not stored, not
    // logged, no error, and the subscriber sees the chip they typed still highlighted. Route
    // them to `stack` instead, the way /preferences already does, because the shortlist
    // full-text query searches stack terms — so the word still steers retrieval even though
    // nothing classified it. Slugifying them into role_categories is NOT the alternative:
    // that is what produced `social_media_specialist`, a value no posting carries, i.e. a
    // filter that silently matched nothing at all.
    //
    // `roleKeyword` is what keeps this independent of the display language: a known chip
    // contributes its English keyword, never the label the visitor happened to be reading.
    const roleSlugs = [...new Set(
      [...roles].map(roleCategory).filter((c): c is string => !!c)
    )];
    const freeRoles = [...roles].filter((r) => !roleCategory(r)).map(roleKeyword);
    return {
      email: email.trim(),
      label: "My digest",
      stack: [...new Set([...skills, ...freeRoles]
        .map((s) => s.trim().toLowerCase()).filter(Boolean))],
      role_categories: roleSlugs,
      countries: loc.countries.length ? loc.countries : ["CZ"],
      cities: loc.cities,
      remote_scope: loc.remoteScope,
      work_modes: loc.workModes,
      education_levels: cleanEducationLevels(edu.levels),
      education_field: cleanEducationField(edu.field),
      work_types: workTypes.length ? workTypes : ["permanent", "freelance/contract"],
      part_time_only: partTimeOnly,
      sectors: cvSignals?.sectors || [],
      seniorities: seniorities.length ? seniorities : ["junior", "mid"],
      min_score: 6,
      frequency: "daily",
      // The language they are reading right now, so tomorrow's digest arrives in it. This is
      // the only moment it can be captured — the send runs from a timer with no browser.
      language: locale,
      cv_signals: cvSignals,
      cf_turnstile_token: tsToken || null,
    };
  }

  const submitGoogle = async () => {
    if (!consent) return show(t.landing.toast.needConsent);
    if (levels.size === 0) return show(t.landing.toast.needLevel);
    setSubmitting(true);
    track("subscribe_submitted", { skills: skills.size, variant: [...levels].join("+") || "none" });
    try {
      await subscribeGoogle(buildPayload());   // email comes from the server-side intent
      track("subscribe_ok");
      router.push(href("/preferences"));       // active + logged in — no inbox step
    } catch (e) {
      track("subscribe_error");
      show(e instanceof Error ? e.message : t.landing.toast.genericError);
      setSubmitting(false);
    }
  };

  const submit = async () => {
    if (googleMode) return submitGoogle();
    if (!consent) return show(t.landing.toast.needConsent);
    if (!email.trim().includes("@")) return show(t.landing.toast.needEmail);
    if (levels.size === 0) return show(t.landing.toast.needLevel);
    // A real person blocked by the bot check is a UX failure worth seeing, not just a stat.
    if (turnstileEnabled && !tsToken) {
      track("turnstile_failed");
      return show(t.landing.toast.needTurnstile);
    }
    setSubmitting(true);
    // `variant` carries the chosen seniority levels (e.g. "junior+mid") — reuses an existing
    // whitelisted event + prop key, so no server-side analytics change is needed.
    track("subscribe_submitted", { skills: skills.size, variant: [...levels].join("+") || "none" });
    const payload = buildPayload();
    // Instant keyword preview, fired in parallel with the signup. Best-effort: if it fails
    // or is slow, we still complete signup — check-inbox just won't show instant matches.
    const previewDone = preview(payload)
      .then((r) => { try { sessionStorage.setItem("jd_preview", JSON.stringify(r)); } catch {} })
      .catch(() => {});
    try {
      await subscribe(payload);
      track("subscribe_ok");
      // Give the preview a brief moment to land, but never block the redirect on it.
      await Promise.race([previewDone, new Promise((res) => setTimeout(res, 2500))]);
      router.push(href(`/check-inbox?email=${encodeURIComponent(email.trim())}`));
    } catch (e) {
      track("subscribe_error");
      // Turnstile tokens are single-use — mint a fresh one so the retry isn't rejected as duplicate.
      tsRef.current?.reset();
      show(e instanceof Error ? e.message : t.landing.toast.genericError);
      setSubmitting(false);
    }
  };

  // Seniority is a hard filter now, so a one-level + one-role + single-country search can
  // starve matches. Flag the tightest combos so we can nudge (not block) before submit.
  const narrow = levels.size === 1 && roles.size <= 1
    && loc.countries.length <= 1 && loc.remoteScope === "country";

  // Per-step guard: advancing shouldn't leave a required choice empty (which would silently
  // fall back to defaults and mismatch what the user thinks they picked).
  const goNext = () => {
    if (step === 0 && roles.size === 0) return show(t.landing.toast.needRole);
    if (step === 2 && levels.size === 0) return show(t.landing.toast.needLevelToContinue);
    setStep((s) => Math.min(LAST, s + 1));
  };

  const progress = `${(step + 1) * 25}%`;

  // Same control in both branches of step 4. `legalHref` because the policy exists in fewer
  // languages than the site — consenting to a policy is the last place to send someone to a
  // 404, so a reader of one of the other six gets the English text rather than nothing.
  const consentLabel = (
    <label className="consent">
      <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
      {" "}
      {rich(t.landing.consent, [
        <Link key="pp" href={legalHref(locale, "privacy")}>{t.landing.consentLink}</Link>,
      ])}
    </label>
  );

  return (
    <>
      <Nav />
      <main>
        <section>
          <div className="wrap hero">
            <div className="build">
              {/* WIZARD */}
              <div className="wizard" id="wizard" aria-label={t.landing.wizardAria}>
                <div className="wz-top">
                  <span className="wz-step">{fmt(t.landing.stepOf, { n: step + 1 })}</span>
                  <span className="wz-prog">
                    <i style={{ width: progress }} />
                  </span>
                </div>

                {/* step 1: roles + CV fast-path */}
                {step === 0 && (
                  <div className="wz-panel">
                    <div className="wz-q">{t.landing.q1}</div>

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
                          <b>{cvBusy ? t.landing.cvReading : t.landing.cvDrop}</b>
                          <span>{t.landing.cvHint}</span>
                        </div>
                        <span className="cvbtn">{t.landing.cvChoose}</span>
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
                          <b>{t.landing.cvDone}</b>
                          <span>{cvSignals.summary} — {cvName}</span>
                        </div>
                        <button type="button" className="bx" aria-label={t.landing.cvRemove} onClick={clearCV}>
                          ×
                        </button>
                      </div>
                    )}
                    <div className="cvor">{t.landing.orPickManually}</div>

                    <div className="chips">
                      {roleOpts.map((o) => (
                        <Chip key={o} label={roleLabel(o)} on={roles.has(o)}
                          toggle={() => toggleIn(roles, o, setRoles)} />
                      ))}
                    </div>
                    <div className="addwrap">
                      <input
                        type="text"
                        value={addRole}
                        onChange={(e) => setAddRole(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCustomRole(); } }}
                        placeholder={t.landing.addRolePlaceholder}
                        aria-label={t.landing.addRoleAria}
                      />
                      <button type="button" onClick={addCustomRole}>{t.common.add}</button>
                    </div>
                  </div>
                )}

                {/* step 2: skills */}
                {step === 1 && (
                  <div className="wz-panel">
                    <div className="wz-q">{t.landing.q2}</div>
                    <p className="wz-hint">{t.landing.q2hint}</p>
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
                        placeholder={t.landing.addSkillPlaceholder}
                        aria-label={t.landing.addSkillAria}
                      />
                      <button type="button" onClick={addCustomSkill}>{t.common.add}</button>
                    </div>
                  </div>
                )}

                {/* step 3: where & how */}
                {step === 2 && (
                  <div className="wz-panel">
                    <div className="wz-q">{t.landing.q3}</div>
                    <p className="wz-hint">{t.landing.q3hint}</p>
                    <LocationPicker value={loc} onChange={setLoc} idPrefix="wz" />
                    <EducationPicker value={edu} onChange={setEdu} idPrefix="wz" />
                    <p className="wz-hint" style={{ marginTop: 16 }}>{t.landing.workTypeHint}</p>
                    <div className="chips">
                      {WORK_TYPE_IDS.map((o) => (
                        <Chip key={o} label={t.workTypes[o]} on={work.has(o)}
                          toggle={() => toggleIn(work, o, setWork)} />
                      ))}
                    </div>
                    <p className="wz-hint" style={{ marginTop: 16 }}>{t.landing.levelHint}</p>
                    <div className="chips">
                      {SENIORITY_IDS.map((o) => (
                        <Chip key={o} label={t.seniorities[o]} on={levels.has(o)}
                          toggle={() => toggleIn(levels, o, setLevels)} />
                      ))}
                    </div>
                    {narrow && (
                      <p className="wz-hint" style={{ marginTop: 10, color: "var(--gold, #C98A18)" }}>
                        {t.landing.narrowWarning}
                      </p>
                    )}
                  </div>
                )}

                {/* step 4: email */}
                {step === 3 && (
                  <div className="wz-panel">
                    {googleMode ? (
                      <>
                        <div className="wz-q">{t.landing.q4google}</div>
                        <p className="wz-hint">
                          {rich(t.landing.googleHint, [<b key="e">{googleEmail}</b>])}
                        </p>
                        {consentLabel}
                      </>
                    ) : (
                      <>
                        <div className="wz-q">{t.landing.q4}</div>
                        <p className="wz-hint">{t.landing.q4hint}</p>
                        {GOOGLE_AUTH_ENABLED && (
                          <>
                            <GoogleButton label={t.landing.googleSignup} onClick={googleStart} />
                            <div className="or-divider"><span>{t.common.or}</span></div>
                          </>
                        )}
                        <input
                          type="email"
                          className="emailin"
                          value={email}
                          onChange={(e) => setEmail(e.target.value)}
                          placeholder={t.landing.emailPlaceholder}
                          aria-label={t.landing.emailAria}
                        />
                        {consentLabel}
                        <Turnstile ref={tsRef} onVerify={setTsToken} />
                        <div className="turnstile">
                          <span className="box">✓</span> {t.landing.turnstileNote}
                        </div>
                      </>
                    )}
                  </div>
                )}

                {/* nav */}
                <div className="wz-nav">
                  {step > 0 && (
                    <button className="btn ghost" onClick={() => setStep((s) => Math.max(0, s - 1))}>
                      {t.landing.back}
                    </button>
                  )}
                  <span className="spacer" />
                  {step < LAST ? (
                    <button className="btn" onClick={goNext}>
                      {t.landing.next}
                    </button>
                  ) : (
                    <button className="btn" onClick={submit} disabled={submitting}>
                      {submitting ? t.landing.sending : t.landing.submit}
                    </button>
                  )}
                </div>
              </div>

              {/* LIVE PREVIEW */}
              <div className="peek">
                <span className="cap">{t.landing.livePreview}</span>
                <EmailPreview roles={roles} skills={skills} work={work} levels={levels}
                  email={email} limit={3} />
              </div>
            </div>

            <div className="microtrust">
              <span><Check /> {t.landing.trustEmail}</span>
              <span><Check /> {t.landing.trustUnsub}</span>
              <span><Check /> {t.landing.trustFree}</span>
            </div>
          </div>

          <div className="wrap section">
            <span className="label">{t.landing.howItWorks}</span>
            <div className="steps">
              <div className="step"><div className="num">01</div><h3>{t.landing.step1Title}</h3><p>{t.landing.step1Body}</p></div>
              <div className="step"><div className="num">02</div><h3>{t.landing.step2Title}</h3><p>{t.landing.step2Body}</p></div>
              <div className="step"><div className="num">03</div><h3>{t.landing.step3Title}</h3><p>{t.landing.step3Body}</p></div>
            </div>
          </div>
        </section>
      </main>
      {/* The mobile sticky "Get my digest" CTA was removed: the wizard now renders first on
          phones (globals.css), so a button that only scrolled down to it — and read as a submit
          on steps 2–4 while merely scrolling upward — no longer earned its place. The
          `landing.mcta` / `landing.mctaAria` catalogue strings are now unused. */}
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
