"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";
import { useToast } from "@/components/useToast";
import {
  establishSession, getPreferences, logout, parseCV, pause, Preferences, resume,
  unsubscribeSession, unsubscribeUrl, updatePreferences,
} from "@/lib/api";
import { cap } from "@/lib/preview";
import { track } from "@/lib/analytics";
import { LocationPicker, LocationValue } from "@/components/LocationPicker";
import { EducationPicker, EducationValue } from "@/components/EducationPicker";
import { REMOTE_SCOPES, RemoteScope, WORK_MODES, cleanWorkModes } from "@/lib/geo";
import {
  EDUCATION_LEVELS, cleanEducationField, cleanEducationLevels, levelsUpTo,
} from "@/lib/education";
import {
  CV_ROLE_ID, DEFAULT_ROLE_IDS, ROLE_ID_FOR_CATEGORY, SENIORITY_IDS, SKILL_OPTS,
  prettifyCategory, roleCategory, roleKeyword,
} from "@/lib/options";
import { fmt } from "@/i18n/config";
import { rich, useI18n } from "@/i18n/context";

const FREQS = ["daily", "weekdays", "weekly"];

// Suggestions only. `sectors` is free text and a *soft* signal for the AI matcher (never a
// hard filter — see `service/geo.py` / the profile export), so a subscriber may add anything.
// These are just common starting points; like skills, they display capitalised and are stored
// lowercased, so a picked suggestion and a typed word collapse to the same value.
const SECTOR_SUGGESTIONS = [
  "E-commerce", "Fintech", "Finance", "Gaming", "Healthtech", "Trading", "Cybersecurity",
];

/**
 * A subscription created before city-level preferences existed still carries only the coarse
 * `regions` bucket. Translate it the same way the migration does, so the form opens on what
 * the filter is actually doing rather than on an empty country list.
 */
function locationFrom(p: Preferences): LocationValue {
  // Absent on a subscription that predates migration 012 — `cleanWorkModes` reads that as
  // "no preference" and returns all three, which is what the column defaults to anyway. The
  // form must never open on a narrower selection than the one being enforced.
  const workModes = cleanWorkModes(p.work_modes);
  if (p.countries && p.countries.length) {
    return {
      countries: p.countries,
      cities: p.cities || [],
      remoteScope: (REMOTE_SCOPES as readonly string[]).includes(p.remote_scope)
        ? (p.remote_scope as RemoteScope) : "eu",
      workModes,
    };
  }
  const regions = p.regions || [];
  return {
    countries: ["CZ"],
    cities: [],
    remoteScope: regions.includes("worldwide") ? "worldwide"
      : regions.includes("eu") ? "eu" : "country",
    workModes,
  };
}

function Inner() {
  const urlToken = useSearchParams().get("token") || "";
  // The magic-link token is used once to mint a session, then dropped from the URL. After
  // that this ref is "" and every call authenticates by cookie. It stays set only in the
  // fallback where the browser refused the cookie, so token-based calls keep the page working.
  const tokenRef = useRef<string>(urlToken);
  const { t, href, locale } = useI18n();
  const { show, element: toast } = useToast();
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmUnsub, setConfirmUnsub] = useState(false);

  // A typed role chip has no catalogue entry, so its id is what to show.
  const roleLabel = (id: string) => t.roles[id] ?? id;

  // editable fields — all id-keyed, never label-keyed (see lib/options.ts)
  const [roleOpts, setRoleOpts] = useState<string[]>(DEFAULT_ROLE_IDS);
  const [roleSet, setRoleSet] = useState<Set<string>>(new Set());
  const [skillOpts, setSkillOpts] = useState<string[]>(SKILL_OPTS);
  const [skillSet, setSkillSet] = useState<Set<string>>(new Set());
  const [sectorOpts, setSectorOpts] = useState<string[]>(SECTOR_SUGGESTIONS);
  const [sectorSet, setSectorSet] = useState<Set<string>>(new Set());
  const [addRole, setAddRole] = useState("");
  const [addSkill, setAddSkill] = useState("");
  const [addSector, setAddSector] = useState("");
  const [freq, setFreq] = useState("daily");
  const [loc, setLoc] = useState<LocationValue>({
    countries: ["CZ"], cities: [], remoteScope: "eu", workModes: [...WORK_MODES],
  });
  const [edu, setEdu] = useState<EducationValue>({
    levels: [...EDUCATION_LEVELS], field: "",
  });
  const [levels, setLevels] = useState<Set<string>>(new Set());
  // Years of experience as the input's raw string: "" is a real state ("no preference",
  // stored as NULL) and a number input's value must round-trip what was typed.
  const [years, setYears] = useState("");
  const [cvBusy, setCvBusy] = useState(false);
  const cvInputRef = useRef<HTMLInputElement | null>(null);

  type SetSetter = (updater: (prev: Set<string>) => Set<string>) => void;
  const toggleInSet = (setter: SetSetter, o: string) =>
    setter((prev) => {
      const next = new Set(prev);
      next.has(o) ? next.delete(o) : next.add(o);
      return next;
    });
  const addChip = (
    val: string, opts: string[], setOpts: (a: string[]) => void, setSet: SetSetter, clear: () => void
  ) => {
    const v = val.trim();
    if (!v) return;
    if (!opts.includes(v)) setOpts([...opts, v]);
    setSet((prev) => new Set(prev).add(v));
    clear();
  };

  useEffect(() => {
    let cancelled = false;
    const hydrate = (p: Preferences) => {
      if (cancelled) return;
      setPrefs(p);
      // A category no chip models becomes a readable chip that round-trips back to the same
      // keyword on save, rather than disappearing from the form.
      const roleIds = p.role_categories.map((c) => ROLE_ID_FOR_CATEGORY[c] || prettifyCategory(c));
      setRoleOpts([...new Set([...DEFAULT_ROLE_IDS, ...roleIds])]);
      setRoleSet(new Set(roleIds));
      const skillLabels = p.stack.map((s) => cap(s));
      setSkillOpts([...new Set([...SKILL_OPTS, ...skillLabels])]);
      setSkillSet(new Set(skillLabels));
      // Same id-vs-label discipline as skills: a stored sector is a lowercase word, shown
      // capitalised. Anything already on file (typed here, or detected from a CV at signup)
      // joins the suggestion chips so it renders selected rather than silently dropping.
      const sectorLabels = (p.sectors || []).map((s) => cap(s));
      setSectorOpts([...new Set([...SECTOR_SUGGESTIONS, ...sectorLabels])]);
      setSectorSet(new Set(sectorLabels));
      setFreq(FREQS.includes(p.frequency) ? p.frequency : "daily");
      setLoc(locationFrom(p));
      // Absent on a subscription that predates migration 014 — `cleanEducationLevels` reads
      // that as "no preference" and returns all five, which is the column default too. The
      // form must never open on a narrower selection than the one being enforced.
      setEdu({
        levels: cleanEducationLevels(p.education_levels),
        field: p.education_field || "",
      });
      setLevels(new Set((p.seniorities || [])
        .filter((c) => (SENIORITY_IDS as readonly string[]).includes(c))));
      setYears(p.years_experience == null ? "" : String(p.years_experience));
    };

    const load = async () => {
      try {
        if (urlToken) {
          // Trade the one-time magic-link token for a session cookie, then strip it from the
          // URL so it doesn't linger in history or a referrer. If the browser refuses the
          // cookie, keep using the token so the page still works.
          try {
            const p = await establishSession(urlToken);
            tokenRef.current = "";
            if (typeof window !== "undefined") {
              window.history.replaceState(null, "", href("/preferences"));
              window.dispatchEvent(new Event("jd-auth-changed"));   // nav: re-check, we're in
            }
            hydrate(p);
          } catch {
            hydrate(await getPreferences(urlToken));
          }
          return;
        }
        // No token in the URL — rely on an existing session cookie from a prior visit.
        hydrate(await getPreferences());
      } catch (e) {
        if (cancelled) return;
        setErr(
          urlToken
            ? e instanceof Error ? e.message : t.prefs.errUnknownLink
            : t.prefs.errNoLink
        );
      }
    };
    load();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlToken]);

  // --- CV re-upload -> POST /cv/parse -> REPLACE the form state -----------------------
  // Replace, not union — "reset, set new according to CV" is the point of re-uploading —
  // but only for the axes the CV actually speaks to: a CV that names no seniority must not
  // wipe an explicit choice (the parser's only safe error is a miss, and this is its UI
  // face). Nothing is saved here: the replaced state sits in the form for review, and Save
  // is what persists it. The file itself is parsed in memory and discarded (privacy page).
  const startCvUpload = () => {
    if (window.confirm(t.prefs.cvReplaceConfirm)) cvInputRef.current?.click();
  };
  const onCVFile = async (file?: File | null) => {
    if (!file) return;
    track("cv_upload_attempted");
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
      const sig = await parseCV(file);
      const roleIds = (sig.role_categories || []).map((c) => CV_ROLE_ID[c]).filter(Boolean);
      if (roleIds.length) {
        setRoleOpts([...new Set([...DEFAULT_ROLE_IDS, ...roleIds])]);
        setRoleSet(new Set(roleIds));
      }
      const skillLabels = (sig.skills || []).map((s) => cap(s));
      if (skillLabels.length) {
        setSkillOpts([...new Set([...SKILL_OPTS, ...skillLabels])]);
        setSkillSet(new Set(skillLabels));
      }
      const sectorLabels = (sig.sectors || []).map((s) => cap(s));
      if (sectorLabels.length) {
        setSectorOpts([...new Set([...SECTOR_SUGGESTIONS, ...sectorLabels])]);
        setSectorSet(new Set(sectorLabels));
      }
      const cvLevels = (sig.seniorities || [])
        .filter((c) => (SENIORITY_IDS as readonly string[]).includes(c));
      if (cvLevels.length) setLevels(new Set(cvLevels));
      if (sig.years_experience != null) setYears(String(sig.years_experience));
      // Same mapping as signup: a detected bachelor's becomes "levels such a person can
      // apply for", visibly, in chips the subscriber can correct before saving.
      if (sig.education || sig.education_field) {
        setEdu({
          levels: sig.education ? levelsUpTo(sig.education) : [],
          field: sig.education_field || "",
        });
      }
      track("cv_parse_ok", { skills: (sig.skills || []).length });
      show(t.landing.toast.cvOk);
    } catch (e) {
      track("cv_parse_failed", { reason: "server_rejected" });
      show(e instanceof Error ? e.message : t.landing.toast.cvUnreadable);
    } finally {
      setCvBusy(false);
      // Reset so picking the same file again still fires onChange.
      if (cvInputRef.current) cvInputRef.current.value = "";
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      // Empty selection means "any level" — send all three rather than an empty target,
      // which the hard-filter matcher would read as "nothing matches".
      const seniorities = [...levels];
      // Dedup slugs: Marketing + Finance both map to other_tech_function.
      // A typed role chip that maps to no category used to be slugified into one anyway
      // ("Social media specialist" -> social_media_specialist), producing a filter no posting
      // could ever match and no error anywhere — the user saw a selected chip doing nothing.
      // Unmapped chips now become search keywords instead, which the shortlist full-text
      // query does use, so the words still steer retrieval and nothing is silently dead.
      // `roleKeyword` supplies the English word for a known chip, so what gets stored does
      // not depend on which of the eight languages the form was rendered in.
      const roleSlugs = [...new Set(
        [...roleSet].map(roleCategory).filter((c): c is string => !!c)
      )];
      const freeRoles = [...roleSet].filter((l) => !roleCategory(l)).map(roleKeyword);
      const updated = await updatePreferences({
        role_categories: roleSlugs,
        stack: [...new Set([...skillSet, ...freeRoles].map((s) => s.trim().toLowerCase()).filter(Boolean))],
        // Free text, stored lowercased like `stack`; the matcher reads it as a soft signal.
        sectors: [...new Set([...sectorSet].map((s) => s.trim().toLowerCase()).filter(Boolean))],
        frequency: freq,
        // No country selected would mean "nowhere". Fall back to the home market rather than
        // saving a filter that can never match — the same choice the server-side default makes.
        countries: loc.countries.length ? loc.countries : ["CZ"],
        cities: loc.cities,
        remote_scope: loc.remoteScope,
        work_modes: loc.workModes,
        education_levels: cleanEducationLevels(edu.levels),
        education_field: cleanEducationField(edu.field),
        seniorities: seniorities.length ? seniorities : ["junior", "mid", "senior"],
        // "" clears it server-side (stored NULL = no preference) — a literal null would be
        // dropped by the API's exclude_none and the old value would silently survive.
        years_experience: years.trim() === "" ? ""
          : Math.max(0, Math.min(50, Math.round(Number(years)) || 0)),
        // Saving from /cs/preferences/ means "write to me in Czech". There is no separate
        // language control on purpose: a subscriber who switched the site to their language
        // and then kept getting English mail is the bug this closes, and a second setting
        // that can disagree with the one they just used would reintroduce it.
        language: locale,
      }, tokenRef.current || undefined);
      setPrefs(updated);
      show(t.prefs.toast.saved);
    } catch (e) {
      show(e instanceof Error ? e.message : t.prefs.toast.saveFailed);
    } finally {
      setSaving(false);
    }
  };

  const doPause = async () => {
    try {
      const r = await pause(14, tokenRef.current || undefined);
      setPrefs((p) => (p ? { ...p, status: "paused", paused_until: r.paused_until } : p));
      show(t.prefs.toast.paused);
    } catch (e) {
      show(e instanceof Error ? e.message : t.prefs.toast.pauseFailed);
    }
  };
  const doResume = async () => {
    try {
      await resume(tokenRef.current || undefined);
      setPrefs((p) => (p ? { ...p, status: "active", paused_until: null } : p));
      show(t.prefs.toast.resumed);
    } catch (e) {
      show(e instanceof Error ? e.message : t.prefs.toast.resumeFailed);
    }
  };

  const doLogout = async () => {
    try { await logout(); } catch { /* clearing the cookie is best-effort */ }
    if (typeof window !== "undefined") window.location.href = href("/");
  };

  const doUnsubscribe = async () => {
    // Cookie-authenticated, in-page unsubscribe (no token in the URL). Two-step: the first
    // click arms it, the second performs it — a click is a POST, never a GET, so a link
    // scanner can't trigger it.
    try {
      await unsubscribeSession();
      show(t.prefs.toast.unsubscribed);
      if (typeof window !== "undefined") setTimeout(() => (window.location.href = href("/")), 900);
    } catch (e) {
      show(e instanceof Error ? e.message : t.prefs.toast.unsubscribeFailed);
    }
  };

  if (err) {
    return (
      <div className="state-wrap">
        <div className="state-card warn">
          <div className="ic">!</div>
          <h1>{t.prefs.errTitle}</h1>
          <p>{err}</p>
          <p style={{ marginTop: 16 }}><Link href={href("/")}>{t.common.goHome}</Link></p>
        </div>
      </div>
    );
  }
  if (!prefs) {
    return <div className="state-wrap"><div className="state-card"><h1>{t.common.loading}</h1></div></div>;
  }

  const paused = prefs.status === "paused";
  return (
    <>
      <div className="wrap page-head">
        <span className="label">{t.prefs.signedInLabel}</span>
        <h1>{t.prefs.title}</h1>
        <p>
          {rich(t.prefs.signedInAs, [
            <b key="e">{prefs.email}</b>,
            <button key="o" type="button" className="linkbtn" onClick={doLogout}>
              {t.prefs.logOutInline}
            </button>,
          ])}
        </p>
      </div>
      <div className="note">
        <b>{t.prefs.noPasswordLead}</b> {t.prefs.noPasswordBody}
      </div>
      <div className="panel">
        <div className="card">
          {/* The "Detected: …" summary sentence used to sit here — removed 2026-08-18: the
              CV's findings live in the editable chips below, and a sentence restating them
              (often stale, never editable) claimed an authority the chips are the truth of.
              Re-uploading replaces the form state for review; Save is what persists it. */}
          <div className="field" style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button type="button" className="btn secondary" onClick={startCvUpload}
              disabled={cvBusy}>
              {cvBusy ? t.landing.cvReading : t.prefs.uploadCv}
            </button>
            <input ref={cvInputRef} type="file" accept=".pdf,.docx"
              style={{ display: "none" }} aria-hidden
              onChange={(e) => onCVFile(e.target.files?.[0])} />
          </div>
          <div className="field">
            <label>{t.prefs.roles}</label>
            <div className="chips">
              {roleOpts.map((o) => (
                <button key={o} type="button" className="chip" aria-pressed={roleSet.has(o)}
                  onClick={() => toggleInSet(setRoleSet, o)}>{roleLabel(o)}</button>
              ))}
            </div>
            <div className="addwrap">
              <input type="text" value={addRole} onChange={(e) => setAddRole(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addChip(addRole, roleOpts, setRoleOpts, setRoleSet, () => setAddRole("")); } }}
                placeholder={t.prefs.addRolePlaceholder} aria-label={t.prefs.addRoleAria} />
              <button type="button" onClick={() => addChip(addRole, roleOpts, setRoleOpts, setRoleSet, () => setAddRole(""))}>{t.common.add}</button>
            </div>
          </div>
          <div className="field">
            <label>{t.prefs.skills}</label>
            <div className="chips">
              {skillOpts.map((o) => (
                <button key={o} type="button" className="chip" aria-pressed={skillSet.has(o)}
                  onClick={() => toggleInSet(setSkillSet, o)}>{o}</button>
              ))}
            </div>
            <div className="addwrap">
              <input type="text" value={addSkill} onChange={(e) => setAddSkill(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addChip(addSkill, skillOpts, setSkillOpts, setSkillSet, () => setAddSkill("")); } }}
                placeholder={t.prefs.addSkillPlaceholder} aria-label={t.prefs.addSkillAria} />
              <button type="button" onClick={() => addChip(addSkill, skillOpts, setSkillOpts, setSkillSet, () => setAddSkill(""))}>{t.common.add}</button>
            </div>
          </div>
          <div className="field">
            <label>{t.prefs.sectors}</label>
            <div className="chips">
              {sectorOpts.map((o) => (
                <button key={o} type="button" className="chip" aria-pressed={sectorSet.has(o)}
                  onClick={() => toggleInSet(setSectorSet, o)}>{o}</button>
              ))}
            </div>
            <div className="addwrap">
              <input type="text" value={addSector} onChange={(e) => setAddSector(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addChip(addSector, sectorOpts, setSectorOpts, setSectorSet, () => setAddSector("")); } }}
                placeholder={t.prefs.addSectorPlaceholder} aria-label={t.prefs.addSectorAria} />
              <button type="button" onClick={() => addChip(addSector, sectorOpts, setSectorOpts, setSectorSet, () => setAddSector(""))}>{t.common.add}</button>
            </div>
            <p style={{ color: "var(--muted)", fontSize: "var(--fs-sm)", marginTop: 6 }}>
              {t.prefs.sectorsHint}
            </p>
          </div>
          <div className="field">
            <label>{t.prefs.seniorityLabel}</label>
            <div className="chips">
              {SENIORITY_IDS.map((o) => (
                <button key={o} type="button" className="chip" aria-pressed={levels.has(o)}
                  onClick={() => toggleInSet(setLevels, o)}>{t.seniorities[o]}</button>
              ))}
            </div>
          </div>
          <div className="field">
            <label htmlFor="p-years">{t.prefs.yearsExperience}</label>
            <input id="p-years" type="number" inputMode="numeric" min={0} max={50} step={1}
              value={years} style={{ maxWidth: 120 }}
              onChange={(e) => setYears(e.target.value)} />
            <p style={{ color: "var(--muted)", fontSize: "var(--fs-sm)", marginTop: 6 }}>
              {t.prefs.yearsExperienceHint}
            </p>
          </div>
          <LocationPicker value={loc} onChange={setLoc} idPrefix="p" />

          <EducationPicker value={edu} onChange={setEdu} idPrefix="p" />

          <div className="field">
            <label htmlFor="p-freq">{t.prefs.frequency}</label>
            <select id="p-freq" value={freq} onChange={(e) => setFreq(e.target.value)}>
              <option value="daily">{t.prefs.freqDaily}</option>
              <option value="weekdays">{t.prefs.freqWeekdays}</option>
              <option value="weekly">{t.prefs.freqWeekly}</option>
            </select>
          </div>

          <div className="pause-row">
            <div className="t">
              <b>{paused ? t.prefs.pausedTitle : t.prefs.pauseTitle}</b>
              <span>
                {paused
                  ? fmt(t.prefs.pausedUntil, {
                    date: prefs.paused_until
                      ? new Date(prefs.paused_until).toLocaleDateString(undefined)
                      : t.prefs.pausedUntilSoon,
                  })
                  : t.prefs.pauseBody}
              </span>
            </div>
            {paused ? (
              <button className="btn secondary" onClick={doResume}>{t.prefs.resumeNow}</button>
            ) : (
              <button className="btn secondary" onClick={doPause}>{t.prefs.pauseTwoWeeks}</button>
            )}
          </div>

          <button className="btn block" style={{ marginTop: 16 }} onClick={save} disabled={saving}>
            {saving ? t.prefs.saving : t.prefs.save}
          </button>
          <div style={{ textAlign: "center", marginTop: 14 }}>
            {tokenRef.current ? (
              // Fallback (cookie refused): use the token-based confirm page.
              <a href={unsubscribeUrl(tokenRef.current)} style={{ fontSize: "var(--fs-sm)", color: "var(--muted)" }}>
                {t.prefs.unsubscribeAll}
              </a>
            ) : confirmUnsub ? (
              <button type="button" className="linkbtn danger" onClick={doUnsubscribe}
                style={{ fontSize: "var(--fs-sm)" }}>
                {t.prefs.unsubscribeConfirm}
              </button>
            ) : (
              <button type="button" className="linkbtn" onClick={() => setConfirmUnsub(true)}
                style={{ fontSize: "var(--fs-sm)", color: "var(--muted)" }}>
                {t.prefs.unsubscribeAll}
              </button>
            )}
          </div>
        </div>
      </div>
      {toast}
    </>
  );
}

export default function PreferencesPage() {
  return (
    <>
      <Nav />
      <main>
        <Suspense fallback={<Loading />}>
          <Inner />
        </Suspense>
      </main>
      <Footer />
    </>
  );
}

function Loading() {
  const { t } = useI18n();
  return <div className="state-wrap"><div className="state-card"><h1>{t.common.loading}</h1></div></div>;
}
