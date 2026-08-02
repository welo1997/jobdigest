"use client";

/**
 * Which education requirements a subscriber will accept, plus what they studied.
 *
 * Shared by the signup wizard and the preferences page, for the same reason `LocationPicker`
 * is: the "Where" control was once copy-pasted into three pages and they drifted apart
 * silently. One component, three callers.
 *
 * Three things the UI has to get across, and the third is the one that is easy to get wrong:
 *
 *   - **This is a set, not a ceiling.** The chips are the requirement levels a job may state,
 *     lowest first, and ticking one means "a job asking for this is fine". Someone with a
 *     bachelor's normally ticks the first three. Nothing in the code ranks them, so unticking
 *     "High school" while leaving "Bachelor's" on is a legitimate — if unusual — thing to say.
 *   - **Unticking everything means "no preference", never "nothing is acceptable".** That is
 *     what `cleanEducationLevels` enforces, mirroring `education.clean_levels`; reading it the
 *     other way would silently empty somebody's digest.
 *   - **Most postings state no requirement and are always shown.** Only ~3.2% of live postings
 *     spell one out, because ~70% of the corpus carries no description text at all. The hint
 *     under the chips says so outright rather than letting the control imply a hard guarantee
 *     it cannot deliver.
 *
 * Field of study is free text and is **never** a filter — whether a degree is "in a related
 * field" is a judgement, so it travels to the AI matcher and stops there.
 */

import { CSSProperties } from "react";
import {
  EDUCATION_LEVELS, EducationLevel, MAX_FIELD_LEN, cleanEducationLevels,
} from "@/lib/education";
import { useI18n } from "@/i18n/context";

const HINT: CSSProperties = {
  color: "var(--muted)", fontSize: "var(--fs-sm)", marginTop: 6,
};

export interface EducationValue {
  levels: EducationLevel[];   // all five = no preference
  field: string;
}

export function EducationPicker({
  value, onChange, idPrefix = "edu",
}: {
  value: EducationValue;
  onChange: (next: EducationValue) => void;
  idPrefix?: string;
}) {
  const { t } = useI18n();
  const levels = cleanEducationLevels(value.levels);

  // Toggling off the last remaining level leaves an empty array, which every reader treats as
  // "no preference" (see `cleanEducationLevels` and `education.clean_levels`). The chips then
  // all render pressed again, which is the honest picture of what is being enforced.
  const toggle = (level: EducationLevel) =>
    onChange({
      ...value,
      levels: levels.includes(level)
        ? levels.filter((l) => l !== level)
        : EDUCATION_LEVELS.filter((l) => l === level || levels.includes(l)),
    });

  return (
    <>
      <div className="field">
        <label>
          {t.education.label}
          {" — "}
          <span style={{ fontWeight: 400, color: "var(--muted)" }}>
            {levels.length === EDUCATION_LEVELS.length
              ? t.education.anyLevel
              : t.education.narrowed}
          </span>
        </label>
        <div className="chips">
          {EDUCATION_LEVELS.map((l) => (
            <button key={l} type="button" className="chip"
              aria-pressed={levels.includes(l)}
              onClick={() => toggle(l)}>
              {t.educationLevels[l] ?? l}
            </button>
          ))}
        </div>
        <p style={HINT}>{t.education.note}</p>
      </div>

      <div className="field">
        <label htmlFor={`${idPrefix}-field`}>{t.education.fieldLabel}</label>
        <input id={`${idPrefix}-field`} type="text" value={value.field}
          maxLength={MAX_FIELD_LEN}
          placeholder={t.education.fieldPlaceholder}
          onChange={(e) => onChange({ ...value, field: e.target.value })} />
        <p style={HINT}>{t.education.fieldHint}</p>
      </div>
    </>
  );
}
