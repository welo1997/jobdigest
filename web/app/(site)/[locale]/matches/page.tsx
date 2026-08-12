"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Nav, Footer } from "@/components/SiteChrome";
import { MatchCard, SelectionBar } from "@/components/MatchCard";
import { setMatchesHidden } from "@/lib/api";
import { track } from "@/lib/analytics";
import { useMatchList } from "@/lib/useMatchList";
import { rich, useI18n } from "@/i18n/context";

// Chip label for a canonical skill id: "microsoft_office" → "Microsoft Office", keeping a
// small set of acronyms upper-cased. Cosmetic only — the stored/filtered value is the id, so
// this never has to agree with anything server-side.
const SKILL_ACRONYMS = new Set([
  "sql", "aws", "gcp", "php", "css", "html", "api", "sap", "bi", "ux", "ui", "qa",
  "ml", "ai", "sre", "dba", "crm", "erp", "ios", "k8s", "nlp", "llm", "cad",
]);
function skillLabel(id: string): string {
  return id
    .split(/[_-]/)
    .map((w) => (SKILL_ACRONYMS.has(w) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}

// A filter as a dropdown of tick boxes, not an inline chip wall — the skill version replaced
// ~80 chips five rows deep in production, which pushed the matches themselves below the fold.
// The options live one click down; the active filters stay on the row as removable chips
// (rendered by the caller). Multi-select is OR — every tick widens the list — which is the
// settled behaviour for all of them.
//
// Generic over the option id rather than copied per filter: skills and work setups differ only
// in their vocabulary and how an id is turned into a label, and a second copy of the
// outside-click/Escape handling is a second copy that can drift.
function FilterMenu({
  options,
  selected,
  onToggle,
  onClear,
  label,
  triggerLabel,
  clearLabel,
}: {
  options: { id: string; count: number }[];
  selected: string[];
  onToggle: (s: string) => void;
  onClear: () => void;
  /** id -> display text. Skills are data (never translated); work setups come from the
   *  catalogue, which is why this is the caller's job and not this component's. */
  label: (id: string) => string;
  triggerLabel: string;
  clearLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on an outside click or Escape. A menu that only closes by re-clicking its trigger
  // feels broken on a page you scroll, and it would sit open over the matches.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const n = selected.length;
  return (
    <div className="skill-menu" ref={ref}>
      <button
        type="button"
        className={`skill-menu-btn${n ? " on" : ""}`}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {triggerLabel}
        {n > 0 && <span className="n">{n}</span>}
        <span className="caret" aria-hidden>▾</span>
      </button>
      {open && (
        <div className="skill-menu-pop">
          <div className="skill-menu-list" role="group">
            {options.map((o) => {
              const on = selected.includes(o.id);
              return (
                <button
                  type="button"
                  key={o.id}
                  className={`skill-opt${on ? " on" : ""}`}
                  aria-pressed={on}
                  onClick={() => onToggle(o.id)}
                >
                  <span className="box" aria-hidden>{on ? "✓" : ""}</span>
                  <span className="nm">{label(o.id)}</span>
                  <span className="c">{o.count}</span>
                </button>
              );
            })}
          </div>
          {n > 0 && (
            <div className="skill-menu-foot">
              <button type="button" className="lnk" onClick={onClear}>
                {clearLabel}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Inner() {
  const { t, href, count } = useI18n();
  const [skills, setSkills] = useState<string[]>([]);
  const [workModes, setWorkModes] = useState<string[]>([]);
  const [greatFits, setGreatFits] = useState(false);
  const list = useMatchList({
    hidden: false,
    path: "/matches",
    skills,
    workModes,
    greatFits,
    // How many matches the page actually had — a page that routinely shows 0 or 1 is
    // a product problem, not a UI one.
    onLoaded: (d, token) => track("matches_viewed", { count: d.count }, token || undefined),
  });
  const { data, jobs } = list;
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [hiding, setHiding] = useState(false);
  const [hideErr, setHideErr] = useState(false);

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  // Toggling any filter refetches (via useMatchList's deps) with the new filter, resetting
  // paging — an offset only means anything against the filter it was counted under.
  const toggleIn = (set: (fn: (p: string[]) => string[]) => void) => (v: string) =>
    set((prev) => (prev.includes(v) ? prev.filter((x) => x !== v) : [...prev, v]));
  const toggleSkill = toggleIn(setSkills);
  const toggleWorkMode = toggleIn(setWorkModes);

  const hide = async () => {
    const ids = [...picked];
    if (!ids.length || hiding) return;
    setHiding(true);
    setHideErr(false);
    try {
      const res = await setMatchesHidden(ids, true, list.token || undefined);
      // Drop exactly what we asked to hide. The server's own fresh totals come back with it,
      // so the header and the "hidden" link never drift from what is stored.
      list.removeJobs(ids, res);
      setPicked(new Set());
    } catch {
      // The rows stay on screen and stay ticked: a failed hide must leave the subscriber
      // able to press the button again, not guessing which half went through.
      setHideErr(true);
    } finally {
      setHiding(false);
    }
  };

  const prefsHref = list.linkTo("/preferences");
  const hiddenHref = list.linkTo("/hidden");

  if (list.err) {
    return (
      <div className="state-wrap">
        <div className="state-card warn">
          <div className="ic">!</div>
          <h1>{t.matches.errTitle}</h1>
          <p>{list.err}</p>
          <p style={{ marginTop: 16 }}><Link href={href("/")}>{t.common.goHome}</Link></p>
        </div>
      </div>
    );
  }
  if (!data) {
    return <div className="state-wrap"><div className="state-card"><h1>{t.common.loading}</h1></div></div>;
  }

  // Rendered whenever anything is hidden, including on the empty state — someone who hid
  // their whole list must still have a way back to it.
  const hiddenLink = data.hidden_count > 0 && (
    <Link className="hidden-link" href={hiddenHref}>
      {count(data.hidden_count, t.matches.hiddenLink)}
    </Link>
  );

  // `facets` is the server's per-menu option set, each counted with the *other* filters
  // applied but never its own — so no single tick can empty the menu it came from.
  // `skill_facets` is the pre-facets shape, read as a fallback so a cached bundle talking to
  // a new server (or the reverse) still renders a filter row.
  const skillOpts = (data.facets?.skills ?? data.skill_facets ?? [])
    .map((f) => ({ id: f.skill, count: f.count }));
  const modeOpts = (data.facets?.work_modes ?? [])
    .map((f) => ({ id: f.work_mode, count: f.count }));
  const greatFitCount = data.facets?.great_fit_count ?? null;
  const filtering = skills.length > 0 || workModes.length > 0 || greatFits;

  // Each control appears only when it can actually discriminate. A subscriber's own
  // preferences already pin most of these axes — someone who asked for remote-only work has
  // one work setup across every match, and a menu offering that single option is furniture,
  // not a filter. The rule is the same one the row itself follows: render it when there is a
  // choice to make, or when it is already on and must stay clearable.
  const showModes = modeOpts.length > 1 || workModes.length > 0;
  const showGreatFits = (greatFitCount !== null && greatFitCount > 0) || greatFits;
  const showRow = skillOpts.length > 0 || showModes || showGreatFits || filtering;

  const filterRow = showRow && (
    <div className="wrap skill-filter">
      {skillOpts.length > 0 && (
        <FilterMenu
          options={skillOpts}
          selected={skills}
          onToggle={toggleSkill}
          onClear={() => setSkills([])}
          label={skillLabel}
          triggerLabel={t.matches.filterBySkill}
          clearLabel={t.matches.clearFilter}
        />
      )}
      {showModes && (
        <FilterMenu
          options={modeOpts}
          selected={workModes}
          onToggle={toggleWorkMode}
          onClear={() => setWorkModes([])}
          // Reused from the signup form's own labels rather than restated: one definition
          // per language, and "Fully remote" must not mean two different things on two pages.
          label={(m) => t.geo.workModeLabel[m] ?? m}
          triggerLabel={t.matches.filterByWorkMode}
          clearLabel={t.matches.clearFilter}
        />
      )}
      {showGreatFits && (
        <button
          type="button"
          className={`skill sel${greatFits ? " on" : ""}`}
          aria-pressed={greatFits}
          onClick={() => setGreatFits((v) => !v)}
        >
          {t.matches.greatFitsOnly}
          {greatFitCount !== null && <span className="c"> {greatFitCount}</span>}
        </button>
      )}
      {/* The ticked values stay on the row as removable chips. A tick can fall out of its own
          menu once the other filters narrow past it, so the chip — not the menu — is what
          guarantees a filter is always removable. */}
      {skills.map((s) => (
        <button
          type="button"
          key={s}
          className="skill sel on"
          aria-pressed={true}
          onClick={() => toggleSkill(s)}
        >
          {skillLabel(s)} <span className="x" aria-hidden>×</span>
        </button>
      ))}
      {workModes.map((m) => (
        <button
          type="button"
          key={m}
          className="skill sel on"
          aria-pressed={true}
          onClick={() => toggleWorkMode(m)}
        >
          {t.geo.workModeLabel[m] ?? m} <span className="x" aria-hidden>×</span>
        </button>
      ))}
    </div>
  );

  return (
    <>
      <div className="wrap page-head">
        <span className="label">{t.matches.label}</span>
        <h1>{data.count > 0 ? count(data.count, t.matches.countTitle) : t.matches.noneTitle}</h1>
        <p>{rich(t.matches.intro, [<b key="e">{data.email}</b>])}</p>
      </div>

      {data.count === 0 && !filtering ? (
        <div className="state-wrap">
          <div className="state-card">
            <h1>{t.matches.nothingTitle}</h1>
            <p>{t.matches.nothingBody}</p>
            <p style={{ marginTop: 16 }}>
              <Link href={prefsHref}>{t.matches.adjustPrefs}</Link>
            </p>
            {hiddenLink && <p style={{ marginTop: 12 }}>{hiddenLink}</p>}
          </div>
        </div>
      ) : (
        <>
          {filterRow}
          {data.count === 0 ? (
            <div className="wrap" style={{ marginTop: 8 }}>
              <p className="hint">{t.matches.nothingTitle}</p>
            </div>
          ) : (
            <>
          <div className="wrap list-tools">
            <p className="hint">{t.matches.hideHint}</p>
            <div className="row">
              <button type="button" className="lnk"
                onClick={() => setPicked(new Set(jobs.map((j) => j.posting_id)))}>
                {t.matches.selectAll}
              </button>
              {hiddenLink}
            </div>
          </div>

          <div className="matches">
            {jobs.map((j) => (
              <MatchCard
                key={j.posting_id}
                j={j}
                token={list.token || undefined}
                selected={picked.has(j.posting_id)}
                onToggle={toggle}
              />
            ))}
          </div>

          <div className="wrap more-wrap">
            {hideErr && <p className="more-err" role="alert">{t.matches.hideFailed}</p>}
            {list.hasMore && (
              <>
                {/* Stated before the button, so "load more" is a decision rather than a
                    guess at how much is left. */}
                <p className="more-count">
                  {count(data.count, t.matches.showing, { shown: jobs.length })}
                </p>
                <button type="button" className="btn more-btn"
                  onClick={list.loadMore} disabled={list.loadingMore}>
                  {list.loadingMore ? t.common.loading : t.matches.loadMore}
                </button>
              </>
            )}
            {list.moreErr && (
              <p className="more-err" role="alert">{t.matches.loadMoreFailed}</p>
            )}
            <p style={{ marginTop: 28 }}>
              <Link href={prefsHref}
                style={{ fontSize: "var(--fs-sm)", color: "var(--muted)" }}>
                {t.matches.notQuiteRight}
              </Link>
            </p>
          </div>
            </>
          )}

          <SelectionBar
            n={picked.size}
            busy={hiding}
            countLabel={count(picked.size, t.matches.selected)}
            label={t.matches.hideSelected}
            busyLabel={t.matches.hiding}
            clearLabel={t.matches.clearSelection}
            onConfirm={hide}
            onClear={() => setPicked(new Set())}
          />
        </>
      )}
    </>
  );
}

export default function MatchesPage() {
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
