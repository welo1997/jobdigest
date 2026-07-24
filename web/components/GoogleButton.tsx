"use client";

import { GOOGLE_AUTH_ENABLED, googleAuthUrl } from "@/lib/api";

/**
 * "Continue with Google" — a one-click login for existing subscribers. It's a plain link to
 * the backend's OAuth start endpoint (a full-page navigation, as OAuth requires), not a fetch.
 * Renders nothing unless the build was configured with NEXT_PUBLIC_GOOGLE_AUTH=1, so a copy
 * without a Google client never shows a dead button.
 */
const GMark = () => (
  <svg className="gmark" viewBox="0 0 18 18" aria-hidden="true" width="18" height="18">
    <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.71-1.57 2.68-3.89 2.68-6.62z" />
    <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
    <path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z" />
    <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
  </svg>
);

/**
 * "Continue with Google". Renders nothing unless the build set NEXT_PUBLIC_GOOGLE_AUTH=1.
 * With no `onClick` it's a plain link to the OAuth start endpoint (the login-page case). Pass
 * `onClick` (e.g. the signup wizard, which must save its state before the full-page redirect)
 * to get a <button> that runs your handler instead.
 */
export default function GoogleButton(
  { label = "Continue with Google", onClick }: { label?: string; onClick?: () => void }
) {
  if (!GOOGLE_AUTH_ENABLED) return null;
  const inner = (<><GMark /><span>{label}</span></>);
  if (onClick)
    return <button type="button" className="gbtn" onClick={onClick}>{inner}</button>;
  return <a className="gbtn" href={googleAuthUrl()}>{inner}</a>;
}
