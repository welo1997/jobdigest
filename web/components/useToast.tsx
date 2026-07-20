"use client";

import { useCallback, useRef, useState } from "react";

/** Minimal toast: returns a `show(msg)` fn and the element to render once. */
export function useToast() {
  const [msg, setMsg] = useState("");
  const [visible, setVisible] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = useCallback((text: string) => {
    setMsg(text);
    setVisible(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setVisible(false), 2000);
  }, []);

  const element = (
    <div className={`toast${visible ? " show" : ""}`} role="status" aria-live="polite">
      {msg}
    </div>
  );

  return { show, element };
}
