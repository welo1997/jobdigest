import type { Messages } from "../schema";

/** Spanish. Two plural forms, like English. */
const es: Messages = {
  meta: {
    title: "JobDigest — empleos que encajan contigo, cada mañana",
    description:
      "Un correo corto al día con una selección ordenada de empleos que encajan contigo. Tech y CZ+EU primero. Empezar es gratis.",
  },

  nav: {
    myMatches: "Mis coincidencias",
    myPreferences: "Mis preferencias",
    logOut: "Cerrar sesión",
    logIn: "Iniciar sesión",
    themeTitle: "Cambiar claro / oscuro",
    themeAria: "Cambiar tema",
    languageAria: "Elegir idioma",
  },

  footer: {
    madeInEu: "Hecho en la UE",
    manage: "Gestionar suscripción",
    privacy: "Privacidad",
    terms: "Términos",
  },

  common: {
    loading: "Cargando…",
    add: "Añadir",
    or: "o",
    backToHome: "← Volver al inicio",
    goHome: "Ir a la página de inicio →",
    viewAndApply: "Ver y aplicar →",
    roleFallback: "Puesto",
  },

  roles: {
    product_manager: "Product Manager",
    marketing: "Marketing",
    social_media: "Redes sociales",
    data_analyst: "Analista de datos",
    designer: "Diseñador",
    software_engineer: "Desarrollador de software",
    data_engineer: "Ingeniero de datos",
    devops: "DevOps",
    finance: "Finanzas",
    ml_engineer: "Ingeniero de ML",
  },

  workTypes: {
    fulltime: "Jornada completa",
    freelance: "Freelance",
    parttime: "Media jornada",
  },

  seniorities: {
    junior: "Prácticas / Junior",
    mid: "Intermedio",
    senior: "Senior",
  },

  geo: {
    countries: {
      AT: "Austria", BE: "Bélgica", BG: "Bulgaria", HR: "Croacia", CY: "Chipre",
      CZ: "Chequia", DK: "Dinamarca", EE: "Estonia", FI: "Finlandia", FR: "Francia",
      DE: "Alemania", GR: "Grecia", HU: "Hungría", IE: "Irlanda", IT: "Italia",
      LV: "Letonia", LT: "Lituania", LU: "Luxemburgo", MT: "Malta", NL: "Países Bajos",
      PL: "Polonia", PT: "Portugal", RO: "Rumanía", SK: "Eslovaquia", SI: "Eslovenia",
      ES: "España", SE: "Suecia",
    },
    remoteScope: {
      country: "Solo en los países que he elegido",
      eu: "En cualquier lugar de la UE",
      worldwide: "En cualquier lugar del mundo",
    },
    workModeLabel: {
      onsite: "Presencial",
      hybrid: "Híbrido",
      remote: "Totalmente remoto",
    },
    workModeHint: {
      onsite: "En la oficina",
      hybrid: "Parte oficina, parte casa — aún tienes que estar cerca",
      remote: "Sin oficina",
    },
  },

  location: {
    countriesLabel: "Países donde puedes trabajar",
    addCountryAria: "Añadir otro país",
    addCountryOption: "Añadir otro país…",
    citiesIn: "Ciudades en {country}",
    anyCityNote: "cualquier ciudad (empleos presenciales en todo el país)",
    pickedCityNote: "empleos presenciales solo en las ciudades que elijas",
    anyCity: "Cualquier ciudad",
    addTownPlaceholder: "Añadir otra localidad en {country}…",
    addCityAria: "Añadir una ciudad en {country}",
    workSetup: "Modalidad de trabajo",
    anythingGoes: "todo vale",
    leaveOutRest: "dejaremos fuera el resto",
    hybridNote:
      "Híbrido significa parte de la semana en la oficina, así que sigue teniendo que ser un sitio al que puedas llegar. Muchos anuncios no lo dicen: esos los conservamos y dejamos que el matcher lea la descripción en lugar de adivinar.",
    remoteLabel: "Empleos totalmente remotos — ¿hasta dónde?",
    remoteDisabledNote: "Solo se aplica cuando arriba está seleccionado «Totalmente remoto».",
  },

  landing: {
    wizardAria: "Crea tu resumen",
    stepOf: "Paso {n} de 4",
    q1: "Empieza a buscar ahora",
    cvReading: "Leyendo tu CV…",
    cvDrop: "Suelta tu CV — lo rellenamos por ti",
    cvHint:
      "PDF o DOCX · Lo leemos para configurar tus coincidencias y después borramos el archivo. Nunca se comparte.",
    cvChoose: "Elegir archivo",
    cvDone: "CV leído — datos rellenados",
    cvRemove: "Quitar CV",
    orPickManually: "o elige manualmente",
    addRolePlaceholder: "Añadir otro puesto…",
    addRoleAria: "Añadir un puesto",
    q2: "¿En qué eres bueno?",
    q2hint: "Es lo que usamos para buscar empleos. Marca todo lo que te aplique.",
    addSkillPlaceholder: "Añadir una habilidad…",
    addSkillAria: "Añadir una habilidad",
    q3: "¿Dónde y cómo?",
    q3hint:
      "Primero la ubicación: elige las ciudades a las que podrías desplazarte de verdad. Los empleos presenciales en cualquier otro sitio se descartan; los totalmente remotos no.",
    workTypeHint: "Tipo de trabajo — marca todo lo que encaje.",
    levelHint: "Tu nivel — solo enviamos puestos en los niveles que elijas.",
    narrowWarning:
      "Es una búsqueda muy estrecha — puede que recibas pocas coincidencias. Añade un nivel, un puesto u otra ciudad.",
    q4google: "Confirma tu resumen",
    googleHint:
      "Te registras como {0} — verificado con Google, así que no hay correo de confirmación. Tu primer resumen llega mañana a las 7:00.",
    consent: "Acepto la {0} y el resumen diario.",
    consentLink: "política de privacidad",
    q4: "¿Adónde te lo enviamos?",
    q4hint: "Primero un correo de confirmación — después tu resumen diario a las 7:00.",
    googleSignup: "Registrarse con Google",
    emailPlaceholder: "tu@example.com",
    emailAria: "Tu correo",
    turnstileNote: "Protegido por Cloudflare Turnstile — sin CAPTCHA",
    back: "← Atrás",
    next: "Siguiente →",
    sending: "Enviando…",
    submit: "Empezar mi resumen →",
    livePreview: "Vista previa en vivo",
    trustEmail: "Un correo al día",
    trustUnsub: "Baja con un clic",
    trustFree: "Empezar es gratis · 13 fuentes revisadas cada noche",
    howItWorks: "Cómo funciona",
    step1Title: "Marca tu perfil",
    step1Body: "Puestos, habilidades, dónde puedes trabajar. Veinte segundos, casi todo tocando.",
    step2Title: "Buscamos por la noche",
    step2Body: "Ofertas nuevas de decenas de fuentes, ordenadas para ti, sin duplicados.",
    step3Title: "Lees un correo",
    step3Body: "Una lista corta y ordenada, con un motivo y un enlace para aplicar en cada una.",
    mcta: "Quiero mi resumen",
    mctaAria: "Ir al registro",
    toast: {
      googleExpired: "Tu inicio de sesión con Google ha caducado — inténtalo de nuevo.",
      cvWrongType: "Sube un archivo PDF o DOCX",
      cvTooLarge: "Ese archivo es demasiado grande (máx. 8 MB)",
      cvOk: "CV leído — hemos rellenado tu perfil",
      cvUnreadable: "No hemos podido leer ese archivo",
      needConsent: "Acepta primero la política de privacidad",
      needLevel: "Elige al menos un nivel",
      needEmail: "Introduce un correo válido",
      needTurnstile: "Completa la verificación",
      needRole: "Elige al menos un puesto para continuar",
      needLevelToContinue: "Elige al menos un nivel para continuar",
      genericError: "Algo ha salido mal — inténtalo de nuevo",
    },
  },

  preview: {
    inbox: "bandeja — {email}",
    time: "7:00",
    subject: "{count} para ti — {date}",
    roleCount: { one: "{n} puesto nuevo", other: "{n} puestos nuevos" },
    roleCountNamed: { one: "{n} puesto nuevo: {role}", other: "{n} puestos nuevos: {role}" },
    pickRole: "Elige un puesto para ver tus coincidencias →",
    noCombo: "Sin coincidencias para esta combinación — amplía niveles o tipo de trabajo →",
    greeting: "Buenos días. Hoy {0} — de 214 ofertas revisadas durante la noche.",
    freshMatches: { one: "{n} coincidencia nueva", other: "{n} coincidencias nuevas" },
    reasonMatches: "Coincide con {0}. Sector: {sector}.",
    reasonGeneric: "Buen puesto de {sector} en tu región. Sector: {sector}.",
    refine: "Ajustar preferencias",
    pause: "Pausar 2 semanas",
    unsubscribe: "Darse de baja",
    fine: "Recibes esto porque te registraste en jobdigest.eu · Un correo al día.",
  },

  prefs: {
    signedInLabel: "Sesión iniciada",
    title: "Tus preferencias",
    signedInAs: "Sesión iniciada como {0}. Cambia lo que quieras, pausa o vete — {1}.",
    logOutInline: "cerrar sesión",
    noPasswordLead: "Sin contraseña.",
    noPasswordBody:
      "Al hacer clic en el enlace de tu correo iniciaste sesión, y sigues con la sesión abierta en este dispositivo, así que aquí no necesitarás el enlace otra vez. Puedes cerrar sesión cuando quieras.",
    roles: "Puestos",
    skills: "Habilidades / palabras clave",
    addRolePlaceholder: "Añadir un puesto…",
    addSkillPlaceholder: "Añadir una habilidad…",
    addRoleAria: "Añadir un puesto",
    addSkillAria: "Añadir una habilidad",
    seniorityLabel: "Nivel — solo enviamos puestos en los niveles que elijas",
    frequency: "Frecuencia",
    freqDaily: "Diario",
    freqWeekdays: "Solo días laborables",
    freqWeekly: "Semanal (lunes)",
    pausedTitle: "Resumen en pausa",
    pauseTitle: "Pausar mi resumen",
    pausedUntil: "En pausa hasta el {date} — reanúdalo cuando quieras.",
    pausedUntilSoon: "pronto",
    pauseBody: "Tómate un descanso sin darte de baja — vuelve cuando estés listo.",
    resumeNow: "Reanudar ahora",
    pauseTwoWeeks: "Pausar 2 semanas",
    saving: "Guardando…",
    save: "Guardar cambios",
    unsubscribeAll: "Darse de baja de todos los correos",
    unsubscribeConfirm: "Haz clic otra vez para confirmar — baja de todos los correos",
    errTitle: "No podemos abrir tus preferencias",
    errNoLink:
      "Abre tus preferencias desde el enlace de tu correo — o usa «Gestionar suscripción» para pedir uno nuevo.",
    errUnknownLink: "Enlace desconocido o caducado.",
    toast: {
      saved: "Preferencias guardadas",
      saveFailed: "No se ha podido guardar — inténtalo de nuevo",
      paused: "Resumen pausado 2 semanas",
      pauseFailed: "No se ha podido pausar",
      resumed: "Resumen reanudado",
      resumeFailed: "No se ha podido reanudar",
      unsubscribed: "Te has dado de baja",
      unsubscribeFailed: "No se ha podido dar de baja",
    },
  },

  matches: {
    label: "Tus coincidencias · sesión iniciada",
    countTitle: { one: "{n} coincidencia para ti", other: "{n} coincidencias para ti" },
    noneTitle: "Aún sin coincidencias",
    intro:
      "Todo lo que hemos encontrado para {0}, de mejor a peor. Las más fuertes te las enviamos por correo cada mañana — esta es la lista completa.",
    nothingTitle: "Todavía nada",
    nothingBody:
      "Tus primeras coincidencias se buscan durante la noche — vuelve después del resumen de mañana a las 7:00.",
    adjustPrefs: "Ajustar tus preferencias →",
    notQuiteRight: "¿No es del todo lo que buscas? Ajusta puestos y habilidades →",
    showing: {
      one: "Mostrando {shown} de {n} coincidencia",
      other: "Mostrando {shown} de {n} coincidencias",
    },
    loadMore: "Cargar más",
    loadMoreFailed: "No se pudo cargar más — comprueba tu conexión e inténtalo de nuevo.",
    errTitle: "No podemos abrir tus coincidencias",
    errNoLink:
      "Abre tus coincidencias desde el enlace de tu correo — o usa «Gestionar suscripción» para pedir uno nuevo.",
    errUnknownLink: "Enlace desconocido o caducado.",
    topMatch: "Mejor coincidencia",
    tagHybrid: "Híbrido",
    tagRemote: "Remoto",
    tagFreelance: "Freelance",
  },

  checkInbox: {
    title: "Revisa tu correo",
    body: "Hemos enviado un enlace de confirmación a {0}. Haz clic y tu resumen diario empieza mañana a las 7:00.",
    inMeantime: " Mientras tanto, aquí tienes empleos en activo que encajan con tu búsqueda:",
    doubleOptIn: "Doble confirmación · consentimiento RGPD",
    yourInbox: "tu bandeja de entrada",
    instantPreview: "Vista previa instantánea · coincidencia por palabras clave",
    instantNote:
      "Una coincidencia rápida por palabras clave para empezar. El correo de mañana ya viene {0} — cada puesto puntuado y con un motivo por el que encaja contigo.",
    instantNoteEmphasis: "ordenado por IA",
  },

  manage: {
    sentTitle: "Revisa tu correo",
    sentBody:
      "Si {0} tiene una suscripción a JobDigest, acabamos de enviar allí su enlace privado de ajustes. Ábrelo para cambiar preferencias, pausar o darte de baja — sin contraseña.",
    didntGet: "¿No te ha llegado? Mira en spam o inténtalo de nuevo en unos minutos.",
    title: "Inicia sesión en JobDigest",
    hideHint:
      "¿Ya te has inscrito o no te interesa? Marca esas ofertas y ocúltalas — desaparecen de esta página y de tu correo diario.",
    selectAll: "Seleccionar todas las mostradas",
    clearSelection: "Quitar selección",
    selectAria: "Seleccionar",
    selected: { one: "{n} seleccionada", other: "{n} seleccionadas" },
    hideSelected: "Ocultar seleccionadas",
    hiding: "Ocultando…",
    hideFailed: "No se pudieron ocultar — inténtalo de nuevo.",
    hiddenLink: { one: "{n} oferta oculta →", other: "{n} ofertas ocultas →" },
  },

  hidden: {
    label: "Ofertas ocultas · sesión iniciada",
    countTitle: { one: "{n} oferta oculta", other: "{n} ofertas ocultas" },
    noneTitle: "Sin ofertas ocultas",
    intro:
      "Las ofertas que has ocultado. No aparecen entre tus coincidencias ni en tu correo diario — puedes volver a mostrarlas cuando quieras.",
    nothingTitle: "Nada oculto",
    nothingBody:
      "Oculta una oferta desde tus coincidencias y aparecerá aquí. Nunca se borra nada.",
    backToMatches: "← Volver a tus coincidencias",
    unhideSelected: "Mostrar seleccionadas",
    unhiding: "Mostrando…",
    unhideFailed: "No se pudieron mostrar — inténtalo de nuevo.",
    showing: {
      one: "Mostrando {shown} de {n} oferta oculta",
      other: "Mostrando {shown} de {n} ofertas ocultas",
    },
    body: "Aquí no hay contraseñas. Introduce tu dirección y te enviaremos un enlace seguro — haz clic y estarás dentro, y seguirás dentro en este dispositivo.",
    googleNoSub:
      "Esa cuenta de Google aún no tiene suscripción. Regístrate primero y después podrás iniciar sesión con Google.",
    googleSuppressed:
      "Esa dirección se dio de baja antes, así que no podemos volver a suscribirla automáticamente. Escríbenos si quieres volver.",
    googleError:
      "El inicio de sesión con Google no se completó. Inténtalo de nuevo o usa el enlace por correo de abajo.",
    googleSignin: "Iniciar sesión con Google",
    emailLabel: "Tu dirección de correo",
    sending: "Enviando…",
    submit: "Envíame mi enlace de ajustes",
    onlyOwnInbox: "Solo enviaremos el enlace a tu propia bandeja de entrada.",
    error: "Algo ha salido mal. Inténtalo de nuevo en un momento.",
  },

  root: {
    title: "Elige tu idioma",
    body: "Te llevamos a JobDigest en tu idioma…",
  },
};

export default es;
