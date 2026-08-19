import type { Messages } from "../schema";

/**
 * German. Second-largest source of inventory after CZ/SK.
 *
 * Role chips keep the anglicisms the German tech market actually advertises with
 * ("Product Manager", "Data Engineer") and translate only the ones that are genuinely
 * German words in job ads ("Softwareentwickler", "Finanzen"). Translating all of them would
 * read as less native here, not more.
 */
const de: Messages = {
  meta: {
    title: "JobDigest — Jobs, die zu dir passen, jeden Morgen",
    description:
      "Eine kurze E-Mail pro Tag mit einer kuratierten, sortierten Auswahl an Jobs, die zu dir passen. Tech und CZ+EU zuerst. Kostenlos.",
  },

  nav: {
    myMatches: "Meine Treffer",
    myPreferences: "Meine Einstellungen",
    logOut: "Abmelden",
    logIn: "Anmelden",
    themeTitle: "Hell / dunkel umschalten",
    themeAria: "Design umschalten",
    languageAria: "Sprache wählen",
  },

  footer: {
    madeInEu: "Hergestellt in der EU",
    manage: "Abo verwalten",
    privacy: "Datenschutz",
    terms: "AGB",
  },

  common: {
    loading: "Wird geladen…",
    add: "Hinzufügen",
    or: "oder",
    backToHome: "← Zurück zur Startseite",
    goHome: "Zur Startseite →",
    viewAndApply: "Ansehen & bewerben →",
    roleFallback: "Stelle",
  },

  roles: {
    cybersecurity: "Cybersicherheit",
    science_research: "Wissenschaft & Forschung",
    social_care: "Soziale Arbeit",
    product_manager: "Product Manager",
    marketing: "Marketing",
    social_media: "Social Media",
    data_analyst: "Datenanalyst",
    designer: "Designer",
    software_engineer: "Softwareentwickler",
    data_engineer: "Data Engineer",
    devops: "DevOps",
    finance: "Finanzen",
    ml_engineer: "ML Engineer",
    sales: "Vertrieb",
    hr: "HR & Recruiting",
    legal: "Recht",
    customer_support: "Kundensupport",
    operations: "Operations",
    healthcare: "Gesundheitswesen",
    education: "Bildung",
    hospitality: "Gastronomie",
    skilled_trades: "Handwerk",
    construction: "Bau",
    logistics: "Logistik",
    manufacturing: "Produktion",
    engineering: "Ingenieurwesen",
  },

  workTypes: {
    fulltime: "Vollzeit",
    freelance: "Freelance",
    parttime: "Teilzeit",
  },

  seniorities: {
    intern: "Praktikum",
    entry_level: "Berufseinstieg",
    junior: "Junior",
    mid: "Mid-Level",
    senior: "Senior",
    lead: "Führung",
  },

  geo: {
    countries: {
      AT: "Österreich", BE: "Belgien", BG: "Bulgarien", HR: "Kroatien", CY: "Zypern",
      CZ: "Tschechien", DK: "Dänemark", EE: "Estland", FI: "Finnland", FR: "Frankreich",
      DE: "Deutschland", GR: "Griechenland", HU: "Ungarn", IE: "Irland", IT: "Italien",
      LV: "Lettland", LT: "Litauen", LU: "Luxemburg", MT: "Malta", NL: "Niederlande",
      PL: "Polen", PT: "Portugal", RO: "Rumänien", SK: "Slowakei", SI: "Slowenien",
      ES: "Spanien", SE: "Schweden",
      CH: "Schweiz", IS: "Island", LI: "Liechtenstein", NO: "Norwegen",
      GB: "Vereinigtes Königreich", US: "Vereinigte Staaten",
      CA: "Kanada",
    },
    remoteScope: {
      country: "Nur in den Ländern, die ich gewählt habe",
      eu: "Überall in der EU oder im EWR",
      worldwide: "Überall auf der Welt",
    },
    workModeLabel: {
      onsite: "Vor Ort",
      hybrid: "Hybrid",
      remote: "Vollständig remote",
    },
    workModeHint: {
      onsite: "Im Büro",
      hybrid: "Teils Büro, teils zu Hause — du musst trotzdem in der Nähe sein",
      remote: "Gar kein Büro",
    },
  },

  educationLevels: {
    secondary: "Schulabschluss / Abitur",
    vocational: "Ausbildung",
    bachelor: "Bachelor",
    master: "Master / Diplom",
    doctorate: "Promotion / PhD",
  },

  education: {
    label: "Abschluss, den eine Stelle verlangen darf",
    anyLevel: "alles möglich",
    narrowed: "den Rest lassen wir weg",
    note:
      "Die meisten Anzeigen nennen gar keine Anforderung — die behalten wir alle und lassen stattdessen den Matcher die Beschreibung lesen. Weggelassen werden nur die wenigen, die ausdrücklich einen Abschluss verlangen, den du nicht hast.",
    fieldLabel: "Was du studiert hast (optional)",
    fieldPlaceholder: "Wirtschaft, Informatik…",
    fieldHint:
      "Wird nie als Filter verwendet — es hilft dem Matcher nur einzuschätzen, ob eine Rolle zu deinem Hintergrund passt.",
  },

  location: {
    countriesLabel: "Länder, in denen du arbeiten kannst",
    addCountryAria: "Weiteres Land hinzufügen",
    addCountryOption: "Weiteres Land hinzufügen…",
    citiesIn: "Städte in {country}",
    anyCityNote: "jede Stadt (Jobs vor Ort im ganzen Land)",
    pickedCityNote: "Jobs vor Ort nur in den gewählten Städten",
    anyCity: "Jede Stadt",
    addTownPlaceholder: "Weiteren Ort in {country} hinzufügen…",
    addCityAria: "Stadt in {country} hinzufügen",
    workSetup: "Arbeitsform",
    anythingGoes: "alles möglich",
    leaveOutRest: "den Rest lassen wir weg",
    hybridNote:
      "Hybrid heißt einen Teil der Woche im Büro — es muss also weiterhin erreichbar sein. Viele Anzeigen sagen dazu gar nichts. Die behalten wir und lassen den Matcher die Beschreibung lesen, statt zu raten.",
    remoteLabel: "Vollständig remote — wie weit weg?",
    remoteDisabledNote: "Gilt erst, wenn oben „Vollständig remote“ ausgewählt ist.",
  },

  landing: {
    wizardAria: "Digest zusammenstellen",
    stepOf: "Schritt {n} von {total}",
    q1: "Jetzt mit der Suche starten",
    cvReading: "Wir lesen deinen Lebenslauf…",
    cvDrop: "Lebenslauf hier ablegen — wir füllen das aus",
    cvHint:
      "PDF oder DOCX · Wir lesen ihn, um deine Treffer einzurichten, und löschen die Datei danach. Wird nie weitergegeben.",
    cvChoose: "Datei wählen",
    cvDone: "Lebenslauf gelesen — vorausgefüllt",
    cvRemove: "Lebenslauf entfernen",
    orPickManually: "oder manuell auswählen",
    addRolePlaceholder: "Weitere Position hinzufügen…",
    addRoleAria: "Position hinzufügen",
    q2: "Worin bist du gut?",
    q2hint: "Danach suchen wir passende Jobs. Tippe alles an, was zutrifft.",
    addSkillPlaceholder: "Fähigkeit hinzufügen…",
    addSkillAria: "Fähigkeit hinzufügen",
    q3: "Wo & wie?",
    q3hint:
      "Zuerst der Ort — wähle die Städte, in die du wirklich pendeln könntest. Vor-Ort-Jobs anderswo fallen raus; vollständig remote nicht.",
    workTypeHint: "Art der Arbeit — tippe alles an, was passt.",
    levelHint: "Dein Level — wir schicken nur Stellen auf den Leveln, die du wählst.",
    narrowWarning:
      "Das ist eine sehr enge Suche — es kommen vielleicht wenige Treffer. Füge ein Level, eine Position oder eine weitere Stadt hinzu.",
    q4google: "Digest bestätigen",
    googleHint:
      "Anmeldung als {0} — mit Google verifiziert, deshalb gibt es keine Bestätigungsmail. Dein erster Digest kommt morgen um 7:00.",
    consent: "Ich stimme der {0} und dem täglichen Digest zu.",
    consentLink: "Datenschutzerklärung",
    q4: "Wohin schicken wir ihn?",
    q4hint: "Zuerst eine Bestätigungsmail — dann täglich dein Digest um 7:00.",
    googleSignup: "Mit Google registrieren",
    emailPlaceholder: "du@example.com",
    emailAria: "Deine E-Mail",
    turnstileNote: "Geschützt durch Cloudflare Turnstile — kein CAPTCHA",
    back: "← Zurück",
    next: "Weiter →",
    sending: "Wird gesendet…",
    submit: "Digest starten →",
    livePreview: "Live-Vorschau",
    trustEmail: "Eine E-Mail pro Tag",
    trustUnsub: "Abmelden mit einem Klick",
    trustFree: "Kostenlos · 20+ Quellen jede Nacht durchsucht",
    howItWorks: "So funktioniert's",
    step1Title: "Profil antippen",
    step1Body: "Positionen, Fähigkeiten, wo du arbeiten kannst. Zwanzig Sekunden, fast nur Tippen.",
    step2Title: "Wir matchen über Nacht",
    step2Body: "Frische Anzeigen aus 20+ Quellen, für dich sortiert, Duplikate raus.",
    step3Title: "Eine E-Mail lesen",
    step3Body: "Eine kurze sortierte Auswahl — mit Begründung und Bewerbungslink für jede Stelle.",
    mcta: "Digest holen",
    mctaAria: "Zur Anmeldung springen",
    toast: {
      googleExpired: "Deine Google-Anmeldung ist abgelaufen — bitte versuche es erneut.",
      cvWrongType: "Bitte lade eine PDF- oder DOCX-Datei hoch",
      cvTooLarge: "Die Datei ist zu groß (max. 8 MB)",
      cvOk: "Lebenslauf gelesen — wir haben dein Profil vorausgefüllt",
      cvUnreadable: "Diese Datei konnte nicht gelesen werden",
      needConsent: "Bitte stimme zuerst der Datenschutzerklärung zu",
      needLevel: "Wähle mindestens ein Level",
      needEmail: "Gib eine gültige E-Mail-Adresse ein",
      needTurnstile: "Bitte schließe die Verifizierung ab",
      needRole: "Wähle mindestens eine Position, um fortzufahren",
      needCountry: "Wähle mindestens ein Land, um fortzufahren",
      needLevelToContinue: "Wähle mindestens ein Level, um fortzufahren",
      genericError: "Etwas ist schiefgelaufen — bitte erneut versuchen",
    },
  },

  preview: {
    inbox: "Posteingang — {email}",
    time: "7:00",
    subject: "{count} für dich — {date}",
    roleCount: { one: "{n} neue Stelle", other: "{n} neue Stellen" },
    roleCountNamed: { one: "{n} neue Stelle: {role}", other: "{n} neue Stellen: {role}" },
    pickRole: "Wähle eine Position, um deine Treffer zu sehen →",
    noCombo: "Keine Treffer für diese Kombination — erweitere Level oder Arbeitsart →",
    greeting: "Guten Morgen. Heute {0} — aus 214 über Nacht geprüften Anzeigen.",
    freshMatches: { one: "{n} neuer Treffer", other: "{n} neue Treffer" },
    reasonMatches: "Passt zu {0}. Branche: {sector}.",
    reasonGeneric: "Starke Stelle im Bereich {sector} in deiner Region. Branche: {sector}.",
    refine: "Einstellungen anpassen",
    pause: "2 Wochen pausieren",
    unsubscribe: "Abmelden",
    fine: "Du bekommst das, weil du dich auf jobdigest.eu angemeldet hast · Eine E-Mail pro Tag.",
  },

  prefs: {
    signedInLabel: "Angemeldet",
    title: "Deine Einstellungen",
    signedInAs: "Angemeldet als {0}. Ändere etwas, pausiere oder geh — {1}.",
    logOutInline: "abmelden",
    noPasswordLead: "Kein Passwort.",
    noPasswordBody:
      "Der Klick auf den Link in deiner E-Mail hat dich angemeldet und hält dich auf diesem Gerät angemeldet — du brauchst den Link hier also nicht noch einmal. Du kannst dich jederzeit abmelden.",
    roles: "Positionen",
    skills: "Fähigkeiten / Stichwörter",
    addRolePlaceholder: "Position hinzufügen…",
    addSkillPlaceholder: "Fähigkeit hinzufügen…",
    addRoleAria: "Position hinzufügen",
    addSkillAria: "Fähigkeit hinzufügen",
    sectors: "Branchen, die dich interessieren (optional)",
    sectorsHint:
      "Bereiche, in denen du am liebsten arbeiten würdest — E-Commerce, Finanzen, Gaming… Wir nutzen es zur Einschätzung der Passung, nie als harten Filter.",
    addSectorPlaceholder: "Branche hinzufügen…",
    addSectorAria: "Branche hinzufügen",
    seniorityLabel: "Level — wir schicken nur Stellen auf den Leveln, die du wählst",
    uploadCv: "Lebenslauf hochladen",
    cvReplaceConfirm:
      "Deine aktuellen Angaben (Rollen, Skills, Branchen, Level, Jahre Berufserfahrung, "
      + "Ausbildung) werden durch das ersetzt, was wir im Lebenslauf erkennen. Gespeichert "
      + "wird erst, wenn du auf Speichern klickst — du kannst vorher alles prüfen und "
      + "anpassen. Die Datei wird einmal gelesen und verworfen. Fortfahren?",
    yearsExperience: "Jahre Berufserfahrung",
    yearsExperienceHint:
      "Stellen, die mehr Jahre verlangen, als du hast, lassen wir weg. Stellen ohne Angabe "
      + "kommen weiterhin durch. Leer lassen = keine Präferenz.",
    minScore: "Schwelle für Top-Treffer",
    minScoreHint:
      "Wie stark ein Treffer sein muss (von 10), um in deiner E-Mail hervorgehoben zu "
      + "werden. Schwächere Treffer erscheinen weiterhin auf deiner Treffer-Seite, und du "
      + "bekommst immer einen täglichen Vorschlag, auch wenn nichts die Schwelle erreicht.",
    frequency: "Häufigkeit",
    freqDaily: "Täglich",
    freqWeekdays: "Nur werktags",
    freqWeekly: "Wöchentlich (montags)",
    pausedTitle: "Digest pausiert",
    pauseTitle: "Digest pausieren",
    pausedUntil: "Pausiert bis {date} — jederzeit fortsetzbar.",
    pausedUntilSoon: "bald",
    pauseBody: "Mach eine Pause, ohne dich abzumelden — geht weiter, wenn du so weit bist.",
    resumeNow: "Jetzt fortsetzen",
    pauseTwoWeeks: "2 Wochen pausieren",
    saving: "Wird gespeichert…",
    save: "Änderungen speichern",
    unsubscribeAll: "Von allen E-Mails abmelden",
    unsubscribeConfirm: "Zum Bestätigen erneut klicken — von allen E-Mails abmelden",
    errTitle: "Deine Einstellungen lassen sich nicht öffnen",
    errNoLink:
      "Öffne deine Einstellungen über den Link in deiner E-Mail — oder hol dir über „Abo verwalten“ einen neuen.",
    errUnknownLink: "Unbekannter oder abgelaufener Link.",
    toast: {
      saved: "Einstellungen gespeichert",
      saveFailed: "Speichern fehlgeschlagen — bitte erneut versuchen",
      paused: "Digest für 2 Wochen pausiert",
      pauseFailed: "Pausieren fehlgeschlagen",
      resumed: "Digest fortgesetzt",
      resumeFailed: "Fortsetzen fehlgeschlagen",
      unsubscribed: "Du hast dich abgemeldet",
      unsubscribeFailed: "Abmelden fehlgeschlagen",
    },
  },

  matches: {
    label: "Deine Treffer · angemeldet",
    countTitle: { one: "{n} Treffer für dich", other: "{n} Treffer für dich" },
    noneTitle: "Noch keine Treffer",
    intro:
      "Alles, was wir für {0} gefunden haben, beste zuerst. Die stärksten schicken wir dir jeden Morgen per E-Mail — das hier ist die vollständige Liste.",
    nothingTitle: "Noch nichts",
    nothingBody:
      "Deine ersten Treffer suchen wir über Nacht — schau nach dem morgigen Digest um 7:00 wieder rein.",
    adjustPrefs: "Einstellungen anpassen →",
    notQuiteRight: "Nicht ganz passend? Passe Positionen & Fähigkeiten an →",
    // „von“ verlangt den Dativ: Singular Treffer, Plural Treffern.
    showing: {
      one: "{shown} von {n} Treffer angezeigt",
      other: "{shown} von {n} Treffern angezeigt",
    },
    loadMore: "Mehr laden",
    loadMoreFailed: "Mehr laden hat nicht geklappt — prüfe deine Verbindung und versuch es erneut.",
    errTitle: "Deine Treffer lassen sich nicht öffnen",
    errNoLink:
      "Öffne deine Treffer über den Link in deiner E-Mail — oder hol dir über „Abo verwalten“ einen neuen.",
    errUnknownLink: "Unbekannter oder abgelaufener Link.",
    topMatch: "Top-Treffer",
    tagHybrid: "Hybrid",
    tagRemote: "Remote",
    tagFreelance: "Freelance",
    hideHint:
      "Schon beworben oder kein Interesse? Hak die Jobs ab und blende sie aus — sie verschwinden von dieser Seite und aus deiner täglichen E-Mail.",
    selectAll: "Alle angezeigten auswählen",
    clearSelection: "Auswahl aufheben",
    selectAria: "Auswählen",
    selected: { one: "{n} ausgewählt", other: "{n} ausgewählt" },
    hideSelected: "Ausgewählte ausblenden",
    hiding: "Wird ausgeblendet…",
    hideFailed: "Ausblenden hat nicht geklappt — versuch es erneut.",
    hiddenLink: {
      one: "{n} ausgeblendeter Treffer →",
      other: "{n} ausgeblendete Treffer →",
    },
    filterBySkill: "Nach Skill filtern",
    filterByWorkMode: "Nach Arbeitsform filtern",
    searchLabel: "Ihre Treffer durchsuchen",
    searchPlaceholder: "Position, Kenntnis oder Unternehmen",
    greatFitsOnly: "Nur Top-Treffer",
    scoreFilterAria: "Nach Mindestscore filtern",
    scoreAny: "Beliebiger Score",
    scoredOn: "Bewertet am {date}",
    filterByExperience: "Berufserfahrung",
    maxExpAny: "Jede Erfahrungsstufe",
    maxExpOption: {
      one: "Höchstens {n} Jahr gefordert",
      other: "Höchstens {n} Jahre gefordert",
    },
    tagExperience: {
      one: "{n}+ Jahr",
      other: "{n}+ Jahre",
    },
    clearFilter: "Zurücksetzen",
    refreshNow: "Treffer jetzt aktualisieren",
    refreshBusy: "Wird aktualisiert…",
    refreshSent: {
      one: "{n} neuer Job an dein Postfach gesendet.",
      other: "{n} neue Jobs an dein Postfach gesendet.",
    },
    refreshNone: "Momentan keine neuen Treffer – schau später wieder vorbei.",
    refreshFailed: "Aktualisierung gerade nicht möglich – bitte gleich erneut versuchen.",
  },

  hidden: {
    label: "Ausgeblendete Treffer · angemeldet",
    countTitle: {
      one: "{n} ausgeblendeter Treffer",
      other: "{n} ausgeblendete Treffer",
    },
    noneTitle: "Keine ausgeblendeten Treffer",
    intro:
      "Treffer, die du ausgeblendet hast. Sie stehen weder auf deiner Trefferseite noch in deiner täglichen E-Mail — blende sie jederzeit wieder ein.",
    nothingTitle: "Nichts ausgeblendet",
    nothingBody:
      "Blende einen Job bei deinen Treffern aus, dann landet er hier. Gelöscht wird nie etwas.",
    backToMatches: "← Zurück zu deinen Treffern",
    unhideSelected: "Ausgewählte einblenden",
    unhiding: "Wird eingeblendet…",
    unhideFailed: "Einblenden hat nicht geklappt — versuch es erneut.",
    // „von“ verlangt den Dativ: Singular Treffer, Plural Treffern.
    showing: {
      one: "{shown} von {n} ausgeblendeten Treffer angezeigt",
      other: "{shown} von {n} ausgeblendeten Treffern angezeigt",
    },
  },

  jobs: {
    navLink: "Jobs durchsuchen",
    metaTitle: "Jobs in ganz Europa durchsuchen · JobDigest",
    metaDescription:
      "Durchsuchen Sie alle Stellen, die wir erfassen — staatliche Stellenregister und hunderte Karriereseiten von Unternehmen an einem Ort. Kostenlos, ohne Konto.",
    label: "Kostenlos · ohne Konto",
    title: "Alle Stellen, die wir erfassen, an einem Ort",
    intro:
      "Staatliche Stellenregister und hunderte Karriereseiten von Unternehmen, gemeinsam durchsuchbar. Wir aktualisieren jeden Morgen und zeigen keine Anzeige, die wir seit einer Woche nicht gesehen haben.",
    searchLabel: "Stellen suchen",
    searchPlaceholder: "Position, Kenntnis oder Unternehmen",
    searchButton: "Suchen",
    startTitle: "Wonach suchen Sie?",
    startBody:
      "Suchen Sie nach Position, Kenntnis oder Unternehmen — oder wählen Sie einen Bereich, ein Land oder ein Level.",
    countTitle: { one: "{n} Stelle", other: "{n} Stellen" },
    countCapped: "{n}+ Stellen",
    showing: {
      one: "{shown} von {n} Stelle angezeigt",
      other: "{shown} von {n} Stellen angezeigt",
    },
    noneTitle: "Dazu passt nichts",
    noneBody: "Versuchen Sie eine breitere Suche oder entfernen Sie einen Filter.",
    filterCategory: "Bereich",
    filterCountry: "Land",
    filterCity: "Stadt",
    cityNeedsCountry: "Wählen Sie zuerst ein Land",
    filterSeniority: "Level",
    filterWorkMode: "Arbeitsform",
    intlEea: "EU-international",
    intlNa: "Nordamerika-international",
    clearFilters: "Filter zurücksetzen",
    loadMore: "Mehr laden",
    loadMoreFailed: "Mehr konnte nicht geladen werden — Verbindung prüfen und erneut versuchen.",
    errTitle: "Stellen konnten nicht geladen werden",
    errBody: "Bei uns ist etwas schiefgelaufen. Bitte versuchen Sie es gleich noch einmal.",
    categories: {
      machine_learning: "Machine Learning",
      other_tech_function: "Weitere Tech-Rollen",
    },
    ctaTitle: "Lieber per E-Mail?",
    ctaBody:
      "Sagen Sie uns, wonach Sie suchen. Wir lesen jeden Morgen jede neue Stelle und mailen Ihnen nur die passenden — mit einem Satz, warum sie es geworden ist.",
    ctaButton: "Tägliches Digest erhalten",
  },

  checkInbox: {
    title: "Schau in dein Postfach",
    body: "Wir haben einen Bestätigungslink an {0} geschickt. Klick ihn an — dein täglicher Digest startet morgen früh um 7:00.",
    inMeantime: " In der Zwischenzeit: aktuelle Jobs, die zu deiner Suche passen:",
    doubleOptIn: "Double Opt-in · DSGVO-Einwilligung",
    yourInbox: "dein Postfach",
    instantPreview: "Sofort-Vorschau · Stichwort-Treffer",
    instantNote:
      "Ein schneller Stichwort-Abgleich für den Anfang. Die E-Mail von morgen ist {0} — jede Stelle bewertet, mit einer Begründung, warum sie passt.",
    instantNoteEmphasis: "KI-sortiert",
  },

  manage: {
    sentTitle: "Schau in dein Postfach",
    sentBody:
      "Falls {0} ein JobDigest-Abo hat, haben wir gerade den privaten Einstellungslink dorthin geschickt. Öffne ihn, um Einstellungen zu ändern, zu pausieren oder dich abzumelden — ohne Passwort.",
    didntGet: "Nichts bekommen? Schau in den Spam oder versuch es in ein paar Minuten erneut.",
    title: "Bei JobDigest anmelden",
    body: "Keine Passwörter. Gib deine Adresse ein und wir schicken dir einen sicheren Link — ein Klick und du bist angemeldet, und bleibst es auf diesem Gerät.",
    googleNoSub:
      "Dieses Google-Konto hat noch kein Abo. Registriere dich zuerst, danach kannst du dich mit Google anmelden.",
    googleSuppressed:
      "Diese Adresse hat sich früher abgemeldet, deshalb können wir sie nicht automatisch wieder anmelden. Melde dich bei uns, wenn du zurückkommen möchtest.",
    googleError:
      "Die Google-Anmeldung wurde nicht abgeschlossen. Bitte versuch es erneut oder nutze unten den Link per E-Mail.",
    googleSignin: "Mit Google anmelden",
    emailLabel: "Deine E-Mail-Adresse",
    sending: "Wird gesendet…",
    submit: "Schick mir meinen Einstellungslink",
    onlyOwnInbox: "Wir schicken den Link immer nur an dein eigenes Postfach.",
    error: "Etwas ist schiefgelaufen. Bitte versuch es gleich noch einmal.",
  },

  root: {
    title: "Sprache wählen",
    body: "Wir bringen dich zu JobDigest in deiner Sprache…",
  },
};

export default de;
