// Thin client for the public digest API (service/webapp.py).

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8811";

export interface CVSignals {
  skills: string[];
  role_categories: string[];
  seniorities: string[];
  sectors: string[];
  years_experience: number | null;
  /** Highest qualification detected in the CV. Used to *pre-tick* the education chips so the
   *  person can see and correct it — never applied as a filter server-side, because narrowing
   *  a digest from a regex over a CV is a filter nobody chose. See `levelsUpTo`. */
  education: string | null;
  education_field: string | null;
  summary: string;
}

export interface SubscribePayload {
  email: string;
  label?: string;
  stack?: string[];
  seniorities?: string[];
  // Location: `countries`/`cities`/`remote_scope` are what the matcher filters on. `regions`
  // is the legacy coarse bucket, derived server-side — send it only if you have nothing else.
  countries?: string[];
  cities?: string[];
  remote_scope?: string;
  regions?: string[];
  // Which work setups they'll accept. Omit for "no preference" — all three.
  work_modes?: string[];
  // Which education requirements they'll accept. Omit for "no preference" — all five.
  // `education_field` is free text for the AI matcher and is never filtered on.
  education_levels?: string[];
  education_field?: string | null;
  role_categories?: string[];
  work_types?: string[];
  part_time_only?: boolean;
  eligible_only?: boolean;
  sectors?: string[];
  min_score?: number;
  frequency?: string;
  /** The locale the visitor signed up under. Decides which language every email to them is
   *  written in — the digest runs from a timer with no browser, so it cannot be inferred
   *  later. See service/db/migration_013_language.sql. */
  language?: string;
  cv_signals?: CVSignals | null;
  cf_turnstile_token?: string | null;
}

export interface Preferences {
  email: string;
  status: string;
  label: string;
  stack: string[];
  seniorities: string[];
  countries: string[];
  cities: string[];
  remote_scope: string;
  regions: string[];
  // Optional: absent on a subscription that predates migration 012. See `cleanWorkModes`.
  work_modes?: string[];
  // Optional: absent on a subscription that predates migration 014. `cleanEducationLevels`
  // reads that as "no preference" and returns all five, which is the column default too.
  education_levels?: string[];
  education_field?: string | null;
  role_categories: string[];
  work_types: string[];
  part_time_only: boolean;
  eligible_only: boolean;
  sectors: string[];
  min_score: number;
  frequency: string;
  /** Absent on a subscription that predates migration 013; the server defaults it to "en". */
  language?: string;
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
  work_mode: string | null;   // remote | hybrid | onsite | null = the ad never said
  /** Carried since /matches gained Country and City filters (2026-08-12). */
  country_code: string | null;
  city: string | null;
  role_category: string | null;
  salary: string | null;
  score: number | null;
  summary: string | null;
  /** Extracted tech/tool facet (migration 020). Canonical names, rendered as read-only chips
   *  untranslated (they are proper nouns). Always an array — empty when the ad named none. */
  skills: string[];
  posted_at: string | null;
}

export interface MatchesResponse {
  email: string;
  label: string;
  /** Total matches in *this* view (visible or hidden) — not the length of `jobs`, which is
   *  one page. */
  count: number;
  /** Total hidden matches, returned by both views so each can link to the other. */
  hidden_count: number;
  offset: number;
  limit: number;
  /** Which half this response is: false = the matches page, true = the hidden page. */
  hidden: boolean;
  /** The skill filter currently applied (validated server-side; junk is dropped). `count`
   *  and `jobs` already reflect it. */
  skills: string[];
  /** The work-setup filter currently applied. Same server-side validation and the same
   *  effect on `count` and `jobs`. */
  work_modes: string[];
  /** The four filters this page gained on 2026-08-12, aligning it with the public feed.
   *  Echoed back as *applied*, not as asked for — a city naming an unselected country is
   *  dropped server-side, and the UI must light up what is actually narrowing the list. */
  q: string;
  categories: string[];
  countries: string[];
  cities: string[];
  seniorities: string[];
  /** Whether the "great fits only" toggle is on, and the score it means. The threshold is
   *  server-owned (it is policy, alongside the match floor and the email bar) — the UI
   *  renders it but must never decide it. */
  great_fits: boolean;
  great_fit_score: number;
  /** Each filter menu's options, counted with the *other* filters applied but never its
   *  own — so no single tick can empty the menu it came from. `great_fit_count` is how many
   *  the toggle would leave; null when the server was not asked. */
  facets: {
    skills: { skill: string; count: number }[];
    work_modes: { work_mode: string; count: number }[];
    categories: SearchFacet[];
    countries: SearchFacet[];
    seniorities: SearchFacet[];
    /** Present only when a country is filtered on — absent, never empty, so "no country
     *  picked yet" and "this country has no cities" stay different statements. */
    cities?: SearchFacet[];
    great_fit_count: number | null;
  };
  /** @deprecated Alias of `facets.skills`, kept so a cached bundle keeps working. */
  skill_facets: { skill: string; count: number }[];
  jobs: MatchJob[];
}

/** What a hide/unhide leaves behind: how many rows actually changed, and the fresh totals
 *  for both lists so the caller can update its headers without refetching. */
export interface HideResponse {
  ok: boolean;
  changed: number;
  visible_count: number;
  hidden_count: number;
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
  /** Same skills facet as MatchJob, extracted from the description for the preview. */
  skills: string[];
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

// The extra filters ride in a trailing object rather than as two more positional args: six
// positionals is where a call site starts passing `undefined, 0, false` to reach the last one.
/** Everything that narrows the /matches list. One object rather than a growing argument
 *  list: the page passes the same value to the first request and to every "load more", and
 *  two of those drifting apart is how page 2 arrives under a different filter than page 1. */
export interface MatchFilters {
  skills?: string[];
  workModes?: string[];
  greatFits?: boolean;
  q?: string;
  categories?: string[];
  countries?: string[];
  cities?: string[];
  seniorities?: string[];
}

export function getMatches(token?: string, offset = 0, hidden = false,
                           filters: MatchFilters = {}) {
  const q = new URLSearchParams();
  if (token) q.set("token", token);
  if (offset) q.set("offset", String(offset));
  if (hidden) q.set("hidden", "true");
  if (filters.skills?.length) q.set("skills", filters.skills.join(","));
  if (filters.workModes?.length) q.set("work_modes", filters.workModes.join(","));
  if (filters.greatFits) q.set("great_fits", "true");
  if (filters.q?.trim()) q.set("q", filters.q.trim());
  if (filters.categories?.length) q.set("categories", filters.categories.join(","));
  if (filters.countries?.length) q.set("countries", filters.countries.join(","));
  if (filters.cities?.length) q.set("cities", filters.cities.join(","));
  if (filters.seniorities?.length) q.set("seniorities", filters.seniorities.join(","));
  const s = q.toString();
  return req<MatchesResponse>(`/matches${s ? `?${s}` : ""}`);
}

// Hide jobs from the matches page and from future digests — "I already applied", "not for
// me". Never a delete: they move to /hidden, and setHidden(ids, false) puts them back.
export function setMatchesHidden(postingIds: string[], hidden: boolean, token?: string) {
  return req<HideResponse>(hidden ? "/matches/hide" : "/matches/unhide", {
    method: "POST",
    body: JSON.stringify(
      token ? { token, posting_ids: postingIds } : { posting_ids: postingIds }
    ),
  });
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

// The Google-verified email waiting to finish signup (set after the OAuth round-trip for a new
// user). Rejects (401) if there's no live intent.
export function googleSignupPending() {
  return req<{ email: string }>("/auth/google/pending");
}

// Finish a Google-verified signup: creates an active subscription (no confirm email) and logs
// the user in. The email comes from the server-side intent (jd_signup cookie), not this payload.
export function subscribeGoogle(payload: SubscribePayload) {
  return req<{ ok: boolean; status: string }>("/subscribe/google", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function confirmUrl(token: string) {
  return `${API_URL}/confirm?token=${encodeURIComponent(token)}`;
}

export function unsubscribeUrl(token: string) {
  return `${API_URL}/unsubscribe?token=${encodeURIComponent(token)}`;
}

// --- public job search (GET /jobs) ----------------------------------------------
// The free tier's feed: no auth, no profile, no model. Deliberately does NOT go through
// `req` — that helper sends `credentials: "include"` and the CSRF header, which are for
// subscriber calls. This endpoint reads public inventory and must work identically for a
// visitor with no cookie, so sending one would be the only thing distinguishing them.

export interface SearchJob {
  posting_id: string;
  title: string | null;
  company: string | null;
  url: string | null;
  location: string | null;
  country_code: string | null;
  city: string | null;
  region: string | null;
  seniority: string | null;
  work_type: string | null;
  work_mode: string | null;
  remote_signal: boolean | null;
  role_category: string | null;
  salary: string | null;
  /** Which adapter carried this row. Present because attribution is a terms obligation for
   *  at least one feed (Remote OK) — see Terms §3. */
  source: string | null;
  skills: string[];
  posted_at: string | null;
}

export interface SearchFacet {
  value: string;
  /** Absent for a menu built from a fixed vocabulary rather than from counts (Level, Work
   *  setup). Those are offered in full — a level with no results today is still a level
   *  someone means to pick — and rendering a `0` beside every option would state a count
   *  nobody computed. */
  count?: number;
}

export interface SearchResponse {
  /** Total matching the current filters, capped — see `count_capped`. Not the length of
   *  `jobs`, which is one page. */
  count: number;
  /** True when the real total is above the server's cap and `count` is that cap. Render
   *  "500+", never the cap as a fact. */
  count_capped: boolean;
  jobs: SearchJob[];
  /** Present on the first page only; absent (not empty) on later ones, because facets do not
   *  change as you page and an empty list would blank the menu in use. */
  facets?: {
    categories: SearchFacet[];
    countries: SearchFacet[];
    /** One row per `geo.REACH_AREAS` id (`eea`, `na`), always both and always in that order,
     *  for the two international options that lead the Country menu. Counted under the current
     *  non-location filters, because they widen across every place.
     *
     *  **The rows overlap and must not be summed**: a scope reading "North America or Europe"
     *  is counted under both, and that intersection is the only set in which someone in the
     *  EEA can hold a US-facing role. */
    reach_areas?: SearchFacet[];
    /** `cz:prague` pairs, present only when a country is filtered on — absent, never empty,
     *  so "no country picked yet" and "this country has no cities" stay distinguishable. */
    cities?: SearchFacet[];
  };
}

export interface SearchParams {
  q?: string;
  countries?: string[];
  /** `cz:prague` pairs. The server drops any whose country is not also selected, so a city
   *  can never outlive the country it belongs to — see `geo.clean_cities`. */
  cities?: string[];
  categories?: string[];
  seniorities?: string[];
  /** `onsite` / `hybrid` / `remote`. Accepted and validated by `/jobs` since it shipped;
   *  the control arrived 2026-08-12. */
  workModes?: string[];
  /** `geo.REACH_AREAS` ids — the international rows in the Country menu. These are *places*:
   *  they widen the location filter, they are not a work-arrangement toggle. Replaced a
   *  `remote?: boolean` on 2026-08-14; see `intlEea` in web/i18n/schema.ts. */
  intl?: string[];
  limit?: number;
  offset?: number;
}

/** Build the query string for a search. Exported so the page can also put it in the URL —
 *  a search worth running is a search worth linking to, and one definition means the link
 *  and the request cannot disagree about what was asked for. */
export function searchQuery(p: SearchParams): URLSearchParams {
  const q = new URLSearchParams();
  if (p.q?.trim()) q.set("q", p.q.trim());
  for (const c of p.countries ?? []) q.append("country", c);
  for (const c of p.cities ?? []) q.append("city", c);
  for (const c of p.categories ?? []) q.append("category", c);
  for (const s of p.seniorities ?? []) q.append("seniority", s);
  for (const m of p.workModes ?? []) q.append("work_mode", m);
  for (const a of p.intl ?? []) q.append("intl", a);
  if (p.offset) q.set("offset", String(p.offset));
  if (p.limit) q.set("limit", String(p.limit));
  return q;
}

export async function searchJobs(p: SearchParams): Promise<SearchResponse> {
  const res = await fetch(`${API_URL}/jobs?${searchQuery(p).toString()}`);
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {}
    throw new Error(detail);
  }
  return (await res.json()) as SearchResponse;
}
