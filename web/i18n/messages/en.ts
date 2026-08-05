import type { Messages } from "../schema";

/**
 * English — the source catalogue. Every other language is a translation of this file and is
 * type-checked against `Messages`, so a key added here fails the build until all eight
 * catalogues carry it.
 *
 * `geo.countries` is empty on purpose: English country names already live in `web/lib/geo.ts`,
 * which is drift-tested against `service/geo.py`. Restating them here would create a second
 * English spelling that the drift test cannot see.
 */
const en: Messages = {
  meta: {
    title: "JobDigest — jobs that fit you, every morning",
    description:
      "One short email a day with a curated, ranked shortlist of jobs that fit you. Tech/CZ+EU first. Free to start.",
  },

  nav: {
    myMatches: "My matches",
    myPreferences: "My preferences",
    logOut: "Log out",
    logIn: "Log in",
    themeTitle: "Toggle light / dark",
    themeAria: "Toggle theme",
    languageAria: "Choose language",
  },

  footer: {
    madeInEu: "Made in the EU",
    manage: "Manage subscription",
    privacy: "Privacy",
    terms: "Terms",
  },

  common: {
    loading: "Loading…",
    add: "Add",
    or: "or",
    backToHome: "← Back to home",
    goHome: "Go to homepage →",
    viewAndApply: "View & apply →",
    roleFallback: "Role",
  },

  roles: {
    product_manager: "Product Manager",
    marketing: "Marketing",
    social_media: "Social Media",
    data_analyst: "Data Analyst",
    designer: "Designer",
    software_engineer: "Software Engineer",
    data_engineer: "Data Engineer",
    devops: "DevOps",
    finance: "Finance",
    ml_engineer: "ML Engineer",
  },

  workTypes: {
    fulltime: "Full-time",
    freelance: "Freelance",
    parttime: "Part-time",
  },

  seniorities: {
    junior: "Intern / Junior",
    mid: "Mid",
    senior: "Senior",
  },

  geo: {
    countries: {},
    remoteScope: {
      country: "Only in the countries I picked",
      eu: "Anywhere in the EU or EEA",
      worldwide: "Anywhere in the world",
    },
    workModeLabel: {
      onsite: "On-site",
      hybrid: "Hybrid",
      remote: "Fully remote",
    },
    workModeHint: {
      onsite: "In the office",
      hybrid: "Part office, part home — you still need to be near it",
      remote: "No office at all",
    },
  },

  educationLevels: {
    secondary: "High school",
    vocational: "Vocational / apprenticeship",
    bachelor: "Bachelor's",
    master: "Master's",
    doctorate: "Doctorate / PhD",
  },

  education: {
    label: "Education a job may ask for",
    anyLevel: "anything goes",
    narrowed: "we'll leave out the rest",
    note:
      "Most ads never state a requirement at all — we keep every one of those and let the matcher read the description instead. This only leaves out the few that spell out a qualification you don't have.",
    fieldLabel: "What you studied (optional)",
    fieldPlaceholder: "Economics, Computer Science…",
    fieldHint:
      "Never used as a filter — it only helps the matcher judge whether a role suits your background.",
  },

  location: {
    countriesLabel: "Countries you can work in",
    addCountryAria: "Add another country",
    addCountryOption: "Add another country…",
    citiesIn: "Cities in {country}",
    anyCityNote: "any city (on-site roles anywhere in the country)",
    pickedCityNote: "on-site roles only in the cities you pick",
    anyCity: "Any city",
    addTownPlaceholder: "Add another town in {country}…",
    addCityAria: "Add a city in {country}",
    workSetup: "Work setup",
    anythingGoes: "anything goes",
    leaveOutRest: "we'll leave out the rest",
    hybridNote:
      "Hybrid means part of the week in the office, so it still has to be somewhere you can get to. Plenty of ads never say either way — we keep those and let the matcher read the description rather than guess.",
    remoteLabel: "Fully remote roles — how far afield?",
    remoteDisabledNote: "Only applies once “Fully remote” is selected above.",
  },

  landing: {
    wizardAria: "Build your digest",
    stepOf: "Step {n} of 4",
    q1: "Start searching now",
    cvReading: "Reading your CV…",
    cvDrop: "Drop your CV — we'll fill this in",
    cvHint: "PDF or DOCX · We read it to set up your matches, then delete the file. Never shared.",
    cvChoose: "Choose file",
    cvDone: "CV read — prefilled",
    cvRemove: "Remove CV",
    orPickManually: "or pick manually",
    addRolePlaceholder: "Add another role…",
    addRoleAria: "Add a role",
    q2: "What are you good at?",
    q2hint: "These are what we match jobs on. Tap all that apply.",
    addSkillPlaceholder: "Add a skill…",
    addSkillAria: "Add a skill",
    q3: "Where & how?",
    q3hint:
      "Location first — pick the cities you could actually commute to. On-site roles anywhere else are dropped; fully remote ones are not.",
    workTypeHint: "Type of work — tap all that fit.",
    levelHint: "Your level — we only send roles at the levels you pick.",
    narrowWarning:
      "That's a narrow search — you may get few matches. Add a level, role, or another city to see more.",
    q4google: "Confirm your digest",
    googleHint:
      "Signing up as {0} — verified with Google, so there's no confirmation email. Your first digest lands at 7:00 tomorrow.",
    consent: "I agree to the {0} and to the daily digest.",
    consentLink: "privacy policy",
    q4: "Where do we send it?",
    q4hint: "One confirmation email first — then your daily digest at 7:00.",
    googleSignup: "Sign up with Google",
    emailPlaceholder: "you@example.com",
    emailAria: "Your email",
    turnstileNote: "Protected by Cloudflare Turnstile — no CAPTCHA",
    back: "← Back",
    next: "Next →",
    sending: "Sending…",
    submit: "Start my digest →",
    livePreview: "Live preview",
    trustEmail: "One email a day",
    trustUnsub: "Unsubscribe in one click",
    trustFree: "Free to start · 16 sources scanned nightly",
    howItWorks: "How it works",
    step1Title: "Tap your profile",
    step1Body: "Roles, skills, where you can work. Twenty seconds, mostly tapping.",
    step2Title: "We match overnight",
    step2Body: "Fresh postings from dozens of sources, ranked to you, duplicates dropped.",
    step3Title: "Read one email",
    step3Body: "A short ranked shortlist with a reason and an apply link for each.",
    mcta: "Get my digest",
    mctaAria: "Jump to sign-up",
    toast: {
      googleExpired: "Your Google sign-in expired — please try again.",
      cvWrongType: "Please upload a PDF or DOCX file",
      cvTooLarge: "That file is too large (max 8 MB)",
      cvOk: "CV read — we prefilled your profile",
      cvUnreadable: "Couldn't read that file",
      needConsent: "Please accept the privacy policy first",
      needLevel: "Pick at least one seniority level",
      needEmail: "Enter a valid email",
      needTurnstile: "Please complete the verification",
      needRole: "Pick at least one role to continue",
      needLevelToContinue: "Pick at least one level to continue",
      genericError: "Something went wrong — please retry",
    },
  },

  preview: {
    inbox: "inbox — {email}",
    time: "7:00 AM",
    subject: "{count} for you — {date}",
    roleCount: { one: "{n} new role", other: "{n} new roles" },
    roleCountNamed: { one: "{n} new {role} role", other: "{n} new {role} roles" },
    pickRole: "Pick a role to see your matches →",
    noCombo: "No matches for this combo — widen your levels or work type →",
    greeting: "Good morning. {0} today, from 214 postings scanned overnight.",
    freshMatches: { one: "{n} fresh match", other: "{n} fresh matches" },
    reasonMatches: "Matches {0}. In {sector}.",
    reasonGeneric: "Strong {sector} role in your region. In {sector}.",
    refine: "Refine preferences",
    pause: "Pause 2 weeks",
    unsubscribe: "Unsubscribe",
    fine: "You're getting this because you signed up at jobdigest.eu · One email a day.",
  },

  prefs: {
    signedInLabel: "Signed in",
    title: "Your preferences",
    signedInAs: "Signed in as {0}. Change anything, pause, or leave — {1}.",
    logOutInline: "log out",
    noPasswordLead: "No password.",
    noPasswordBody:
      "Clicking your email link signed you in and keeps you signed in on this device, so you won't need the link again here. Log out any time.",
    roles: "Roles",
    skills: "Skills / keywords",
    addRolePlaceholder: "Add a role…",
    addSkillPlaceholder: "Add a skill…",
    addRoleAria: "Add a role",
    addSkillAria: "Add a skill",
    sectors: "Industries you're interested in (optional)",
    sectorsHint:
      "Domains you'd most like to work in — e-commerce, finance, gaming… Used to judge fit, never as a hard filter.",
    addSectorPlaceholder: "Add an industry…",
    addSectorAria: "Add an industry",
    seniorityLabel: "Seniority — we only send roles at the levels you pick",
    frequency: "Frequency",
    freqDaily: "Daily",
    freqWeekdays: "Weekdays only",
    freqWeekly: "Weekly (Mondays)",
    pausedTitle: "Digest paused",
    pauseTitle: "Pause my digest",
    pausedUntil: "Paused until {date} — resume anytime.",
    pausedUntilSoon: "soon",
    pauseBody: "Take a break without unsubscribing — resumes when you're ready.",
    resumeNow: "Resume now",
    pauseTwoWeeks: "Pause 2 weeks",
    saving: "Saving…",
    save: "Save changes",
    unsubscribeAll: "Unsubscribe from all emails",
    unsubscribeConfirm: "Click again to confirm — unsubscribe from all emails",
    errTitle: "Can't open your preferences",
    errNoLink:
      "Open your preferences from the link in your email — or use “Manage subscription” to get a fresh one.",
    errUnknownLink: "Unknown or expired link.",
    toast: {
      saved: "Preferences saved",
      saveFailed: "Couldn't save — please retry",
      paused: "Digest paused for 2 weeks",
      pauseFailed: "Couldn't pause",
      resumed: "Digest resumed",
      resumeFailed: "Couldn't resume",
      unsubscribed: "You've unsubscribed",
      unsubscribeFailed: "Couldn't unsubscribe",
    },
  },

  matches: {
    label: "Your matches · signed in",
    countTitle: { one: "{n} match for you", other: "{n} matches for you" },
    noneTitle: "No matches yet",
    intro:
      "Everything we found for {0}, ranked best-first. We email you the strongest few each morning — this is the full list.",
    nothingTitle: "Nothing yet",
    nothingBody: "Your first matches are found overnight — check back after tomorrow's 7:00 digest.",
    adjustPrefs: "Adjust your preferences →",
    notQuiteRight: "Not quite right? Adjust your roles & skills →",
    showing: {
      one: "Showing {shown} of {n} match",
      other: "Showing {shown} of {n} matches",
    },
    loadMore: "Load more",
    loadMoreFailed: "Couldn't load more — check your connection and try again.",
    errTitle: "Can't open your matches",
    errNoLink:
      "Open your matches from the link in your email — or use “Manage subscription” to get a fresh one.",
    errUnknownLink: "Unknown or expired link.",
    topMatch: "Top match",
    tagHybrid: "Hybrid",
    tagRemote: "Remote",
    tagFreelance: "Freelance",
    hideHint:
      "Already applied, or not interested? Tick those jobs and hide them — they leave this page and your daily email.",
    selectAll: "Select all shown",
    clearSelection: "Clear",
    selectAria: "Select",
    selected: { one: "{n} selected", other: "{n} selected" },
    hideSelected: "Hide selected",
    hiding: "Hiding…",
    hideFailed: "Couldn't hide those — try again.",
    hiddenLink: { one: "{n} hidden job →", other: "{n} hidden jobs →" },
  },

  hidden: {
    label: "Hidden jobs · signed in",
    countTitle: { one: "{n} hidden job", other: "{n} hidden jobs" },
    noneTitle: "No hidden jobs",
    intro:
      "Jobs you've hidden. They stay off your matches page and out of your daily email — unhide any of them and they come straight back.",
    nothingTitle: "Nothing hidden",
    nothingBody: "Hide a job from your matches and it lands here. Nothing is ever deleted.",
    backToMatches: "← Back to your matches",
    unhideSelected: "Unhide selected",
    unhiding: "Unhiding…",
    unhideFailed: "Couldn't unhide those — try again.",
    showing: {
      one: "Showing {shown} of {n} hidden job",
      other: "Showing {shown} of {n} hidden jobs",
    },
  },

  checkInbox: {
    title: "Check your inbox",
    body: "We sent a confirmation link to {0}. Click it and your daily digest starts tomorrow morning at 7:00.",
    inMeantime: " In the meantime, here are live jobs matching your search:",
    doubleOptIn: "Double opt-in · GDPR consent",
    yourInbox: "your inbox",
    instantPreview: "Instant preview · keyword match",
    instantNote:
      "A quick keyword match to get you started. Tomorrow's email is {0} — each role scored, with a reason it fits you.",
    instantNoteEmphasis: "AI-ranked",
  },

  manage: {
    sentTitle: "Check your inbox",
    sentBody:
      "If {0} has a JobDigest subscription, we've just emailed its private settings link. Open it to edit your preferences, pause, or unsubscribe — no password needed.",
    didntGet: "Didn't get it? Check spam, or try again in a few minutes.",
    title: "Log in to JobDigest",
    body: "No passwords here. Enter your address and we'll email you a secure link — click it and you're signed in, and you'll stay signed in on this device.",
    googleNoSub:
      "That Google account isn't subscribed yet. Sign up first, then you can sign in with Google.",
    googleSuppressed:
      "That address previously unsubscribed, so we can't re-subscribe it automatically. Contact us if you'd like to return.",
    googleError: "Google sign-in didn't complete. Please try again, or use your email link below.",
    googleSignin: "Sign in with Google",
    emailLabel: "Your email address",
    sending: "Sending…",
    submit: "Email me my settings link",
    onlyOwnInbox: "We'll only ever send the link to your own inbox.",
    error: "Something went wrong. Please try again in a moment.",
  },

  root: {
    title: "Choose your language",
    body: "Taking you to JobDigest in your language…",
  },
};

export default en;
