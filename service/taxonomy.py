"""The role taxonomy — one definition, several consumers.

`role_category` is the spine of this dataset. It decides what lands in a subscriber's
shortlist, what the digest subject line claims the email is about, what a CV upload
prefills, and how the analysis marts segment. It used to be written out five times — the
ingest regexes, the shortlist keywords, the CV rules, the digest subject words, and the
dbt `accepted_values` test — so adding or renaming a category silently worked in some
places and not others.

Everything in Python now imports from here. Two consumers can't:

  - ``dbt/models/staging/stg_job_postings.sql`` + ``.yml`` run in Snowflake
  - ``web/app/page.tsx`` runs in the browser

so ``service/tests/test_taxonomy.py`` asserts those two stay in step and goes red if they
drift. Short of code generation that is the best available guarantee, and it converts a
silent inconsistency into a failing test.

Adding a category: add it to ``PATTERNS`` (order matters — see below), give it a
``SUBJECT_WORDS`` label, add it to the dbt ``accepted_values`` list, and run the tests.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------- classification --
# Ordered most-specific first, and the order is load-bearing:
#   - data / ML / devops must precede the broad software "engineer" catch-all
#   - product must precede design, so a "Product Designer" lands in `design`
# Patterns carry English, Czech/Slovak, Swedish and Norwegian terms, and since 2026-08-10
# also French, German, Dutch, Spanish, Italian and Polish. A localised title ("Java vývojář",
# "Ingénieur de production", "Addetto/a vendite") is not a rare case — it is most of what the
# non-anglophone boards publish, and without its language here it falls through to
# `uncategorised` and never matches a role filter.
#
# **Two things about the multi-language pass are load-bearing and easy to undo.**
#
# One: this is ONE ordered list shared by every language, so a term added for language A
# silently reclassifies language B's titles wherever the strings overlap. That is not
# hypothetical — `projektant` means *designer* in Polish and *design engineer* in Czech, and
# it is why Polish `projektant → design` is deliberately absent (it costs the Czech key 14
# rows and no ordering fixes it, because the two languages disagree about the word). Every
# term below was checked against both cached answer keys before it was added; the union of
# all six languages leaves SE at 81.1896% and CZ at 77.6961%, unchanged row for row.
#
# Two: **only Swedish and Czech can be graded at all.** Those two have answer keys (SSYK,
# ISCO); French, German, Dutch, Spanish, Italian and Polish have none and no permitted route
# to one, so their vocabulary is gated on coverage plus a trap review, never on accuracy.
# Coverage rising in those languages is not evidence that it rose *correctly*. See
# `notes/2026-08-10-SESSION-5-log.md` before treating any of their numbers as accuracy.
#: The Italian role nouns that a domain word attaches to — "Addetto/a **vendite**",
#: "Responsabile **commerciale**", "Tecnico **manutenzione**". Italian terms are bound to one
#: of these rather than used bare, and that is measured, not stylistic: bare `commerciale`
#: appears in 28 corpus titles of which **only 7 are sales roles** (the rest use it as a sector
#: adjective on HR, support, admin and marketing titles), and `sales` runs before all five of
#: those. Bare, it would be the largest single misfile available; bound, it is 18 right and 0
#: wrong. The `(?:/\w{1,8})?` is the other half of the lesson — Italian ads write the gendered
#: slash inline, and `addett\w+\s+vendit` matched **0 of 9** `Addetto/a vendite …` titles
#: without it.
_IT_ROLE = (r"(?:addett\w+(?:/\w{1,8})?|tecnic\w+(?:/\w{1,8})?|operai\w+(?:/\w{1,8})?|"
            r"impiegat\w+(?:/\w{1,8})?|responsabil[ei]|direttor[ei]|direttric[ei])")

#: The French role nouns a domain word attaches to — "Technicien **maintenance**", "Chargé de
#: **recrutement**", "Responsable **logistique**". Same construction as `_IT_ROLE` and for the
#: same measured reason: bare `technicien` covers 46 corpus titles and spreads across five
#: categories, and bare `responsable` is worth +101 coverage and is wrong.
#:
#: **Written as full word forms, not stems, and that is measured rather than stylistic.** The
#: stem draft (`op[ée]rat`, `coordinat`) bound to `maintenance` silently took the English
#: "AI Application Operations & Maintenance Engineer" out of `software_engineering`, because
#: `skilled_trades` runs first. Full forms cannot reach an English title that way.
#:
#: The bounded gap `[^|]{0,20}?` is what reads the French inclusive forms for free —
#: `Technicien(ne)`, `Technicien.ne`, `Technicien/ne`, `Chargé(e)` — which `_IT_ROLE` needed
#: explicit `(?:/\w{1,8})?` syntax to do.
#: **The leading `\b` was added 2026-08-17 and it closes a hazard, at a cost of one row.**
#: Without it these heads matched INSIDE compounds in other languages: `chef` reached into the
#: Swedish `Sektionschef`, so a French pattern was reading Swedish morphology and — by luck —
#: landing on a defensible answer. Measured over 110k rows, hardening costs exactly one live
#: row (`Sektionschef Production Quality Industrialisation`, `manufacturing_production` ->
#: `uncategorised`) and all five answer-key slices stay flat. An accidental match is not a
#: feature: it is a pattern that will keep reading languages nobody measured it against, and
#: the French wave-3 agent declined its own `…transport` binding rather than build on it.
#: If that row matters, the fix is to teach Swedish `sektionschef` explicitly, not to reopen
#: the boundary.
_FR_ROLE = (r"\b(?:technicien(?:ne)?s?|charg[ée]e?s?|responsable|chef(?:fe)?s?|"
            r"assistant(?:e)?s?|directeur|directrice|agent(?:e)?s?|"
            r"coordinateur|coordinatrice|coordonnateur|coordonnatrice|"
            r"op[ée]rat(?:eur|rice)s?|conduct(?:eur|rice)s?|gestionnaire|pilote|"
            r"r[ée]f[ée]rent(?:e)?s?|superviseur|animat(?:eur|rice)s?|"
            r"int[ée]grat(?:eur|rice)s?)")

#: The German generic role heads. **NONE of these may ship bare** — a bare head is exactly
#: what over-reached in the first German pass: bare `produktion|fertigung` takes 6 SSYK key
#: rows, 2 of them out of a correct answer. Every use below binds one to a DOMAIN word, and
#: the domain word is what decides the category. `leiter` is also a LADDER, `leitung` also a
#: PIPE, `meister` also a title — which is the whole reason for the binding.
_DE_ROLE = (r"(?:mitarbeiter|techniker|fachkraft|fachkr[äa]fte|referent|sachbearbeiter|"
            r"spezialist|fachspezialist|koordinator|berater|experte|expertin|"
            r"leiter|leitung|meister)")


def _de_bind(domain: str) -> str:
    """Bind a German role head to a domain word, reading BOTH compound forms.

    German writes a role two ways and a pattern that reads only one silently halves its own
    coverage:

        CLOSED compound   Vertriebsmitarbeiter / Fertigungstechniker / Verkaufsleitung
        OPEN phrase       Mitarbeiter im Vertrieb / Leitung (w/m/d) Produktion

    Three of the six motivating titles, including both holdout wins, need the open branch.
    `[a-zäöüß]{0,4}?` is the German compound linking element (-s-, -es-, -n-, -en-, or
    none); the open form uses the same bounded `[^|]{0,20}?` gap `_FR_ROLE` uses, because a
    gender marker, a preposition and an article all sit between the head and the domain.

    **The `(?:...)` wrap is load-bearing, not tidiness.** An unguarded alternation inside
    `domain` leaks out of the binding and the whole fragment degenerates to the BARE domain
    word — the exact over-reach this construction exists to prevent, arriving through the
    construction. The symptom is that bound and bare measure identically.
    """
    d = domain if domain.startswith("(?:") else f"(?:{domain})"
    return (rf"{d}[a-zäöüß]{{0,4}}?{_DE_ROLE}"     # closed compound
            rf"|{_DE_ROLE}[^|]{{0,20}}?{d}")                           # open phrase


#: The Dutch generic role nouns a domain word attaches to. Dutch also writes compounds both
#: ways — closed and domain-first ("zorgmedewerker"), or open and role-first ("Medewerker
#: Zorg en Welzijn"). Used for exactly one binding: the Italian construction does NOT
#: transfer to Dutch, because the domain words Dutch attaches to these nouns are
#: overwhelmingly environment, water, soil and spatial planning — a domain the 28 categories
#: do not contain. Binding them cannot help, because there is nothing to bind them to.
_NL_ROLE = (r"(?:medewerk(?:er|ers|ster)s?|adviseur|adviseuse|projectleider|"
            r"teamleider|voorman|voorvrouw|beheerder|consulent)")

PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # --- whole-labour-market sectors ---------------------------------------------------
    # Added 2026-08-09, when the taxonomy stopped being tech-only. These come FIRST and the
    # order is load-bearing in one direction only: a nurse's title never contains a tech
    # word, but plenty of tech patterns are broad enough to swallow a sector title
    # (`\bengineer\b` would take "Automationsingenjör", `\banalyst\b` would take "Financial
    # Analyst"). Specific-before-general is the whole rule in this file.
    #
    # Sized against production before being written, per CLAUDE.md's "grow the taxonomy from
    # the report *and* real inventory": these are the sectors with measurable rows in the
    # 42 532 `uncategorised` postings, not a guess at what a job board ought to have.
    # The Swedish half is stems, not whole words, and that is the lesson of the 2026-08-09
    # scorer run: `sjuksköterska` does not match "Sjuksköterskor", and the register writes far
    # more ads in the plural than the singular. `skötersk` covers sjuk-, under-, tand-, skol-
    # and distriktssköterska in every inflection. It deliberately does NOT read `skötare`,
    # which would take `fastighetsskötare` (a caretaker) out of skilled_trades.
    ("healthcare", re.compile(
        r"\bnurse|\bdoctor\b|physician|dentist|physiotherap|pharmacist|paramedic|"
        r"caregiver|midwife|\bgp\b|surgeon|radiolog|"
        r"sestra|sestry|zdravotn|lékař|lékárn|zubní|ošetřovatel|pečovat|"
        r"skötersk|läkare|tandläkare|barnmorska|vårdbiträde|\bvårdare\b|vårdsamordnare|"
        # Norwegian/Danish nurse (SE `skötersk` does not read these): sykepleier / sygeplejer(ske).
        r"sykepleier|sjukepleier|sygeplejer|"
        # `terapeut` covers fysio-, arbets-, psyko- and samtalsterapeut in both languages.
        # `farmaceut(?!yczn)` — the lookahead must live on THIS token, not on a second copy
        # added later in the alternation: a bare `farmaceut` anywhere in the pattern still
        # matches *farmaceutyczny*, so a duplicate-with-guard fixes nothing. The Polish
        # adjective is how a pharma **sales** rep advertises, and it filed as healthcare.
        r"terapeut|sjukgymnast|psykolog(?!i)|farmaceut(?!yczn)|apotekare|tandhygienist|tandvård|"
        r"logoped|audionom|veterinär|djursjukskötare|hemtjänst|äldreomsorg|sjukvård|"
        r"omsorgsassistent|stödassistent|"
        # 2026-08-10 multi-language pass. `farmaceut` gained `(?!yczn)`: the Polish adjective
        # *farmaceutyczny* is how a pharma **sales** rep advertises, and it was filing as
        # healthcare. NL `therapeut` is unreachable from the existing `terapeut` (the h), so
        # Dutch and German ergo-/fysiotherapeut fell through today — a free win, not a new word.
        r"infirmi(?:er|ère|ere)|"                                              # fr
        r"audioprotesi|"                                                       # it
        r"enfermer|gerocultor|higienista|"                                     # es
        r"verpleegkund|verzorgende|\bhelpende|thuiszorg|ouderenzorg|"        # nl
        r"wijkverpleg|doktersassistent|apothekersassistent|"
        r"huisarts|tandarts|verloskundige|therapeut|zorgmedewerker|"
        r"zorgco[oö]rdinator|zorgassistent|zorgkundige|psycholoog|"
        # `th[ée]rapeute` is unreachable from either `terapeut` (SE/CZ) or `therapeut` (NL) —
        # the é. Three spellings of one profession, none of which reads the others.
        r"m[ée]decin\b|pharmacien|th[ée]rapeute|"                       # fr-2
        # --- wave 2 -------------------------------------------------------------
        # EN audiology and speech. `hearing (aid|instrument|care)` carries a tech-role
        # guard: the hearing-aid INDUSTRY employs security architects, and healthcare
        # runs before cybersecurity. The term is meant for the audiology PROFESSION.
        r"audiolog|audioloog|"                                          # en
        r"^(?!.*(?:architect|engineer|software|platform)).*hearing (?:aid|instrument|care)|"
        r"speech(?:[- ]language)? patholog|speech therap|"
        # ES. `facultativo` is the register's word for a physician. The two anchored
        # guards are the load-bearing half and were added by the integrator, not the
        # proposal: `m[ée]dico` is an ADJECTIVE, and healthcare runs before sales and
        # skilled_trades, so unguarded it took the French `Délégué Médico-Technique` (a
        # device SALES rep) and the Spanish `Técnico de Mantenimiento en dispositivos
        # médicos` (a device FITTER). Same shape as `farmaceut(?!yczn)` above: the sector
        # adjective is how a sales or maintenance role advertises. The trailing
        # `(?!\s*\))` is the third guard — a trailing `(Licencia Médica)` is LEAVE COVER.
        r"facultativ[oa]|"                                              # es
        r"^(?!.*(?:d[ée]l[ée]gu|dispositiv|mantenimiento)).*\bm[ée]dic[oa]s?\b(?!\s*\))|"
        # `\bdental\b` MUST be bounded: unbounded it reaches inside `bucodental` (a
        # dental-hygiene TEACHER) and inside `Occidental` — `Andalucía Occidental` is a
        # region, and that is the `georgia` rule in Spanish. `recepcionista` keeps 13
        # `Recepcionista clínica dental` rows in hospitality; `professional` keeps
        # `Marketing Director - Dental Professionals`, who sells TO dentists.
        r"^(?!.*(?:recepcionista|professional)).*(?:cl[íi]nic[oa]s?|\bdental(?:es)?)\b|"
        r"odont[óo]log|neur[óo]log|psic[óo]log|farmac[ée]utic|cirug[íi]a|cirujan|"
        # NL. The closed compound `zorgmedewerker` already ships; this is the OPEN half
        # of the same word. Three guards, each measured: `\b` before `medewerk` keeps
        # `Beleidsmedewerker zorginkoop` (a policy officer) in operations; `{0,2}` filler
        # words rather than `\w*` stops the gap reaching across a whole title; and
        # `zorg\b` keeps `Medior adviseur zorghuisvesting` (care FACILITIES) out.
        r"\bmedewerk(?:er|ster)s?\s+(?:\w+[\s&-]+){0,2}zorg\b|"         # nl-2
        # --- Norwegian, 2026-08-11 (N1, N2, N3, N4, N5, N6, N7, N8, N9) — graded against NAV STYRK-08 ---
        r"helsefagarbeid|s[yj]ukeplei|verneplei|\blege\b|(?:over|fast|tann|øye|fylkes|sykehjems|bedrifts|kommune|smittevern|turnus)lege\b|tannplei|tannhelse|tannklinikk|helsesekretær|legesekretær|farmasøyt|jordmor|hjelpepleier|pleiemedarbeider|pleieassistent|omsorgsarbeider|sykehjem|omsorgssenter|hjemmetjeneste|heimeteneste|"
        # --- wave 3 (2026-08-17) ---
        # sv-04  sv — roles-sv-w3.md
        r"^(?!.*receptionist).*vårdcentral|"
        # sv-12  sv — roles-sv-w3.md
        r"dietist|"
        # sv-17  sv — roles-sv-w3.md
        r"allmänmedicin|"
        # cs-01  cs — roles-cs-w3.md
        r"^(?!.*pedagog).*psycholo(?:g(?![iy])|ž)|"
        # cs-06  cs — roles-cs-w3.md
        r"optometrist|hygienist|^(?!.*prodava).*oční optik|"
        # en-10  en — roles-en-w3.md
        r"\bmedical (?:director|reviewer|writer|advisor)\b|"
        r"wijkzorg|verpleegzorg|zorgprofessional|zorgverlener|zorgstudent|geneeskunde", re.I)),
    # Social work, added 2026-08-10 — the register's "Yrken med social inriktning" and Czech
    # ISCO 2635/3412, ~130+ postings the answer keys used to mark out of scope. AFTER healthcare
    # so a title carrying both a medical and a social word files as care; kept to social-work
    # nouns that do not collide with healthcare's `omsorg`/`stödassistent`. Czech `kurátor`
    # (a museum curator) is deliberately absent; Swedish `kurator` (a welfare counsellor) is not.
    ("social_care", re.compile(
        r"social worker|social work|caseworker|youth worker|support worker|child protection|"
        r"sociální pracovn|sociáln[íy] prác|"
        r"socialsekreterare|socialarbetare|socialpedagog|\bkurator\b|behandlingsassistent|"
        r"boendestödjare|biståndshandläggare|socionom|"
        # `personlig assistent` is LSS disability support, and it is here because the Swedish
        # social occupation field was wired up on 2026-08-17: of its 3 762 ads, 1 673 are the
        # `Personliga assistenter` group — 45% of the field — and every one of them was landing
        # in `other_tech_function` on that pattern's bare, unbounded `assistent`. A title
        # pattern beats the SSYK hint in `classify`, so no adapter-side change could reach it.
        # The consequence was not a wrong digest but *no* digest: no chip maps to
        # `other_tech_function` (`ROLE_ID_FOR_CATEGORY` is display-only, one way), so those rows
        # were reachable by nobody. A residual bucket is a coverage hole, not a misroute.
        #
        # SWEDISH ONLY, and the false friend is the whole reason: English "Personal Assistant"
        # is an executive admin and stays `other_tech_function` on this same pattern's
        # `(?:executive|administrative|office|personal) assistant` — two near-identical strings,
        # two unrelated jobs. `assistent` (Swedish/Czech) is not `assistant` (English).
        # The boundary is the publisher's, not ours: SSYK files this group under *social*, which
        # is how `gehandicaptenzorg` was decided below. Residual after this: 200 bare
        # `assistent`/`nattassistent`/`kvällsassistent` titles, left as declines rather than
        # bounding `assistent` itself, which is a wider blast radius than this field warrants.
        r"personlig[at]?\s+assistent(?:er)?|personlig\s+assistans|"
        r"(?<!uitvoerings)begeleid(?:st)?er|maatschappelijk werk|jeugdhulp|jongerenwerk|"      # nl
        r"sociaal werker|welzijnswerk|"
        # --- wave 2 -------------------------------------------------------------
        # `gehandicaptenzorg` MOVED here from healthcare. Disability care is social
        # care, and the word has to LAND somewhere: removing it from healthcare alone
        # left 7 titles whose only signal is that word (`Flexmedewerker
        # gehandicaptenzorg`, `Vrijwilligerswerk gehandicaptenzorg`) uncategorised. The
        # other 22 carry `begeleider` and land here anyway, which is why a
        # single-language pass could not see the loss.
        r"dagbesteding|gehandicaptenzorg|"                              # nl-2
        # ES. The literal `trabajador social` fails on the form the ads actually use,
        # `Trabajador/a Social` — the slash is the whole fix.
        # --- Norwegian, 2026-08-11 (N10, N12) — graded against NAV STYRK-08 ---
        r"sosionom|barnevern|støttekontakt|jobbspesialist|"
        # --- wave 3 (2026-08-17) ---
        # sv-07  sv — roles-sv-w3.md
        r"arbetskonsulent|"
        # cs-02  cs — roles-cs-w3.md
        r"sociáln\w*\s+služb|"
        # cs-03  cs — roles-cs-w3.md
        r"sociáln[ěe]\s*[- ]?\s*práv\w*\s+ochran|"
        r"trabajador[a-z]*(?:/[ao])?\s+social|animaci[óo]n\s+sociocultural", re.I)), # es-2
    ("education", re.compile(
        r"teacher|lecturer|professor|educator|\btutor\b|kindergarten|preschool|"
        r"teaching assistant|"
        r"učitel|učitelka|vychovatel|pedagog|lektor(?!ov)|docent|vysokoškolsk[ýá] uči|"
        r"lärare|förskol|barnskötare|studie- och yrkesvägledare|"
        r"elevassistent|elevresurs|studiehandledare|fritidspedagog|"
        r"skolvikarie|lärarvikarie|husvikarie|doktorand|amanuens|forskare|utbildare\b|"
        # Coaching and instructing is education's nearest true home; SSYK files it there too.
        r"instruktör|\btränare\b|dansledare|"
        # `\bformation\b` is bounded because unbounded it eats "information". It is the one
        # French term here with a live English risk: `education` runs 3rd, so a future English
        # "Formation …" title would misfile. `formateur|formatrice` is the safe subset if that
        # ever shows up.
        r"formateur|formatrice|\bformation\b|"                                  # fr
        r"ausbilder|"                                                           # de
        r"leerkracht|onderwijsassistent|pedagogisch medewerker|kinderopvang|"   # nl
        r"\bleraar\b|onderwijzer|"
        # --- wave 2 -------------------------------------------------------------
        # `\btrainer\b` / `\binstructor\b` follow the file's own `instruktör` decision:
        # instructing IS the profession. Measured on the union this takes 12 rows from
        # other categories (`Sales Trainer`, `Cybersecurity Technical Trainer`) rather
        # than the 3 the English proposal predicted on its own corpus. Accepted as the
        # same call the file already made, with the true count recorded here.
        r"\binstructor\b|\btrainer\b|"                                  # en
        # `\bonderwijs` bounded away from `onderwijsinstelling` (the institution, not a
        # teaching role). ES `profesor` has one s and English `professor` cannot reach it.
        r"\bonderwijs(?!instelling)|"                                   # nl-2
        # --- Norwegian, 2026-08-11 (N13, N14, N15, N16, N17) — graded against NAV STYRK-08 ---
        r"lærer|lærar|barnehage|ungdomsarbeider|ungdomsarbeidar|\bforsker|\bforskar|stipendiat|postdoktor|førsteamanuensis|\brektor|\bsfo\b|"
        # --- wave 3 (2026-08-17) ---
        # cs-04  cs — roles-cs-w3.md
        r"tren[ée]r|"
        # cs-05  cs — roles-cs-w3.md
        r"instruktor|"
        # de-08  de — roles-de-w3.md
        r"lehrkraft|lehrbeauftragt|dozent|"
        r"\bprofesor", re.I)),                                          # es-2
    # "chef" is deliberately absent: in Swedish it means *manager* (Restaurangchef, IT-chef,
    # Ekonomichef), so a bare match would misfile every Swedish leadership title into
    # hospitality. Only the French-derived "chef de cuisine" is unambiguous.
    ("hospitality", re.compile(
        r"chef de cuisine|barist|waiter|waitress|bartender|receptionist|housekeep|"
        r"restaurant manager|hotel manager|"
        # `kuchyn` (kitchen) is the single largest Czech gap in the key: "Pomocná síla do
        # kuchyně", "Pomocník v kuchyni", "studená kuchyně" — kitchen-helper ads the -ař cook
        # words never read (kuchyni appeared 38× among the misses). `barista` widened to
        # `barist` for the plural "baristé". `pizzař` is the pizza cook.
        r"kuchař|kuchařk|kuchyn|pizzař|číšník|servírk|recepční|barman|pokojsk|občerstven|"
        # `kock` with its Swedish suffixes and no others: "Sushikock", "Eventkockar" and
        # "Lunchkock" are cooks, and the shipyard "Kockums" is not.
        r"kock(?:ar|en|arna|erska)?\b|servitör|servitris|restaurang|hotellchef|bartender|"
        r"pizzabagare|köksbiträde|köksmästare|kökschef|souschef|hovmästare|"
        r"servering|servis\b|servispersonal|diskare|barpersonal|"
        r"måltidsbiträde|måltidsservice|kostchef|gatukök|värdinna|"
        r"\bbagare\b|konditor|cafébiträde|\bcafé|\bkafé|\bcafe\b|"
        # `\bkok\b` is bounded: unbounded it matches "bangkok".
        r"recepcionista|"                                                       # es
        r"keukenmedewerker|keukenhulp|gastvrouw|gastheer|\bkok\b|horeca|"  # nl
        # --- wave 2 -------------------------------------------------------------
        # `\bkoch\b` MUST be bounded: bare `koch` matches KOCHI, an Indian city, in the
        # trap corpus. Same rule as `\bkok\b` and bangkok directly above.
        r"\bkoch\b|\bk[öo]chin\b|k[üu]chenhilfe|k[üu]chenkraft|"        # de-2
        r"keuken\s*medewerk|chef de partie|"                            # nl-2
        rf"{_NL_ROLE}\s+bediening|"
        # --- Norwegian, 2026-08-11 (N18, N19, N20) — graded against NAV STYRK-08 ---
        r"kokk(?:e|en|er|ene|ar|ane)?\b|servitør|resepsjonist|kjøkken\s?(?:medarbeid|assistent|hjelp|sjef|ansvarlig|personale|team)|gatekjøkken|"
        # --- wave 3 (2026-08-17) ---
        # sv-18  sv — roles-sv-w3.md
        r"kallskänk|"
        # cs-10  cs — roles-cs-w3.md
        r"cukrá[řr]|"
        r"cociner[oa]|camarer[oa]", re.I)),                             # es-2
    # Construction comes BEFORE skilled_trades, and that ordering was measured rather than
    # assumed (2026-08-09-c: +4.4 points on the Czech key, Swedish unchanged). A building-site
    # title routinely carries both a trade word and a domain word — "Montér ve stavebnictví",
    # "Údržbář budov" — and the domain is the more specific of the two: a fitter on a site is
    # doing construction, while a fitter in a plant is not. It also has to precede
    # logistics_transport, so a site's machine drivers (grävmaskinist, hjullastarförare) are
    # read here rather than by that pattern's `förare`.
    ("construction", re.compile(
        # `estimator` is a construction cost estimator ("Estimator I/II", "Sr Estimator",
        # 2026-08-10) — the register writes it with a seniority word and nothing else.
        r"construction|site manager|bricklayer|carpenter|surveyor|estimator|"
        # Czech. Stems, because the register writes the plural: "Zedníci", "Dělníci". The
        # nominative singular this pattern used to require matched almost none of them.
        r"stavbyvedoucí|stavebn|výstavb|zedn|tesař|dlaždič|kamnář|potrubář|"
        # `štukatér`/`omítkář` (plasterers) and `malíř` (painter) — recurring in the ISCO key's
        # construction misses; the register files a house painter as construction, not a trade.
        r"natěrač|lakýrník|pokrývač|obkladač|izolatér|lešenář|betonář|štukatér|omítkář|malíř|"
        r"byggledare|byggnadsarbetare|snickare|snickeri|murare|platschef|"
        r"anläggningsarbetare|anläggare|rörläggare|byggarbetare|byggprojektledare|"
        # Norwegian: `anlegg` (civil works) compounds — "Anleggsleder", "Anleggsarbeider".
        r"anleggsleder|anleggsarbeider|"
        r"stensättare|plattsättare|betongarbetare|betonghåltagare|takläggare|"
        r"träarbetare|markarbet|grävmaskinist|hjullastar|maskinförare|"
        r"ventilationsmontör|ventilationstekniker|kyltekniker|isoleringsmontör|"
        # `betong` (concrete) and `måleri` (painting) — the bare stems the SSYK misses needed:
        # "Renovering av betong", "Måleri", "Projektledare inom betong". `målar` already read
        # "målare" but not the noun "måleri".
        r"ställningsmontör|ställningsbyggare|putsare|golvläggare|målar|måleri|betong|"
        r"hantverkare|rivning|\brivare\b|"
        # `\bobras?\b` is bounded, and the answer key is why: unbounded, `obra` matches the
        # Czech `Obráběč/ka kovů` (a machinist, truth `manufacturing_production`), and
        # construction runs first. `(?<!werktuig)bouw` keeps Dutch *werktuigbouwkunde*
        # (mechanical engineering) out of construction for the same ordering reason.
        r"bauleit|"                                                             # de
        r"construcci[óo]n|edificaci[óo]n|\bobras?\b|"                           # es
        r"(?<!werktuig)bouw|uitvoerder|werkvoorbereid|\bcalculator|timmerman|"  # nl
        r"timmervrouw|metselaar|stukadoor|dakdekker|opzichter|projectontwikkelaar|"
        r"gebiedsontwikkelaar|planontwikkelaar|grondwerker|straatmaker|"
        # --- wave 2 ---
        # --- Norwegian, 2026-08-11 (N21, N22, N23) — graded against NAV STYRK-08 ---
        r"tømrer|tømrar|\bmaler(?!i)|flislegg|blikkenslager|\bmurer|maskinfører|"
        # --- wave 3 (2026-08-17) ---
        # sv-09  sv — roles-sv-w3.md
        r"kalkylator|"
        # de-06  de — roles-de-w3.md
        r"tiefbau|hochbau|bauhandwerker|galabau|\bmaurer\b|bauprojekt|baumanager|"
        r"wegbeheer", re.I)),                                           # nl-2
    # `elkonstruktör` left this pattern on 2026-08-09: an electrical *designer* is an engineer,
    # and it only lived here because `engineering` did not exist yet. `konstruktör` picks it up.
    ("skilled_trades", re.compile(
        r"electrician|welder|plumber|\bmechanic\b|locksmith|\bfitter\b|hvac|"
        r"maintenance technician|field service (?:technician|engineer|engr)|"
        # A *qualified* technician is a trade; a bare "Technician" is still declined (it spreads
        # across trades, manufacturing, IT support and labs — 2026-08-10). These four qualifiers
        # are unambiguously service/field maintenance work.
        r"(?:equipment|controls|facilities|service) technician|"
        # Czech, added 2026-08-09-c from the ISCO key. The stem, minus one word: `zámečna` is
        # the metalworking *shop floor*, which the register files as manufacturing, not the
        # trade. Written as a lookahead rather than as a list of inflections because Czech
        # declines the í as well ("zámečník" → "zámečníci"), and a hand-listed plural is
        # exactly the kind of near-miss that looks correct and matches nothing.
        r"elektrikář|elektrikár|elektrotechni|zámečn(?!a\b)|instalatér|montér|údržbář|"
        # `mechani[kc](?!al)` replaces `automechanik|mechanik`: the register writes the plural
        # "Mechanici"/"Automechanici"/"Elektromechanici", where k→c dodged the -k singulars.
        # The `(?!al)` lookahead is load-bearing — it keeps English "mechanical engineer" out
        # (that stays `engineering`, which runs later) while still reading "mechanic(s)".
        # `servisní technik` is the Czech service technician (bare `technik` stays declined).
        # `(?!al|z)` — `al` keeps English "mechanical engineer" out (that stays `engineering`,
        # which runs later); `z` was added 2026-08-10 for the Polish adjective *mechaniczny*,
        # which was filing two Polish engineering roles as trades.
        r"mechani[kc](?!al|z)|opravář|údržb|servisní technik|"
        r"elektriker|rörmokare|mekaniker|"
        r"servicetekniker|underhållstekniker|driftstekniker|fastighetsskötare|"
        # Named trades only. A bare `tekniker` is NOT here on purpose: the register spreads it
        # across trades, manufacturing, construction, IT support and networks, and taking it
        # first would cost more rows than it wins (measured 2026-08-09: +12, −17).
        r"låstekniker|vitvaru|hjälpmedelstekniker|stationstekniker|teletekniker|"
        r"lastbilstekniker|industritekniker|installatör|vaktmästare|sömmersk|sömmare|"
        # **`monteur` is decided here, once, on purpose.** The German and French proposals both
        # wanted it in `manufacturing_production`; Dutch wanted `skilled_trades`. Because
        # skilled_trades runs first, leaving it in both would have let the Dutch choice
        # silently override the other two — a decision by ordering accident, which is exactly
        # what the next person to reorder this file would undo without knowing. It sits here
        # because the repo already splits the cognates by language (Czech `montér` is a trade,
        # two patterns up; Swedish/Norwegian `montör|montør` is manufacturing, one pattern
        # down, on the SSYK boundary) and because every collected instance is a field fitter:
        # Servicemonteur, Reifenmonteur, Onderhoudsmonteur, Monteur-Câbleur.
        r"monteur|"                                                             # de/fr/nl
        r"technicien[^|]{0,16}?maintenance|maintenance industriel|"             # fr
        rf"manutentor|{_IT_ROLE}\s+(?:alla\s+|della\s+)?manutenzion|"           # it
        r"elektroniker|mechatroniker|servicetechniker|"                         # de
        r"instandhalt(?!ungsingenieur)|meister(?:in|innen)?\b|"
        r"instalaciones|mantenimiento|"                                         # es
        r"technieker|installateur|elektrotechnisch installat|storingsdienst|"   # nl
        r"utrzyman\w* ruchu|konserwator|"                                       # pl
        rf"{_FR_ROLE}[^|]{{0,20}}?(?:maintenance|entretien)|"           # fr-2
        # --- wave 2 -------------------------------------------------------------
        # `wartung` is the other German word for the job `instandhalt` already reads,
        # and it was unreadable. `gebäudetechnikER` is the PERSON, never `gebäudetechnik`
        # the FIELD: the field word steals a sales title, because skilled_trades runs 16
        # patterns before sales.
        r"wartung|reparatur|inbetriebnahme|inbetriebnehmer|"            # de-2
        r"geb[äa]udetechniker|geb[äa]udeleittechnik|"
        r"(?:electrical|electronics?) technician|pipe ?fitter|"         # en
        r"maintenance (?:manager|supervisor|planner|coordinator)|"
        r"technische dienst|"                                           # nl-2
        # IT bare `manutenzion` — must stay AHEAD of manufacturing's bound
        # `_IT_ROLE + impiant`, which is what keeps `PROJECT MANAGER MANUTENZIONE
        # IMPIANTI TERMICI` in trades rather than on a production line.
        r"manutenzion|"                                                 # it-2
        # ES `mecánico` is anchored away from `ingeniero mecánico`: the bare word wins
        # 1 working + 3 holdout rows but steals 4 mechanical ENGINEERS, and the anchored
        # guard beat the bound-role form head to head (the binding won 0 and still stole).
        # --- Norwegian, 2026-08-11 (N24) — graded against NAV STYRK-08 ---
        r"rørlegger|røyrleggjar|"
        # --- wave 3 (2026-08-17) ---
        # sv-02  sv — roles-sv-w3.md
        r"\bservicerådgivare|"
        # sv-13  sv — roles-sv-w3.md
        r"fordonstekniker|"
        # sv-14  sv — roles-sv-w3.md
        r"fastighetstekniker|"
        # sv-15  sv — roles-sv-w3.md
        r"fibertekniker|"
        # sv-19  sv — roles-sv-w3.md
        r"^(?!.*(?:devops|\bit\b|linux|windows|server)).*drifttekniker|"
        # fr-02  fr — roles-fr-w3.md
        r"\br[ée]parat(?:eur|rice|ion)|"
        r"^(?!.*ingenier).*mec[áa]nic[oa]s?\b", re.I)),                 # es-2
    ("logistics_transport", re.compile(
        r"warehouse|forklift|truck driver|delivery driver|courier|dispatcher|"
        r"logistics coordinator|freight|"
        # `sklad(?!atel)` is the warehouse stem, and it replaces the singular-only
        # `skladník|skladnic`: the register writes the plural "Skladníci", where the k→c
        # declension dodged both (the same trap the comments above keep meeting). It covers
        # skladník/skladu/skladový; the lookahead keeps out `skladatel` (a composer). Then
        # dispatch/delivery: `expedic`/`expedien` (NOT bare `expedi`, which would eat the
        # Swedish retail "Expedit"), `rozvoz`, `dispečer`, `doplňovač`, customs `deklarant`.
        r"sklad(?!atel)|řidič|kurýr|spediter|logistik|závozník|"
        r"expedic|expedien|rozvoz|dispečer|doplňovač|deklarant|"
        # Before manufacturing's `obsluha`: a forklift is materials handling, not production.
        r"manipulačn|vysokozdvižn|"
        r"\blager|truckkort|chaufför|orderplockare|terminalarbetare|godsmottag|"
        # `förare` as a suffix: buss-, taxi-, lastbils-, skjutstativ-, motvikts-, båt-.
        # Everything a building site drives was claimed by `construction` one pattern up.
        r"förare|brevbärare|paketbud|distributör|\btaxi|bärgare|bärgning|"
        r"transportledare|transportplanerare|trafikplanerare|depåmedarbetare|"
        # `\bautist[ai]\b` MUST keep its boundaries — unbounded, `autist` reads "autistic" and
        # "autism". `magazzin` (it) has a double z and so cannot reach English "magazine" or
        # Czech "magazín"; the NL/PL/IT warehouse words are three distinct strings, not one.
        # The German `fahrer` lookbehinds keep *Anlagenfahrer* and *Leitstandfahrer* — plant
        # operators, not drivers — in manufacturing, where the next pattern claims them.
        r"magasinier|magasini[èe]re|\bcariste|"                                 # fr
        r"\bautist[ai]\b|conducent[ei]|magazzin|"                               # it
        r"disponent|(?<!anlagen)(?<!leitstand)fahrer|zugbegleiter|zugchef|"     # de
        r"zugverkehr|"
        r"almac[ée]n|log[íi]stica|"                                             # es
        r"chauffeur|bezorger|bezorging|koerier|magazijn|heftruck|orderpick|"    # nl
        r"logistiek|expediti|vrachtwagen|trambestuurder|buschauffeur|"
        r"logistyk|magazyn|"                                                    # pl
        # BOUND, never bare: bare `logistique` measured 10 right and 11 wrong — it steals nine
        # key-account *sales* titles from one employer alone.
        rf"{_FR_ROLE}[^|]{{0,16}}?logistique|"                          # fr-2
        # --- wave 2 -------------------------------------------------------------
        r"lokf[üu]hrer|lokrangierf[üu]hrer|triebfahrzeugf[üu]hrer|binnenschiffer|" # de-2
        r"fachlagerist|"
        # The English word for a job the file already knows in five other languages.
        r"logistics? (?:manager|coordinator|specialist|planner|operator|supervisor|" # en
        r"assistant|technician|clerk|trainee|buyer|associate|intern|analyst|"
        r"director|despatch)|"
        r"material handler|store ?keeper|"
        r"transport(?:ation)? (?:manage|planner|rate)|"
        # ES. `conductor` needs the suffix — bare it is also an English/physics noun.
        # `\bmozo\b` is bounded on the file's usual precaution — it is also a Spanish
        # surname and must not be read inside a longer token. **The proposal's stated
        # reason was wrong and is corrected here rather than repeated:** it claimed
        # unbounded `mozo` reaches *Mozambique*, and it does not — that word is `moza`,
        # not `mozo`. Across 60,621 corpus and answer-key titles the bare and bounded
        # forms match the identical 2 rows, so this boundary is currently untestable and
        # deliberately carries no test; a guard that cannot fail documents nothing.
        r"conductor(?:a|/a|es|/es)\b|\bmozo\b|mozo/a|"                  # es-2
        # PL customs. `\bceln` must start the word: it cannot reach `cel` (a goal). It
        # also correctly reads the Czech `Celník/celnice` — a cross-language gain, and
        # the one answer-key row the union moves (ungraded: the register maps it outside
        # our 28 categories, so it is in no measured slice).
        # --- Norwegian, 2026-08-11 (N25) — graded against NAV STYRK-08 ---
        r"sjåfør|"
        r"\bceln\w*|kontroli eksportu", re.I)),                         # pl-2
    ("manufacturing_production", re.compile(
        r"production (?:operator|technician|planner|manager|associate|supervisor|worker)|"
        r"machine operator|manufacturing (?:technician|associate|operator)|"
        r"assembly|\bassembler\b|equipment installer|quality (?:inspector|technician)|cnc|"
        # Czech. `dělní` and `výrob` are stems for the same reason the Swedish half is:
        # "Dělníci", "Dělnice", "v kovovýrobě". `obsluha` (machine tending) sits here only
        # because hospitality's `občerstven` and logistics' `manipulačn` read their own
        # senses of it first — this is the ordering rule doing real work.
        # Bare `operátor` (was `operátor výroby`) is the single biggest ISCO-key gap — the
        # register writes "Operátor", "OPERÁTOR / OPERÁTORKA", "Operátor/ka" with no domain
        # word (22× among the misses). Safe here only because logistics runs first and takes
        # "operátor skladu" via its `sklad` stem. `švadlen`/`šičk` (seamstress/sewer),
        # `nástroja` (toolmaker), `tiskař` (printer), `lakovn` (paint shop) are the other
        # recurring production nouns the -ař/-ič list missed.
        r"operátor|seřizovač|výrob|montážní|dělní|obsluha|švadlen|šičk|nástroja|tiskař|lakovn|"
        # `obr[áa]běč`: the register writes it BOTH ways and the unaccented "Obraběč/ka kovů"
        # was landing uncategorised — found 2026-08-10 while mutation-checking the Spanish
        # `\bobras?\b` boundary, which is the collision this same row causes in the other
        # direction. The accented-only spelling is the near-miss this file's comments keep
        # warning about, one more time.
        r"obr[áa]běč|frézař|soustružník|brusič|lisař|balič|"
        r"truhlář|řezník|karosář|strojírensk|"
        r"produktionstekniker|produktionsmedarbetare|produktionspersonal|"
        r"produktionsarbetare|operatör|ställare|"
        # `svářeč` (CZ welder) sits here, not in skilled_trades, for the same reason `svetsare`
        # does: SSYK/ISCO file welders under industrial manufacturing (factory production), not
        # trades. The Swedish decision (2026-08-09) was never applied to the Czech word until now
        # — it cost 43 rows on the ISCO key that the classifier called skilled_trades. `zámečník`
        # stays a trade (fitter/locksmith), so this is `svářeč` alone, not the whole stem list.
        r"svářeč|"
        r"svetsare|\bsvets\b|montör|montør|montering|montage|"  # montør: Norwegian fitter
        # Vehicle body repair: the register files it as manufacturing, not as a trade.
        r"plåtslagare|skadetekniker|bilskade|däcktekniker|tryckeri|"
        r"produktionsledare|industriarbetare|"
        # `monteur` is NOT here — see the note in `skilled_trades`. `montör|montør` above is a
        # different string and stays. `\blasser\b` (nl welder) is bounded: unbounded it matches
        # the Dutch place name *Alblasserdam* and the Swedish *musikklasser*.
        r"op[ée]rat(?:eur|rice)|monteuse|"                                      # fr
        r"produktionsmitarbeiter|schichtleit|chemikant|anlagenf[uü]hrer|"       # de
        r"anlagenfahrer|anlagenbediener|maschinenf[uü]hrer|"
        r"producci[óo]n|fabricaci[óo]n|"                                        # es
        r"productiemedewerker|productieleider|productietechniek|\blasser\b|"    # nl
        r"plaatwerker|verspan|bankwerker|metaalbewerker|"
        r"produkcj|produkcyj|"                                                  # pl
        # The `production` binding carries a lookahead because "Chargé de production
        # marketing" and audiovisual production are not factory work — a real corpus title
        # taught that. `technicien … qualité` and `contrôle qualité` are the *inspection* half
        # of the quality split pinned in `test_quality_work_splits_three_ways…`; the engineer
        # half is in `engineering`, which runs later.
        rf"{_FR_ROLE}[^|]{{0,20}}?production(?![^|]{{0,24}}"                    # fr-2
        r"(?:marketing|communication|audiovisuel))|"
        rf"{_FR_ROLE}[^|]{{0,16}}?m[ée]thodes?\b|"
        r"technicien[^|]{0,20}?qualit[ée]|contr[ôo]le\s+qualit[ée]|"
        r"ordonnanceu|planificat(?:eur|rice)|"
        r"assemblage|conditionnement|usinage|soudeu(?:r|se)|ajusteu(?:r|se)|"
        # --- wave 2 -------------------------------------------------------------
        # THE bound-role German term. Bare `produktion|fertigung` FAILS the gate: it
        # takes 6 SSYK key rows, 2 of them out of a correct answer. Bound, it holds
        # row-for-row and still wins on both halves of the board holdout.
        rf"{_de_bind('produktion|fertigung')}|"                         # de-2
        r"kunststoff|kautschuktechnolog|verfahrenstechnolog|abf[üu]ll|"
        r"zerspanung|fr[äa]stechnik|spritzguss|gie[ßs]erei|gussputzerei|"
        r"schichtf[üu]hrer|maschinenbediener|chemiefacharbeiter|vorarbeiter|"
        r"werkstoffpr[üu]fer|leitstand|prozessleittechnik|"
        # EN. `shift (leader|lead|supervisor)` is DELIBERATELY ABSENT: the SSYK key
        # grades three `Shift Leader` ads as hospitality, because the register writes
        # the phrase in English for café and fast-food shift work. German
        # `schichtführer` above is a different string and does not carry that sense.
        r"plant (?:manager|operator|director|supervisor)|\bmachinist\b|" # en
        r"quality (?:control|assurance) (?:inspector|technician)|"
        r"production line operator|"
        # IT. `capo` bound to a shop-floor noun — bare `capo` is a boss AND the place
        # name Capo d'Orlando. `reparto` carries a `(?<!de )` lookbehind because Italian
        # `reparto` is a DEPARTMENT while Spanish `reparto` is a DELIVERY ROUND, and
        # `_IT_ROLE` contains `tecnic\w+`: without it, an unaccented `TECNICO DE REPARTO`
        # (a Spanish delivery technician) filed as factory work. Italian writes `di
        # reparto`, Spanish writes `de reparto`, so the guard costs no Italian title.
        rf"{_IT_ROLE}[^|]{{0,20}}?(?:conduzione|impiant)|"              # it-2
        rf"{_IT_ROLE}[^|]{{0,20}}?(?<!de )reparto|"
        r"capo\s?(?:squadra|turno|officina|reparto)|caposquadra|capoturno|"
        r"capoofficina|controllo\s+qualit|\boperai[oa]\b|"
        # PL quality CONTROL only — `Inżynier ... jakości` stays in engineering, per the
        # three-way quality split (engineer -> engineering, inspector -> here, QA ->
        # software). ES `montaje`/`tejidos` both bounded.
        r"kontrol\w*\s+jako[śs]ci|kontroler\w*\s+jako[śs]ci|jako[śs]ci dostawc|" # pl-2
        # --- Norwegian, 2026-08-11 (N26) — graded against NAV STYRK-08 ---
        r"produksjons(?:medarbeid|leder|leiar|operatør|tekniker|sjef|arbeider|assistent)|"
        # --- wave 3 (2026-08-17) ---
        # sv-10  sv — roles-sv-w3.md
        r"däckskift|"
        # sv-16  sv — roles-sv-w3.md
        r"lackerare|"
        # de-04  de — roles-de-w3.md
        r"qualit[äa]tspr[üu]f|"
        r"\bmontaje\b|\btejidos\b", re.I)),                             # es-2
    # --- tech --------------------------------------------------------------------------
    ("data_engineering", re.compile(
        r"data engineer|analytics engineer|dataops|data platform|data warehouse|\betl\b|"
        # `dataingenjör` (SE) explicitly, so it is not swept up by engineering's broad
        # `ingenjör` a few patterns down — data_engineering is more specific and comes first.
        # **`dataingeniør` (NO) is the identical defect and was simply missed**: `engineering`
        # carries a broad `ingeniør`, so until 2026-08-11 a Norwegian data engineer filed as
        # generic engineering — the exact outcome the line above exists to prevent, one
        # vowel away. `[øo]` because a de-accented feed writes the bare o, the `obr[áa]běč`
        # lesson. Measured at 0 titles moved across 7 172 corpus and answer-key rows: the
        # compound is specific enough that it can only ever add.
        r"datov[ýá] inžen|dátový inžinier|data inžinier|dataingenjör|dataingeni[øo]r|"
        r"data architect|data modell?er", re.I)),
    ("machine_learning", re.compile(
        r"machine learning|\bml engineer|\bai engineer|data scientist|mlops|"
        # `data science` as a phrase, not only `data scientist`: "Data Science Manager" and
        # "Data Science Trainee" were the two commonest ML titles left uncategorised
        # (2026-08-10, 73 rows). The register advertises the *field* with a seniority word
        # attached, which the -ist form never matched. `data_engineering` runs earlier and
        # keeps "data engineer"; nothing there reads a bare "data science", so it lands here.
        r"data science|"
        r"deep learning|computer vision|\bnlp\b|strojové uč|"
        # The vocabulary the field actually advertises in now. "Large Language Model
        # Architect" was the single commonest uncategorised English title in production
        # (2026-08-09, 46 postings) and nothing here could read it.
        r"large language model|\bllms?\b|generative ai|\bgen ?ai\b|"
        r"\bai\b[^|]{0,20}(?:architect|scientist)|prompt engineer|"
        r"umělá inteligence|umelá inteligencia|datov[ýá] v[ěe]dec", re.I)),
    # Cybersecurity is a fourth tech axis, added 2026-08-10 (~306 uncategorised postings and
    # named as unmet demand in CLAUDE.md). It MUST precede data_analysis (whose bare
    # `\banalyst\b` would take "Security Analyst"), software_engineering (`\bengineer\b` ->
    # "Security Engineer") and devops_platform ("cloud architect" -> "Cloud Security
    # Architect"). Anchored to the discipline word: bare "security" is a physical guard or a
    # safety role, so only the named security professions are read, and the Swedish half is the
    # compound ("informationssäkerhet"), never bare "säkerhet".
    ("cybersecurity", re.compile(
        r"cyber ?security|information security|infosec|\bappsec\b|application security|"
        r"security (?:engineer|analyst|architect|specialist|consultant|operations|engineering)|"
        r"penetration test|pentest|red team|blue team|\bsoc analyst\b|"
        r"threat (?:intelligence|hunting|detection)|vulnerability (?:management|analyst)|"
        r"\bsiem\b|security operations cent|"
        r"it-säkerhet|informationssäkerhet|cybersäkerhet|säkerhetsanalytiker|"
        r"kybernetick[áé] bezpečnost|informační bezpečnost|bezpečnostní analytik|"
        r"cybers[ée]curit[ée]|"                                         # fr
        # --- wave 2 ---
        # The `cyber-` prefix is the WHOLE term in both languages. Bare `bezpiecze` eats
        # Polish BHP (occupational health & safety, which has no category), and bare
        # `seguridad` is Spanish for both security and safety. Neither may ship.
        r"cyberbezpiecze|"                                              # pl-2
        # --- Norwegian, 2026-08-11 (N37) — graded against NAV STYRK-08 ---
        r"informasjonssikkerhet|cybersikkerhet|it-sikkerhet|sikkerhetsanalytiker|sikkerhetsarkitekt|"
        # --- wave 3 (2026-08-17) ---
        # fr-03  fr — roles-fr-w3.md
        r"analyste\w*\s+(?:en\s+)?(?:s[ée]curit[ée]|cybers)|"
        r"ciberseguridad", re.I)),                                      # es-2
    # Science / R&D — the applied, industry science the ATS boards carry (pharma, life sciences,
    # labs), added 2026-08-10 (~300 uncategorised). AFTER machine_learning so "Data Scientist"
    # stays ML; academic research (`forskare`, `doktorand`) stays in education on purpose — this
    # is the industry bench, not the university. `\bscientist\b` is safe here because every
    # data/ML sense of it was already claimed above.
    ("science_research", re.compile(
        r"\bscientist\b|research scientist|clinical research (?:associate|scientist|coordinator)|"
        r"\bbiologist\b|\bchemist\b|physicist|microbiolog|biochemist|pharmacolog|toxicolog|"
        r"bioinformatic|laboratory scientist|lab scientist|"
        r"vědecký pracovník|výzkumný pracovník|"
        # --- wave 2 ---
        r"clinical (?:research|trials?)|medical science liaison|"       # en
        r"lab(?:oratory)? technician|regulatory affairs", re.I)),
    # Before `data_analysis` on purpose: that pattern ends in a bare `\banalyst\b`, so
    # "Financial Analyst" and "Credit Analyst" were landing in data analysis — a subscriber
    # asking for data work got finance roles, and one asking for finance got nothing.
    ("finance_accounting", re.compile(
        r"\bfinanc(?:e|ial)\b|account(?:ant|ing)|\bcontroller\b|bookkeep|\btreasury\b|"
        r"\baudit(?:or)?\b|payroll|tax (?:advisor|manager|specialist)|"
        r"účetní|účtovník|mzdová účetní|daňov|finanční|"
        # `ekonom` moved here from `other_tech_function` on 2026-08-09. In Swedish and Czech it
        # names the finance profession itself ("Senior ekonom", "Ekonomichef"), and the residual
        # bucket was the wrong home for it once finance_accounting existed. `(?!ick)` keeps the
        # Czech adjective "ekonomický" out.
        # `(?<!m)` is a two-character guard against an EMPLOYER NAME, added 2026-08-17: bare
        # `ekonom` was reading **Mekonomen**, the Swedish car-parts chain, and filing its
        # service advisers as accountants (`FORDONSTEKNIKER (MEKONOMEN)` among 3 rows). That is
        # the `georgia` rule in a place CLAUDE.md's note on company names does not look — the
        # collision is inside the *classifier*, not inside `search_tsv`.
        #
        # A leading `\b` is the obvious fix and it is the WRONG one, measured: it moves 28 rows,
        # only 3 of them Mekonomen, and the 25 casualties are exactly the Swedish compounds this
        # pattern exists for — `Bolagsekonom`, `Hälsoekonom`, `Förvaltningsekonom`,
        # `Projektekonom`, `Verksamhetsekonom`. A prefix census over 122 208 titles is what
        # settles it: the prefixes before `ekonom` are `''` 423, `redovisnings` 83, **`m` 10**,
        # `projekt` 10, `fastighets` 4, `bolags` 3, `företags` 3, `national` 2 — `m` is the only
        # one that is not a Swedish compound morpheme, and all 10 of its occurrences are
        # Mekonomen. A whole-title `mekonomen` exclusion loses `Ekonomiassistent till Mekonomen`,
        # which is genuinely finance; the lookbehind keeps it. Same shape as the engineer guard:
        # positional beats whole-title, and the answer keys cannot tell them apart.
        #
        # Known residual, stated rather than hidden: this would also block a Swedish
        # `hemekonom` (home economist). Zero occurrences in the corpus, so it is untestable
        # today and gets no test — the `mozo`/`Mozambique` situation.
        r"revisor|redovisning|(?<!m)ekonom(?!ick)|lönespecialist|"
        r"löne(?:administratör|assistent|konsult)|"
        # The French `comptab` lookbehinds keep "cabinet comptable" (an accounting *firm* named
        # in a title for some other role) out. The Polish term is now `finans[oó]w` (widened in
        # place by wave 3, 2026-08-17): Polish writes the genitive *finansów* with an acute o,
        # and the unaccented `finansow` this comment used to name simply never matched it — the
        # accented form is the one that appears in `Dyrektor Finansów`. It still cannot separate
        # "financial" from "financed", so it remains 3 of 4 right and the weakest term here;
        # widening the spelling did not make it sharper, only reachable.
        r"(?<!cabinet )(?<!cabinets )comptab|contr[ôo]l\w*\s+de\s+gestion|"     # fr
        r"contr[ôo]leur\w*\s+interne|"
        r"contabil|"                                                            # it
        r"controlling|"                                                         # de
        r"boekhoud|fiscalist|fiscaal|financie|salarisadministra|accountancy|"   # nl
        r"crediteuren|debiteuren|"
        r"finans[oó]w|"                                                            # pl
        # **Bound by the integrator, not by the proposal.** The proposal offered a bare
        # `\bpaie\b`; measured across every corpus it claimed 5 payroll titles correctly and
        # stole "Data Analyst H/F - Equipe Paie / Facturation" from `data_analysis`, which
        # runs one pattern later. Binding it to a role noun keeps all 5 and releases the
        # analyst — a data analyst on the payroll team is a data analyst.
        r"(?:gestionnaire|responsable|charg[ée]e?|assistant(?:e)?)\S*\s+(?:de\s+)?paie|"
        # --- wave 2 -------------------------------------------------------------
        # The English shared-service back office — an entire function `account(?:ant|ing)`
        # cannot reach. This block is why finance_accounting must stay BEFORE
        # data_analysis: `Accounts Payable Analyst` and `Analyst, General Ledger` are
        # finance jobs with an analyst's title, and the ordering files them correctly.
        r"accounts? (?:payable|receivable)|general ledger|credit control|" # en
        r"\bteller\b|(?<!service )\bbanker\b|investment banking|"
        r"investment (?:manager|analyst|associate|director|strategist|specialist|"
        r"banker)|"
        r"\btax\b|\bfp&a\b|credit risk|"
        r"collections specialist|collections manager|"
        # ES. `contable` carries a lookbehind: `conciliación contable` is a sales-ops
        # reconciliation title, not an accountant.
        r"n[óo]minas?\b|\bfinanzas?\b|(?<!conciliaci[óo]n )\bcontable|" # es-2
        # --- Norwegian, 2026-08-11 (N30, N31, N32) — graded against NAV STYRK-08 ---
        r"regnskap|økonomi(?:rådgiver|rådgjevar|medarbeider|konsulent|sjef|leder|ansvarlig|avdeling|styring)|lønns(?:medarbeider|konsulent|ansvarlig|rådgiver|sjef|kontor)|seksjon for lønn|finanssjef|"
        # --- wave 3 (2026-08-17) ---
        # en-13  en — roles-en-w3.md
        r"investor relations|"
        # de-01  de — roles-de-w3.md
        r"buchhalt|"
        # de-05  de — roles-de-w3.md
        r"rechnungswesen|"
        r"nale[żz]no[śs]ci",                                            # pl-2 (receivables)
        re.I)),                                                                 # fr-2
    ("data_analysis", re.compile(
        r"data analyst|bi analyst|business intelligence|power bi|\btableau\b|\banalyst\b|"
        # `analist` is the Dutch/loan spelling that arrives via the NL boards ("Data Analist").
        r"quantitative (?:researcher|analyst)|\banalist\b|"
        # `analityk` (pl) is a separate string from the Czech `analytik` — the i/y is exactly
        # the kind of near-miss that looks already-covered and matches nothing.
        r"analytics|analytik|analytičk|analytičc|analityk|analitycz|"   # pl
        # --- wave 2 --- both bound: bare `datos` takes `Centro de Datos` and
        # `Protección de Datos` (legal), bare `análisis` takes `Análisis Clínicos` (a
        # hospital lab).
        # --- wave 3 (2026-08-17) ---
        # fr-04  fr — roles-fr-w3.md
        r"\banalyste\b|"
        r"an[áa]lisis\s+de\s+datos|gobierno\s+del?\s+dato", re.I)),     # es-2
    ("devops_platform", re.compile(
        r"devops|platform engineer|site reliability|\bsre\b|cloud engineer|"
        r"infrastructure engineer|\bkubernetes\b|cloud architect|"
        r"správce systém|správca systémov|systémov[ýá] administr|"
        r"administrátor (?:is|it|systém|sít|server)|síťov[ýá] administr|"
        r"database administrator|\bdba\b|"
        r"nätverkstekniker|systemtekniker|systemförvaltare|infrastrukturarkitekt|"
        r"systeembeheerder|netwerkbeheerder|applicatiebeheer|"                  # nl
        r"functioneel beheerder|"
        # --- wave 2 ---
        # --- wave 3 (2026-08-17) ---
        # sv-20  sv — roles-sv-w3.md
        r"drifttekniker|"
        # de-03  de — roles-de-w3.md
        r"systemadministra|"
        r"systems? administrator|\bsysadmin\b", re.I)),                 # en
    ("product", re.compile(
        r"product manager|product owner|product lead|product management|\btpm\b|program manager|"
        r"produktov\w*\s+manaž|produktov\w*\s+vlastník|"
        # --- wave 2 ---
        # --- Norwegian, 2026-08-11 (N36) — graded against NAV STYRK-08 ---
        r"produktsjef|"
        # --- wave 3 (2026-08-17) ---
        # en-19  en — roles-en-w3.md
        r"programme (?:manager|lead|director)|"
        r"scrum master", re.I)),                                        # en
    ("design", re.compile(
        r"designer|\bux\b|\bui\b|user experience|user interface|design lead|"
        r"designér|dizajnér|grafik|grafičk|návrhá[řr]|formgivare|grafisk|"
        # --- wave 2 ---
        r"art director|creative director|"                              # en
        # --- wave 3 (2026-08-17) ---
        # en-06  en — roles-en-w3.md
        r"^(?!.*\bengineer).*\bproduct design\b(?!\s*(?:engineer|&\s*research))|"
        r"grafisch ontwerp|grafisch vormgev", re.I)),                   # nl-2
    # Non-software engineering — mechanical, electrical, civil, process. MUST precede
    # software_engineering, whose bare `\bengineer\b` catch-all would otherwise file
    # "Mechanical Engineer" as software. It requires a discipline qualifier before "engineer"
    # (never bare), so a software title stays put; the Swedish "-ingenjör" compounds have no
    # software collision (Sweden titles software work "utvecklare", not "ingenjör"). This is the
    # home for ISCO major 21/31 and Platsbanken's "Yrken med teknisk inriktning" — ~4 000
    # register rows that had no category until 2026-08-09 and sat uncategorised.
    ("engineering", re.compile(
        r"mechanical engineer|electrical engineer|civil engineer|structural engineer|"
        r"process engineer|chemical engineer|automotive engineer|aerospace engineer|"
        r"industrial engineer|manufacturing engineer|mechatronic|electronics engineer|"
        r"hardware engineer|electrical\b.{0,40}engineer|"
        r"ingenjör|ingeniör|ingeniør|"            # SE -ingenjör / -ingeniör and NO -ingeniør
        # `konstruktör` (SE) as well as `konstruktér` (CZ): mechanical and electrical designers
        # are the single largest group inside the register's technical field, and until
        # 2026-08-09 the Swedish spelling was the one missing.
        r"strojní inžen|strojní inžinier|elektroinžen|konstruktér|konštruktér|konstruktör|"
        # Bare `inženýr` (CZ), which only ever appeared with a discipline in front of it —
        # "Inženýr kvality" and "Průmyslový inženýr" were the common shapes and both missed.
        # `data_engineering` reads `datový inžen` several patterns earlier, so it keeps those.
        # `technolog\b` is bounded on purpose: "informačních technologií" is not an engineer.
        r"inženýr|inžinier|projektant|technolog\b|"
        r"kvalitetstekniker|"
        # **Polish `projektant → design` is deliberately absent.** `projektant` is already here
        # as the Czech *design engineer*; in Polish the same string means *designer*. Moving it
        # to `design` (which runs earlier) costs the Czech key 14 rows, 77.70% -> 77.01%, and no
        # ordering resolves it — the two languages disagree about the word. Cut, not deferred.
        #
        # Three of the four Romance/Slavic engineer words need a software lookahead because
        # `engineering` runs BEFORE `software_engineering`; German does not, because German
        # software work advertises as `entwickler`, which the next pattern claims.
        r"ing[ée]nieur(?:e|es|s)?\b(?![^|]{0,40}"                               # fr
        r"(?:logiciel|logicel|software|web|d[ée]veloppement|devops|full ?stack))|"
        r"ing[ée]nierie|"
        r"progettis|progettazion|"                                              # it
        r"ingenieur|konstrukteur|"                                              # de
        r"ingenier(?![\w/()@.]*\s+(?:de\s+)?"                                   # es
        r"(?:software|backend|front[- ]?end|full[- ]?stack))|"
        r"werktuigbouw|constructeur|\btekenaar|modelleur|technisch tekenaar|"   # nl
        r"in[żz]ynier|"                                                         # pl
        # **Quality is folded in here rather than given its own category, and that was
        # measured both ways** (2026-08-10, `notes/proposals/category-gaps.md`). By raw
        # inventory quality is the biggest cluster in the corpus — 199 non-software titles
        # across all six languages, three times the health & safety cluster — so a category
        # looks obviously right. It is not: **both answer keys already file quality work under
        # engineering** (ISCO 3119/2149), SSYK has no quality occupation at all, and a separate
        # category measured *negative* at every position in the order (CZ −0.13 to −0.28).
        # This fold measures *positive*: SE 81.1896% -> 81.3383%, CZ flat, both moved key rows
        # gains and no regression.
        #
        # It also fixes a live defect. `software_engineering`'s `\bengineer(?:ing)?\b` was
        # taking industrial quality engineers into software subscribers' shortlists — 28 of
        # 126 non-software quality titles in the corpus, now 4. The split that remains is
        # deliberate and correct: a quality *inspector* on a line stays
        # `manufacturing_production` (`quality (inspector|technician)`, which runs earlier), a
        # quality *engineer* is engineering. No single category is honest across both.
        r"quality engineer|qualitätsingenieur|ingénieur\w*\s+qualité|"
        r"inżynier\w*\s+jakości|ingeniero\w*\s+de\s+calidad|kvalitetsingenjör|"
        r"supplier quality|quality engineering|"
        # The same defect the quality fold above fixed, two families later — and the file had
        # ALREADY decided both of these, in other languages. German `Sicherheitsingenieur` and
        # Swedish `HSE-ingenjör` reach here through `ingenieur`/`ingenjör`, and `skilled_trades`
        # carries `instandhalt(?!ungsingenieur)` for the sole purpose of releasing the German
        # maintenance ENGINEER to this pattern. Only the English strings were missing, so 76
        # English rows sat in software subscribers' shortlists.
        #
        # The keys agree, and they are why there is no 29th category: STYRK 3119 grades
        # safety/preparedness roles `engineering`, ISCO 2149 is where safety engineers live, and
        # SSYK's own group for them ("Arbetsmiljöingenjörer") is OUT_OF_SCOPE — the register
        # declines to give them a better home too.
        #
        # Both anchored guards are measured, not decorative. Unguarded, `safety engineer` pulls
        # "Trust & Safety Engineer" and "Fullstack Engineer, Safety Engineering" out of
        # software_engineering, and `maintenance engineer` pulls "AI Application Operations &
        # Maintenance Engineer (Azure)" — the exact title `_FR_ROLE`'s docstring records as
        # having been rescued from this pattern family once already.
        r"^(?!.*(?:trust\s*(?:and|&)\s*safety|software|full[- ]?stack)).*\bsafety engineer|"
        r"\b(?:hse|ehs)[\s-]*engineer|"
        r"^(?!.*(?:software|application)).*\bmaintenance engineer|"
        # `essais` = trials/testing. The lookahead keeps *essais cliniques* (clinical trials)
        # out — that is research, not engineering.
        rf"{_FR_ROLE}[^|]{{0,20}}?essais?\b(?![^|]{{0,14}}clinique)|"   # fr-2
        # --- wave 2 -------------------------------------------------------------
        # THE `laborant` verdict, REVERSED on 2026-08-17 — and the reversal is the lesson.
        #
        # This shipped in wave 1 as `[a-zäöüß]{4,}laborant`, and the mandatory compound
        # prefix was described here as "what makes this safe": it kept the 13 German
        # `-laborant` wins while the standalone Czech `Laborant/ka` could not reach it.
        # Wave 2 inherited that verdict without re-running it. Wave 3's German agent
        # re-tested it under the corrected gate and found the collision **does not exist
        # against this category** — it declined to claim the win itself, having no
        # `laborant` titles in its own corpus, and handed it over.
        #
        # Re-measured through the joint harness: **CZ 1586 -> 1588 (+2, 0 lost), CZ
        # majors 1-3 256 -> 258, SE and NO flat.** Reaching `Laborant/ka` is now the
        # POINT, not the hazard the prefix was defending against. Both steals are Czech
        # and both move toward the answer ISCO itself assigns.
        #
        # **A term rejected against category A is not rejected; it is untested against
        # B.** Wave 1 was right about the collision and wrong about the category. That is
        # why the prefix is gone rather than tightened, and why this comment records the
        # reversal instead of being deleted — the next pass needs to know the prefix was
        # tried and found to be defending nothing.
        #
        # Category is `engineering`, not `science_research`, and that AGREES with the
        # Czech key rather than hiding from it: ISCO grades 3111/3119 lab technicians as
        # engineering and reserves science for 211x. The `# de-2` marker it carried was
        # wrong even in wave 2 — the term is cs/de, and it is now a cross-language term
        # nobody proposed as one, the same shape as `tren[ée]r` reading Norwegian.
        r"laborant|"                                       # cs/de, wave 1 -> w3
        # `anwendungstechnik` is safe as a FIELD word where `gebäudetechnik` was not,
        # because engineering runs AFTER skilled_trades and manufacturing.
        r"anwendungstechnik|messtechniker|"
        # NL civil engineering. `civiel` is what stops the five Dutch civil engineers
        # being filed as SOFTWARE by `software_engineering`'s bare `\bengineer\b` — the
        # exact failure the `engineering` category exists to prevent, in Dutch.
        r"civiel|geotechniek|geohydrolo|ontwerper|kunstwerken|"         # nl-2
        # --- wave 3 (2026-08-17) ---
        # en-15  en — roles-en-w3.md
        r"quality (?:manager|assurance manager|systems? (?:manager|specialist|engineer)|specialist)|"
        r"telecomunicaciones|redes\s+el[ée]ctricas", re.I)),            # es-2
    ("software_engineering", re.compile(
        r"software engineer|software developer|back[- ]?end|front[- ]?end|full[- ]?stack|"
        r"web developer|mobile developer|\bios\b|android|\bdeveloper\b|programmer|"
        # "Software Development Manager/Lead" — `software developer` does not match "software
        # development", so the manager or lead of a dev team read as uncategorised (2026-08-10).
        r"software development (?:manager|lead|director)|"
        # `engr` because Workday and Oracle tenants abbreviate it in the title itself
        # ("Software Engr I", "Application Engr II") — 25 postings in one production sample,
        # invisible to `\bengineer\b`. `solutions?` because the plural is the commoner form
        # and `solution architect` alone matched none of it.
        # **The guard is on the CATCH-ALL only, and it is POSITIONAL, not whole-title.** Every
        # specific term in this pattern stays unguarded; only the bare `\bengineer(?:ing)?\b`
        # declines. It has to decline rather than be out-competed: `sales` and `customer_support`
        # run AFTER this pattern, so an "X Engineer" whose home is one of them cannot be rescued
        # by appending anywhere — the catch-all has already answered. Declining lets the later
        # pattern claim it, which is how `pre[- ]?sales` (added to `sales` in wave 2, and dead
        # ever since for any title containing "Engineer") finally becomes reachable.
        # The keys decide the destinations: ISCO 2433/2434 -> sales, ISCO 351/3512 and SSYK
        # "Supporttekniker, IT" -> customer_support.
        #
        # A LOOKBEHIND, not `^(?!.*sales)`, and that distinction is measured — it is the
        # `georgia` rule in a new costume. The whole-title form passes ALL FIVE answer keys with
        # byte-identical numbers, so the keys cannot tell the two apart; only the corpus can. It
        # moves a further 147 postings the wrong way, led by **`Senior Salesforce Engineer`**,
        # because "sales" is inside SALESFORCE, plus `Sr. Systems Engineer, Sales & Marketing`
        # and `Desktop Engineer (2nd Line Support)`. `test_f` exists to pin exactly that, which
        # is why it is kept even though it passes unpatched.
        #
        # The `-` variants are load-bearing: ads write "Pre-Sales Engineer" and "IT-Support
        # Engineer" with a hyphen, which `(?<!sales )` cannot see.
        #
        # `support engineer` is deliberately NOT added to `customer_support` to catch the 129
        # released declines (`Application Support Engineer`, `Cloud Support Engineer`). Measured:
        # identical on all five slices, and it would convert 129 honest declines into a
        # confident answer on the most arguable member of the family. The only safe error is a
        # miss, and a decline still reaches the AI matcher on the keyword path. Decided
        # 2026-08-17; reopen it with numbers, not intuition.
        r"(?<!sales )(?<!sales-)(?<!support )(?<!support-)"
        r"\bengineer(?:ing)?\b|\bengr\b|qa engineer|\bsdet\b|"
        r"solutions? architect|enterprise architect|"
        r"vývojá[řr]|vývojárk|programátor|programátork|softwarov|softvérov|"
        # Any Swedish -utvecklare compound, except the two that are not software:
        # "affärsutvecklare" (business development) and "verksamhetsutvecklare".
        r"(?<!affärs)(?<!verksamhets)utvecklare|"
        r"lösningsarkitekt|systemarkitekt|dataarkitekt|it-arkitekt|integrationsarkitekt|"
        r"solution architect|software architect|"
        r"\btestare\b|testledare|systemtestare|"
        # `entwickler` needs both lookbehinds: German *Produktentwickler* is product development
        # and *Elektronikentwickler* is hardware. `logicel` is not a typo here — Thales
        # publishes it live, and a real corpus contains real misspellings.
        r"logiciel|logicel|d[ée]veloppeur|d[ée]veloppeuse|"                     # fr
        r"architecte\s+(?:logiciel|syst[èe]me|technique|solution|d'entreprise)|"
        r"sviluppator|"                                                         # it
        r"(?<!produkt)(?<!elektronik)entwickler|fachinformatiker|"      # de
        # --- wave 2 ---
        r"systems? architect|integration architect|"                    # en
        # PT. Portuguese for `developer`; Spanish is `desarrollador`, so no collision.
        # --- Norwegian, 2026-08-11 (N34, N35) — graded against NAV STYRK-08 ---
        r"(?<=[a-zæøå]{3})(?<!forretnings)(?<!produkt)(?<!elektronikk)(?<!organisasjons)(?<!tjeneste)utvikl(?:er|ar)\b|testleder|testansvarlig|"
        # --- wave 3 (2026-08-17) ---
        # cs-08  cs — roles-cs-w3.md
        r"(?:it|enterprise|solution|software|datov\w+|cloud\w*|síťov\w+|technick\w+)[- ]?\s*architekt|"
        # de-07  de — roles-de-w3.md
        r"softwareentwickl|wirtschaftsinformatik|informatiker|"
        # nl-01  nl — roles-nl-w3.md
        r"(?<!product)(?<!beleids)ontwikkelaar|"
        # es-01  es — roles-es-w3.md
        r"\bprogramador(?:a|es|as|/a|es/as)?\b|"
        # no-01  fi — roles-nordic-w3.md FIN-1
        r"ohjelmisto|"
        r"(?:ohjelmisto|sovellus|algoritmi|pilvi|web|full ?stack|back ?end|front ?end|järjestelmä|js-|\.js-)kehittäj|"
        r"\bdesenvolvedor", re.I)),                                     # pt
    # Must precede other_tech_function: nearly every social title also says "marketing" or
    # "content", so without this it lands in the catch-all and a subscriber who asked for
    # social media gets the whole marketing/sales/finance/HR bucket instead.
    ("social_media", re.compile(
        r"social[ -]?media|\bsmm\b|community manager|influencer|content creator|"
        r"paid social|social ads|"
        r"sociáln\w*\s+(?:sít|siet|médi|medi)", re.I)),
    # --- business functions -----------------------------------------------------------
    # Split out of `other_tech_function` on 2026-08-09. That catch-all held 15 004 active
    # postings and six distinct professions inside it — sales 6 290, operations 2 602,
    # marketing 2 395, finance 2 028, HR 708, legal 589 — so a subscriber asking for sales
    # got the whole bucket. `other_tech_function` is KEPT below as the residual, because
    # subscribers already hold it in `profiles.role_categories` and removing a stored value
    # is a migration, not a taxonomy edit.
    ("marketing", re.compile(
        r"marketing|marketingov|marketér|\bseo\b|\bsem\b|growth|brand manager|"
        r"copywriter|content marketing|\bpr\b|public relations|kampan|"
        r"marknad|kommunikatör|kommunikationsansvarig|kommunikationschef|"
        # `\bcomunicazion` needs its LEADING boundary — without it, "telecomunicazioni" reads
        # as marketing.
        r"\bcomunicazion|"                                                      # it
        r"marketeer|communicatie|redacteur|woordvoerder|tekstschrijver|"        # nl
        # Kept knowingly imperfect: this claims "Responsable Communication Interne, Direction
        # des Ressources Humaines" out of `hr_recruiting`. That was judged correct rather than
        # a steal — it is a communications role and the HR bit names the department.
        rf"{_FR_ROLE}[^|]{{0,16}}?communication|"                       # fr-2
        # --- wave 2 ---
        r"communications? (?:manager|specialist|lead|director|officer|intern)|" # en
        r"brand (?:activation|marketing|strategist|director|lead)|"
        # --- wave 3 (2026-08-17) ---
        # cs-07  cs — roles-cs-w3.md
        r"^(?!.*business development).*\bppc\b|"
        # en-01  en — roles-en-w3.md
        r"(?:director|head|vp|vice president)[^|]{0,24}?\bcommunications?\b|"
        # en-02  en — roles-en-w3.md
        r"demand gen(?:eration)?\b|"
        # en-05  en — roles-en-w3.md
        r"^(?!.*recruit).*\bevents?\s+(?:manager|coordinator|producer|specialist|lead|director|associate|marketer)|"
        r"public affairs|government affairs|corporate affairs", re.I)),
    ("sales", re.compile(
        r"\bsales\b|account executive|account manager|key account|business development|"
        # Retail shop floor, which the taxonomy could read in Swedish (`butik`) and Czech
        # (`prodava`) but not in English. `customer assistant` is UK supermarket language for
        # a shop-floor job, not a support role — support advertises itself as customer
        # support/service/care, all of which this pattern leaves to `customer_support`.
        r"store (?:manager|associate|assistant)|retail associate|grocery associate|"
        r"shop assistant|sales assistant|customer assistant|"
        # IT/FR field-sales titles that arrive through adzuna and arbeitnow. `commercial\S*`
        # rather than `commercial(?:e)?`: the ads write "Commercial(e) terrain" with a literal
        # "(e)", and the old `\s+` after an optional bare "e" could not cross it (2026-08-10, 31
        # rows). `\bsdr\b`/`\bbdr\b`/`sales development`/`account development` are the standard US
        # pipeline titles ("Sales Development Representative"), none of which contained "sales".
        r"agente di commercio|commercial\S*\s+terrain|"
        r"\bsdr\b|\bbdr\b|sales development|account development|"
        # `prodejc`/`predajc` (was `prodejce`/`predajca`): the plural "Prodejci"/"Predajcovia"
        # dodged the singular via the e→i/-a declension, the same trap fixed elsewhere.
        r"obchodn|prodejc|predajc|prodava|prodejn|pokladní|maloobchod|"
        # `sälj` as a stem, because the register writes "säljarjobb", "säljteam" and "Sälj på
        # förbokade möten" far more often than the bare "säljare" this used to require.
        r"sälj|försäljning|butik|kundansvarig|kundrådgivare|\bprovision\b|"
        # Appointment setting sits between sales and support in SSYK; the register files more
        # of it under Företagssäljare than under Kundtjänstpersonal, so sales takes it.
        r"mötesbokare|mötesbokning|besöksbokare|företagsbokare|"
        # **Three languages, three different verdicts on the same-looking word, all measured.**
        # French: bare `commercial` is an English adjective ("Commercial Finance Manager"), so
        # only the bound form ships. Italian: bare `commerciale` is 7 right of 28, so only the
        # bound form ships. Spanish: `comercial` has ONE m and English "commercial" has two, so
        # it cannot collide and ships bare. Do not "simplify" these into one rule.
        r"(?:responsable|assistant|directeur|directrice|charg[ée])\w*\s+commercial|"   # fr
        rf"{_IT_ROLE}\s+commercial[ei]\b|^commerciale\b|venditor[ei]|"          # it
        rf"venditric[ei]|{_IT_ROLE}\s+(?:alle\s+)?vendit|vendita assistita|"
        rf"teleselling|televendit|{_IT_ROLE}\s+(?:alla\s+)?cassa\b|cassier[ei]|"
        r"vertrieb|"                                                            # de
        r"ventas|comercial|"                                                    # es
        # `\bverkoper` — the LEADING boundary is the whole term. Unbounded it matches the
        # Swedish `sågverkoperatörer` (a sawmill operator, key `manufacturing_production`),
        # which today is masked only because manufacturing runs 14 patterns earlier and its
        # `operatör` claims the title first. That is ordering luck, not safety, and it would
        # break silently the moment anything reordered. The Swedish hit is mid-compound
        # (sågverk|operatörer), so a boundary drops it and keeps Dutch "Verkoper".
        r"winkelmedewerker|winkelmanager|winkels\b|verkoopmedewerker|"          # nl
        r"\bverkoper|verkoop|storemanager|vestigingsmanager|"
        r"sprzeda|handlow|"                                                     # pl
        # `délégué médical/hospitalier` is a pharma rep — sales, not healthcare, and
        # `healthcare` runs first so it must be specific enough not to be caught there.
        # `de secteur` is a territory sales role, and **it is only correct because the
        # `production` binding in manufacturing runs earlier** — alone it is 60% right. That
        # ordering dependency is pinned by a test.
        r"d[ée]l[ée]gu[ée]?\S*[^|]{0,12}?(?:m[ée]dic|hospitali)|"               # fr-2
        r"(?:chef|responsable)\S*\s+de\s+secteur|"
        r"commercial(?:\.e\b|\(e\))|"
        # --- wave 2 -------------------------------------------------------------
        # `bid manager` is DELIBERATELY ABSENT. It was the one graded answer-key row the
        # union moved: SSYK grades `Bid Manager/Anbudsansvarig till Nordic Talent` as
        # marketing. Accuracy would not change (the row was already counted wrong), but
        # it converts an honest decline into a confident misfile — and a declined posting
        # still reaches the AI matcher on the keyword path, while a misfiled one is
        # filtered out of somebody's digest. The only safe error is a miss.
        r"bids? (?:and|&) proposals?|proposal manager|tender manager|"  # en
        r"solutions? consultant|pre[- ]?sales|telesales|inside sales|"
        r"territory manager|"
        r"verk[äa]uf|"                                                  # de-2
        r"winkelbediende|"                                              # nl-2
        # --- Norwegian, 2026-08-11 (N27, N28, N29) — graded against NAV STYRK-08 ---
        r"selger|selgar|salg|kunderådgiver|kundekonsulent|"
        # --- wave 3 (2026-08-17) ---
        # sv-06  sv — roles-sv-w3.md
        r"affärsutvecklare|"
        # sv-08  sv — roles-sv-w3.md
        r"merchandiser|"
        # en-11  en — roles-en-w3.md
        r"^(?!.*counsel).*(?:\bdeal desk\b|\brenewals?\s+(?:manager|specialist|analyst|lead|associate|representative))|"
        # en-12  en — roles-en-w3.md
        r"(?:sales|gtm|revenue|partner|customer|commercial)\s+enablement|"
        # fr-05  fr — roles-fr-w3.md
        r"d[ée]veloppement commercial|repr[ée]sentant\w*\s+commercial|"
        # nl-02  nl — roles-nl-w3.md
        r"accountmanager|"
        r"\bvendas\b", re.I)),                                          # pt
    ("hr_recruiting", re.compile(
        r"recruit|talent acquisition|people ops|people partner|people operations|"
        r"human resources|(?<!\d/)\bhr\b|rekryter|"
        r"personalist|nábor|náborář|mzdov[áý] účetní|"
        # French `recrut` is NOT reachable from the existing English `recruit`: recrutement has
        # no i. The two words diverge at the fifth letter — the same near-miss as
        # `skladník`/`Skladníci`. `\brh\b` is a two-letter token and the weakest term here;
        # `ressources humaines` alone is the safe subset if it ever misbehaves.
        r"recrut|ressources humaines|\brh\b|"                                   # fr
        r"risorse\s+umane|"                                                     # it
        r"recursos humanos|\brrhh\b|"                                   # es
        # --- wave 2 ---
        r"\bhrbp\b|\bpeople\b|learning (?:&|and) development|\bl&d\b|"  # en
        # PL. Polish *rekrutacja* diverges from English *recruit* at the fifth letter —
        # the kind of near-miss that looks already-covered and matches nothing.
        r"\bkadr|\brekrutacj|"                                          # pl-2
        # ES: all two-word phrases, because bare `selección` is selection in general and
        # bare `formación` is also a company department.
        r"selecci[óo]n\s+(?:de\s+)?personal|atracci[óo]n\s+de\s+talento|" # es-2
        # --- wave 3 (2026-08-17) ---
        # en-03  en — roles-en-w3.md
        r"talent (?:management|development|attraction|partner)|"
        # en-07  en — roles-en-w3.md
        r"(?<!workers )(?<!workers. )\bcompensation\b|"
        # en-08  en — roles-en-w3.md
        r"\bsourcer\b|"
        # en-09  en — roles-en-w3.md
        r"^(?!.*counsel).*(?:\bpeople (?:partner|team|experience)\b|employee (?:experience|relations|engagement))|"
        # de-09  de — roles-de-w3.md
        r"personalreferent|personalsachbearbeit|personalentwickl|personalleit|personalwesen|"
        r"personalabteilung|personalberat|"
        r"selecci[óo]n\s+y\s+formaci[óo]n", re.I)),
    ("legal", re.compile(
        # `lawyer` and `attorney` — the plain English words were both absent, so "Immigration
        # Lawyer" (2026-08-10, 21 rows) and every US-style "... Attorney" title fell through to
        # uncategorised while the Czech `advokát` and the Latinate `counsel` were already read.
        r"\blegal\b|\blawyer\b|\battorney\b|counsel|paralegal|compliance officer|"
        r"právník|právnik|advokát|jurist|koncipient|"
        # Dutch `recht` is deliberately absent — it is inside **Utrecht**. Only the compounds
        # ship. That is the `georgia` rule in Dutch.
        r"avvocat|societari|"                                        # it
        r"advocaat|advocaten|notaris|notarieel|omgevingsrecht|arbeidsrecht|"  # nl
        # --- wave 2 ---
        # German `rechtsreferendar` in full: bare `recht` sits inside UTRECHT and inside
        # 102 Dutch legal titles.
        r"rechtsreferendar|"                                            # de-2
        r"juridisch|"                                                   # nl-2
        r"abogad|"                                                      # es-2
        # PT. Swedish `juridik`/`juridisk` is `jurid-i-s-k` and does not match `...dic`.
        # --- Norwegian, 2026-08-11 (N33) — graded against NAV STYRK-08 ---
        r"advokat|juridisk|"
        # --- wave 3 (2026-08-17) ---
        # en-14  en — roles-en-w3.md
        r"\bcontracts?\s+(?:manager|specialist|administrator|analyst|lead|director)\b|"
        r"jur[íi]dic", re.I)),                                          # pt
    ("customer_support", re.compile(
        r"customer (?:support|service|care)|help ?desk|technical support|support specialist|"
        r"it[- ]?support|service desk|zákaznick|kundtjänst|kundservice|podpora zákazn|"
        r"supporttekniker|first[- ]line|kundbokare|bokningsmedarbetare|kundinformatör|"
        r"centralin|assistenza\s+client|supporto\s+client|relazion\w*\s+client|"  # it
        r"servizio\s+client|"
        r"kundenbetreu|kundensupport|"                                          # de
        r"atenci[óo]n a(?:l)? (?:cliente|p[úu]blico)|"                          # es
        r"klantenservice|klantcontact|servicedesk|klantadviseur|"               # nl
        r"obs[łl]ug\w* klienta|"                                                # pl
        # --- wave 2 --- bound: bare `client`/`gestione` is meaningless alone, and
        # `sales` must keep running BEFORE this so a `commerciale` title is not
        # swallowed by the customer-relations reading.
        # --- wave 3 (2026-08-17) ---
        # cs-09  cs — roles-cs-w3.md
        r"(?:it|ict)\s*[- ]?\s*podpor|uživatelsk\w+\s+podpor|"
        r"^(?!.*(?:síť|servis|manažer|manažér|nákup)).*technick\w+\s+podpor|"
        # de-02  de — roles-de-w3.md
        r"kundenservice|"
        rf"{_IT_ROLE}[^|]{{0,20}}?(?:gestione|consulenza)\s+client",    # it-2
        re.I)),
    ("operations", re.compile(
        r"\boperations\b|customer success|supply chain|procurement|strategic sourcing|"
        r"office manager|"
        r"provozn|nákupčí|nákupca|inköpare|"
        r"acheteu|approvisionn|\bachats?\b|"                                    # fr
        r"\bacquisti\b|"                                                        # it
        r"eink[äa]uf|"                                                          # de
        r"operacion|operaci[óo]n|compras|comprador|"                            # es
        r"inkoop|inkoper|"                                                      # nl
        r"zakup|"                                                               # pl
        r"am[ée]lioration continue|excellence op[ée]rationnelle|"        # fr-2
        # --- wave 2 --- English words the file already knows in other languages.
        r"\bbuyer\b|purchasing|facilit(?:y|ies) manager|"               # en
        r"continuous improvement|operational excellence|\blean\b|"
        # --- wave 3 (2026-08-17) ---
        # sv-01  sv — roles-sv-w3.md
        r"verksamhetsutvecklare|"
        # sv-03  sv — roles-sv-w3.md
        r"upphandl|"
        r"(?:demand|supply|material|capacity) plann?(?:er|ing)", re.I)),
    # Residual for a business function at a tech company that none of the above names.
    # Deliberately last of the business group and much narrower than it was.
    ("other_tech_function", re.compile(
        r"business analyst|\bcontent\b|community|partnerships|strategy|"
        # Clerical data entry ("Data Entry Clerk", "Remote Data Entry", ~226 postings): no data
        # pattern above reads it (they all want engineer/analyst/scientist/science), so it falls
        # here to the admin residual, which is what it is.
        r"data entry|"
        r"administrativ|asistent|assistent|koordinátor|koordinator|"
        # English admin titles: the CZ/SE spellings above never matched "Executive Assistant".
        r"(?:executive|administrative|office|personal) assistant|"
        r"assistant\w*[ .]?e?\s+de\s+direction|"                                # fr
        r"segretari|segreteria|"                                                # it
        r"sachbearbeiter|kaufmann|kauffrau|kaufleute|"                  # de
        # --- wave 2 ---
        r"partnership|business process|"                                # en
        # --- Norwegian, 2026-08-11 (N38) — graded against NAV STYRK-08 ---
        r"\bsekretær|"
        # --- wave 3 (2026-08-17) ---
        # sv-05  sv — roles-sv-w3.md
        r"projektadministratör|"
        # en-16  en — roles-en-w3.md
        r"implementation (?:consultant|manager|specialist|lead)|"
        # en-17  en — roles-en-w3.md
        r"chief of staff|"
        # en-18  en — roles-en-w3.md
        r"technical writer|\bdocumentation\s+(?:specialist|manager|lead|engineer)|"
        # it-01  it — roles-it-w3.md
        r"analista\s+funzional|"
        # it-02  it — roles-it-w3.md
        r"amministrativ|"
        # es-02  es — roles-es-w3.md
        r"analista\s+funcional|"
        r"administratie", re.I)),                                       # nl-2
)

UNCATEGORISED = "uncategorised"

#: Every valid value of `role_category`, including the fallback. Rows are never dropped for
#: being `uncategorised` — it is a first-class value and useful for auditing the taxonomy.
CATEGORIES: tuple[str, ...] = tuple(c for c, _ in PATTERNS) + (UNCATEGORISED,)

#: Membership set for the guard in `classify`. Derived, never a second list — a copy is how
#: the value written to `postings.role_category` and the value a subscriber may ask for drift
#: apart, which is the whole reason this module is the single definition.
_VALID: frozenset[str] = frozenset(CATEGORIES)


def classify(title: str | None, hint: str | None = None) -> str:
    """Classify a job title into a role_category.

    Title patterns win. When none match, fall back to the source-provided profession
    `hint` (e.g. a jobs.cz "Marketing" field), which rescues localised CZ/SK titles the
    regexes still miss. Only `uncategorised` when neither fires.

    **The hint must already be a value in `CATEGORIES`; anything else is discarded.** It used
    to be returned verbatim, and five adapters were passing a raw third-party string —
    platsbanken's Swedish SSYK label, workable's employer-typed department, startupjobs' field
    slug, recruitee's `category_code`, oraclecloud's `JobFamily`. That put 14 135 of 98 858
    active postings (14%, measured 2026-08-08) into a category no subscriber can select and no
    query can match, reachable only through the keyword half of the recall predicate. Nothing
    reported it: the dbt `accepted_values` test runs against `stg_job_postings.sql`'s own SQL
    `case`, which never sees a hint.

    Mapping a raw label to a real category is a per-source curation job (see
    `smartrecruiters.FUNCTION_HINTS`, `themuse.CATEGORIES`) and belongs in the adapter, where
    the source's vocabulary is known. Here the only safe answer is `uncategorised`: not better
    at matching, but it means "unknown" to every consumer rather than naming a category that
    does not exist.
    """
    text = title or ""
    for category, pattern in PATTERNS:
        if pattern.search(text):
            return category
    return hint if hint in _VALID else UNCATEGORISED


# ------------------------------------------------------------------- retrieval ---
# Keywords for the cheap full-text prefilter that builds a subscriber's shortlist. These
# are search terms, not classifiers: recall-first, bilingual, and deliberately broader
# than the regexes above, because the AI matcher does the precision afterwards.
SHORTLIST_KEYWORDS: dict[str, list[str]] = {
    "data_engineering": ["data engineer", "analytics engineer", "datový inženýr", "etl", "dbt"],
    "data_analysis": ["data analyst", "bi analyst", "analytik", "power bi", "reporting"],
    "machine_learning": ["machine learning", "ml engineer", "data scientist", "data science",
                         "ai engineer", "strojové učení"],
    "cybersecurity": ["security engineer", "security analyst", "cybersecurity", "infosec",
                      "penetration test", "soc analyst", "informationssäkerhet",
                      "kybernetická bezpečnost"],
    "science_research": ["research scientist", "scientist", "laboratory", "clinical research",
                         "biologist", "chemist", "r&d", "vědecký pracovník"],
    "engineering": ["mechanical engineer", "electrical engineer", "civil engineer",
                    "process engineer", "ingenjör", "konstruktér", "konstruktör",
                    "strojní inženýr"],
    "software_engineering": ["software engineer", "developer", "vývojář", "programátor",
                             "backend", "frontend", "fullstack"],
    "devops_platform": ["devops", "sre", "platform engineer", "cloud engineer",
                        "kubernetes", "administrátor"],
    "product": ["product manager", "product owner", "produktový manažer", "produktový vlastník"],
    "design": ["designer", "designér", "ux", "ui", "grafik", "návrhář"],
    "social_media": ["social media", "sociální sítě", "sociálních sítí", "sociálne siete",
                     "community manager", "influencer", "content creator", "smm",
                     "instagram", "tiktok"],
    "marketing": ["marketing", "marketingový", "seo", "brand", "copywriter", "kampaň"],
    "sales": ["sales", "obchodní", "obchodník", "prodejce", "säljare", "account manager",
              "business development", "butik"],
    "finance_accounting": ["finance", "účetní", "účetnictví", "controller", "audit",
                           "redovisning", "ekonomiassistent"],
    "hr_recruiting": ["recruiter", "personalista", "nábor", "human resources", "hr",
                      "rekryterare"],
    "legal": ["legal", "lawyer", "právník", "advokát", "counsel", "compliance", "jurist"],
    "customer_support": ["customer support", "zákaznická podpora", "help desk", "it support",
                         "kundtjänst", "supporttekniker"],
    "operations": ["operations", "provozní", "supply chain", "nákup", "customer success"],
    # The Swedish terms here are stems and plurals as the register writes them, for the same
    # reason the patterns above are: retrieval that only knows the singular does not find the
    # ad. Recall-first — the AI matcher does the precision afterwards.
    "healthcare": ["nurse", "sestra", "zdravotní sestra", "lékař", "sjuksköterska", "läkare",
                   "undersköterska", "fysioterapeut", "psykolog", "hemtjänst",
                   "fyzioterapeut", "pečovatelka"],
    "social_care": ["social worker", "social work", "sociální pracovník", "socialsekreterare",
                    "socionom", "youth worker", "support worker"],
    "education": ["teacher", "učitel", "učitelka", "lärare", "pedagog", "lektor",
                  "förskollärare", "förskola", "elevassistent"],
    "hospitality": ["kuchař", "číšník", "recepční", "kock", "servitör", "barista",
                    "restaurant", "restaurang", "servering"],
    "skilled_trades": ["elektrikář", "svářeč", "instalatér", "zámečník", "elektriker",
                       "svetsare", "mekaniker", "electrician", "welder", "servicetekniker"],
    "construction": ["stavbyvedoucí", "stavební", "construction", "byggledare", "snickare",
                     "zedník", "träarbetare", "målare"],
    "logistics_transport": ["skladník", "řidič", "logistika", "warehouse", "lagerarbetare",
                            "truckförare", "chaufför", "lager", "förare"],
    "manufacturing_production": ["výrobní", "operátor výroby", "produktionstekniker",
                                 "maskinoperatör", "cnc", "montážní", "operatör", "montör"],
    "other_tech_function": ["business analyst", "koordinátor", "asistent", "administrativa"],
}

# ---------------------------------------------------------------------- display --
#: What a human calls each category in a digest subject line.
SUBJECT_WORDS: dict[str, str] = {
    "data_engineering": "data engineering",
    "data_analysis": "data",
    "machine_learning": "ML",
    "cybersecurity": "security",
    "science_research": "science",
    "engineering": "engineering",
    "software_engineering": "engineering",
    "devops_platform": "platform",
    "product": "product",
    "design": "design",
    "social_media": "social media",
    "marketing": "marketing",
    "sales": "sales",
    "finance_accounting": "finance",
    "hr_recruiting": "HR",
    "legal": "legal",
    "customer_support": "support",
    "operations": "operations",
    "healthcare": "healthcare",
    "social_care": "social care",
    "education": "education",
    "hospitality": "hospitality",
    "skilled_trades": "trades",
    "construction": "construction",
    "logistics_transport": "logistics",
    "manufacturing_production": "production",
    "other_tech_function": "tech",
}

# ------------------------------------------------------------------------- CV ----
# Substring rules for reading a role out of an uploaded CV. Deliberately a SUBSET of the
# categories: a CV is prose, and the broad patterns above would fire on almost any tech CV
# ("engineer" appears in most of them), producing a profile that asks for everything. These
# few are the ones a CV states clearly enough to act on. Kept here rather than in
# cvparse.py so the divergence from PATTERNS is visible next to what it diverges from.
CV_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("data_engineering", ("data engineer", "analytics engineer", "dataops", "etl",
                          "data platform", "data pipeline")),
    ("machine_learning", ("machine learning", "ml engineer", "ai engineer",
                          "data scientist", "mlops", "deep learning")),
    ("data_analysis", ("data analyst", "bi analyst", "business intelligence",
                       "reporting analyst", "insights analyst")),
    # Unambiguous in prose: nobody writes "social media manager" on a CV by accident, and
    # without this a social/community CV reads as no role at all (see the "Detected: r" bug).
    ("social_media", ("social media", "sociální sítě", "sociálních sítí", "sociálne siete",
                      "community manager", "influencer marketing", "content creator")),
)
