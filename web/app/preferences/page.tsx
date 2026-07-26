"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";
import { useToast } from "@/components/useToast";
import {
  establishSession, getPreferences, logout, pause, Preferences, resume,
  unsubscribeSession, unsubscribeUrl, updatePreferences,
} from "@/lib/api";
import { cap } from "@/lib/preview";

const FREqS = ["daily", "weekdays", "weekly"];
// Chip vocabularies mirror the signup wizard (web/app/page.tsx) so both forms speak the same
// role_category language — free-text boxes let a user type a "role" that maps to no category.
const ROLE_OPTS = ["Product Manager", "Marketing", "Social Media", "Data Analyst", "Designer", "Software Engineer", "Data Engineer", "DevOps", "Finance"];
const SKILL_OPTS = ["SQL", "Figma", "Analytics", "Excel", "Python", "SEO", "Looker", "Roadmapping", "Power BI", "dbt"];
const ROLE_CAT: Record<string, string> = {
  "Data Engineer": "data_engineering", "Data Analyst": "data_analysis",
  "Software Engineer": "software_engineering", "DevOps": "devops_platform",
  "Product Manager": "product", "Designer": "design",
  "Social Media": "social_media",
  "Marketing": "other_tech_function", "Finance": "other_tech_function",
};
// slug -> a representative display label for preselecting chips on load. other_tech_function
// is a bucket (Marketing/Finance both map to it); we show "Marketing" and accept the minor
// lossiness — no worse than the old prettified "Other Tech Function", and better for the rest.
const LABEL_FOR_SLUG: Record<string, string> = {
  data_engineering: "Data Engineer", data_analysis: "Data Analyst",
  software_engineering: "Software Engineer", devops_platform: "DevOps",
  product: "Product Manager", design: "Designer", social_media: "Social Media",
  other_tech_function: "Marketing",
};
const REGION_LABEL_TO_CODES: Record<string, string[]> = {
  "Czechia": ["cz"], "EU remote": ["cz", "eu"], "Worldwide remote": ["cz", "eu", "worldwide"],
};
function regionLabel(codes: string[]): string {
  const key = [...codes].sort().join(",");
  if (key === "cz") return "Czechia";
  if (key === "cz,eu") return "EU remote";
  return "Worldwide remote";
}
const prettify = (c: string) => c.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());

const SENIORITY_OPTS = ["Intern / Junior", "Mid", "Senior"];
const SENIORITY_CODE: Record<string, string> = { "Intern / Junior": "junior", "Mid": "mid", "Senior": "senior" };
const CODE_SENIORITY: Record<string, string> = { junior: "Intern / Junior", mid: "Mid", senior: "Senior" };

function Inner() {
  const urlToken = useSearchParams().get("token") || "";
  // The magic-link token is used once to mint a session, then dropped from the URL. After
  // that this ref is "" and every call authenticates by cookie. It stays set only in the
  // fallback where the browser refused the cookie, so token-based calls keep the page working.
  const tokenRef = useRef<string>(urlToken);
  const { show, element: toast } = useToast();
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmUnsub, setConfirmUnsub] = useState(false);

  // editable fields
  const [roleOpts, setRoleOpts] = useState<string[]>(ROLE_OPTS);
  const [roleSet, setRoleSet] = useState<Set<string>>(new Set());
  const [skillOpts, setSkillOpts] = useState<string[]>(SKILL_OPTS);
  const [skillSet, setSkillSet] = useState<Set<string>>(new Set());
  const [addRole, setAddRole] = useState("");
  const [addSkill, setAddSkill] = useState("");
  const [freq, setFreq] = useState("daily");
  const [regionSel, setRegionSel] = useState("EU remote");
  const [levels, setLevels] = useState<Set<string>>(new Set());

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
      const roleLabels = p.role_categories.map((c) => LABEL_FOR_SLUG[c] || prettify(c));
      setRoleOpts([...new Set([...ROLE_OPTS, ...roleLabels])]);
      setRoleSet(new Set(roleLabels));
      const skillLabels = p.stack.map((s) => cap(s));
      setSkillOpts([...new Set([...SKILL_OPTS, ...skillLabels])]);
      setSkillSet(new Set(skillLabels));
      setFreq(FREqS.includes(p.frequency) ? p.frequency : "daily");
      setRegionSel(regionLabel(p.regions));
      setLevels(new Set((p.seniorities || []).map((c) => CODE_SENIORITY[c]).filter(Boolean)));
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
              window.history.replaceState(null, "", "/preferences");
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
            ? e instanceof Error ? e.message : "Unknown or expired link."
            : "Open your preferences from the link in your email — or use “Manage subscription” to get a fresh one."
        );
      }
    };
    load();
    return () => { cancelled = true; };
  }, [urlToken]);

  const save = async () => {
    setSaving(true);
    try {
      // Empty selection means "any level" — send all three rather than an empty target,
      // which the hard-filter matcher would read as "nothing matches".
      const seniorities = [...levels].map((l) => SENIORITY_CODE[l]).filter(Boolean);
      // Dedup slugs: Marketing + Finance both map to other_tech_function.
      // A typed role chip that maps to no category used to be slugified into one anyway
      // ("Social media specialist" -> social_media_specialist), producing a filter no posting
      // could ever match and no error anywhere — the user saw a selected chip doing nothing.
      // Unmapped chips now become search keywords instead, which the shortlist full-text
      // query does use, so the words still steer retrieval and nothing is silently dead.
      const roleSlugs = [...new Set([...roleSet].map((l) => ROLE_CAT[l]).filter(Boolean))];
      const freeRoles = [...roleSet].filter((l) => !ROLE_CAT[l]);
      const updated = await updatePreferences({
        role_categories: roleSlugs,
        stack: [...new Set([...skillSet, ...freeRoles].map((s) => s.trim().toLowerCase()).filter(Boolean))],
        frequency: freq,
        regions: REGION_LABEL_TO_CODES[regionSel] || ["cz", "eu", "worldwide"],
        seniorities: seniorities.length ? seniorities : ["junior", "mid", "senior"],
      }, tokenRef.current || undefined);
      setPrefs(updated);
      show("Preferences saved");
    } catch (e) {
      show(e instanceof Error ? e.message : "Couldn't save — please retry");
    } finally {
      setSaving(false);
    }
  };

  const doPause = async () => {
    try {
      const r = await pause(14, tokenRef.current || undefined);
      setPrefs((p) => (p ? { ...p, status: "paused", paused_until: r.paused_until } : p));
      show("Digest paused for 2 weeks");
    } catch (e) {
      show(e instanceof Error ? e.message : "Couldn't pause");
    }
  };
  const doResume = async () => {
    try {
      await resume(tokenRef.current || undefined);
      setPrefs((p) => (p ? { ...p, status: "active", paused_until: null } : p));
      show("Digest resumed");
    } catch (e) {
      show(e instanceof Error ? e.message : "Couldn't resume");
    }
  };

  const doLogout = async () => {
    try { await logout(); } catch { /* clearing the cookie is best-effort */ }
    if (typeof window !== "undefined") window.location.href = "/";
  };

  const doUnsubscribe = async () => {
    // Cookie-authenticated, in-page unsubscribe (no token in the URL). Two-step: the first
    // click arms it, the second performs it — a click is a POST, never a GET, so a link
    // scanner can't trigger it.
    try {
      await unsubscribeSession();
      show("You've unsubscribed");
      if (typeof window !== "undefined") setTimeout(() => (window.location.href = "/"), 900);
    } catch (e) {
      show(e instanceof Error ? e.message : "Couldn't unsubscribe");
    }
  };

  if (err) {
    return (
      <div className="state-wrap">
        <div className="state-card warn">
          <div className="ic">!</div>
          <h1>Can&apos;t open your preferences</h1>
          <p>{err}</p>
          <p style={{ marginTop: 16 }}><Link href="/">Go to homepage →</Link></p>
        </div>
      </div>
    );
  }
  if (!prefs) {
    return <div className="state-wrap"><div className="state-card"><h1>Loading…</h1></div></div>;
  }

  const paused = prefs.status === "paused";
  return (
    <>
      <div className="wrap page-head">
        <span className="label">Signed in</span>
        <h1>Your preferences</h1>
        <p>
          Signed in as <b>{prefs.email}</b>. Change anything, pause, or leave —
          {" "}<button type="button" className="linkbtn" onClick={doLogout}>log out</button>.
        </p>
      </div>
      <div className="note">
        <b>No password.</b> Clicking your email link signed you in and keeps you signed in on
        this device, so you won&apos;t need the link again here. Log out any time.
      </div>
      <div className="panel">
        <div className="card">
          {prefs.has_cv && prefs.cv_summary && (
            <div className="note" style={{ margin: "0 0 16px" }}>{prefs.cv_summary}</div>
          )}
          <div className="field">
            <label>Roles</label>
            <div className="chips">
              {roleOpts.map((o) => (
                <button key={o} type="button" className="chip" aria-pressed={roleSet.has(o)}
                  onClick={() => toggleInSet(setRoleSet, o)}>{o}</button>
              ))}
            </div>
            <div className="addwrap">
              <input type="text" value={addRole} onChange={(e) => setAddRole(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addChip(addRole, roleOpts, setRoleOpts, setRoleSet, () => setAddRole("")); } }}
                placeholder="Add a role…" aria-label="Add a role" />
              <button type="button" onClick={() => addChip(addRole, roleOpts, setRoleOpts, setRoleSet, () => setAddRole(""))}>Add</button>
            </div>
          </div>
          <div className="field">
            <label>Skills / keywords</label>
            <div className="chips">
              {skillOpts.map((o) => (
                <button key={o} type="button" className="chip" aria-pressed={skillSet.has(o)}
                  onClick={() => toggleInSet(setSkillSet, o)}>{o}</button>
              ))}
            </div>
            <div className="addwrap">
              <input type="text" value={addSkill} onChange={(e) => setAddSkill(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addChip(addSkill, skillOpts, setSkillOpts, setSkillSet, () => setAddSkill("")); } }}
                placeholder="Add a skill…" aria-label="Add a skill" />
              <button type="button" onClick={() => addChip(addSkill, skillOpts, setSkillOpts, setSkillSet, () => setAddSkill(""))}>Add</button>
            </div>
          </div>
          <div className="field">
            <label>Seniority — we only send roles at the levels you pick</label>
            <div className="chips">
              {SENIORITY_OPTS.map((o) => (
                <button key={o} type="button" className="chip" aria-pressed={levels.has(o)}
                  onClick={() => toggleInSet(setLevels, o)}>{o}</button>
              ))}
            </div>
          </div>
          <div className="row2">
            <div className="field">
              <label htmlFor="p-freq">Frequency</label>
              <select id="p-freq" value={freq} onChange={(e) => setFreq(e.target.value)}>
                <option value="daily">Daily</option>
                <option value="weekdays">Weekdays only</option>
                <option value="weekly">Weekly (Mondays)</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="p-region">Where</label>
              <select id="p-region" value={regionSel} onChange={(e) => setRegionSel(e.target.value)}>
                <option>Czechia</option>
                <option>EU remote</option>
                <option>Worldwide remote</option>
              </select>
            </div>
          </div>

          <div className="pause-row">
            <div className="t">
              <b>{paused ? "Digest paused" : "Pause my digest"}</b>
              <span>
                {paused
                  ? `Paused until ${prefs.paused_until ? new Date(prefs.paused_until).toLocaleDateString() : "soon"} — resume anytime.`
                  : "Take a break without unsubscribing — resumes when you're ready."}
              </span>
            </div>
            {paused ? (
              <button className="btn secondary" onClick={doResume}>Resume now</button>
            ) : (
              <button className="btn secondary" onClick={doPause}>Pause 2 weeks</button>
            )}
          </div>

          <button className="btn block" style={{ marginTop: 16 }} onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save changes"}
          </button>
          <div style={{ textAlign: "center", marginTop: 14 }}>
            {tokenRef.current ? (
              // Fallback (cookie refused): use the token-based confirm page.
              <a href={unsubscribeUrl(tokenRef.current)} style={{ fontSize: "var(--fs-sm)", color: "var(--muted)" }}>
                Unsubscribe from all emails
              </a>
            ) : confirmUnsub ? (
              <button type="button" className="linkbtn danger" onClick={doUnsubscribe}
                style={{ fontSize: "var(--fs-sm)" }}>
                Click again to confirm — unsubscribe from all emails
              </button>
            ) : (
              <button type="button" className="linkbtn" onClick={() => setConfirmUnsub(true)}
                style={{ fontSize: "var(--fs-sm)", color: "var(--muted)" }}>
                Unsubscribe from all emails
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
        <Suspense fallback={<div className="state-wrap"><div className="state-card"><h1>Loading…</h1></div></div>}>
          <Inner />
        </Suspense>
      </main>
      <Footer />
    </>
  );
}
