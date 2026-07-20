"use client";

import { useEffect, useState } from "react";

/** Light/dark toggle that stamps data-theme on <html> (see globals.css overrides). */
export default function ThemeToggle() {
  const [theme, setTheme] = useState<string | null>(null);

  useEffect(() => {
    const stored = localStorage.getItem("theme");
    if (stored) {
      document.documentElement.setAttribute("data-theme", stored);
      setTheme(stored);
    }
  }, []);

  const toggle = () => {
    const current =
      document.documentElement.getAttribute("data-theme") ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    setTheme(next);
  };

  return (
    <button className="themebtn" onClick={toggle} title="Toggle light / dark" aria-label="Toggle theme">
      {theme === "dark" ? "☀" : "◑"}
    </button>
  );
}
