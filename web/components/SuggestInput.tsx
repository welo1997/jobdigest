"use client";

import { useId, useMemo, useRef, useState } from "react";
import { normalizeRoleText } from "@/lib/options";

/**
 * A text box that *suggests* without constraining: the visitor may always submit something the
 * option list has never heard of.
 *
 * It replaces a native `<datalist>`, and the reason is the one thing a datalist cannot do. The
 * browser draws that popup itself, outside the document — so its width, font and colours are
 * unreachable from CSS, and Chrome sizes it to its content, which on a wide field reads as a
 * narrow strip hanging under one end of the box. Owning the list is the only way to make it
 * match the input, and once we own it we also own the ARIA the native widget gave us for free:
 * hence `role="combobox"`, `aria-expanded`, `aria-controls`, `aria-activedescendant` and a
 * `role="listbox"` of `role="option"`s. That is the cost of the change, and it is the whole cost
 * — no dependency, and nothing here needs a server.
 *
 * **The free-text path is load-bearing, not a convenience.** This control is how someone says
 * "Sales" or "Cybersecurity", roles the taxonomy models with nothing; CLAUDE.md's rule is that
 * every lookup in front of the matcher must be able to say "I don't know" and hand off rather
 * than drop. So Enter with nothing highlighted submits exactly what was typed, `onSubmit` is
 * never given anything the visitor did not either type or pick, and there is deliberately no
 * "must match an option" state anywhere in here.
 *
 * Matching folds case and accents through `normalizeRoleText` — the same fold
 * `resolveRoleId` uses, imported rather than reimplemented — so a Czech visitor typing
 * "datovy" still sees "Datový analytik". A substring match, like the datalist it replaces.
 */
export function SuggestInput({
  value,
  onChange,
  onSubmit,
  options,
  placeholder,
  ariaLabel,
  inputId,
}: {
  value: string;
  onChange: (next: string) => void;
  /** The text to add: what was typed, or the option that was picked. Never a filtered value. */
  onSubmit: (chosen: string) => void;
  options: string[];
  placeholder?: string;
  ariaLabel?: string;
  inputId?: string;
}) {
  const [open, setOpen] = useState(false);
  // -1 means "no option highlighted", which is the state Enter must treat as "submit my text".
  // It is the resting state on purpose: arrowing up off the top of the list returns to it, so
  // the typed words are always one key away from being submitted as typed.
  const [active, setActive] = useState(-1);
  const listId = `${useId()}-list`;
  const optionId = (i: number) => `${listId}-${i}`;
  const boxRef = useRef<HTMLInputElement>(null);

  const matches = useMemo(() => {
    const needle = normalizeRoleText(value);
    if (!needle) return options;
    return options.filter((o) => normalizeRoleText(o).includes(needle));
  }, [options, value]);

  // Clamped rather than reset in an effect: `matches` shrinks as the visitor types, and an
  // index left pointing past the end would put `aria-activedescendant` on an id that no longer
  // exists — a screen reader announcing nothing, with no visible symptom.
  const activeIndex = active < matches.length ? active : -1;
  const showList = open && matches.length > 0;

  const choose = (text: string) => {
    onSubmit(text);
    setActive(-1);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) return setOpen(true);
      setActive(activeIndex + 1 >= matches.length ? 0 : activeIndex + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) return setOpen(true);
      setActive(activeIndex <= 0 ? -1 : activeIndex - 1);
    } else if (e.key === "Enter") {
      // Always prevented: this control lives inside the wizard, and a bare Enter here used to
      // be the thing that added the role rather than advancing a step.
      e.preventDefault();
      choose(showList && activeIndex >= 0 ? matches[activeIndex] : value);
    } else if (e.key === "Escape") {
      // Only swallowed while the list is open, so Escape keeps whatever meaning the page gives
      // it once there is no popup to dismiss.
      if (showList) {
        e.preventDefault();
        setOpen(false);
        setActive(-1);
      }
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <div className="sugg">
      <input
        id={inputId}
        ref={boxRef}
        type="text"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showList && activeIndex >= 0 ? optionId(activeIndex) : undefined}
        // The browser's own form-history dropdown would otherwise open on top of this one.
        autoComplete="off"
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setActive(-1);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        // Closing on blur is what handles a click anywhere else on the page, so no
        // document-level listener is needed. It is safe only because the options below
        // suppress the blur they would otherwise cause — see the `onMouseDown` there.
        onBlur={() => { setOpen(false); setActive(-1); }}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        aria-label={ariaLabel}
      />
      {showList && (
        <ul className="sugg-list" id={listId} role="listbox" aria-label={ariaLabel}>
          {matches.map((o, i) => (
            <li
              key={o}
              id={optionId(i)}
              role="option"
              aria-selected={i === activeIndex}
              // Without this the input blurs on press, the list unmounts, and the click lands
              // on whatever moved under the pointer — so the option could never be clicked.
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(o)}
              onMouseEnter={() => setActive(i)}
            >
              {o}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
