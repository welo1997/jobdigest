/**
 * The shape every language catalogue must fill.
 *
 * This is an explicit interface rather than `typeof en`, for two reasons:
 *   - a missing key becomes a TypeScript error at build time, so a half-translated language
 *     cannot ship silently rendering English in the gaps;
 *   - plural fields can be typed as `PluralForms`, which lets Czech, Slovak and Polish add
 *     the `few`/`many` forms that English has no slot for.
 *
 * Placeholders come in two flavours. `{name}` is substituted by `fmt()` and is plain text.
 * `{0}`, `{1}` are substituted by `rich()` with React nodes — a bold address, a link — and
 * exist so a translator can move the inserted element to wherever the sentence needs it
 * instead of being pinned to English word order by a pre/post string pair.
 *
 * Job titles, skills and city names are deliberately absent: skills are stored verbatim as
 * search keywords (`profiles.stack`), and city display names are drift-tested against
 * `service/geo.py`. Country names are safe to translate because they are read through an
 * overlay here, never by editing `web/lib/geo.ts`.
 */

import type { PluralForms } from "./config";

export interface Messages {
  meta: {
    title: string;
    description: string;
  };

  nav: {
    myMatches: string;
    myPreferences: string;
    logOut: string;
    logIn: string;
    themeTitle: string;
    themeAria: string;
    languageAria: string;
  };

  footer: {
    madeInEu: string;
    manage: string;
    privacy: string;
    terms: string;
  };

  common: {
    loading: string;
    add: string;
    or: string;
    backToHome: string;
    goHome: string;
    viewAndApply: string;
    roleFallback: string;
  };

  /** Display labels for the role chips. Keyed by the stable ids in `lib/options.ts` —
   *  never by the label itself, because the label is what changes per language. */
  roles: Record<string, string>;
  /** Employment type chips (`fulltime` | `freelance` | `parttime`). */
  workTypes: Record<string, string>;
  /** Seniority chips, keyed by the stored code (`junior` | `mid` | `senior`). */
  seniorities: Record<string, string>;

  geo: {
    /** ISO-2 -> localised country name. Falls back to `COUNTRIES` in lib/geo.ts when absent. */
    countries: Partial<Record<string, string>>;
    remoteScope: Record<string, string>;
    workModeLabel: Record<string, string>;
    workModeHint: Record<string, string>;
  };

  /** Education requirement chips, keyed by the stable ids in `lib/education.ts`. */
  educationLevels: Record<string, string>;

  education: {
    label: string;
    anyLevel: string;
    narrowed: string;
    /** Must say that most ads state no requirement and are kept — only ~3% can be filtered. */
    note: string;
    fieldLabel: string;
    fieldPlaceholder: string;
    fieldHint: string;
  };

  location: {
    countriesLabel: string;
    addCountryAria: string;
    addCountryOption: string;
    citiesIn: string;
    anyCityNote: string;
    pickedCityNote: string;
    anyCity: string;
    addTownPlaceholder: string;
    addCityAria: string;
    workSetup: string;
    anythingGoes: string;
    leaveOutRest: string;
    hybridNote: string;
    remoteLabel: string;
    remoteDisabledNote: string;
  };

  landing: {
    wizardAria: string;
    stepOf: string;
    q1: string;
    cvReading: string;
    cvDrop: string;
    cvHint: string;
    cvChoose: string;
    cvDone: string;
    cvRemove: string;
    orPickManually: string;
    addRolePlaceholder: string;
    addRoleAria: string;
    q2: string;
    q2hint: string;
    addSkillPlaceholder: string;
    addSkillAria: string;
    q3: string;
    q3hint: string;
    workTypeHint: string;
    levelHint: string;
    narrowWarning: string;
    q4google: string;
    googleHint: string;
    consent: string;
    consentLink: string;
    q4: string;
    q4hint: string;
    googleSignup: string;
    emailPlaceholder: string;
    emailAria: string;
    turnstileNote: string;
    back: string;
    next: string;
    sending: string;
    submit: string;
    livePreview: string;
    trustEmail: string;
    trustUnsub: string;
    trustFree: string;
    howItWorks: string;
    step1Title: string;
    step1Body: string;
    step2Title: string;
    step2Body: string;
    step3Title: string;
    step3Body: string;
    mcta: string;
    mctaAria: string;
    toast: {
      googleExpired: string;
      cvWrongType: string;
      cvTooLarge: string;
      cvOk: string;
      cvUnreadable: string;
      needConsent: string;
      needLevel: string;
      needEmail: string;
      needTurnstile: string;
      needRole: string;
      needCountry: string;
      needLevelToContinue: string;
      genericError: string;
    };
  };

  preview: {
    inbox: string;
    time: string;
    subject: string;
    /** "3 new roles" — when several roles are picked, so none can be named. */
    roleCount: PluralForms;
    /** "3 new Product Manager roles" — one role picked. A separate string rather than a
     *  `{role}` hole inside `roleCount`, because Czech, Polish and German all want the role
     *  name somewhere English does not put it. */
    roleCountNamed: PluralForms;
    pickRole: string;
    noCombo: string;
    greeting: string;
    freshMatches: PluralForms;
    reasonMatches: string;
    reasonGeneric: string;
    refine: string;
    pause: string;
    unsubscribe: string;
    fine: string;
  };

  prefs: {
    signedInLabel: string;
    title: string;
    signedInAs: string;
    logOutInline: string;
    noPasswordLead: string;
    noPasswordBody: string;
    roles: string;
    skills: string;
    addRolePlaceholder: string;
    addSkillPlaceholder: string;
    addRoleAria: string;
    addSkillAria: string;
    /** Free-text industries/domains of interest (`profiles.sectors`). A soft signal for the
     *  AI matcher, never a hard filter — the hint must say so. */
    sectors: string;
    sectorsHint: string;
    addSectorPlaceholder: string;
    addSectorAria: string;
    seniorityLabel: string;
    frequency: string;
    freqDaily: string;
    freqWeekdays: string;
    freqWeekly: string;
    pausedTitle: string;
    pauseTitle: string;
    pausedUntil: string;
    pausedUntilSoon: string;
    pauseBody: string;
    resumeNow: string;
    pauseTwoWeeks: string;
    saving: string;
    save: string;
    unsubscribeAll: string;
    unsubscribeConfirm: string;
    errTitle: string;
    errNoLink: string;
    errUnknownLink: string;
    toast: {
      saved: string;
      saveFailed: string;
      paused: string;
      pauseFailed: string;
      resumed: string;
      resumeFailed: string;
      unsubscribed: string;
      unsubscribeFailed: string;
    };
  };

  matches: {
    label: string;
    countTitle: PluralForms;
    noneTitle: string;
    intro: string;
    nothingTitle: string;
    nothingBody: string;
    adjustPrefs: string;
    notQuiteRight: string;
    /** "Showing {shown} of {n}" — pluralised on {n}, the total, not on how many are loaded. */
    showing: PluralForms;
    loadMore: string;
    loadMoreFailed: string;
    errTitle: string;
    errNoLink: string;
    errUnknownLink: string;
    topMatch: string;
    tagHybrid: string;
    tagRemote: string;
    tagFreelance: string;
    /** Hiding: tick jobs already applied to / not wanted, confirm, and they move to /hidden. */
    hideHint: string;
    selectAll: string;
    clearSelection: string;
    /** Prefix of each checkbox's accessible name; the job title is appended to it. */
    selectAria: string;
    selected: PluralForms;
    hideSelected: string;
    hiding: string;
    hideFailed: string;
    /** Link to the hidden page, shown only when something is hidden. */
    hiddenLink: PluralForms;
    /** Filter row: the menu triggers, and the "clear" that drops a filter (shared by all of
     *  them). Skill chip labels are the skill names (data) and are never translated; the work
     *  setup options reuse `geo.workModeLabel` rather than restating it here — one definition
     *  per language, and "Fully remote" must not differ between the signup form and this page.
     *  `greatFitsOnly` is the top of the score ladder; the score itself is server-owned and
     *  arrives in the response, so it is never written into a catalogue. */
    filterBySkill: string;
    filterByWorkMode: string;
    /** The free-text box over this subscriber's own matches (2026-08-12). The submit button
     *  is shared with `jobs.searchButton` — it is the same word doing the same job, and the
     *  Field / Country / City / Level triggers are borrowed from `jobs` for the same reason:
     *  the two pages now offer the same axes and must not name them differently. */
    searchLabel: string;
    searchPlaceholder: string;
    greatFitsOnly: string;
    clearFilter: string;
  };

  /** `/hidden` — the jobs the subscriber hid. Errors, paging and the tick-box strings are
   *  reused from `matches` rather than duplicated: the two pages are one list read through
   *  opposite filters, and a second copy of "Load more" is a second copy that can drift. */
  hidden: {
    label: string;
    countTitle: PluralForms;
    noneTitle: string;
    intro: string;
    nothingTitle: string;
    nothingBody: string;
    backToMatches: string;
    unhideSelected: string;
    unhiding: string;
    unhideFailed: string;
    showing: PluralForms;
  };

  /** `/jobs` — the public, unauthenticated job feed. Nothing here is behind a login, so this
   *  is the first page most visitors will read: it is marketing copy as much as UI. Tag and
   *  seniority words are reused from the top-level `seniorities` / `geo.workModeLabel`
   *  records rather than restated. */
  jobs: {
    navLink: string;
    metaTitle: string;
    metaDescription: string;
    label: string;
    title: string;
    intro: string;
    searchLabel: string;
    searchPlaceholder: string;
    searchButton: string;
    /** The idle state: shown before anything has been searched for, in place of the result
     *  count and the list. Deliberately distinct from `noneTitle`/`noneBody` — "nothing
     *  matches" is an answer, and at this point there is no question. */
    startTitle: string;
    startBody: string;
    countTitle: PluralForms;
    /** Shown instead of `countTitle` when the server capped the total: "500+ jobs". The cap
     *  is never rendered as an exact figure. */
    countCapped: string;
    showing: PluralForms;
    noneTitle: string;
    noneBody: string;
    filterCategory: string;
    filterCountry: string;
    filterCity: string;
    /** Shown inside the City menu before a country is picked. The server only counts cities
     *  within the selected countries, so until then the menu genuinely has nothing to offer
     *  — and "no cities" would be the wrong thing to say. */
    cityNeedsCountry: string;
    filterSeniority: string;
    /** Work setup. Its option labels come from `geo.workModeLabel`, not from here — one
     *  definition per language, shared with the signup form and /matches. */
    filterWorkMode: string;
    remoteOnly: string;
    clearFilters: string;
    loadMore: string;
    loadMoreFailed: string;
    errTitle: string;
    errBody: string;
    /** Labels for the `role_category` values no signup chip maps to, so the filter menu never
     *  renders a raw id. Every other category takes its label from `roles` via
     *  `lib/options.ts` `categoryLabel` — one definition, no drift. */
    categories: Record<string, string>;
    /** The upgrade path off the free feed: this page is the hook, the digest is the product. */
    ctaTitle: string;
    ctaBody: string;
    ctaButton: string;
  };

  checkInbox: {
    title: string;
    body: string;
    inMeantime: string;
    doubleOptIn: string;
    yourInbox: string;
    instantPreview: string;
    instantNote: string;
    instantNoteEmphasis: string;
  };

  manage: {
    sentTitle: string;
    sentBody: string;
    didntGet: string;
    title: string;
    body: string;
    googleNoSub: string;
    googleSuppressed: string;
    googleError: string;
    googleSignin: string;
    emailLabel: string;
    sending: string;
    submit: string;
    onlyOwnInbox: string;
    error: string;
  };

  /** The bare `/` page, which redirects on browser language and lists every locale as a
   *  no-JS / crawler fallback. */
  root: {
    title: string;
    body: string;
  };
}
