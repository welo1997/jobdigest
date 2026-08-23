import type { Messages } from "../schema";

/**
 * Polish. The one catalogue that needs all four plural categories: `one` (1), `few` (2–4),
 * `many` (5+ and 0) and `other` (fractions). Leaving `many` out would render "5 nowe oferty",
 * which is wrong in a way every Polish reader notices immediately.
 */
const pl: Messages = {
  meta: {
    title: "JobDigest — oferty pracy dopasowane do Ciebie, każdego ranka",
    description:
      "Jeden krótki e-mail dziennie z wyselekcjonowaną, uszeregowaną listą ofert dopasowanych do Ciebie. Tech oraz CZ+EU na pierwszym miejscu. Za darmo.",
  },

  nav: {
    myMatches: "Moje dopasowania",
    myPreferences: "Moje ustawienia",
    logOut: "Wyloguj się",
    logIn: "Zaloguj się",
    themeTitle: "Przełącz tryb jasny / ciemny",
    themeAria: "Przełącz motyw",
    languageAria: "Wybierz język",
  },

  footer: {
    madeInEu: "Zrobione w UE",
    manage: "Zarządzaj subskrypcją",
    privacy: "Prywatność",
    terms: "Regulamin",
  },

  common: {
    loading: "Wczytywanie…",
    add: "Dodaj",
    or: "lub",
    backToHome: "← Powrót na stronę główną",
    goHome: "Przejdź na stronę główną →",
    viewAndApply: "Zobacz i aplikuj →",
    roleFallback: "Stanowisko",
  },

  roles: {
    cybersecurity: "Cyberbezpieczeństwo",
    science_research: "Nauka i badania",
    social_care: "Praca socjalna",
    program_management: "Zarządzanie programami",
    partnerships: "Partnerstwa",
    strategy: "Strategia",
    product_manager: "Product Manager",
    marketing: "Marketing",
    social_media: "Social media",
    data_analyst: "Analityk danych",
    designer: "Projektant",
    software_engineer: "Programista",
    data_engineer: "Inżynier danych",
    devops: "DevOps",
    finance: "Finanse",
    ml_engineer: "Inżynier ML",
    sales: "Sprzedaż",
    hr: "HR i rekrutacja",
    legal: "Prawo",
    customer_support: "Obsługa klienta",
    operations: "Operacje",
    healthcare: "Opieka zdrowotna",
    education: "Edukacja",
    hospitality: "Gastronomia",
    skilled_trades: "Rzemiosło",
    construction: "Budownictwo",
    logistics: "Logistyka",
    manufacturing: "Produkcja",
    engineering: "Inżynieria",
  },

  workTypes: {
    fulltime: "Pełny etat",
    freelance: "Freelance",
    parttime: "Część etatu",
  },

  seniorities: {
    intern: "Staż",
    entry_level: "Absolwent",
    junior: "Junior",
    mid: "Mid",
    senior: "Senior",
    lead: "Kierownictwo",
  },

  geo: {
    countries: {
      AT: "Austria", BE: "Belgia", BG: "Bułgaria", HR: "Chorwacja", CY: "Cypr",
      CZ: "Czechy", DK: "Dania", EE: "Estonia", FI: "Finlandia", FR: "Francja",
      DE: "Niemcy", GR: "Grecja", HU: "Węgry", IE: "Irlandia", IT: "Włochy",
      LV: "Łotwa", LT: "Litwa", LU: "Luksemburg", MT: "Malta", NL: "Holandia",
      PL: "Polska", PT: "Portugalia", RO: "Rumunia", SK: "Słowacja", SI: "Słowenia",
      ES: "Hiszpania", SE: "Szwecja",
      CH: "Szwajcaria", IS: "Islandia", LI: "Liechtenstein", NO: "Norwegia",
      GB: "Wielka Brytania", US: "Stany Zjednoczone",
      CA: "Kanada",
    },
    remoteScope: {
      country: "Tylko w wybranych przeze mnie krajach",
      eu: "Gdziekolwiek w UE lub EOG",
      worldwide: "Gdziekolwiek na świecie",
    },
    workModeLabel: {
      onsite: "Stacjonarnie",
      hybrid: "Hybrydowo",
      remote: "W pełni zdalnie",
    },
    workModeHint: {
      onsite: "W biurze",
      hybrid: "Część w biurze, część z domu — nadal musisz być w pobliżu",
      remote: "Żadnego biura",
    },
  },

  educationLevels: {
    secondary: "Szkoła średnia",
    vocational: "Wykształcenie zawodowe",
    bachelor: "Licencjat / inżynier",
    master: "Magister",
    doctorate: "Doktorat / PhD",
  },

  education: {
    label: "Wykształcenie, którego może wymagać ogłoszenie",
    anyLevel: "cokolwiek",
    narrowed: "resztę pominiemy",
    note:
      "Większość ogłoszeń w ogóle nie podaje wymagań — wszystkie je zachowujemy, a opis czyta zamiast tego matcher. Pomijamy tylko te nieliczne, które wprost wymagają dyplomu, którego nie masz.",
    fieldLabel: "Co studiowałeś/aś (opcjonalnie)",
    fieldPlaceholder: "Ekonomia, Informatyka…",
    fieldHint:
      "Nigdy nie jest używane jako filtr — pomaga tylko matcherowi ocenić, czy rola pasuje do twojego doświadczenia.",
  },

  language: {
    label: "Języki, które rozumiesz",
    any: "dowolny język",
    narrowed: "tylko te",
    note:
      "Angielski zawsze zostaje zachowany, podobnie jak ogłoszenia, których języka nie udało się rozpoznać. Pomijamy tylko ogłoszenia napisane w całości w języku, którego nie wybrałeś.",
  },

  location: {
    countriesLabel: "Kraje, w których możesz pracować",
    addCountryAria: "Dodaj kolejny kraj",
    addCountryOption: "Dodaj kolejny kraj…",
    citiesIn: "Miasta — {country}",
    anyCityNote: "dowolne miasto (oferty stacjonarne w całym kraju)",
    pickedCityNote: "oferty stacjonarne tylko w wybranych miastach",
    anyCity: "Dowolne miasto",
    addTownPlaceholder: "Dodaj kolejną miejscowość — {country}…",
    addCityAria: "Dodaj miasto — {country}",
    workSetup: "Forma pracy",
    anythingGoes: "wszystko pasuje",
    leaveOutRest: "resztę pominiemy",
    hybridNote:
      "Hybryda oznacza część tygodnia w biurze, więc nadal musi to być miejsce, do którego dojedziesz. Wiele ogłoszeń w ogóle tego nie podaje — te zostawiamy i pozwalamy, by matcher przeczytał opis, zamiast zgadywać.",
    remoteLabel: "Oferty w pełni zdalne — jak daleko?",
    remoteDisabledNote: "Działa dopiero po wybraniu powyżej „W pełni zdalnie”.",
  },

  landing: {
    heroEyebrow: "Za darmo · Bez konta · Stworzone w UE",
    heroTitle: "Oferty warte Twojego poranka — i nic poza tym.",
    heroSubtitle:
      "Każdej nocy JobDigest czyta każde nowe ogłoszenie z ponad 20 źródeł i krajowych rejestrów, porównuje je z tym, czego naprawdę szukasz, i rano wysyła Ci krótką listę — przy każdej ofercie jedna linijka, dlaczego pasuje.",
    wizardAria: "Zbuduj swój przegląd",
    stepOf: "Krok {n} z {total}",
    q1: "Zacznij szukać od razu",
    cvReading: "Czytamy Twoje CV…",
    cvDrop: "Upuść tu CV — wypełnimy to za Ciebie",
    cvHint:
      "PDF lub DOCX · Czytamy je, żeby ustawić Twoje dopasowania, a potem usuwamy plik. Nigdy go nie udostępniamy.",
    cvChoose: "Wybierz plik",
    cvDone: "CV wczytane — uzupełnione",
    cvRemove: "Usuń CV",
    orPickManually: "albo wybierz ręcznie",
    addRolePlaceholder: "Dodaj kolejne stanowisko…",
    addRoleAria: "Dodaj stanowisko",
    q2: "W czym jesteś dobry?",
    q2hint: "Na tej podstawie dobieramy oferty. Zaznacz wszystko, co pasuje.",
    addSkillPlaceholder: "Dodaj umiejętność…",
    addSkillAria: "Dodaj umiejętność",
    q3: "Gdzie i jak?",
    q3hint:
      "Najpierw lokalizacja — wybierz miasta, do których realnie dojedziesz. Oferty stacjonarne gdziekolwiek indziej odpadają; w pełni zdalne nie.",
    workTypeHint: "Rodzaj pracy — zaznacz wszystko, co pasuje.",
    levelHint: "Twój poziom — wysyłamy tylko oferty na wybranych przez Ciebie poziomach.",
    narrowWarning:
      "To bardzo wąskie wyszukiwanie — dopasowań może być niewiele. Dodaj poziom, stanowisko albo kolejne miasto.",
    q4google: "Potwierdź swój przegląd",
    googleHint:
      "Rejestrujesz się jako {0} — zweryfikowane przez Google, więc nie wysyłamy e-maila potwierdzającego. Pierwszy przegląd dotrze jutro o 7:00.",
    consent: "Zgadzam się na {0} i na codzienny przegląd.",
    consentLink: "politykę prywatności",
    q4: "Gdzie mamy to wysyłać?",
    q4hint: "Najpierw jeden e-mail potwierdzający — potem codzienny przegląd o 7:00.",
    googleSignup: "Zarejestruj się przez Google",
    emailPlaceholder: "ty@example.com",
    emailAria: "Twój e-mail",
    turnstileNote: "Chronione przez Cloudflare Turnstile — bez CAPTCHA",
    back: "← Wstecz",
    next: "Dalej →",
    sending: "Wysyłanie…",
    submit: "Uruchom mój przegląd →",
    livePreview: "Podgląd na żywo",
    trustEmail: "Jeden e-mail dziennie",
    trustUnsub: "Wypisanie jednym kliknięciem",
    trustFree: "Za darmo · 20+ źródeł przeszukiwanych każdej nocy",
    howItWorks: "Jak to działa",
    step1Title: "Wyklikaj swój profil",
    step1Body: "Stanowiska, umiejętności, gdzie możesz pracować. Dwadzieścia sekund, głównie klikania.",
    step2Title: "Dopasowujemy w nocy",
    step2Body: "Świeże ogłoszenia z 20+ źródeł, uszeregowane pod Ciebie, duplikaty usunięte.",
    step3Title: "Czytasz jeden e-mail",
    step3Body: "Krótka uszeregowana lista — przy każdej ofercie powód i link do aplikowania.",
    mcta: "Chcę swój przegląd",
    mctaAria: "Przejdź do rejestracji",
    toast: {
      googleExpired: "Logowanie przez Google wygasło — spróbuj ponownie.",
      cvWrongType: "Prześlij plik PDF lub DOCX",
      cvTooLarge: "Ten plik jest za duży (maks. 8 MB)",
      cvOk: "CV wczytane — uzupełniliśmy Twój profil",
      cvUnreadable: "Nie udało się odczytać tego pliku",
      needConsent: "Najpierw zaakceptuj politykę prywatności",
      needLevel: "Wybierz co najmniej jeden poziom",
      needEmail: "Podaj prawidłowy e-mail",
      needTurnstile: "Ukończ weryfikację",
      needRole: "Wybierz co najmniej jedno stanowisko, aby kontynuować",
      needCountry: "Wybierz co najmniej jeden kraj, aby kontynuować",
      needLevelToContinue: "Wybierz co najmniej jeden poziom, aby kontynuować",
      genericError: "Coś poszło nie tak — spróbuj ponownie",
    },
  },

  preview: {
    inbox: "skrzynka — {email}",
    time: "7:00",
    subject: "{count} dla Ciebie — {date}",
    roleCount: {
      one: "{n} nowa oferta",
      few: "{n} nowe oferty",
      many: "{n} nowych ofert",
      other: "{n} nowej oferty",
    },
    roleCountNamed: {
      one: "{n} nowa oferta: {role}",
      few: "{n} nowe oferty: {role}",
      many: "{n} nowych ofert: {role}",
      other: "{n} nowej oferty: {role}",
    },
    pickRole: "Wybierz stanowisko, aby zobaczyć swoje dopasowania →",
    noCombo: "Brak dopasowań dla tej kombinacji — poszerz poziomy lub rodzaj pracy →",
    greeting: "Dzień dobry. Dziś {0} — z 214 ogłoszeń przejrzanych w nocy.",
    freshMatches: {
      one: "{n} nowe dopasowanie",
      few: "{n} nowe dopasowania",
      many: "{n} nowych dopasowań",
      other: "{n} nowego dopasowania",
    },
    reasonMatches: "Pasuje do: {0}. Branża: {sector}.",
    reasonGeneric: "Mocna oferta w branży {sector} w Twoim regionie. Branża: {sector}.",
    refine: "Zmień ustawienia",
    pause: "Wstrzymaj na 2 tygodnie",
    unsubscribe: "Wypisz się",
    fine: "Dostajesz to, bo zapisałeś się na jobdigest.eu · Jeden e-mail dziennie.",
  },

  prefs: {
    signedInLabel: "Zalogowano",
    title: "Twoje ustawienia",
    signedInAs: "Zalogowano jako {0}. Zmień co chcesz, wstrzymaj albo odejdź — {1}.",
    logOutInline: "wyloguj się",
    noPasswordLead: "Bez hasła.",
    noPasswordBody:
      "Kliknięcie linku w e-mailu zalogowało Cię i utrzymuje zalogowanie na tym urządzeniu, więc link nie będzie tu już potrzebny. Wylogować się możesz w każdej chwili.",
    roles: "Stanowiska",
    skills: "Umiejętności / słowa kluczowe",
    addRolePlaceholder: "Dodaj stanowisko…",
    addSkillPlaceholder: "Dodaj umiejętność…",
    addRoleAria: "Dodaj stanowisko",
    addSkillAria: "Dodaj umiejętność",
    sectors: "Branże, które Cię interesują (opcjonalne)",
    sectorsHint:
      "Obszary, w których najbardziej chciałbyś pracować — e-commerce, finanse, gry… Używamy tego do oceny dopasowania, nigdy jako sztywny filtr.",
    addSectorPlaceholder: "Dodaj branżę…",
    addSectorAria: "Dodaj branżę",
    seniorityLabel: "Poziom — wysyłamy tylko oferty na wybranych przez Ciebie poziomach",
    uploadCv: "Wgraj CV",
    cvReplaceConfirm:
      "Twoje obecne wybory (role, umiejętności, branże, poziom, lata doświadczenia, "
      + "wykształcenie) zostaną zastąpione tym, co wykryjemy w CV. Nic nie zostanie zapisane, "
      + "dopóki nie klikniesz Zapisz — najpierw możesz wszystko sprawdzić i poprawić. Plik "
      + "odczytujemy raz i usuwamy. Kontynuować?",
    yearsExperience: "Lata doświadczenia",
    yearsExperienceHint:
      "Pomijamy oferty wymagające więcej lat doświadczenia, niż masz. Oferty, które tego "
      + "nie podają, nadal przychodzą. Zostaw puste, jeśli bez preferencji.",
    minScore: "Próg wyróżnień",
    minScoreHint:
      "Jak silne musi być dopasowanie (na 10), aby trafić na czoło Twojego e-maila. Słabsze "
      + "dopasowania nadal pojawiają się na stronie dopasowań, a codzienny wybór otrzymasz "
      + "nawet wtedy, gdy nic nie przekroczy progu.",
    frequency: "Częstotliwość",
    freqDaily: "Codziennie",
    freqWeekdays: "Tylko w dni robocze",
    freqWeekly: "Co tydzień (w poniedziałki)",
    pausedTitle: "Przegląd wstrzymany",
    pauseTitle: "Wstrzymaj przegląd",
    pausedUntil: "Wstrzymane do {date} — wznowisz, kiedy zechcesz.",
    pausedUntilSoon: "wkrótce",
    pauseBody: "Zrób przerwę bez wypisywania się — wróci, kiedy będziesz gotowy.",
    resumeNow: "Wznów teraz",
    pauseTwoWeeks: "Wstrzymaj na 2 tygodnie",
    saving: "Zapisywanie…",
    save: "Zapisz zmiany",
    unsubscribeAll: "Wypisz się ze wszystkich e-maili",
    unsubscribeConfirm: "Kliknij ponownie, aby potwierdzić — wypisanie ze wszystkich e-maili",
    errTitle: "Nie można otworzyć Twoich ustawień",
    errNoLink:
      "Otwórz ustawienia z linku w swoim e-mailu — albo przez „Zarządzaj subskrypcją” poproś o nowy.",
    errUnknownLink: "Nieznany lub wygasły link.",
    toast: {
      saved: "Ustawienia zapisane",
      saveFailed: "Nie udało się zapisać — spróbuj ponownie",
      paused: "Przegląd wstrzymany na 2 tygodnie",
      pauseFailed: "Nie udało się wstrzymać",
      resumed: "Przegląd wznowiony",
      resumeFailed: "Nie udało się wznowić",
      unsubscribed: "Wypisano Cię",
      unsubscribeFailed: "Nie udało się wypisać",
    },
  },

  matches: {
    label: "Twoje dopasowania · zalogowano",
    countTitle: {
      one: "{n} oferta dla Ciebie",
      few: "{n} oferty dla Ciebie",
      many: "{n} ofert dla Ciebie",
      other: "{n} oferty dla Ciebie",
    },
    noneTitle: "Brak dopasowań",
    intro:
      "Wszystko, co znaleźliśmy dla {0}, od najlepszych. Najmocniejsze wysyłamy Ci e-mailem każdego ranka — to jest pełna lista.",
    nothingTitle: "Jeszcze nic",
    nothingBody:
      "Pierwsze dopasowania znajdujemy w nocy — zajrzyj tu po jutrzejszym przeglądzie o 7:00.",
    adjustPrefs: "Zmień ustawienia →",
    notQuiteRight: "Nie do końca to? Popraw stanowiska i umiejętności →",
    // Po przyimku „z” liczony rzeczownik stoi w dopełniaczu: 1 → oferty, 2+ → ofert.
    showing: {
      one: "Pokazano {shown} z {n} oferty",
      few: "Pokazano {shown} z {n} ofert",
      many: "Pokazano {shown} z {n} ofert",
      other: "Pokazano {shown} z {n} ofert",
    },
    loadMore: "Wczytaj więcej",
    loadMoreFailed: "Nie udało się wczytać więcej — sprawdź połączenie i spróbuj ponownie.",
    errTitle: "Nie można otworzyć Twoich dopasowań",
    errNoLink:
      "Otwórz dopasowania z linku w swoim e-mailu — albo przez „Zarządzaj subskrypcją” poproś o nowy.",
    errUnknownLink: "Nieznany lub wygasły link.",
    topMatch: "Najlepsze dopasowanie",
    tagHybrid: "Hybrydowo",
    tagRemote: "Zdalnie",
    tagFreelance: "Freelance",
    // Bez form rodzajowych („ukryłeś/ukryłaś”): zgłoszenie wysłane, nie „wysłałeś”.
    hideHint:
      "Masz już wysłane zgłoszenie albo oferta Cię nie interesuje? Zaznacz ją i ukryj — zniknie z tej strony i z codziennego e-maila.",
    selectAll: "Zaznacz wszystkie widoczne",
    clearSelection: "Wyczyść",
    selectAria: "Zaznacz",
    selected: {
      one: "{n} zaznaczona oferta",
      few: "{n} zaznaczone oferty",
      many: "{n} zaznaczonych ofert",
      other: "{n} zaznaczonej oferty",
    },
    hideSelected: "Ukryj zaznaczone",
    hiding: "Ukrywam…",
    hideFailed: "Nie udało się ukryć tych ofert — spróbuj ponownie.",
    hiddenLink: {
      one: "{n} ukryta oferta →",
      few: "{n} ukryte oferty →",
      many: "{n} ukrytych ofert →",
      other: "{n} ukrytej oferty →",
    },
    filterBySkill: "Filtruj według umiejętności",
    filterByWorkMode: "Filtruj według formy pracy",
    searchLabel: "Szukaj wśród swoich ofert",
    searchPlaceholder: "Stanowisko, umiejętność lub firma",
    greatFitsOnly: "Tylko najlepsze oferty",
    scoreFilterAria: "Filtruj według minimalnego wyniku",
    scoreAny: "Dowolny wynik",
    scoredOn: "Oceniono {date}",
    filterByExperience: "Doświadczenie",
    maxExpAny: "Dowolne doświadczenie",
    maxExpOption: {
      one: "Wymagany maks. {n} rok",
      few: "Wymagane maks. {n} lata",
      many: "Wymagane maks. {n} lat",
      other: "Wymagane maks. {n} roku",
    },
    tagExperience: {
      one: "{n}+ rok",
      few: "{n}+ lata",
      many: "{n}+ lat",
      other: "{n}+ roku",
    },
    clearFilter: "Wyczyść",
    refreshNow: "Odśwież dopasowania",
    refreshBusy: "Odświeżanie…",
    refreshSent: {
      one: "Wysłano {n} nową ofertę na Twoją skrzynkę.",
      few: "Wysłano {n} nowe oferty na Twoją skrzynkę.",
      many: "Wysłano {n} nowych ofert na Twoją skrzynkę.",
      other: "Wysłano {n} nowych ofert na Twoją skrzynkę.",
    },
    refreshNone: "Na razie brak nowych dopasowań — zajrzyj później.",
    refreshFailed: "Nie udało się odświeżyć — spróbuj za chwilę.",
  },

  hidden: {
    label: "Ukryte oferty · zalogowano",
    countTitle: {
      one: "{n} ukryta oferta",
      few: "{n} ukryte oferty",
      many: "{n} ukrytych ofert",
      other: "{n} ukrytej oferty",
    },
    noneTitle: "Brak ukrytych ofert",
    intro:
      "Oferty ukryte przez Ciebie. Nie pojawiają się wśród Twoich dopasowań ani w codziennym e-mailu — w każdej chwili możesz je przywrócić.",
    nothingTitle: "Nic nie jest ukryte",
    nothingBody: "Ukryj ofertę wśród swoich dopasowań, a trafi tutaj. Nic nie jest usuwane.",
    backToMatches: "← Wróć do swoich dopasowań",
    unhideSelected: "Przywróć zaznaczone",
    unhiding: "Przywracam…",
    unhideFailed: "Nie udało się przywrócić tych ofert — spróbuj ponownie.",
    // Po przyimku „z” liczony rzeczownik stoi w dopełniaczu: 1 → oferty, 2+ → ofert.
    showing: {
      one: "Pokazano {shown} z {n} ukrytej oferty",
      few: "Pokazano {shown} z {n} ukrytych ofert",
      many: "Pokazano {shown} z {n} ukrytych ofert",
      other: "Pokazano {shown} z {n} ukrytych ofert",
    },
  },

  jobs: {
    navLink: "Przeglądaj oferty",
    metaTitle: "Oferty pracy w Europie · JobDigest",
    metaDescription:
      "Przeszukaj wszystkie oferty, które śledzimy — państwowe rejestry wolnych miejsc pracy i setki firmowych stron karier w jednym miejscu. Za darmo, bez konta.",
    label: "Za darmo · bez konta",
    title: "Wszystkie oferty, które śledzimy, w jednym miejscu",
    intro:
      "Państwowe rejestry wolnych miejsc pracy i setki firmowych stron karier, przeszukiwane razem. Odświeżamy co rano i nigdy nie pokazujemy ogłoszenia, którego nie widzieliśmy od tygodnia.",
    searchLabel: "Szukaj ofert",
    searchPlaceholder: "Stanowisko, umiejętność lub firma",
    searchButton: "Szukaj",
    startTitle: "Czego szukasz?",
    startBody:
      "Szukaj po stanowisku, umiejętności lub firmie — albo wybierz branżę, kraj lub poziom.",
    countTitle: {
      one: "{n} oferta",
      few: "{n} oferty",
      many: "{n} ofert",
      other: "{n} oferty",
    },
    countCapped: "{n}+ ofert",
    showing: {
      one: "Pokazano {shown} z {n} oferty",
      few: "Pokazano {shown} z {n} ofert",
      many: "Pokazano {shown} z {n} ofert",
      other: "Pokazano {shown} z {n} oferty",
    },
    noneTitle: "Nic nie pasuje",
    noneBody: "Spróbuj szerszego zapytania albo usuń jeden z filtrów.",
    filterCategory: "Dziedzina",
    filterCountry: "Kraj",
    filterCity: "Miasto",
    cityNeedsCountry: "Najpierw wybierz kraj",
    filterSeniority: "Poziom",
    filterWorkMode: "Tryb pracy",
    intlEea: "UE – międzynarodowe",
    intlNa: "Ameryka Północna – międzynarodowe",
    clearFilters: "Wyczyść filtry",
    loadMore: "Pokaż więcej",
    loadMoreFailed: "Nie udało się wczytać więcej — sprawdź połączenie i spróbuj ponownie.",
    errTitle: "Nie udało się wczytać ofert",
    errBody: "Coś poszło nie tak po naszej stronie. Spróbuj za chwilę.",
    categories: {
      machine_learning: "Uczenie maszynowe",
      other_tech_function: "Inne role techniczne",
    },
    ctaTitle: "Wolisz dostawać je e-mailem?",
    ctaBody:
      "Powiedz nam, czego szukasz. Każdego ranka przeczytamy wszystkie nowe oferty i wyślemy tylko te pasujące — z jednym zdaniem, dlaczego każda przeszła.",
    ctaButton: "Chcę codzienny przegląd",
  },

  checkInbox: {
    title: "Sprawdź skrzynkę",
    body: "Wysłaliśmy link potwierdzający na {0}. Kliknij go, a codzienny przegląd ruszy jutro rano o 7:00.",
    inMeantime: " W międzyczasie — aktualne oferty pasujące do Twojego wyszukiwania:",
    doubleOptIn: "Podwójna zgoda · RODO",
    yourInbox: "Twoją skrzynkę",
    instantPreview: "Natychmiastowy podgląd · dopasowanie po słowach kluczowych",
    instantNote:
      "Szybkie dopasowanie po słowach kluczowych na start. Jutrzejszy e-mail będzie już {0} — każda oferta oceniona, z powodem, dlaczego pasuje.",
    instantNoteEmphasis: "uszeregowany przez AI",
  },

  manage: {
    sentTitle: "Sprawdź skrzynkę",
    sentBody:
      "Jeśli {0} ma subskrypcję JobDigest, właśnie wysłaliśmy tam prywatny link do ustawień. Otwórz go, aby zmienić ustawienia, wstrzymać lub wypisać się — bez hasła.",
    didntGet: "Nie dotarł? Sprawdź spam albo spróbuj ponownie za kilka minut.",
    title: "Zaloguj się do JobDigest",
    body: "Żadnych haseł. Podaj swój adres, a wyślemy Ci bezpieczny link — kliknięcie loguje Cię i zostaniesz zalogowany na tym urządzeniu.",
    googleNoSub:
      "To konto Google nie ma jeszcze subskrypcji. Najpierw się zarejestruj, potem zalogujesz się przez Google.",
    googleSuppressed:
      "Ten adres wcześniej się wypisał, więc nie możemy go automatycznie zapisać ponownie. Napisz do nas, jeśli chcesz wrócić.",
    googleError:
      "Logowanie przez Google nie zostało ukończone. Spróbuj ponownie albo użyj linku e-mailowego poniżej.",
    googleSignin: "Zaloguj się przez Google",
    emailLabel: "Twój adres e-mail",
    sending: "Wysyłanie…",
    submit: "Wyślij mi link do ustawień",
    onlyOwnInbox: "Link wyślemy zawsze tylko na Twoją własną skrzynkę.",
    error: "Coś poszło nie tak. Spróbuj ponownie za chwilę.",
  },

  root: {
    title: "Wybierz język",
    body: "Przenosimy Cię do JobDigest w Twoim języku…",
  },
};

export default pl;
