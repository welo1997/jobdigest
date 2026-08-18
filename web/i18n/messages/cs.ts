import type { Messages } from "../schema";

/**
 * Czech. The home market — most of the live inventory and every current subscriber.
 *
 * Two things worth knowing if you edit this file. Plurals carry `one` / `few` / `other`
 * because Czech splits 2–4 from 5+ ("2 nabídky", "5 nabídek"); dropping `few` reads as broken
 * Czech to every reader. And `roleCountNamed` puts the role after a colon rather than inside
 * the noun phrase, which sidesteps declining an English job title in a Czech sentence.
 */
const cs: Messages = {
  meta: {
    title: "JobDigest — nabídky práce, které vám sedí, každé ráno",
    description:
      "Jeden krátký e-mail denně s vybraným a seřazeným přehledem nabídek, které vám sedí. Tech a CZ+EU na prvním místě. Zdarma.",
  },

  nav: {
    myMatches: "Moje nabídky",
    myPreferences: "Moje nastavení",
    logOut: "Odhlásit se",
    logIn: "Přihlásit se",
    themeTitle: "Přepnout světlý / tmavý režim",
    themeAria: "Přepnout motiv",
    languageAria: "Vybrat jazyk",
  },

  footer: {
    madeInEu: "Vyrobeno v EU",
    manage: "Správa odběru",
    privacy: "Soukromí",
    terms: "Podmínky",
  },

  common: {
    loading: "Načítání…",
    add: "Přidat",
    or: "nebo",
    backToHome: "← Zpět na úvod",
    goHome: "Přejít na úvodní stránku →",
    viewAndApply: "Zobrazit a odpovědět →",
    roleFallback: "Pozice",
  },

  roles: {
    cybersecurity: "Kybernetická bezpečnost",
    science_research: "Věda a výzkum",
    social_care: "Sociální práce",
    product_manager: "Produktový manažer",
    marketing: "Marketing",
    social_media: "Sociální sítě",
    data_analyst: "Datový analytik",
    designer: "Designér",
    software_engineer: "Vývojář softwaru",
    data_engineer: "Datový inženýr",
    devops: "DevOps",
    finance: "Finance",
    ml_engineer: "ML inženýr",
    sales: "Obchod",
    hr: "HR a nábor",
    legal: "Právo",
    customer_support: "Zákaznická podpora",
    operations: "Provoz",
    healthcare: "Zdravotnictví",
    education: "Školství",
    hospitality: "Pohostinství",
    skilled_trades: "Řemesla",
    construction: "Stavebnictví",
    logistics: "Logistika",
    manufacturing: "Výroba",
    engineering: "Inženýrství",
  },

  workTypes: {
    fulltime: "Plný úvazek",
    freelance: "Freelance",
    parttime: "Částečný úvazek",
  },

  seniorities: {
    intern: "Stáž",
    entry_level: "Absolvent",
    junior: "Junior",
    mid: "Medior",
    senior: "Senior",
    lead: "Vedoucí / management",
  },

  geo: {
    countries: {
      AT: "Rakousko", BE: "Belgie", BG: "Bulharsko", HR: "Chorvatsko", CY: "Kypr",
      CZ: "Česko", DK: "Dánsko", EE: "Estonsko", FI: "Finsko", FR: "Francie",
      DE: "Německo", GR: "Řecko", HU: "Maďarsko", IE: "Irsko", IT: "Itálie",
      LV: "Lotyšsko", LT: "Litva", LU: "Lucembursko", MT: "Malta", NL: "Nizozemsko",
      PL: "Polsko", PT: "Portugalsko", RO: "Rumunsko", SK: "Slovensko", SI: "Slovinsko",
      ES: "Španělsko", SE: "Švédsko",
      CH: "Švýcarsko", IS: "Island", LI: "Lichtenštejnsko", NO: "Norsko",
      GB: "Spojené království", US: "Spojené státy",
      CA: "Kanada",
    },
    remoteScope: {
      country: "Jen v zemích, které jsem vybral(a)",
      eu: "Kdekoli v EU nebo EHP",
      worldwide: "Kdekoli na světě",
    },
    workModeLabel: {
      onsite: "Z kanceláře",
      hybrid: "Hybridně",
      remote: "Plně na dálku",
    },
    workModeHint: {
      onsite: "V kanceláři",
      hybrid: "Část v kanceláři, část z domova — pořád musíte být poblíž",
      remote: "Žádná kancelář",
    },
  },

  educationLevels: {
    secondary: "Střední škola",
    vocational: "Vyučen/a v oboru",
    bachelor: "Bakalář",
    master: "Magistr / Ing.",
    doctorate: "Doktorát / Ph.D.",
  },

  education: {
    label: "Vzdělání, které může inzerát požadovat",
    anyLevel: "cokoli",
    narrowed: "ostatní vynecháme",
    note:
      "Většina inzerátů žádné vzdělání neuvádí — ty všechny ponecháváme a popis místo toho přečte matcher. Vynecháme jen těch pár, které výslovně žádají titul, jaký nemáte.",
    fieldLabel: "Co jste studovali (nepovinné)",
    fieldPlaceholder: "Ekonomie, Informatika…",
    fieldHint:
      "Nikdy se nepoužívá jako filtr — jen pomáhá matcheru posoudit, jestli k vám role sedí.",
  },

  location: {
    countriesLabel: "Země, kde můžete pracovat",
    addCountryAria: "Přidat další zemi",
    addCountryOption: "Přidat další zemi…",
    citiesIn: "Města — {country}",
    anyCityNote: "kdekoli v zemi (nabídky z kanceláře kdekoli)",
    pickedCityNote: "nabídky z kanceláře jen ve vybraných městech",
    anyCity: "Kdekoli",
    addTownPlaceholder: "Přidat další město — {country}…",
    addCityAria: "Přidat město — {country}",
    workSetup: "Forma práce",
    anythingGoes: "cokoli",
    leaveOutRest: "zbytek vynecháme",
    hybridNote:
      "Hybridní práce znamená část týdne v kanceláři, takže to pořád musí být někam, kam se dostanete. Spousta inzerátů to neuvádí vůbec — ty necháváme a formu práce si přečte matcher přímo z popisu, místo aby hádal.",
    remoteLabel: "Plně vzdálené nabídky — jak daleko?",
    remoteDisabledNote: "Platí, až když výše vyberete „Plně na dálku“.",
  },

  landing: {
    wizardAria: "Sestavte si přehled",
    stepOf: "Krok {n} ze {total}",
    q1: "Začněte hledat hned",
    cvReading: "Čteme váš životopis…",
    cvDrop: "Přetáhněte sem životopis — vyplníme to za vás",
    cvHint:
      "PDF nebo DOCX · Přečteme ho, abychom vám nastavili nabídky, a pak soubor smažeme. Nikdy ho nesdílíme.",
    cvChoose: "Vybrat soubor",
    cvDone: "Životopis načten — předvyplněno",
    cvRemove: "Odebrat životopis",
    orPickManually: "nebo vyberte ručně",
    addRolePlaceholder: "Přidat další pozici…",
    addRoleAria: "Přidat pozici",
    q2: "V čem jste dobří?",
    q2hint: "Podle toho hledáme nabídky. Vyberte vše, co sedí.",
    addSkillPlaceholder: "Přidat dovednost…",
    addSkillAria: "Přidat dovednost",
    q3: "Kde a jak?",
    q3hint:
      "Nejdřív místo — vyberte města, kam byste reálně dojížděli. Nabídky z kanceláře kdekoli jinde vyřadíme; plně vzdálené ne.",
    workTypeHint: "Typ práce — vyberte vše, co sedí.",
    levelHint: "Vaše úroveň — posíláme jen nabídky na úrovních, které vyberete.",
    narrowWarning:
      "To je hodně úzké hledání — nabídek může přijít málo. Přidejte úroveň, pozici nebo další město.",
    q4google: "Potvrďte svůj přehled",
    googleHint:
      "Registrujete se jako {0} — ověřeno přes Google, takže žádný potvrzovací e-mail nepřijde. První přehled dorazí zítra v 7:00.",
    consent: "Souhlasím se {0} a se zasíláním denního přehledu.",
    consentLink: "zásadami ochrany soukromí",
    q4: "Kam vám to máme posílat?",
    q4hint: "Nejdřív jeden potvrzovací e-mail — pak denní přehled v 7:00.",
    googleSignup: "Registrovat se přes Google",
    emailPlaceholder: "vy@example.com",
    emailAria: "Váš e-mail",
    turnstileNote: "Chráněno službou Cloudflare Turnstile — žádná CAPTCHA",
    back: "← Zpět",
    next: "Další →",
    sending: "Odesíláme…",
    submit: "Spustit můj přehled →",
    livePreview: "Živá ukázka",
    trustEmail: "Jeden e-mail denně",
    trustUnsub: "Odhlášení jedním kliknutím",
    trustFree: "Zdarma · 20+ zdrojů prohledáváme každou noc",
    howItWorks: "Jak to funguje",
    step1Title: "Naklikejte si profil",
    step1Body: "Pozice, dovednosti, kde můžete pracovat. Dvacet vteřin, hlavně klikání.",
    step2Title: "Přes noc hledáme",
    step2Body: "Čerstvé inzeráty z 20+ zdrojů, seřazené pro vás, duplicity pryč.",
    step3Title: "Přečtete jeden e-mail",
    step3Body: "Krátký seřazený výběr — u každé nabídky důvod a odkaz, kde se přihlásit.",
    mcta: "Chci svůj přehled",
    mctaAria: "Přejít k registraci",
    toast: {
      googleExpired: "Přihlášení přes Google vypršelo — zkuste to prosím znovu.",
      cvWrongType: "Nahrajte prosím soubor PDF nebo DOCX",
      cvTooLarge: "Soubor je příliš velký (max. 8 MB)",
      cvOk: "Životopis načten — předvyplnili jsme váš profil",
      cvUnreadable: "Soubor se nepodařilo přečíst",
      needConsent: "Nejdřív prosím odsouhlaste zásady ochrany soukromí",
      needLevel: "Vyberte alespoň jednu úroveň",
      needEmail: "Zadejte platný e-mail",
      needTurnstile: "Dokončete prosím ověření",
      needRole: "Pokračujte výběrem alespoň jedné pozice",
      needCountry: "Pokračujte výběrem alespoň jedné země",
      needLevelToContinue: "Pokračujte výběrem alespoň jedné úrovně",
      genericError: "Něco se pokazilo — zkuste to prosím znovu",
    },
  },

  preview: {
    inbox: "doručená pošta — {email}",
    time: "7:00",
    subject: "{count} pro vás — {date}",
    roleCount: {
      one: "{n} nová nabídka",
      few: "{n} nové nabídky",
      other: "{n} nových nabídek",
    },
    roleCountNamed: {
      one: "{n} nová nabídka: {role}",
      few: "{n} nové nabídky: {role}",
      other: "{n} nových nabídek: {role}",
    },
    pickRole: "Vyberte pozici a uvidíte své nabídky →",
    noCombo: "Pro tuto kombinaci nic nemáme — rozšiřte úrovně nebo typ práce →",
    greeting: "Dobré ráno. Dnes {0} — z 214 inzerátů prohledaných přes noc.",
    freshMatches: {
      one: "{n} nová shoda",
      few: "{n} nové shody",
      other: "{n} nových shod",
    },
    reasonMatches: "Odpovídá: {0}. Obor: {sector}.",
    reasonGeneric: "Silná nabídka v oboru {sector} ve vašem regionu. Obor: {sector}.",
    refine: "Upravit nastavení",
    pause: "Pozastavit na 2 týdny",
    unsubscribe: "Odhlásit odběr",
    fine: "Tento e-mail dostáváte, protože jste se přihlásili na jobdigest.eu · Jeden e-mail denně.",
  },

  prefs: {
    signedInLabel: "Přihlášeni",
    title: "Vaše nastavení",
    signedInAs: "Přihlášeni jako {0}. Cokoli změňte, pozastavte nebo zrušte — {1}.",
    logOutInline: "odhlásit se",
    noPasswordLead: "Žádné heslo.",
    noPasswordBody:
      "Kliknutím na odkaz v e-mailu jste se přihlásili a na tomto zařízení zůstanete přihlášeni, takže odkaz už tu znovu potřebovat nebudete. Odhlásit se můžete kdykoli.",
    roles: "Pozice",
    skills: "Dovednosti / klíčová slova",
    addRolePlaceholder: "Přidat pozici…",
    addSkillPlaceholder: "Přidat dovednost…",
    addRoleAria: "Přidat pozici",
    addSkillAria: "Přidat dovednost",
    sectors: "Obory, které vás zajímají (nepovinné)",
    sectorsHint:
      "Odvětví, ve kterých byste nejraději pracovali — e-commerce, finance, hry… Slouží k posouzení vhodnosti, nikdy jako striktní filtr.",
    addSectorPlaceholder: "Přidat obor…",
    addSectorAria: "Přidat obor",
    seniorityLabel: "Úroveň — posíláme jen nabídky na úrovních, které vyberete",
    uploadCv: "Nahrát životopis",
    cvReplaceConfirm:
      "Nahrazením se vaše současné volby (role, dovednosti, obory, úroveň, roky praxe, "
      + "vzdělání) přepíšou tím, co v životopisu najdeme. Nic se neuloží, dokud nestisknete "
      + "Uložit — vše si nejdřív můžete zkontrolovat a upravit. Soubor přečteme jednou a "
      + "smažeme. Pokračovat?",
    yearsExperience: "Roky praxe",
    yearsExperienceHint:
      "Nabídky vyžadující více let praxe, než máte, vynecháme. Nabídky, které délku praxe "
      + "neuvádějí, chodí dál. Prázdné pole = bez preference.",
    frequency: "Frekvence",
    freqDaily: "Denně",
    freqWeekdays: "Jen ve všední dny",
    freqWeekly: "Týdně (v pondělí)",
    pausedTitle: "Přehled pozastaven",
    pauseTitle: "Pozastavit přehled",
    pausedUntil: "Pozastaveno do {date} — obnovit můžete kdykoli.",
    pausedUntilSoon: "brzy",
    pauseBody: "Dejte si pauzu bez rušení odběru — spustíme to, až budete chtít.",
    resumeNow: "Obnovit hned",
    pauseTwoWeeks: "Pozastavit na 2 týdny",
    saving: "Ukládáme…",
    save: "Uložit změny",
    unsubscribeAll: "Odhlásit se ze všech e-mailů",
    unsubscribeConfirm: "Potvrďte dalším kliknutím — odhlásit se ze všech e-mailů",
    errTitle: "Vaše nastavení nelze otevřít",
    errNoLink:
      "Otevřete nastavení z odkazu ve svém e-mailu — nebo si přes „Správa odběru“ nechte poslat nový.",
    errUnknownLink: "Neznámý nebo vypršelý odkaz.",
    toast: {
      saved: "Nastavení uloženo",
      saveFailed: "Uložení se nezdařilo — zkuste to prosím znovu",
      paused: "Přehled pozastaven na 2 týdny",
      pauseFailed: "Pozastavení se nezdařilo",
      resumed: "Přehled obnoven",
      resumeFailed: "Obnovení se nezdařilo",
      unsubscribed: "Odběr byl zrušen",
      unsubscribeFailed: "Zrušení odběru se nezdařilo",
    },
  },

  matches: {
    label: "Vaše nabídky · přihlášeni",
    countTitle: {
      one: "{n} nabídka pro vás",
      few: "{n} nabídky pro vás",
      other: "{n} nabídek pro vás",
    },
    noneTitle: "Zatím žádné nabídky",
    intro:
      "Vše, co jsme našli pro {0}, seřazeno od nejlepšího. Ty nejsilnější vám každé ráno pošleme e-mailem — tohle je celý seznam.",
    nothingTitle: "Zatím nic",
    nothingBody:
      "První nabídky hledáme přes noc — podívejte se sem po zítřejším přehledu v 7:00.",
    adjustPrefs: "Upravit nastavení →",
    notQuiteRight: "Není to ono? Upravte si pozice a dovednosti →",
    // Po předložce „z“ je počítaný výraz v genitivu: 1 → nabídky, 2+ → nabídek.
    showing: {
      one: "Zobrazeno {shown} z {n} nabídky",
      few: "Zobrazeno {shown} z {n} nabídek",
      other: "Zobrazeno {shown} z {n} nabídek",
    },
    loadMore: "Načíst další",
    loadMoreFailed: "Další se nepodařilo načíst — zkontrolujte připojení a zkuste to znovu.",
    errTitle: "Vaše nabídky nelze otevřít",
    errNoLink:
      "Otevřete nabídky z odkazu ve svém e-mailu — nebo si přes „Správa odběru“ nechte poslat nový.",
    errUnknownLink: "Neznámý nebo vypršelý odkaz.",
    topMatch: "Nejlepší shoda",
    tagHybrid: "Hybridně",
    tagRemote: "Na dálku",
    tagFreelance: "Freelance",
    hideHint:
      "Už jste se přihlásili, nebo vás nabídka nezajímá? Označte ji a skryjte — zmizí z této stránky i z denního e-mailu.",
    selectAll: "Označit vše zobrazené",
    clearSelection: "Zrušit výběr",
    selectAria: "Vybrat",
    selected: {
      one: "{n} vybraná nabídka",
      few: "{n} vybrané nabídky",
      other: "{n} vybraných nabídek",
    },
    hideSelected: "Skrýt vybrané",
    hiding: "Skrývám…",
    hideFailed: "Nabídky se nepodařilo skrýt — zkuste to znovu.",
    hiddenLink: {
      one: "{n} skrytá nabídka →",
      few: "{n} skryté nabídky →",
      other: "{n} skrytých nabídek →",
    },
    filterBySkill: "Filtrovat podle dovednosti",
    filterByWorkMode: "Filtrovat podle formy práce",
    searchLabel: "Hledat ve vašich nabídkách",
    searchPlaceholder: "Pozice, dovednost nebo firma",
    greatFitsOnly: "Jen nejlepší nabídky",
    filterByExperience: "Praxe",
    maxExpAny: "Bez omezení praxe",
    maxExpOption: {
      one: "Max. {n} rok praxe",
      few: "Max. {n} roky praxe",
      other: "Max. {n} let praxe",
    },
    tagExperience: {
      one: "{n}+ rok",
      few: "{n}+ roky",
      other: "{n}+ let",
    },
    clearFilter: "Zrušit",
    refreshNow: "Aktualizovat nabídky",
    refreshBusy: "Aktualizuji…",
    refreshSent: {
      one: "Do vaší schránky byla odeslána {n} nová nabídka.",
      few: "Do vaší schránky byly odeslány {n} nové nabídky.",
      other: "Do vaší schránky bylo odesláno {n} nových nabídek.",
    },
    refreshNone: "Zatím žádné nové nabídky — zkuste to později.",
    refreshFailed: "Aktualizace se teď nezdařila — zkuste to za chvíli.",
  },

  hidden: {
    label: "Skryté nabídky · přihlášeni",
    countTitle: {
      one: "{n} skrytá nabídka",
      few: "{n} skryté nabídky",
      other: "{n} skrytých nabídek",
    },
    noneTitle: "Žádné skryté nabídky",
    intro:
      "Nabídky, které jste skryli. Nezobrazují se mezi vašimi nabídkami ani v denním e-mailu — kdykoli je můžete zase zobrazit.",
    nothingTitle: "Nic skrytého",
    nothingBody: "Skryjte nabídku mezi svými nabídkami a objeví se tady. Nic se nemaže.",
    backToMatches: "← Zpět na vaše nabídky",
    unhideSelected: "Zobrazit vybrané",
    unhiding: "Zobrazuji…",
    unhideFailed: "Nabídky se nepodařilo zobrazit — zkuste to znovu.",
    // Po předložce „z“ je počítaný výraz v genitivu: 1 → nabídky, 2+ → nabídek.
    showing: {
      one: "Zobrazeno {shown} z {n} skryté nabídky",
      few: "Zobrazeno {shown} z {n} skrytých nabídek",
      other: "Zobrazeno {shown} z {n} skrytých nabídek",
    },
  },

  jobs: {
    navLink: "Procházet nabídky",
    metaTitle: "Nabídky práce v Evropě · JobDigest",
    metaDescription:
      "Prohledejte všechny nabídky, které sledujeme — státní registry volných míst i stovky firemních kariérních stránek na jednom místě. Zdarma a bez registrace.",
    label: "Zdarma · bez registrace",
    title: "Všechny nabídky, které sledujeme, na jednom místě",
    intro:
      "Státní registry volných míst a stovky firemních kariérních stránek, prohledatelné najednou. Aktualizujeme každé ráno a nikdy nezobrazíme nabídku, kterou jsme týden neviděli.",
    searchLabel: "Hledat nabídky",
    searchPlaceholder: "Pozice, dovednost nebo firma",
    searchButton: "Hledat",
    startTitle: "Co hledáte?",
    startBody:
      "Zadejte pozici, dovednost nebo firmu — nebo si vyberte obor, zemi či úroveň.",
    countTitle: { one: "{n} nabídka", few: "{n} nabídky", other: "{n} nabídek" },
    countCapped: "{n}+ nabídek",
    showing: {
      one: "Zobrazena {shown} z {n} nabídky",
      few: "Zobrazeno {shown} z {n} nabídek",
      other: "Zobrazeno {shown} z {n} nabídek",
    },
    noneTitle: "Nic tomu neodpovídá",
    noneBody: "Zkuste obecnější dotaz nebo zrušte některý filtr.",
    filterCategory: "Obor",
    filterCountry: "Země",
    filterCity: "Město",
    cityNeedsCountry: "Nejprve vyberte zemi",
    filterSeniority: "Úroveň",
    filterWorkMode: "Forma práce",
    intlEea: "EU – mezinárodní",
    intlNa: "Severní Amerika – mezinárodní",
    clearFilters: "Zrušit filtry",
    loadMore: "Načíst další",
    loadMoreFailed: "Další se nepodařilo načíst — zkontrolujte připojení a zkuste to znovu.",
    errTitle: "Nabídky se nepodařilo načíst",
    errBody: "Na naší straně se něco pokazilo. Zkuste to prosím za chvíli.",
    categories: {
      machine_learning: "Strojové učení",
      other_tech_function: "Další technické role",
    },
    ctaTitle: "Chcete je raději do e-mailu?",
    ctaBody:
      "Řekněte nám, co hledáte. Každé ráno projdeme všechny nové nabídky a pošleme vám jen ty, které sedí — u každé i větu, proč prošla.",
    ctaButton: "Chci denní přehled",
  },

  checkInbox: {
    title: "Zkontrolujte e-mail",
    body: "Poslali jsme potvrzovací odkaz na {0}. Klikněte na něj a denní přehled začne zítra ráno v 7:00.",
    inMeantime: " Než tak učiníte, tady jsou aktuální nabídky odpovídající vašemu hledání:",
    doubleOptIn: "Dvojité potvrzení · souhlas dle GDPR",
    yourInbox: "vaši schránku",
    instantPreview: "Okamžitá ukázka · shoda podle klíčových slov",
    instantNote:
      "Rychlá shoda podle klíčových slov na úvod. Zítřejší e-mail už {0} — každá nabídka obodovaná a s důvodem, proč vám sedí.",
    instantNoteEmphasis: "seřadí AI",
  },

  manage: {
    sentTitle: "Zkontrolujte e-mail",
    sentBody:
      "Pokud má {0} odběr JobDigest, právě jsme na tuto adresu poslali soukromý odkaz na nastavení. Otevřete ho a upravte si nastavení, pozastavte odběr nebo ho zrušte — bez hesla.",
    didntGet: "Nepřišlo? Zkontrolujte spam nebo to za pár minut zkuste znovu.",
    title: "Přihlaste se do JobDigest",
    body: "Žádná hesla. Zadejte svou adresu a pošleme vám bezpečný odkaz — kliknutím se přihlásíte a na tomto zařízení zůstanete přihlášeni.",
    googleNoSub:
      "Tento účet Google zatím odběr nemá. Nejdřív se zaregistrujte, pak se budete moci přihlásit přes Google.",
    googleSuppressed:
      "Tato adresa se dříve odhlásila, takže ji nemůžeme automaticky znovu přihlásit. Pokud se chcete vrátit, ozvěte se nám.",
    googleError:
      "Přihlášení přes Google se nedokončilo. Zkuste to prosím znovu, nebo použijte odkaz v e-mailu níže.",
    googleSignin: "Přihlásit se přes Google",
    emailLabel: "Vaše e-mailová adresa",
    sending: "Odesíláme…",
    submit: "Poslat mi odkaz na nastavení",
    onlyOwnInbox: "Odkaz vždy pošleme jen do vaší vlastní schránky.",
    error: "Něco se pokazilo. Zkuste to prosím za chvíli znovu.",
  },

  root: {
    title: "Vyberte si jazyk",
    body: "Přesměrováváme vás na JobDigest ve vašem jazyce…",
  },
};

export default cs;
