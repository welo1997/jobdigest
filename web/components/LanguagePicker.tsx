"use client";

/**
 * Which languages a subscriber can read. Sixth axis, shaped like `EducationPicker` — one
 * component, shared by the signup wizard and the preferences page, so the control cannot drift
 * between them.
 *
 * Two things the copy has to get across:
 *
 *   - **Empty means "no preference", never "nothing is acceptable".** With nothing ticked the
 *     gate applies no filter at all — the safe default every existing subscriber sits in. That
 *     is what `cleanUnderstoodLanguages` enforces, mirroring `language.clean_languages`.
 *   - **English and unreadable ads are always kept.** English is the bulk of inventory and says
 *     nothing about what a job requires, and a posting whose language could not be read is shown
 *     too — so this only ever removes an ad *confidently* written in a language you did not pick.
 *
 * The chips render each language in its own name (endonym) from `lib/language.ts`, so the picker
 * reads identically in every UI locale; only the framing strings are translated.
 */

import { CSSProperties } from "react";
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

  const toggle = (code: LanguageId) =>
    onChange(
      picked.includes(code)
        ? picked.filter((c) => c !== code)
        : LANGUAGE_IDS.filter((c) => c === code || picked.includes(c)),
    );

  return (
    <div className="field">
      <label>
        {t.language.label}
        {" — "}
        <span style={{ fontWeight: 400, color: "var(--muted)" }}>
          {noPreference ? t.language.any : t.language.narrowed}
        </span>
      </label>
      <div className="chips">
        {LANGUAGE_IDS.map((c) => (
          <button key={c} type="button" className="chip"
            aria-pressed={picked.includes(c)}
            onClick={() => toggle(c)}>
            {LANGUAGE_ENDONYM[c]}
          </button>
        ))}
      </div>
      <p style={HINT}>{t.language.note}</p>
    </div>
  );
}
