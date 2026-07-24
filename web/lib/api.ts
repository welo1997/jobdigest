// Thin client for the public digest API (service/webapp.py).

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8811";

export interface CVSignals {
  skills: string[];
  role_categories: string[];
  seniorities: string[];
  sectors: string[];
  years_experience: number | null;
  summary: string;
}

export interface SubscribePayload {
  email: string;
  label?: string;
  stack?: string[];
  seniorities?: string[];
  regions?: string[];
  role_categories?: string[];
  work_types?: string[];
  part_time_only?: boolean;
  eligible_only?: boolean;
  sectors?: string[];
  min_score?: number;
  frequency?: string;
  cv_signals?: CVSignals | null;
  cf_turnstile_token?: string | null;
}

export interface Preferences {
  email: string;
  status: string;
  label: string;
  stack: string[];
  seniorities: string[];
  regions: string[];
  role_categories: string[];
  work_types: string[];
  part_time_only: boolean;
  eligible_only: boolean;
  sectors: string[];
  min_score: number;
  frequency: string;
  has_cv: boolean;
  cv_summary: string | null;
  years_experience: number | null;
  paused_until: string | null;
}

export interface MatchJob {
  posting_id: string;
  title: string | null;
  company: string | null;
  url: string | null;
  location: string | null;
  region: string | null;
  seniority: string | null;
  work_type: string | null;
  role_category: string | null;
  salary: string | null;
  score: number | null;
  summary: string | null;
  posted_at: string | null;
}

export interface MatchesResponse {
  email: string;
  label: string;
  count: number;
  jobs: MatchJob[];
}

// Instant keyword preview shown right after signup (POST /preview). No score — this is
// keyword relevance, not the AI's judged fit (that arrives by email the next morning).
export interface PreviewJobCard {
  posting_id: string;
  title: string | null;
  company: string | null;
  url: string | null;
  location: string | null;
  region: string | null;
  seniority: string | null;
  work_type: string | null;
  tags: string[];
  why: string;
}

export interface PreviewResponse {
  count: number;
  jobs: PreviewJobCard[];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    // Send/receive the session cookie (same-origin in prod, CORS-credentialed in dev).
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      // CSRF token for cookie-authenticated writes. Harmless on reads and on token-in-body
      // calls; the backend only requires it when auth comes from the cookie on a write.
      "X-JobDigest-Auth": "1",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function subscribe(payload: SubscribePayload) {
  return req<{ ok: boolean; status: string; message: string }>("/subscribe", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// Best-effort: never send the Turnstile token here (it's single-use and consumed by
// /subscribe). Callers should treat a rejection as "no instant matches", not an error.
export function preview(payload: SubscribePayload) {
  return req<PreviewResponse>("/preview", {
    method: "POST",
    body: JSON.stringify({ ...payload, cf_turnstile_token: null }),
  });
}

export async function parseCV(
  file: File,
  turnstileToken?: string | null
): Promise<CVSignals> {
  const form = new FormData();
  form.append("file", file);
  if (turnstileToken) form.append("cf_turnstile_token", turnstileToken);
  const res = await fetch(`${API_URL}/cv/parse`, { method: "POST", body: form });
  if (!res.ok) {
    let detail = "Couldn't read that file.";
    try {
      detail = (await res.json())?.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return (await res.json()).signals as CVSignals;
}

// Passwordless "email me my settings link" — for a subscriber who lost their private link.
// The API always returns the same generic message whether or not the address is subscribed
// (no enumeration), so the UI shows that message regardless of the address entered.
export function requestManageLink(email: string) {
  return req<{ ok: boolean; message: string }>("/manage-link", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

// --- login sessions (persisted magic links) -------------------------------------
// JobDigest stays passwordless. Clicking a magic link is the login: the page trades its
// one-time `?token=` for a session cookie via establishSession, drops the token from the URL,
// then rides the cookie. All the calls below accept an optional token so they still work if
// cookies are blocked (the magic-link fallback) — omit it and the cookie authenticates.

// Exchange a magic-link token for a session cookie. Returns the subscriber's public profile.
export function establishSession(token: string) {
  return req<Preferences>("/session", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

// Who is this browser logged in as? Rejects (401) if there is no valid session cookie.
export function getSession() {
  return req<Preferences>("/session");
}

export function logout() {
  return req<{ ok: boolean }>("/logout", { method: "POST" });
}

const tokenQuery = (token?: string) =>
  token ? `?token=${encodeURIComponent(token)}` : "";

export function getPreferences(token?: string) {
  return req<Preferences>(`/preferences${tokenQuery(token)}`);
}

export function getMatches(token?: string) {
  return req<MatchesResponse>(`/matches${tokenQuery(token)}`);
}

export function updatePreferences(changes: Partial<Preferences>, token?: string) {
  return req<Preferences>("/preferences", {
    method: "POST",
    body: JSON.stringify(token ? { token, ...changes } : changes),
  });
}

export function pause(days = 14, token?: string) {
  return req<{ ok: boolean; status: string; paused_until: string }>("/pause", {
    method: "POST",
    body: JSON.stringify(token ? { token, days } : { days }),
  });
}

export function resume(token?: string) {
  return req<{ ok: boolean; status: string }>("/resume", {
    method: "POST",
    body: JSON.stringify(token ? { token } : {}),
  });
}

// Logged-in unsubscribe: no token, authenticated by the session cookie. The backend tears
// down every session for the profile and clears the cookie, so we send it form-encoded
// (the endpoint parses form fields) with no token and no `confirm` -> JSON reply.
export function unsubscribeSession() {
  return req<{ ok: boolean }>("/unsubscribe", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: "",
  });
}

// "Sign in with Google" — a login for existing subscribers. Gated at build time so a copy
// without a Google OAuth client shows no dead button; the backend endpoints 404 when the
// server-side credentials are absent, so the two gates fail closed together.
export const GOOGLE_AUTH_ENABLED = process.env.NEXT_PUBLIC_GOOGLE_AUTH === "1";

export function googleAuthUrl() {
  return `${API_URL}/auth/google/start`;
}

export function confirmUrl(token: string) {
  return `${API_URL}/confirm?token=${encodeURIComponent(token)}`;
}

export function unsubscribeUrl(token: string) {
  return `${API_URL}/unsubscribe?token=${encodeURIComponent(token)}`;
}
