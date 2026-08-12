import type { Messages } from "../schema";

/**
 * Slovak. Like Czech it needs `one` / `few` / `other` plurals (2–4 splits from 5+), and like
 * Czech it names the role after a colon rather than declining an English job title inside the
 * sentence. Close to `cs.ts` but deliberately a separate file — the two diverge in ordinary
 * vocabulary ("nastavenie" vs "nastavení", "ponuka" vs "nabídka") and sharing one catalogue
 * would mean shipping Czech to Slovak readers.
 */
const sk: Messages = {
  meta: {
    title: "JobDigest — ponuky práce, ktoré vám sadnú, každé ráno",
    description:
      "Jeden krátky e-mail denne s vybraným a zoradeným prehľadom ponúk, ktoré vám sadnú. Tech a CZ+EU na prvom mieste. Začiatok zadarmo.",
  },

  nav: {
    myMatches: "Moje ponuky",
    myPreferences: "Moje nastavenia",
    logOut: "Odhlásiť sa",
    logIn: "Prihlásiť sa",
    themeTitle: "Prepnúť svetlý / tmavý režim",
    themeAria: "Prepnúť motív",
    languageAria: "Vybrať jazyk",
  },

  footer: {
    madeInEu: "Vyrobené v EÚ",
    manage: "Správa odberu",
    privacy: "Súkromie",
    terms: "Podmienky",
  },

  common: {
    loading: "Načítava sa…",
    add: "Pridať",
    or: "alebo",
    backToHome: "← Späť na úvod",
    goHome: "Prejsť na úvodnú stránku →",
    viewAndApply: "Zobraziť a odpovedať →",
    roleFallback: "Pozícia",
  },

  roles: {
    cybersecurity: "Kybernetická bezpečnosť",
    science_research: "Veda a výskum",
    social_care: "Sociálna práca",
    product_manager: "Produktový manažér",
    marketing: "Marketing",
    social_media: "Sociálne siete",
    data_analyst: "Dátový analytik",
    designer: "Dizajnér",
    software_engineer: "Vývojár softvéru",
    data_engineer: "Dátový inžinier",
    devops: "DevOps",
    finance: "Financie",
    ml_engineer: "ML inžinier",
    sales: "Obchod",
    hr: "HR a nábor",
    legal: "Právo",
    customer_support: "Zákaznícka podpora",
    operations: "Prevádzka",
    healthcare: "Zdravotníctvo",
    education: "Školstvo",
    hospitality: "Pohostinstvo",
    skilled_trades: "Remeslá",
    construction: "Stavebníctvo",
    logistics: "Logistika",
    manufacturing: "Výroba",
    engineering: "Inžinierstvo",
  },

  workTypes: {
    fulltime: "Plný úväzok",
    freelance: "Freelance",
    parttime: "Čiastočný úväzok",
  },

  seniorities: {
    intern: "Stáž",
    entry_level: "Absolvent",
    junior: "Junior",
    mid: "Medior",
    senior: "Senior",
    lead: "Vedúci / manažment",
  },

  geo: {
    countries: {
      AT: "Rakúsko", BE: "Belgicko", BG: "Bulharsko", HR: "Chorvátsko", CY: "Cyprus",
      CZ: "Česko", DK: "Dánsko", EE: "Estónsko", FI: "Fínsko", FR: "Francúzsko",
      DE: "Nemecko", GR: "Grécko", HU: "Maďarsko", IE: "Írsko", IT: "Taliansko",
      LV: "Lotyšsko", LT: "Litva", LU: "Luxembursko", MT: "Malta", NL: "Holandsko",
      PL: "Poľsko", PT: "Portugalsko", RO: "Rumunsko", SK: "Slovensko", SI: "Slovinsko",
      ES: "Španielsko", SE: "Švédsko",
      CH: "Švajčiarsko", IS: "Island", LI: "Lichtenštajnsko", NO: "Nórsko",
      GB: "Spojené kráľovstvo", US: "Spojené štáty",
      CA: "Kanada",
    },
    remoteScope: {
      country: "Len v krajinách, ktoré som vybral(a)",
      eu: "Kdekoľvek v EÚ alebo EHP",
      worldwide: "Kdekoľvek na svete",
    },
    workModeLabel: {
      onsite: "Z kancelárie",
      hybrid: "Hybridne",
      remote: "Plne na diaľku",
    },
    workModeHint: {
      onsite: "V kancelárii",
      hybrid: "Časť v kancelárii, časť z domu — stále musíte byť nablízku",
      remote: "Žiadna kancelária",
    },
  },

  educationLevels: {
    secondary: "Stredná škola",
    vocational: "Vyučený/á v odbore",
    bachelor: "Bakalár",
    master: "Magister / Ing.",
    doctorate: "Doktorát / PhD.",
  },

  education: {
    label: "Vzdelanie, ktoré môže inzerát požadovať",
    anyLevel: "čokoľvek",
    narrowed: "ostatné vynecháme",
    note:
      "Väčšina inzerátov vzdelanie neuvádza — tie všetky ponechávame a popis namiesto toho prečíta matcher. Vynecháme len tých pár, ktoré výslovne žiadajú titul, ktorý nemáte.",
    fieldLabel: "Čo ste študovali (nepovinné)",
    fieldPlaceholder: "Ekonómia, Informatika…",
    fieldHint:
      "Nikdy sa nepoužíva ako filter — len pomáha matcheru posúdiť, či k vám rola sedí.",
  },

  location: {
    countriesLabel: "Krajiny, kde môžete pracovať",
    addCountryAria: "Pridať ďalšiu krajinu",
    addCountryOption: "Pridať ďalšiu krajinu…",
    citiesIn: "Mestá — {country}",
    anyCityNote: "kdekoľvek v krajine (ponuky z kancelárie kdekoľvek)",
    pickedCityNote: "ponuky z kancelárie len vo vybraných mestách",
    anyCity: "Kdekoľvek",
    addTownPlaceholder: "Pridať ďalšie mesto — {country}…",
    addCityAria: "Pridať mesto — {country}",
    workSetup: "Forma práce",
    anythingGoes: "čokoľvek",
    leaveOutRest: "zvyšok vynecháme",
    hybridNote:
      "Hybridná práca znamená časť týždňa v kancelárii, takže to stále musí byť niekam, kam sa dostanete. Veľa inzerátov to neuvádza vôbec — tie necháváme a formu práce si prečíta matcher priamo z popisu, namiesto hádania.",
    remoteLabel: "Plne vzdialené ponuky — ako ďaleko?",
    remoteDisabledNote: "Platí, až keď vyššie vyberiete „Plne na diaľku“.",
  },

  landing: {
    wizardAria: "Zostavte si prehľad",
    stepOf: "Krok {n} zo {total}",
    q1: "Začnite hľadať hneď",
    cvReading: "Čítame váš životopis…",
    cvDrop: "Presuňte sem životopis — vyplníme to za vás",
    cvHint:
      "PDF alebo DOCX · Prečítame ho, aby sme vám nastavili ponuky, a potom súbor zmažeme. Nikdy ho nezdieľame.",
    cvChoose: "Vybrať súbor",
    cvDone: "Životopis načítaný — predvyplnené",
    cvRemove: "Odstrániť životopis",
    orPickManually: "alebo vyberte ručne",
    addRolePlaceholder: "Pridať ďalšiu pozíciu…",
    addRoleAria: "Pridať pozíciu",
    q2: "V čom ste dobrí?",
    q2hint: "Podľa toho hľadáme ponuky. Vyberte všetko, čo sedí.",
    addSkillPlaceholder: "Pridať zručnosť…",
    addSkillAria: "Pridať zručnosť",
    q3: "Kde a ako?",
    q3hint:
      "Najprv miesto — vyberte mestá, kam by ste reálne dochádzali. Ponuky z kancelárie kdekoľvek inde vyradíme; plne vzdialené nie.",
    workTypeHint: "Typ práce — vyberte všetko, čo sedí.",
    levelHint: "Vaša úroveň — posielame len ponuky na úrovniach, ktoré vyberiete.",
    narrowWarning:
      "To je veľmi úzke hľadanie — ponúk môže prísť málo. Pridajte úroveň, pozíciu alebo ďalšie mesto.",
    q4google: "Potvrďte svoj prehľad",
    googleHint:
      "Registrujete sa ako {0} — overené cez Google, takže žiadny potvrdzovací e-mail nepríde. Prvý prehľad dorazí zajtra o 7:00.",
    consent: "Súhlasím so {0} a so zasielaním denného prehľadu.",
    consentLink: "zásadami ochrany súkromia",
    q4: "Kam vám to máme posielať?",
    q4hint: "Najprv jeden potvrdzovací e-mail — potom denný prehľad o 7:00.",
    googleSignup: "Registrovať sa cez Google",
    emailPlaceholder: "vy@example.com",
    emailAria: "Váš e-mail",
    turnstileNote: "Chránené službou Cloudflare Turnstile — žiadna CAPTCHA",
    back: "← Späť",
    next: "Ďalej →",
    sending: "Odosielame…",
    submit: "Spustiť môj prehľad →",
    livePreview: "Živá ukážka",
    trustEmail: "Jeden e-mail denne",
    trustUnsub: "Odhlásenie jedným kliknutím",
    trustFree: "Začiatok zadarmo · 16 zdrojov prehľadávame každú noc",
    howItWorks: "Ako to funguje",
    step1Title: "Naklikajte si profil",
    step1Body: "Pozície, zručnosti, kde môžete pracovať. Dvadsať sekúnd, hlavne klikanie.",
    step2Title: "Cez noc hľadáme",
    step2Body: "Čerstvé inzeráty z desiatok zdrojov, zoradené pre vás, duplicity preč.",
    step3Title: "Prečítate jeden e-mail",
    step3Body: "Krátky zoradený výber — pri každej ponuke dôvod a odkaz, kde sa prihlásiť.",
    mcta: "Chcem svoj prehľad",
    mctaAria: "Prejsť na registráciu",
    toast: {
      googleExpired: "Prihlásenie cez Google vypršalo — skúste to prosím znova.",
      cvWrongType: "Nahrajte prosím súbor PDF alebo DOCX",
      cvTooLarge: "Súbor je príliš veľký (max. 8 MB)",
      cvOk: "Životopis načítaný — predvyplnili sme váš profil",
      cvUnreadable: "Súbor sa nepodarilo prečítať",
      needConsent: "Najprv prosím odsúhlaste zásady ochrany súkromia",
      needLevel: "Vyberte aspoň jednu úroveň",
      needEmail: "Zadajte platný e-mail",
      needTurnstile: "Dokončite prosím overenie",
      needRole: "Pokračujte výberom aspoň jednej pozície",
      needCountry: "Pokračujte výberom aspoň jednej krajiny",
      needLevelToContinue: "Pokračujte výberom aspoň jednej úrovne",
      genericError: "Niečo sa pokazilo — skúste to prosím znova",
    },
  },

  preview: {
    inbox: "doručená pošta — {email}",
    time: "7:00",
    subject: "{count} pre vás — {date}",
    roleCount: {
      one: "{n} nová ponuka",
      few: "{n} nové ponuky",
      other: "{n} nových ponúk",
    },
    roleCountNamed: {
      one: "{n} nová ponuka: {role}",
      few: "{n} nové ponuky: {role}",
      other: "{n} nových ponúk: {role}",
    },
    pickRole: "Vyberte pozíciu a uvidíte svoje ponuky →",
    noCombo: "Pre túto kombináciu nič nemáme — rozšírte úrovne alebo typ práce →",
    greeting: "Dobré ráno. Dnes {0} — z 214 inzerátov prehľadaných cez noc.",
    freshMatches: {
      one: "{n} nová zhoda",
      few: "{n} nové zhody",
      other: "{n} nových zhôd",
    },
    reasonMatches: "Zodpovedá: {0}. Odbor: {sector}.",
    reasonGeneric: "Silná ponuka v odbore {sector} vo vašom regióne. Odbor: {sector}.",
    refine: "Upraviť nastavenia",
    pause: "Pozastaviť na 2 týždne",
    unsubscribe: "Odhlásiť odber",
    fine: "Tento e-mail dostávate, pretože ste sa prihlásili na jobdigest.eu · Jeden e-mail denne.",
  },

  prefs: {
    signedInLabel: "Prihlásení",
    title: "Vaše nastavenia",
    signedInAs: "Prihlásení ako {0}. Čokoľvek zmeňte, pozastavte alebo zrušte — {1}.",
    logOutInline: "odhlásiť sa",
    noPasswordLead: "Žiadne heslo.",
    noPasswordBody:
      "Kliknutím na odkaz v e-maile ste sa prihlásili a na tomto zariadení zostanete prihlásení, takže odkaz už tu znova potrebovať nebudete. Odhlásiť sa môžete kedykoľvek.",
    roles: "Pozície",
    skills: "Zručnosti / kľúčové slová",
    addRolePlaceholder: "Pridať pozíciu…",
    addSkillPlaceholder: "Pridať zručnosť…",
    addRoleAria: "Pridať pozíciu",
    addSkillAria: "Pridať zručnosť",
    sectors: "Odvetvia, ktoré vás zaujímajú (nepovinné)",
    sectorsHint:
      "Oblasti, v ktorých by ste najradšej pracovali — e-commerce, financie, hry… Používame to na posúdenie vhodnosti, nikdy ako striktný filter.",
    addSectorPlaceholder: "Pridať odvetvie…",
    addSectorAria: "Pridať odvetvie",
    seniorityLabel: "Úroveň — posielame len ponuky na úrovniach, ktoré vyberiete",
    frequency: "Frekvencia",
    freqDaily: "Denne",
    freqWeekdays: "Len cez pracovné dni",
    freqWeekly: "Týždenne (v pondelok)",
    pausedTitle: "Prehľad pozastavený",
    pauseTitle: "Pozastaviť prehľad",
    pausedUntil: "Pozastavené do {date} — obnoviť môžete kedykoľvek.",
    pausedUntilSoon: "čoskoro",
    pauseBody: "Dajte si pauzu bez rušenia odberu — spustíme to, keď budete chcieť.",
    resumeNow: "Obnoviť hneď",
    pauseTwoWeeks: "Pozastaviť na 2 týždne",
    saving: "Ukladáme…",
    save: "Uložiť zmeny",
    unsubscribeAll: "Odhlásiť sa zo všetkých e-mailov",
    unsubscribeConfirm: "Potvrďte ďalším kliknutím — odhlásiť sa zo všetkých e-mailov",
    errTitle: "Vaše nastavenia sa nedajú otvoriť",
    errNoLink:
      "Otvorte nastavenia z odkazu vo svojom e-maile — alebo si cez „Správa odberu“ nechajte poslať nový.",
    errUnknownLink: "Neznámy alebo vypršaný odkaz.",
    toast: {
      saved: "Nastavenia uložené",
      saveFailed: "Uloženie zlyhalo — skúste to prosím znova",
      paused: "Prehľad pozastavený na 2 týždne",
      pauseFailed: "Pozastavenie zlyhalo",
      resumed: "Prehľad obnovený",
      resumeFailed: "Obnovenie zlyhalo",
      unsubscribed: "Odber bol zrušený",
      unsubscribeFailed: "Zrušenie odberu zlyhalo",
    },
  },

  matches: {
    label: "Vaše ponuky · prihlásení",
    countTitle: {
      one: "{n} ponuka pre vás",
      few: "{n} ponuky pre vás",
      other: "{n} ponúk pre vás",
    },
    noneTitle: "Zatiaľ žiadne ponuky",
    intro:
      "Všetko, čo sme našli pre {0}, zoradené od najlepšieho. Tie najsilnejšie vám každé ráno pošleme e-mailom — toto je celý zoznam.",
    nothingTitle: "Zatiaľ nič",
    nothingBody:
      "Prvé ponuky hľadáme cez noc — pozrite sa sem po zajtrajšom prehľade o 7:00.",
    adjustPrefs: "Upraviť nastavenia →",
    notQuiteRight: "Nie je to ono? Upravte si pozície a zručnosti →",
    // Po predložke „z“ je počítaný výraz v genitíve: 1 → ponuky, 2+ → ponúk.
    showing: {
      one: "Zobrazené {shown} z {n} ponuky",
      few: "Zobrazené {shown} z {n} ponúk",
      other: "Zobrazené {shown} z {n} ponúk",
    },
    loadMore: "Načítať ďalšie",
    loadMoreFailed: "Ďalšie sa nepodarilo načítať — skontrolujte pripojenie a skúste to znova.",
    errTitle: "Vaše ponuky sa nedajú otvoriť",
    errNoLink:
      "Otvorte ponuky z odkazu vo svojom e-maile — alebo si cez „Správa odberu“ nechajte poslať nový.",
    errUnknownLink: "Neznámy alebo vypršaný odkaz.",
    topMatch: "Najlepšia zhoda",
    tagHybrid: "Hybridne",
    tagRemote: "Na diaľku",
    tagFreelance: "Freelance",
    hideHint:
      "Už ste sa prihlásili, alebo vás ponuka nezaujíma? Označte ju a skryte — zmizne z tejto stránky aj z denného e-mailu.",
    selectAll: "Označiť všetko zobrazené",
    clearSelection: "Zrušiť výber",
    selectAria: "Vybrať",
    selected: {
      one: "{n} vybraná ponuka",
      few: "{n} vybrané ponuky",
      other: "{n} vybraných ponúk",
    },
    hideSelected: "Skryť vybrané",
    hiding: "Skrývam…",
    hideFailed: "Ponuky sa nepodarilo skryť — skúste to znova.",
    hiddenLink: {
      one: "{n} skrytá ponuka →",
      few: "{n} skryté ponuky →",
      other: "{n} skrytých ponúk →",
    },
    filterBySkill: "Filtrovať podľa zručnosti",
    filterByWorkMode: "Filtrovať podľa formy práce",
    searchLabel: "Hľadať vo vašich ponukách",
    searchPlaceholder: "Pozícia, zručnosť alebo firma",
    greatFitsOnly: "Len najlepšie ponuky",
    clearFilter: "Zrušiť",
  },

  hidden: {
    label: "Skryté ponuky · prihlásení",
    countTitle: {
      one: "{n} skrytá ponuka",
      few: "{n} skryté ponuky",
      other: "{n} skrytých ponúk",
    },
    noneTitle: "Žiadne skryté ponuky",
    intro:
      "Ponuky, ktoré ste skryli. Nezobrazujú sa medzi vašimi ponukami ani v dennom e-maile — kedykoľvek ich môžete znova zobraziť.",
    nothingTitle: "Nič skryté",
    nothingBody: "Skryte ponuku medzi svojimi ponukami a objaví sa tu. Nič sa nemaže.",
    backToMatches: "← Späť na vaše ponuky",
    unhideSelected: "Zobraziť vybrané",
    unhiding: "Zobrazujem…",
    unhideFailed: "Ponuky sa nepodarilo zobraziť — skúste to znova.",
    // Po predložke „z“ je počítaný výraz v genitíve: 1 → ponuky, 2+ → ponúk.
    showing: {
      one: "Zobrazené {shown} z {n} skrytej ponuky",
      few: "Zobrazené {shown} z {n} skrytých ponúk",
      other: "Zobrazené {shown} z {n} skrytých ponúk",
    },
  },

  jobs: {
    navLink: "Prehliadať ponuky",
    metaTitle: "Pracovné ponuky v Európe · JobDigest",
    metaDescription:
      "Prehľadajte všetky ponuky, ktoré sledujeme — štátne registre voľných miest aj stovky firemných kariérnych stránok na jednom mieste. Zadarmo a bez registrácie.",
    label: "Zadarmo · bez registrácie",
    title: "Všetky ponuky, ktoré sledujeme, na jednom mieste",
    intro:
      "Štátne registre voľných miest a stovky firemných kariérnych stránok, prehľadateľné naraz. Aktualizujeme každé ráno a nikdy nezobrazíme ponuku, ktorú sme týždeň nevideli.",
    searchLabel: "Hľadať ponuky",
    searchPlaceholder: "Pozícia, zručnosť alebo firma",
    searchButton: "Hľadať",
    startTitle: "Čo hľadáte?",
    startBody:
      "Zadajte pozíciu, zručnosť alebo firmu — alebo si vyberte odbor, krajinu či úroveň.",
    countTitle: { one: "{n} ponuka", few: "{n} ponuky", other: "{n} ponúk" },
    countCapped: "{n}+ ponúk",
    showing: {
      one: "Zobrazená {shown} z {n} ponuky",
      few: "Zobrazené {shown} z {n} ponúk",
      other: "Zobrazených {shown} z {n} ponúk",
    },
    noneTitle: "Nič tomu nezodpovedá",
    noneBody: "Skúste všeobecnejší dotaz alebo zrušte niektorý filter.",
    filterCategory: "Odbor",
    filterCountry: "Krajina",
    filterCity: "Mesto",
    cityNeedsCountry: "Najprv vyberte krajinu",
    filterSeniority: "Úroveň",
    filterWorkMode: "Forma práce",
    remoteOnly: "Len na diaľku",
    clearFilters: "Zrušiť filtre",
    loadMore: "Načítať ďalšie",
    loadMoreFailed: "Ďalšie sa nepodarilo načítať — skontrolujte pripojenie a skúste to znova.",
    errTitle: "Ponuky sa nepodarilo načítať",
    errBody: "Na našej strane sa niečo pokazilo. Skúste to prosím o chvíľu.",
    categories: {
      machine_learning: "Strojové učenie",
      other_tech_function: "Ďalšie technické role",
    },
    ctaTitle: "Chcete ich radšej do e-mailu?",
    ctaBody:
      "Povedzte nám, čo hľadáte. Každé ráno prejdeme všetky nové ponuky a pošleme vám len tie, ktoré sedia — pri každej aj vetu, prečo prešla.",
    ctaButton: "Chcem denný prehľad",
  },

  checkInbox: {
    title: "Skontrolujte e-mail",
    body: "Poslali sme potvrdzovací odkaz na {0}. Kliknite naň a denný prehľad začne zajtra ráno o 7:00.",
    inMeantime: " Medzitým sú tu aktuálne ponuky zodpovedajúce vášmu hľadaniu:",
    doubleOptIn: "Dvojité potvrdenie · súhlas podľa GDPR",
    yourInbox: "vašu schránku",
    instantPreview: "Okamžitá ukážka · zhoda podľa kľúčových slov",
    instantNote:
      "Rýchla zhoda podľa kľúčových slov na úvod. Zajtrajší e-mail už {0} — každá ponuka obodovaná a s dôvodom, prečo vám sadne.",
    instantNoteEmphasis: "zoradí AI",
  },

  manage: {
    sentTitle: "Skontrolujte e-mail",
    sentBody:
      "Ak má {0} odber JobDigest, práve sme na túto adresu poslali súkromný odkaz na nastavenia. Otvorte ho a upravte si nastavenia, pozastavte odber alebo ho zrušte — bez hesla.",
    didntGet: "Neprišlo? Skontrolujte spam alebo to o pár minút skúste znova.",
    title: "Prihláste sa do JobDigest",
    body: "Žiadne heslá. Zadajte svoju adresu a pošleme vám bezpečný odkaz — kliknutím sa prihlásite a na tomto zariadení zostanete prihlásení.",
    googleNoSub:
      "Tento účet Google zatiaľ odber nemá. Najprv sa zaregistrujte, potom sa budete môcť prihlásiť cez Google.",
    googleSuppressed:
      "Táto adresa sa predtým odhlásila, takže ju nemôžeme automaticky znova prihlásiť. Ak sa chcete vrátiť, ozvite sa nám.",
    googleError:
      "Prihlásenie cez Google sa nedokončilo. Skúste to prosím znova, alebo použite odkaz v e-maile nižšie.",
    googleSignin: "Prihlásiť sa cez Google",
    emailLabel: "Vaša e-mailová adresa",
    sending: "Odosielame…",
    submit: "Poslať mi odkaz na nastavenia",
    onlyOwnInbox: "Odkaz vždy pošleme len do vašej vlastnej schránky.",
    error: "Niečo sa pokazilo. Skúste to prosím o chvíľu znova.",
  },

  root: {
    title: "Vyberte si jazyk",
    body: "Presmerúvame vás na JobDigest vo vašom jazyku…",
  },
};

export default sk;
