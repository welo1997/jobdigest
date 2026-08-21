"use client";

/**
 * Which languages a subscriber can read. Sixth axis, shaped like `EducationPicker` — one
 * component, shared by the signup wizard and the preferences page, so the control cannot drift
 * between them.
 *
 * A multi-select dropdown, not an inline chip wall: 27 languages laid out as buttons ran several
 * rows deep and read as noise on a form nobody needs to touch. The trigger shows the chosen
 * languages *as they are* — each in its own name (endonym) from `lib/language.ts` — so a glance
 * says what is picked without opening it, and it reads identically in every UI locale. Same
 * tick-box popover as the `/matches` and `/jobs` filters (`.skill-menu`), only the trigger
 * differs: it fills the field and reads like the native <select>s beside it.
 *
 * Two things the copy has to get across:
 *
 *   - **Empty means "no preference", never "nothing is acceptable".** With nothing ticked the
 *     gate applies no filter at all — the safe default every existing subscriber sits in. That
 *     is what `cleanUnderstoodLanguages` enforces, mirroring `language.clean_languages`.
 *   - **English and unreadable ads are always kept.** English is the bulk of inventory and says
 *     nothing about what a job requires, and a posting whose language could not be read is shown
 *     too — so this only ever removes an ad *confidently* written in a language you did not pick.
 */

import { CSSProperties, useEffect, useRef, useState } from "react";
import {
  LANGUAGE_IDS, LanguageId, LANGUAGE_ENDONYM, cleanUnderstoodLanguages,
} from "@/lib/language";
import { useI18n } from "@/i18n/context";

const HINT: CSSProperties = {
  color: "var(--muted)", fontSize: "var(--fs-sm)", marginTop: 6,
};

export function LanguagePicker({
  value, onChange,
}: {
  value: LanguageId[];
  onChange: (next: LanguageId[]) => void;
}) {
  const { t } = useI18n();
  const picked = cleanUnderstoodLanguages(value);
  const noPreference = picked.length === 0;

  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on an outside click or Escape — same behaviour as the filter menus so the two feel
  // like one control vocabulary. Selecting a language does NOT close it: this is multi-select.
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

  const toggle = (code: LanguageId) =>
    onChange(
      picked.includes(code)
        ? picked.filter((c) => c !== code)
        : LANGUAGE_IDS.filter((c) => c === code || picked.includes(c)),
    );

  // The chosen languages, in stable `LANGUAGE_IDS` order, shown verbatim in the trigger.
  const summary = noPreference
    ? t.language.any
    : picked.map((c) => LANGUAGE_ENDONYM[c]).join(", ");

  return (
    <div className="field">
      <label>{t.language.label}</label>
      <div className="lang-select" ref={ref}>
        <button
          type="button"
          className={`skill-menu-btn${noPreference ? "" : " on"}`}
          aria-haspopup="listbox"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          <span className={`val${noPreference ? " none" : ""}`}>{summary}</span>
          <span className="caret" aria-hidden>▾</span>
        </button>
        {open && (
          <div className="skill-menu-pop">
            <div className="skill-menu-list" role="listbox" aria-multiselectable="true">
              {LANGUAGE_IDS.map((c) => {
                const on = picked.includes(c);
                return (
                  <button
                    type="button"
                    key={c}
                    role="option"
                    className={`skill-opt${on ? " on" : ""}`}
                    aria-selected={on}
                    aria-label={LANGUAGE_ENDONYM[c]}
                    onClick={() => toggle(c)}
                  >
                    <span className="box" aria-hidden>{on ? "✓" : ""}</span>
                    <span className="nm">{LANGUAGE_ENDONYM[c]}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
      <p style={HINT}>{t.language.note}</p>
    </div>
  );
}
