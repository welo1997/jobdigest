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

#: The words that turn a **service/operations technician** into an IT job. Swedish
#: `drifttekniker` has carried this guard since the sv-19 wave; its Norwegian cognate
#: `driftstekniker` and the German `servicetechniker` shipped bare beside it, so
#: "IT-driftstekniker" and "IT Servicetechniker Onsite Support" both read as skilled trades.
#: Same class as the software lookahead on the fr/es/it engineer words — a decision the file
#: had already made, not applied to the sibling — so it is a **constant**, not a third copy:
#: this file has already watched a duplicated word list drift when one copy lost a term and
#: only the regression list noticed.
#:
#: Deliberately NOT applied to `elektroniker`/`mechatroniker`. *Elektroniker für Informations-
#: und Systemtechnik* is a German apprenticed trade whose own title names IT, so the guard
#: would refuse the profession it exists to keep — and neither branch shows a misfile today.
_NOT_IT = r"^(?!.*(?:devops|\bit\b|linux|windows|server))"

#: The engineering disciplines that turn a bare "engineer" into a non-software one. Written
#: once because it is now read twice — once with the discipline BEFORE the head, once after —
#: and a duplicated word list in this file has already drifted when one copy lost a term.
#: `mechanics` is the English NOUN (the science): "Rock Mechanics Engineer", "Solid Mechanics
#: Engineer", "Design Engineer Mechanics" — three titles `mechanical|mech\b` cannot read,
#: and the same word `skilled_trades` had to stop claiming as a tradesman.
_ENG_DISCIPLINE = (r"(?:mechanical|mechanics|mech\b|electrical|electronics?|civil|structural|"
                   r"process|chemical|automotive|aerospace|industrial|manufacturing|hardware)")

#: The guard both arms carry. `software engineer|developer`, never bare `software`: bare also
#: released "Smart Manufacturing Engineer, Software", which is engineering.
_NOT_SOFTWARE = (r"^(?!.*(?:software (?:engineer|developer)|full[- ]?stack|"
                 r"customer (?:support|success)))")

#: The reversed arm reads a TRAILING qualifier, so it has one failure the forward arm cannot
#: have: **a head that already names its own function**, with a discipline word arriving later
#: as scope rather than as the job. Measured on the ~186 postings the reversed arm moves, every
#: false positive is that one shape — "Sr. Frontend Engineer - Process Modeling Team", "Android
#: Engineer: IPP Hardware", "Sr. Forward Deployed Engineer (FDE) - Manufacturing", "Sr.
#: Solutions Engineer - Public Sector (Defense Industrial Base)" (the discipline is inside the
#: words *Industrial Base*), "Sales Engineer, Retail and Manufacturing", "Technical Support
#: Engineer - Electronic Access Systems".
#:
#: The last two are the ones worth stating, because they are not software: `sales` and
#: `customer_support` both run AFTER `engineering`, so those titles were answered correctly
#: only because nothing earlier claimed them — widening an earlier pattern is exactly how a
#: later category loses a row it already had. This file already writes the same rule from the
#: other side, as `software_engineering`'s `(?<!sales )(?<!support )` lookbehind stack.
#:
#: Naming the heads costs 0 correct rows and is not an opinion about any of them: each one
#: keeps exactly the answer it has today. `solutions engineer` in particular is listed to
#: PRESERVE the status quo, not to settle it — see the open question in the misfile ledger.
#: Bare `software` IS in this list, and that is the one place it may be: `_NOT_SOFTWARE` above
#: cannot be bare (it would release "Smart Manufacturing Engineer, Software", which is
#: engineering) — but that title is answered by the FORWARD arm, where the discipline leads, so
#: the reversed arm never sees it. What the reversed arm does see is "Software Functional Safety
#: Engineer – Automotive", where `software engineer` is not adjacent and `automotive` trails.
#: The existing test for it caught this; the guard is why it still passes.
#: "<domain> quality" is not industrial quality, and `engineering` carries the guard for it
#: TWICE — once on `quality engineer`, once on the wave-3 `quality (manager|specialist|…)`
#: fragment, whose comment says "same guard as above" and then repeats the list. It is a
#: constant on 2026-08-26 for the reason this file keeps learning: the second copy had already
#: fallen out of step by omission, and the fix below had to be made in two places to work.
#:
#: `software` is the new word. Eight postings of "Software Quality Engineer" (also "Software
#: Design Quality Engineer", "Software Quality Engineering Platforms Leader") were reaching
#: `engineering` through a guard written for exactly this class that had three of its four
#: words. The `[^|]{0,8}` window is what keeps "Quality Engineer, Data Center" and a plant
#: title that happens to mention AI where they are — the word has to sit ON `quality`.
_NOT_DOMAIN_QUALITY = r"^(?!.*(?:\bdata|\bai|\bml|\bsoftware)\b[^|]{0,8}qualit)"

_HEAD_ALREADY_BOUND = (r"(?!.*(?:software|frontend|front[- ]end|backend|back[- ]end|android|"
                       r"\bios\b|forward deployed|solutions? engineer|data annotation|"
                       r"sales engineer|support engineer))")

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
        # `\bgp\b` is two letters and the corpus holds exactly four titles carrying it: two
        # real Norwegian GP posts and two where GP is a business unit or a company suffix
        # (`GP-Supply Chain and Quality Management`, `Senior Software Engineer … Scale GP`).
        # Bounded is not enough for an abbreviation — `georgia` in two letters.
        r"caregiver|midwife|^(?!.*(?:software|engineer|supply chain)).*\bgp\b|"
        r"surgeon|radiolog|"
        # `pečovat` carries the same academic guard, for the same reason one register over:
        # `UČITEL/KA ODBORNÝCH PŘEDMĚTŮ PRO OBOR VZDĚLÁNÍ Pečovatelské služby` teaches the
        # care trade, it does not practise it. (2026-08-26)
        r"sestra|sestry|zdravotn|lékař|lékárn|zubní|ošetřovatel|"
        r"^(?!.*(?:učitel|odborného výcviku)).*pečovat|"
        r"skötersk|läkare|tandläkare|barnmorska|vårdbiträde|\bvårdare\b|vårdsamordnare|"
        # Norwegian/Danish nurse (SE `skötersk` does not read these): sykepleier / sygeplejer(ske).
        r"sykepleier|sjukepleier|sygeplejer|"
        # `terapeut` covers fysio-, arbets-, psyko- and samtalsterapeut in both languages.
        # `farmaceut(?!yczn)` — the lookahead must live on THIS token, not on a second copy
        # added later in the alternation: a bare `farmaceut` anywhere in the pattern still
        # matches *farmaceutyczny*, so a duplicate-with-guard fixes nothing. The Polish
        # adjective is how a pharma **sales** rep advertises, and it filed as healthcare.
        # `(?<!milj[øö])terapeut` — settled by NAV's own coding, not by argument, the way
        # `miljøarbeider` was. Of the 10 `miljøterapeut` rows in the Norwegian answer key,
        # **7 are ISCO 3412 and 1 is 2635 — both social_care — 1 is 5321 (healthcare) and 1
        # is a publisher mis-code (3331, forwarding agents)**. That is 8-1, not the 5/4/3
        # coin flip that made `miljøarbeider` a decline, so the word moves to `social_care`
        # below rather than staying a therapy word. A milieu therapist works in residential
        # child welfare and psychiatry; the `terapeut` stem was reading the suffix only.
        r"(?<!milj[øö])terapeut|sjukgymnast|psykolog(?!i)|farmaceut(?!yczn)|apotekare|"
        r"tandhygienist|tandvård|"
        r"logoped|audionom|"
        # NO/DK `dyrlege` and `dyreplei` are unreachable from Swedish `veterinär`, and
        # `[äæ]` covers the Danish/Norwegian spelling of the Latin word. Academic guard for
        # the same reason as `fastlege` below: healthcare runs before education, so a
        # "Stipendiat i veterinærmedisin" is a PhD. 14 corpus postings, +1/+1 on the
        # committed key and +9/+9 on the refreshed one.
        r"^(?!.*(?:stipendiat|postdoktor|førsteamanuensis|professor)).*veterin[äæ]r|"
        r"dyrlege|dyreplei|djursjukskötare|hemtjänst|äldreomsorg|sjukvård|"
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
        # `therapeut(?!ic)` — the English pharma-industry noun. `Therapeutics` and
        # `Therapeutic Area` name a drug modality or a business unit, never the profession
        # *therapist*, and they filed five Workday science and pharma-exec roles as
        # healthcare (`Experienced Scientist, Biotherapeutics RD`). Same class as the
        # `farmaceut(?!yczn)` guard three lines above: the sector adjective is how a
        # non-clinical role advertises. Dutch `therapeutisch` (`Forensisch therapeutisch
        # medewerker`) is untouched — `is` is not `ic`.
        r"huisarts|tandarts|verloskundige|therapeut(?!ic)|zorgmedewerker|"
        # `psychiater` beside `psycholoog`: the Dutch psychiatrist was never read, so five
        # NIFP / prison-psychiatry postings depended on `marketing`'s `\bppc\b` declining
        # them to become uncategorised rather than healthcare. The narrowing over there and
        # this widening have to ship together, or the fix trades a misfile for a decline.
        r"zorgco[oö]rdinator|zorgassistent|zorgkundige|psycholoog|psychiater|"
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
        # The 2026-08-22 additions are the same rule reaching English and Swedish, where
        # `Dental` is a BRAND and an INDUSTRY word (`Aqua Dental`, `Snø Dental`, `West
        # Dental`, `Dental B2B`) rather than the Spanish adjective this token was written
        # for: `Receptionist till West Dental`, `Campus Recruiter - Dental Hygiene`,
        # `Sales Development Representative (SDR) — Dental B2B`, `Lead DSO Dental Billing
        # Success Consultant` and `Norsktalande kundservicemedarbetare inom dental` were
        # healthcare. `receptionist` is the English/Swedish spelling of the `recepcionista`
        # already here, and the other four are the intruder professions themselves.
        r"^(?!.*(?:recepcionista|receptionist|professional|recruiter|billing|"
        r"kundservice|\bsales\b)).*(?:cl[íi]nic[oa]s?|\bdental(?:es)?)\b|"
        # The academic guard four of its siblings already carry (`veterin[äæ]r`,
        # `fastlege|allmennlege|legesenter|legekontor`, `ambulansearbeid|paramedisin`).
        # `odont[óo]log` was the one place it was not copied, and healthcare runs before
        # education, so a dental-school post filed as a dentist. 3 of 4 wrong, fix costs
        # 0 — the one correct row names no academic rank. (2026-08-26)
        r"^(?!.*(?:stipendiat|postdoktor|førsteamanuensis|professor|doktorand)).*odont[óo]log|neur[óo]log|psic[óo]log|farmac[ée]utic|cirug[íi]a|cirujan|"
        # NL. The closed compound `zorgmedewerker` already ships; this is the OPEN half
        # of the same word. Three guards, each measured: `\b` before `medewerk` keeps
        # `Beleidsmedewerker zorginkoop` (a policy officer) in operations; `{0,2}` filler
        # words rather than `\w*` stops the gap reaching across a whole title; and
        # `zorg\b` keeps `Medior adviseur zorghuisvesting` (care FACILITIES) out.
        r"\bmedewerk(?:er|ster)s?\s+(?:\w+[\s&-]+){0,2}zorg\b|"         # nl-2
        # --- Norwegian, 2026-08-11 (N1, N2, N3, N4, N5, N6, N7, N8, N9) — graded against NAV STYRK-08 ---
        r"helsefagarbeid|s[yj]ukeplei|verneplei|\blege\b|(?:over|fast|tann|øye|fylkes|sykehjems|bedrifts|kommune|smittevern|turnus)lege\b|tannplei|tannhelse|tannklinikk|helsesekretær|legesekretær|farmasøyt|jordmor|hjelpepleier|pleiemedarbeider|pleieassistent|omsorgsarbeider|sykehjem|omsorgssenter|hjemmetjeneste|heimeteneste|"
        # **`bioingeni[øo]r` is a HEALTHCARE profession, and it was filing as `engineering`.**
        # A Norwegian *bioingeniør* is an autorisert biomedical laboratory scientist — hospital
        # labs, medical biochemistry, cytology, pathology — and `engineering`'s broad `ingeniør`
        # was taking all of it: 27 corpus postings and **5 of 5 NAV-keyed rows**, which is the
        # `healthcare -> engineering` entry the scorer reports. Placed HERE rather than guarded
        # in `engineering`, because healthcare runs first: the same shape as `dataingeni[øo]r`
        # living in `data_engineering` to pre-empt the identical broad stem.
        #
        # **Norwegian/Danish spelling only, on purpose.** Swedish `bioingenjör` (the `j`) is a
        # biotech ENGINEER and not a protected title; only `-ingeniør` names the licensed
        # laboratory profession. One vowel apart, opposite answers — the `dataingenjör` /
        # `dataingeniør` lesson, arriving from the other direction.
        r"bioingeni[øo]r|"
        # `ortopedingenjör` / `ortopedtekniker` — the Swedish sibling of the line above, and the
        # ONE finding in this pass with no keyed row behind it: a *legitimerad ortopedingenjör*
        # is a licensed (Socialstyrelsen) prosthetist-orthotist who fits devices to patients,
        # and all 11 corpus postings are clinics — Ottobock Care, ForMotion, "ortopedtekniskt
        # centrum", "Ortopedingenjör/Ortopedteknisk patientmottagare". Bound to the two
        # professional titles rather than a bare `ortoped`, which would also read the ward name
        # in a non-clinical ad and the device INDUSTRY's own sales roles — the `m[ée]dico`
        # adjective trap two dozen lines down. Evidence is the licence and the corpus, not a
        # register row; drop this fragment first if the pass is trimmed.
        r"ortopedingenj|ortopedtekni|"
        # --- Norwegian, 2026-08-22 — graded against a refreshed NAV STYRK-08 key ---
        # `fastlege` could only ever match the bare word behind a boundary, and the register
        # advertises the *position*: fastlegevikariat, fastlegehjemmel, fastlegeavtale,
        # fastlegeliste, fastlegepraksis. It was refusing 44 live postings of the commonest
        # Norwegian doctor word. The academic guard is this file's own
        # `^(?!.*receptionist).*vårdcentral` idiom, and it is not hypothetical: healthcare
        # runs before education, so "Stipendiat i allmennmedisin ... på fastlegekontoret" is
        # a PhD, not a GP. Measured free — the guard costs 0 rows on all five keys.
        r"^(?!.*(?:stipendiat|postdoktor|førsteamanuensis|professor))"
        r".*(?:fastlege|allmennlege|legesenter|legekontor)|"
        # Clinical professions this file knows in Swedish and never learned in Norwegian:
        # `radiograf` (15 postings), `ernæringsfysiolog` — SE `dietist` (7), `audiograf` —
        # SE `audionom` (3), `psykiater`, of which only `psykolog` ever shipped (3), and the
        # paramedic (4), whose university posts need the same guard as the vet's.
        r"radiograf|ernæringsfysiolog|audiograf|psykiater|"
        r"^(?!.*(?:stipendiat|postdoktor|førsteamanuensis|professor|lektor|lærer))"
        r".*(?:ambulansearbeid|paramedisin)|"
        # `koordinator` belongs to `other_tech_function`, where bare it is a genuine spread
        # — 56 NAV postings over 50 titles, no plurality, ranging from Pasientkoordinator to
        # Meeting & Events Koordinator — so the WORD stays declined and only the compounds
        # NAV itself codes as clinical are taken. STYRK 2221/2263/5321.
        # `forløpskoordinator` was in this list and is NOT shipped: 0 rows in 136 417 corpus
        # postings and 0 in either key bucket. Plausible and inert is how two wave-3
        # fragments came to ship dead.
        r"pasient\w*koordinator|kreftkoordinator|demenskoordinator|"
        # `miljøarbeider` — settled on the refreshed NAV key (2026-08-23), not the small one that
        # made it a decline. On the bigger key 22 of 27 code it healthcare (5321/5329 pleie-/
        # omsorgsarbeid), so the earlier 5/4/3 three-way split does not reproduce. Healthcare runs
        # before social_care and education, which is the majority answer here. Both spellings.
        r"milj[øö]arbeider|"
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
        # `youth worker` needs the `grocery` guard: every instance in the corpus is
        # `Grocery Associate - Youth Worker`, where "Youth Worker" is a youth-HIRE
        # employment label on a retail shelf-stacking job, not the youth-support
        # profession. Three titles, four postings, and no correct instance to lose.
        r"social worker|social work|caseworker|^(?!.*grocery).*youth worker|"
        r"support worker|child protection|"
        r"sociální pracovn|sociáln[íy] prác|"
        r"socialsekreterare|socialarbetare|socialpedagog|\bkurator\b|behandlingsassistent|"
        # `ekonomiskt bistånd` is Sweden's municipal social-assistance casework, and the
        # word inside it is *ekonomi*. Most of these rows say "Socialsekreterare" and were
        # already answered here; the five that do not ("Handläggare ekonomiskt bistånd",
        # "Gruppledare till utredningsenheten på ekonomiskt bistånd") ran on to
        # `finance_accounting`'s `(?<!m)ekonom(?!ick)`, whose comment names this exact false
        # friend and left it to be caught somewhere. This is that somewhere: naming the
        # function here answers the rows instead of merely declining them.
        r"ekonomiskt bist[åa]nd|försörjningsstöd|"
        # `behandlingspedagog` / `st[öo]dpedagog` are the same decision as
        # `behandlingsassistent` one string to the left, and they were missed. Swedish
        # `-pedagog` is a CREDENTIAL suffix, not a workplace: education's bare `pedagog`
        # was filing 148 postings of LSS/SiS residential care as teaching (94
        # `stödpedagog`, every one naming gruppbostad / servicebostad / BmSS / daglig
        # verksamhet / funktionsstöd; 54 `behandlingspedagog`, every one SiS, LVM-hem or
        # HVB). Named HERE rather than guarded in education because social_care runs
        # first: positive detection, and the ~500 genuine `pedagog` postings
        # (`Pedagogisk leder`, `Specialpedagog`, `Speciální pedagog`) are untouched.
        r"behandlingspedagog|st[öo]dpedagog|"
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
        # `assistent`/`nattassistent`/`kvällsassistent` titles, not bounded here because
        # `assistent` is a wider blast radius than this field warrants.
        #
        # **The claim that those 200 were "left as declines" was false, and is corrected here
        # (2026-08-22).** They were never declines: `other_tech_function`'s own unbounded
        # `assistent` claimed every one of them — `Nattassistent till yngre tjej i Gråbo` and
        # `Aktiv och engagerad kvällsassistent sökes` both classify `other_tech_function`
        # today. That matters twice over. No chip maps to that bucket, so the rows are
        # reachable by nobody; and `ingest._classify` consults the model cache **last and only
        # on a decline**, so a confident wrong answer meant the residue this comment described
        # as a decline was never once put to the classifier. A residual bucket does not decline
        # — it answers, silently and unreachably.
        r"personlig[at]?\s+assistent(?:er)?|personlig\s+assistans|"
        # `begeleider` is a generic Dutch role head ("supervisor/handler"); the existing
        # lookbehind shows the collision was known for one compound. Three more are
        # administrative, claims-handling and research jobs at Dutch central government,
        # and all three are 100% wrong in the corpus. (2026-08-26)
        r"(?<!uitvoerings)(?<!zaak)(?<!project)begeleid(?:st)?er|maatschappelijk werk|jeugdhulp|jongerenwerk|"      # nl
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
        # 2026-08-22. Moved OUT of healthcare's `terapeut` stem — see the guard there for
        # NAV's 8-1 coding. Removing a word is not the same as moving it (the `roles-nl-2`
        # / `gehandicaptenzorg` lesson), so it has to land here or the 119 postings (86
        # titles) it carries fall to `uncategorised`. Both spellings: NO/DK `miljø`, SE
        # `miljö`. The other 26 of the word's 145 postings name a nurse or vernepleier too
        # and stay `healthcare` — which is the row NAV coded 5321.
        # `barnekoordinator` is Norway's statutory coordinator for a child with complex
        # needs, not an admin job; `familie`/`ungdomsveileder` are the two `veileder`
        # compounds that name a profession — bare `veileder` is 48 postings spread across
        # everything and stays declined, exactly like bare `technicien`.
        # `miljøveileder` — social_care 8 of 10 on the refreshed NAV key (2026-08-23), coded 3412
        # (Miljøarbeidere innen sosiale fagfelt). Distinct from `miljøarbeider` above, which the
        # same key codes healthcare — healthcare runs first, so the two coexist by order.
        r"milj[øö]terapeut|milj[øö]veileder|barnekoordinator|(?:familie|ungdoms)(?:veileder|rettleiar)|"
        # 2026-08-22. Swedish `-pedagog` is a CREDENTIAL suffix, not a workplace: it marks a
        # pedagogical qualification, and Swedish residential-care employers hire qualified
        # pedagogues. `education`'s bare `pedagog` was therefore filing 150 postings of
        # disability, addiction and youth residential care as education — 143 titles, and
        # **not one of them is a school**: every one names LSS, gruppbostad, servicebostad,
        # BmSS, daglig verksamhet, funktionsstöd, HVB, SiS, LVM-hem, ungdomshem,
        # socialpsykiatri or kvinnoboende (a grep for skola/förskol/gymnasi/elev/undervis
        # over all 143 returns 0).
        #
        # A POSITIVE detection here beats a negative guard there, measured both ways:
        # `social_care` runs before `education`, so naming the three compounds moves them to
        # a category a subscriber can select, while `(?<!behandlings)(?<!st[öo]d)pedagog` in
        # `education` drops 143 of them to `uncategorised`, leaks 2 to `other_tech_function`,
        # and still misses `Pedagogisk samordnare (stödpedagog) till Annelöv gruppbostad`,
        # which matches on its second `pedagog`.
        #
        # SWEDISH ONLY, and Norwegian is the reason — the `personlig assistent` false-friend
        # rule again. NO `støttepedagog` is a *barnehage* support pedagogue
        # (`Støttepedagog 50% stilling i barnehage`, `Spesialpedagog/støttepedagog`) and
        # stays `education`. `stötte` is not `stöd`.
        #
        # `Stödpedagog/Stödassistent` titles keep `healthcare`: that pattern runs first and
        # owns `stödassistent`, which is the standing (undecided) boundary call, not this one.
        r"st[öo]dpedagog|behandlingspedagog|boendepedagog|"
        # --- wave 3 (2026-08-17) ---
        # sv-07  sv — roles-sv-w3.md
        r"arbetskonsulent|"
        # cs-02  cs — roles-cs-w3.md
        # `slu[žz]e?b`, not `služb`: Czech inserts a fugitive **e** in the genitive plural, so
        # *sociálních SLUŽEB* — the form a municipality uses for its social-services DEPARTMENT
        # — could never match the stem written for *služby*. Four postings, two of them
        # uncategorised and two deferred into `healthcare`. Same class as `mechani[kc]` and
        # `inžený`: this register writes the inflected form far more often than the citation
        # one, and a stem taken from the dictionary entry reads the wrong half of the corpus.
        r"sociáln\w*\s+slu[žz]e?b|"
        # cs-03  cs — roles-cs-w3.md
        r"sociáln[ěe]\s*[- ]?\s*práv\w*\s+ochran|"
        r"trabajador[a-z]*(?:/[ao])?\s+social|animaci[óo]n\s+sociocultural", re.I)), # es-2
    ("education", re.compile(
        r"teacher|lecturer|professor|educator|kindergarten|preschool|"
        # **"AI Tutor" is a PRODUCT an engineer builds, not a teaching job** —
        # "Principal AI Engineer - AI Tutor" (4 postings) and "The quest build a better
        # AI tutor". Same mechanism as the `\btrainer\b` guard below; positional, so
        # "Physics Tutor" and "1 on 1 Math Tutor" are untouched.
        r"(?<!\bai )(?<!\bai-)\btutor\b|"
        r"teaching assistant|"
        r"učitel|učitelka|vychovatel|pedagog|lektor(?!ov)|docent|vysokoškolsk[ýá] uči|"
        r"lärare|barnskötare|studie- och yrkesvägledare|"
        # **`förskol` is a WORKPLACE, not a profession (2026-08-26).** Swedish municipalities
        # advertise preschool KITCHEN and facilities jobs with "förskola" in the title, and
        # `education` runs before `hospitality`, so every one of them was claimed here. The
        # proof is that the same job at a plain `skola` was already right: `Kock till Lunda
        # skola` classified `hospitality` while `Kock till Lunda förskola` classified
        # `education` — same job, same municipality, one prefix. 40 wrong of 308, 29 of them
        # catering. Cost zero: no live title pairs a teaching word with a kitchen word, and
        # `Förskollärare`, `Timvikarie förskola` and `Resurs till förskola` are untouched.
        r"^(?!.*(?:kock|köksb|köksans|måltid|grovarbetare)).*förskol|"
        r"elevassistent|elevresurs|studiehandledare|fritidspedagog|"
        r"skolvikarie|lärarvikarie|husvikarie|doktorand|amanuens|forskare|utbildare\b|"
        # Coaching and instructing is education's nearest true home; SSYK files it there too.
        r"instruktör|\btränare\b|dansledare|"
        # `\bformation\b` is bounded because unbounded it eats "information". It is the one
        # French term here with a live English risk: `education` runs 3rd, so a future English
        # "Formation …" title would misfile. `formateur|formatrice` is the safe subset if that
        # ever shows up.
        # The English risk this comment predicted has ARRIVED — 11 of the span's 21 live
        # postings are English engineering: battery-cell "Formation & Ageing"
        # (Manufacturing/Automation Engineer, 7), CT "Image Formation" (2), "Formation
        # Process Engineer", "Subcontract Formation Specialist". Bound to the French
        # preposition or role head instead of taken bare — the `_FR_ROLE` construction —
        # which keeps all 10 real ones ("Responsable de Formation", 8x "Consultant.e en
        # Formation", "Spécialiste Formation") and drops all 11. `formateur|formatrice`,
        # the escape this comment named, would have lost all 10 as well.
        r"formateur|formatrice|\b(?:de|en)\s+formation\b|"
        r"\b(?:sp[ée]cialiste|responsable|charg[ée]{1,2})\s+formation\b|"      # fr
        r"ausbilder|"                                                           # de
        r"leerkracht|onderwijsassistent|pedagogisch medewerker|kinderopvang|"   # nl
        r"\bleraar\b|onderwijzer|"
        # --- wave 2 -------------------------------------------------------------
        # `\btrainer\b` / `\binstructor\b` follow the file's own `instruktör` decision:
        # instructing IS the profession. Measured on the union this takes 12 rows from
        # other categories (`Sales Trainer`, `Cybersecurity Technical Trainer`) rather
        # than the 3 the English proposal predicted on its own corpus. Accepted as the
        # same call the file already made, with the true count recorded here.
        # **"AI Trainer" is not a teaching job — the trainee is a model.** 11 live postings
        # are RLHF/data-annotation gigs ("AI Trainer Freelance Data Annotator",
        # "Freelance Annotator (English) - AI Trainer", "Generalist AI Trainer $35
        # hour"); "AI Tutor" is a product an engineer builds ("Principal AI Engineer -
        # AI Tutor", 4). 16 postings. The guard is positional, not whole-title, so
        # "Junior KI-Projektbegleiter & Trainer" and every Sales/Technical Trainer the
        # wave-2 note deliberately accepted are untouched.
        r"\binstructor\b|(?<!\bai )(?<!\bai-)\btrainer\b|"           # en
        # `\bonderwijs` bounded away from `onderwijsinstelling` (the institution, not a
        # teaching role). ES `profesor` has one s and English `professor` cannot reach it.
        r"\bonderwijs(?!instelling)|"                                   # nl-2
        # --- Norwegian, 2026-08-11 (N13, N14, N15, N16, N17) — graded against NAV STYRK-08 ---
        r"lærer|lærar|barnehage|ungdomsarbeider|ungdomsarbeidar|\bforsker|\bforskar|stipendiat|postdoktor|førsteamanuensis|\brektor|\bsfo\b|"
        # --- Norwegian, 2026-08-22 ---
        # `undervisningsstilling` is how a Norwegian school advertises a teaching post
        # without ever writing `lærer` (19 postings, 19 distinct titles). `postdoctoral` is
        # the English half of the already-shipped `postdoktor`, and it earns its place beyond
        # Norway — 23 of its 28 corpus rows are English-language academic boards, and five
        # were misfiled by their topic word (a `Postdoctoral Research Fellow in Algal
        # Microbiome Engineering` was reading as software_engineering).
        r"undervisningsstilling|postdoctoral|"
        # --- Norwegian, 2026-08-22 ---
        # `undervisningsstilling` is how a Norwegian school advertises a teaching post
        # without ever writing `lærer` (19 postings, 19 distinct titles); `postdoctoral` is
        # the English half of the already-shipped `postdoktor`, and it earns its place
        # beyond Norway — 23 of its 28 corpus rows are English-language academic boards,
        # and five of them were misfiled by their topic word (a `Postdoctoral Research
        # Fellow in Algal Microbiome Engineering` was reading as software_engineering).
        r"undervisningsstilling|postdoctoral|"
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
        # `ekonomibiträde` is a kitchen assistant — the *ekonomi* is the household kind.
        # `måltidsbiträde` beside it was already read; the sibling was not.
        r"måltidsbiträde|ekonomibiträd|måltidsservice|kostchef|gatukök|värdinna|"
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
        # `(?<!re)` because RECONSTRUCTION is not construction: it took 6 postings, led by
        # three "Johnson & Johnson MedTech, Aesthetics and Reconstruction" plastic-surgery
        # sales reps, plus an accident-reconstruction engineer, a 3D-reconstruction engineer
        # and the European Bank for Reconstruction and Development. No corpus title uses the
        # word for building work. `quantity surveyor` replaces bare `surveyor`: the bare word
        # is a SHIP inspector far more often than a construction one here — 22 of 31 postings
        # are marine/classification-society surveys (FiS, Newbuilding, Maritime, "Surveyor
        # Ships In Operation"), against 9 quantity-surveying rows.
        # **`construction` is also a SECTOR, and this pattern runs 4th of 28.** So every role in
        # the industry answered "construction" before its own category could: measured
        # 2026-08-22, 21 postings carry a function word a LATER pattern owns and gets right —
        # `Sales Specialist, Construction`, `Inside Sales, Coatings & Construction`,
        # `Business Development - Construction`, `Construction Data Analyst`, `Senior Director,
        # Global Design and Construction Data Analytics`, `Android Developer (Construction
        # Tech)`, `Applied Scientist, New Construction`, `Global Procurement Specialist-
        # Capital Equipment and Construction`. `construction equipment` is separate and is the
        # employer-name mechanism: Volvo CE's welder, fitter and forklift driver (5 postings)
        # are manufacturing and logistics, and the phrase means MACHINERY, never building work.
        #
        # A whole-title lookahead is used here and it is the narrow choice, not the lazy one: a
        # lookbehind cannot work because the function word sits at the far end of the title
        # ("Sales Specialist, Construction"). `\bsales\b` is bounded against SALESFORCE for the
        # reason `software_engineering` records; `data analy`/`analytics` are written as two
        # strings because "Data Analytics" and "Data Analyst" share no usable stem.
        #
        # **Only the ENGLISH branch is guarded.** The 66 postings with no rival claimant —
        # `Construction Manager`, `Construction Worker`, `Construction Project Manager` — must
        # keep answering construction, and dropping the branch outright would send several to
        # `software_engineering`'s bare `\bengineer\b` instead (`Construction Engineer`,
        # `Operations Engineer (Construction)`), which is a worse misfile, not a fix.
        #
        # `(?<!re)` because RECONSTRUCTION is not construction: 6 more postings, led by three
        # "Johnson & Johnson MedTech, Aesthetics and Reconstruction" plastic-surgery sales
        # reps, plus an accident-reconstruction engineer, a 3D-reconstruction engineer and the
        # European Bank for Reconstruction and Development. No corpus title uses the word for
        # building work.
        r"^(?!.*(?:\bsales\b|business development|data analy|analytics|"
        r"\bdeveloper\b|software|scientist|procurement|construction equipment))"
        r".*(?<!re)construction|"
        # `quantity surveyor` replaces bare `surveyor`: the bare word is a SHIP inspector far
        # more often than a construction one here — 22 of 31 postings are marine /
        # classification-society surveys (FiS, Newbuilding, Maritime, "Surveyor Ships In
        # Operation", "E&I Surveyor - Vung Tau"), against 9 quantity-surveying rows. The one
        # piece of collateral is `Utsättare/surveyor`, a Swedish setting-out surveyor, which
        # becomes a decline — the cheap error, not the expensive one.
        # `site manager` needs the same treatment `surveyor` got, for the same reason: the
        # English word "site" is not only a building site. `(?<!on-)(?<!on )` is the staffing
        # sense — an *On-Site Manager* runs an agency's people at a client's premises ("UK -
        # On-Site Manager (Manchester area)") — and the four named contexts are the rest of the
        # branch's 12 wrong rows: an events company's fan zones, a DHL Supply Chain warehouse,
        # and a "Site Manager (Remote Positon)", which cannot be a building site at all.
        r"^(?!.*(?:\bevents?\b|fan zone|supply chain|remote))"
        r".*(?<!on-)(?<!on )site manager|"
        r"bricklayer|carpenter|quantity surveyor|estimator|"
        # Czech. Stems, because the register writes the plural: "Zedníci", "Dělníci". The
        # nominative singular this pattern used to require matched almost none of them.
        r"stavbyvedoucí|stavebn|výstavb|zedn|tesař|dlaždič|kamnář|potrubář|"
        # `štukatér`/`omítkář` (plasterers) and `malíř` (painter) — recurring in the ISCO key's
        # construction misses; the register files a house painter as construction, not a trade.
        r"natěrač|lakýrník|pokrývač|obkladač|izolatér|lešenář|betonář|štukatér|omítkář|malíř|"
        r"byggledare|byggnadsarbetare|snickare|murare|"
        # `snickeri` is the joinery WORKSHOP, and `snickare` on the same line already reads the
        # joiner. What the workshop noun adds is a CNC operator, two assembly fitters, and a
        # checkout assistant at the *Willys Oskarshamn Snickeriet* supermarket counter.
        r"^(?!.*(?:butiksmedarbetare|\bcnc\b|montör|operatör)).*snickeri|"
        # **`platschef` is a site manager of ANY site, and it was the largest single misfile
        # left in this pattern**: 130 postings, of which 70 are gyms (SATS, five Indoor Golf
        # branches), car workshops (Werksta ×2, Ford, Däckia, Autoexperten, MECA), retail
        # (Byggmax ×2 — a builders' merchant is a shop), restaurants, a hotel, a plastics
        # works and a tool factory. The word means "the manager who is on the premises", and
        # `construction` runs SEVENTH, so it answered for all of them first.
        #
        # Bound POSITIVELY, the way `_FR_ROLE` and `_DE_ROLE` bind their role nouns, rather
        # than by excluding the employers: a gym chain, a golf franchise and a tyre shop are an
        # unbounded list, and the same lesson closed `receptionist` in the ledger. What is
        # bounded is the work — this list is the construction vocabulary the corpus's own
        # correct rows use.
        #
        # The 15 bare "Platschef" rows become `uncategorised`, which is the honest answer: the
        # title genuinely does not say. That is the cheap error — an uncategorised row still
        # reaches the AI matcher, a misfiled one never does.
        r"platschef[^|]{0,60}?(?:bygg|anlägg|\bmark\b|entreprenad|\brot\b|prefab|betong|"
        r"väg\b|\bva\b|stambyte|nyproduktion|bostad|järnväg|infrastruktur|gräv|asfalt|"
        r"utemiljö|renover|fastighet|block)|"
        r"(?:bygg|anlägg|\bmark\b|entreprenad|\brot\b|prefab|betong|väg\b|\bva\b|stambyte|"
        r"nyproduktion|bostad|järnväg|infrastruktur|gräv|asfalt|asphalt|utemiljö|renover|"
        r"fastighet|building|construct)[^|]{0,60}?platschef|"
        r"anläggningsarbetare|anläggare|rörläggare|byggarbetare|byggprojektledare|"
        # Norwegian: `anlegg` (civil works) compounds — "Anleggsleder", "Anleggsarbeider".
        r"anleggsleder|anleggsarbeider|"
        r"stensättare|plattsättare|betongarbetare|betonghåltagare|takläggare|"
        r"träarbetare|markarbet|grävmaskinist|hjullastar|"
        # `maskinförare` is a machine operator of any machine. The named contexts are the ones
        # the corpus puts somewhere else entirely — snow clearing, mowing, waste, recycling,
        # a ski lift, a harbour, a lorry cab. `grävmaskinförare` and `hjulgrävare` keep coming
        # through this fragment, which is where the branch's real construction rows live.
        # Employer names (SSAB's steelworks, Boliden's mine) are NOT listed: that list has no
        # end, and the same rule is why `receptionist` and `platschef` are bound to the work.
        # **The answer key overturned the first draft of this guard and it is worth recording
        # why.** `snöröj` and `gräsklipp` were in it, on the reasoning that clearing snow and
        # mowing grass are not building work — and SSYK keys "Lofsdalens Fjällanläggningar
        # söker snöröjare/maskinförare" as *construction*. The register files the operator by
        # the MACHINE, not by what the machine is doing that season, and `avfall`/`återvinning`
        # would almost certainly have gone the same way. Only three contexts survive: a ski
        # lift, a harbour and a lorry cab, none of which is an earthmoving machine at all.
        r"^(?!.*(?:liftsköt|hamnarbetare|lastbil)).*maskinförare|"
        r"ventilationsmontör|ventilationstekniker|kyltekniker|isoleringsmontör|"
        # **`VVS-montör` is the third sibling of the two `-montör` words already on this line,
        # and it was the one filing as factory work.** Heating/ventilation/sanitation
        # installation is done on a building site; `manufacturing_production`'s broad `montör`
        # was taking 71 of the corpus's 82 VVS postings. Three keyed rows, two registers'
        # worth of agreement and no counter-example: SSYK files `VVS montör` in the group
        # "VVS-montörer m.fl." under the field "Bygg och anläggning", and NAV codes `VVS Montør`
        # and `Rørlegger/VVS-Montør` **STYRK 7126** — which `ISCO_CATEGORIES` already maps to
        # construction. That last row was reaching `skilled_trades` via `rörmokare`, so this
        # line fixes it too, and the test asserting otherwise moves with it.
        # `v+s` because the trade abbreviates itself both ways — "VVS-montör" and "VS-montör"
        # are the same job, and 12 corpus postings use the short form.
        #
        # **Deliberately VVS alone, not `-montör` compounds in general.** The register does not
        # agree with itself about the rest: bare `Montör` is keyed construction, skilled_trades
        # AND manufacturing on different rows, and `solcellsmontör` (solar panels — the compound
        # that looks most like this one) is keyed **manufacturing_production**. Widening this to
        # every installation compound would be arguing with the publisher, not reading it.
        r"\bv+s[\s-]?mont[öø]r|"
        # `betong` (concrete) and `måleri` (painting) — the bare stems the SSYK misses needed:
        # "Renovering av betong", "Måleri", "Projektledare inom betong". `målar` already read
        # "målare" but not the noun "måleri".
        r"ställningsmontör|ställningsbyggare|putsare|golvläggare|målar|"
        # All three of the bare stems below name a MATERIAL or a WORKSHOP, not a profession,
        # and each leaks in its own direction. Guards are role heads, never employer names.
        #
        # `betong` — 18 of 62 postings are somebody else's job at a concrete company:
        # six mixer-truck drivers ("Betongbilschaufför", "Betongbilsförare"), seven precast
        # and ready-mix FACTORY roles ("Produktionspersonal till betongfabrik",
        # "Prefabtillverkare Betong till fabrik", "Betongblandare"), and three rows that are
        # simply the employer *Thomas Betong* attached to a salesman and a planning head —
        # `ekonom`/*Mekonomen* in a third dress. `logistics_transport` and
        # `manufacturing_production` both run AFTER construction, so only a decline here
        # releases them.
        # `projektledning` was in this list and the key took it back out: "Chef projektledning
        # till Thomas Betong (Prefeb)" is keyed *construction*. Running the projects IS the
        # construction job even when the employer is the concrete works — only the driving,
        # the factory floor and the selling belong elsewhere.
        r"^(?!.*(?:chaufför|förare|fabrik|tillverkare|blandare|säljare|\bsales\b|"
        r"produktionsarbetare|produktionspersonal|processtekniker))"
        r".*betong|"
        # `måleri` is the paint SHOP: an industrial paint line ("Industriarbetare till måleri",
        # "Enklare målerijobb i prefabfabrik", "Produktionsledare till måleriet") is factory
        # work, and a *Målerikonservator* restores paintings in a museum. `målar` above still
        # reads the painter, which is the profession this fragment was added for.
        r"^(?!.*(?:konservator|industriarbetare|prefabfabrik|produktionsledare))"
        r".*måleri|"
        # `\brivning` — the boundary is the whole point, and `\brivare\b` beside it shows the
        # author already knew: unbounded, `rivning` reads the INSIDE of Swedish compounds that
        # have nothing to do with demolition. It took 9 postings, all of them somebody else's
        # job: utsk-RIVNING-ssamordnare (a hospital discharge coordinator, 4 postings + 2
        # more), Insk-RIVNING-shandläggare (an enrolment officer) and framd-RIVNING (marine
        # propulsion, 2 engineers). Every genuine demolition title has a boundary in front of
        # it — "Rivningsarbetare", "inom rivning", ", rivning" — so the fix costs nothing.
        r"hantverkare|\brivning|\brivare\b|"
        # `\bobras?\b` is bounded, and the answer key is why: unbounded, `obra` matches the
        # Czech `Obráběč/ka kovů` (a machinist, truth `manufacturing_production`), and
        # construction runs first. `(?<!werktuig)bouw` keeps Dutch *werktuigbouwkunde*
        # (mechanical engineering) out of construction for the same ordering reason.
        r"bauleit|"                                                             # de
        r"construcci[óo]n|edificaci[óo]n|\bobras?\b|"                           # es
        # `(?<!machine)` and `(?<!land)` are the same lesson as `(?<!werktuig)`, arriving twice
        # more: Dutch `machinebouw` is MACHINE building — 18 postings, every one of them a
        # "Software Engineer Machinebouw" (SCADA/PLC/Siemens), and `landbouw` is AGRICULTURE
        # (a jurist on common agricultural policy). A `-bouw` compound only means construction
        # when the thing being built is a building.
        # `(?<!mijn)(?<!ge)` added 2026-08-26 — the documented `-bouw` lesson arriving through
        # two more doors the lookbehind list did not cover: **`mijnbouw`** is mining
        # (`Bureausecretaris Commissie Mijnbouwschade` is the mining-damage commission's
        # secretary) and **`gebouwde`** is the adjective *built*, where `bouw` is a mid-word
        # substring (`Adviseur Energie Innovatie Gebouwde Omgeving`). Neither is building work.
        r"(?<!werktuig)(?<!machine)(?<!land)(?<!mijn)(?<!ge)bouw|"
        r"uitvoerder|werkvoorbereid|\bcalculator|timmerman|"  # nl
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
        # `(?<!bra )` — a *Bra Fitter* is a retail customer assistant, not a trade;
        # one UK chain supplies all three ("Customer Assistant - Bra Fitter - Ipswich").
        # The `250 CZK/hr` mechanism again: an English trade word inside a garment term.
        r"electrician|welder|plumber|\bmechanic\b|locksmith|(?<!bra )\bfitter\b|"
        # **`hvac` shipped bare and HVAC is an industry, not a job.** It is the same shape as
        # the `\bengineer\b` misfile: a domain word running 16 patterns ahead of `sales` and
        # before `engineering`, so it answered first for every role in the sector. Measured
        # 2026-08-22: 161 postings, and the majority were somebody else's — 30+ `HVAC …
        # Engineer` rows (Building Controls Systems, Application, Commissioning, Design,
        # Thermodynamic), ~15 `HVAC … Sales`/`Account Executive`, `HVAC Truck Base Customer
        # Resource Coordinator` (a call desk), `Software Developer (m/f/d) HVAC and Heat Pump
        # Systems`, `Director, Marketing - HVAC/Controls N.A.`, `Construction Data Analyst`'s
        # cousin `HVAC Product Management Lead`.
        # Bound BOTH directions, because the corpus writes it both ways: "HVAC Technician" and
        # "Technician, HVAC Service". `tech\b` earns its place separately from `technician` —
        # this publisher abbreviates ("HVAC Sr Controls Service Tech", "HVAC Data Service Tech
        # Team Lead"). The heads are the ones the rest of this pattern already treats as
        # trades, so nothing new is being decided about what a trade is.
        rf"hvac[^|]{{0,28}}?(?:technician|tech\b|mechanic|fitter|apprentice|journey|"
        rf"installer|operative|tester)|"
        # The reverse branch needs the same abbreviation and the same three languages as the
        # forward one: without them "Senior Tech, Maintenance HVAC", "Facilities Tech II, HVAC
        # Maintenance" and the French "Technicien Utilités CVC / HVAC H/F" were three real
        # trades left uncategorised by the narrowing.
        rf"(?:technician|technicien|techniker|tech\b|mechanic|fitter|installer)"
        rf"[^|]{{0,30}}?hvac|"
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
        r"elektrikář|elektrikár|"
        # `elektrotechni` is a FIELD word, and this pattern runs before
        # `manufacturing_production`, so it answered first for electronics ASSEMBLY work: four
        # ISCO-keyed rows (`Operátor/ka v elektrotechnické výrobě` 82122, `Dělník v
        # elektrotechnice` 82121, `Dělníci v elektrotechnice` 82122, `dělnice ve výrobě - drobná
        # elektrotechnika` 82122) are production-line jobs the register codes in major 8.
        # `d[ěe]ln[ií]` and not `dělní` alone — "dělnice" carries no í, the `obr[áa]běč` lesson.
        # A `^(?!.*inžený)` guard was proposed for the two rows that lean the OTHER way
        # (`Specialista v elektronice a elektrotechnice` 3114, `vedoucí elektrotechnik` 21519)
        # and was measured at **0 key rows moved** — neither title contains the word. That half
        # is declined; the production half is what the key can see.
        # `(?!ek)` is the second, independent guard on the same fragment and it is a one-letter
        # LANGUAGE split, not a typo guard: Czech and German write the PERSON
        # `elektrotechnik`, Dutch writes the FIELD `elektrotechniek` and the person
        # `elektrotechnicus`. Unguarded, the Dutch field word took 11 postings of
        # engineering advisory work — "Adviseur elektrotechniek" (x5 plus Curacao, Aruba,
        # Defensiecomplexen), "Assetmanager elektrotechniek", "Lokaal Elektrotechniek
        # Verantwoordelijke". Dutch electrical INSTALLERS are read separately below
        # (`elektrotechnisch installat`), so nothing Dutch and trade-shaped is lost.
        # `inžený|projektant|konstrukté` is the re-measure the comment above invites, not a
        # re-argument of it: "0 key rows moved" was true of the ANSWER KEY and is no longer
        # true of the live corpus, where the register writes whole ISCO group names —
        # "Inženýři elektrotechnici a energetici projektanti, konstruktéři", "Elektrotechnici
        # a technici energetici projektanti, konstruktéři", "PROJEKTANT/KA- silnoproudá
        # elektrotechnika". Those are design and engineering titles that name the field, and
        # `engineering` runs later with the right answer.
        r"^(?!.*(?:d[ěe]ln[ií]|operátor|inžený|projektant|konstrukté))"
        r".*elektrotechni(?!ek)|"
        r"zámečn(?!a\b)|instalatér|montér|údržbář|"
        # `mechani[kc](?!al)` replaces `automechanik|mechanik`: the register writes the plural
        # "Mechanici"/"Automechanici"/"Elektromechanici", where k→c dodged the -k singulars.
        # The `(?!al)` lookahead is load-bearing — it keeps English "mechanical engineer" out
        # (that stays `engineering`, which runs later) while still reading "mechanic(s)".
        # `servisní technik` is the Czech service technician (bare `technik` stays declined).
        # `(?!al|z)` — `al` keeps English "mechanical engineer" out (that stays `engineering`,
        # which runs later); `z` was added 2026-08-10 for the Polish adjective *mechaniczny*,
        # which was filing two Polish engineering roles as trades.
        # Three more one-letter senses the `(?!al|z)` pair never anticipated, all measured
        # on live rows rather than argued: `s\b` is the English NOUN "Mechanics" — the
        # science, not the tradesman ("Rock Mechanics Engineer", "Solid Mechanics
        # Engineer", "Design Engineer Mechanics", "Delivery Manager (Biomechanics)");
        # `h` is the corpus's own misspelling of the adjective ("Mechanichal Engineer
        # R&D"); and `k[áéíý]` is the Czech adjective *mechanick·á/é/ý* ("Konstruktér/ka
        # - mechanická konstrukce"), which is a design job, not a repair one.
        # **Bare vowels are deliberately NOT in the set**: `mechaniky` is the accusative
        # plural of the tradesman ("Hledáme elektromechaniky a mechaniky") and blocking
        # `y` outright would refuse the profession. Only the `k`-stem adjective is wrong.
        r"mechani[kc](?!al|z|s\b|h|k[áéíý])|opravář|údržb|servisní technik|"
        r"elektriker|rörmokare|mekaniker|"
        r"servicetekniker|underhållstekniker|fastighetsskötare|"
        # `driftstekniker` (NO) is `drifttekniker` (SE) with one letter; it shipped bare
        # beside the guarded Swedish form, so "IT-driftstekniker" read as a trade.
        rf"{_NOT_IT}.*driftstekniker|"
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
        r"elektroniker|mechatroniker|"                                         # de
        rf"{_NOT_IT}.*servicetechniker|"                                       # de
        # **`meister` shipped BARE here, against this file's own `_DE_ROLE` doctrine ("`meister`
        # is also a title — which is the whole reason for the binding"), and it was the largest
        # single misfile in the corpus.** Measured 2026-08-22 over every active posting: bare
        # `meister` won 68 postings, of which **66 were wrong** — 65 `Hörakustik-Meister`
        # (a German hearing-care professional, one employer spamming ~60 towns) and one
        # `CNC-operatör – Siemens 840D / Gildemeister`, where the match is inside the
        # MACHINE-TOOL MAKER's name. The `Mekonomen`/`ekonom` mechanism exactly.
        # It won two correct postings, both `Hausmeister`, which is the German `vaktmästare`
        # two lines below — so the binding keeps that and drops the rest. Only `haus` is
        # evidenced in this corpus; the other four are unambiguous German trade compounds and
        # cost nothing measurable today. **`bau` is deliberately absent** — an Austrian/German
        # `Baumeister` is a master builder and `construction` runs first.
        r"instandhalt(?!ungsingenieur)|"
        r"(?:haus|elektro|industrie|anlagen|kfz)meister(?:in|innen)?\b|"
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
        rf"{_NOT_IT}.*drifttekniker|"
        # fr-02  fr — roles-fr-w3.md
        r"\br[ée]parat(?:eur|rice|ion)|"
        r"^(?!.*ingenier).*mec[áa]nic[oa]s?\b", re.I)),                 # es-2
    ("logistics_transport", re.compile(
        # A **data** warehouse is a database, not a building, and the two words are the same
        # word. Unguarded `warehouse` filed 18 postings of data-engineering work in logistics
        # — "Senior Solutions Architect (EDW Enterprise Data Warehouse Migrations)" alone is 7
        # — where `data_engineering` (which runs 155 lines later) had the right answer and
        # never got to see them. Both lookbehinds are needed: the spaced "Data Warehouse" and
        # the closed Swedish/Dutch "Datawarehouse" are different strings.
        # The second guard is the `\bengineer\b` lesson in one word: a warehouse is a *domain*
        # a robotics vendor sells into, and "Staff Software Engineer - Warehouse Platform" is
        # someone's software job, not warehouse work (16 postings across four employers).
        # `software|robotic|\bwms\b` only — deliberately NOT `automation`, which a real
        # warehouse can carry ("Automation Senior Manager, Skroutz Warehouse"): a measured 6
        # postings are left behind on purpose rather than guessed at.
        r"^(?!.*(?:software|robotic|\bwms\b))(?:.*)(?<!data )(?<!data-)(?<!data)warehouse|"
        r"forklift|truck driver|delivery driver|dispatcher|"
        r"logistics coordinator|"
        # `courier` and `freight` are both the delivery platform's BUSINESS LINE rather than
        # the vacancy — "Analytics Manager, Courier and Logistics", "Product Manager, Courier
        # Delivery Experience", "Courier HR Senior Specialist" — and `freight` is additionally
        # an employer name: *DHL Freight* appears in 11 of that branch's 18 titles, attached to
        # customer-service agents and two integration developers. The terminal workers and
        # forwarders at the same employer stay, because the guard names the head, not DHL.
        r"^(?!.*(?:analytics|analyst|\bhr\b|product manager|operations associate)).*courier|"
        r"^(?!.*(?:kundservice|kundtjänst|integrations|\bdeveloper\b|advisor)).*freight|"
        # `sklad(?!atel)` is the warehouse stem, and it replaces the singular-only
        # `skladník|skladnic`: the register writes the plural "Skladníci", where the k→c
        # declension dodged both (the same trap the comments above keep meeting). It covers
        # skladník/skladu/skladový; the lookahead keeps out `skladatel` (a composer). Then
        # dispatch/delivery: `expedic`/`expedien` (NOT bare `expedi`, which would eat the
        # Swedish retail "Expedit"), `rozvoz`, `dispečer`, `doplňovač`, customs `deklarant`.
        # `med logistikansvar` is a *duty attached to another job*, not the job: SSYK codes
        # "SÄLJARE MED LOGISTIKANSVAR TILL JYSK …" as *Butikssäljare, fackhandel* (6 postings,
        # one retail chain's template). The guard has to be this narrow — the register codes
        # bare "Logistikansvarig" as *Inköps-, logistik- och transportchefer*, so blanket
        # `logistik(?!ansvar)` would trade a right answer for a wrong one.
        # The Czech genitive turns both of these stems into a DEPARTMENT name: *skladu* /
        # *skladová* is "of the warehouse", so an accountant, an invoicing clerk, a chemistry
        # lab technician and a buyer all arrive with the warehouse in their title and none of
        # them works in it. `řidič/ka` is worse — the register appends it as a SECONDARY DUTY
        # to somebody else's job ("Technik/technička, řidič/ka", "Osobní asistent/ka +
        # řidič/ka") and once names the driver REGISTRY ("REFERENT REGISTRU ŘIDIČŮ"). That is
        # the class `med logistikansvar` two comments below already defines — a duty attached
        # to another job is not the job — applied to the two Czech fragments that needed it.
        r"^(?!.*(?:laborant|fakturant|administrativní|adm\. pracovník|it specialist|"
        r"ekonom|nákup|obchodní)).*sklad(?!atel)|"
        r"^(?!.*(?:technik|asistent|registru)).*řidič|"
        r"kurýr|spediter|"
        # `logistik` is a SECTOR word and it runs seventh, so every other function AT a
        # logistics company answered here first: 25 postings of purchasing, sales, account
        # management, engineering, recruitment, customer support, finance and — in one German
        # row — a commercial real-estate broker ("Senior Makler Logistikimmobilien"). The
        # `construction` pattern documents and guards this exact class ("construction is also a
        # SECTOR"); this fragment's only guard was the `med logistikansvar` duty rule below.
        # `utvecklare` is deliberately NOT in the list: a *Logistikutvecklare* improves flows,
        # and excluding it would hand three rows to `software_engineering`, which is a worse
        # answer than the one being fixed.
        r"^(?!.*(?:med\s+logistikansvar|säljare|\bsales\b|account manager|inköp|buyer|"
        r"kategoriledare|ingenjör|kandidatansvarig|forskningsassistent|kundsupport|"
        r"controller|redovisning|mäklare|makler))(?:.*)logistik|závozník|"
        r"expedic|expedien|rozvoz|dispečer|doplňovač|deklarant|"
        # Before manufacturing's `obsluha`: a forklift is materials handling, not production.
        r"manipulačn|vysokozdvižn|"
        # `\blager` is bounded at the start so *kullager* (a ball bearing) cannot reach it.
        # The two lookaheads are the `georgia` rule in Swedish: **Lagerhaus** is a home-decor
        # retail chain and **Lagerlöf** a surname (Selma Lagerlöfs Torg is a Gothenburg
        # square) — 6 postings of shop-floor sales and visual merchandising, none of them
        # warehouse work. Same mechanism as *Mekonomen* under `ekonom`.
        # **`truckkort` is a FORKLIFT LICENCE** — a qualification bolted onto another job, and
        # precisely the class the `med logistikansvar` guard on this pattern already defines
        # ("a duty attached to another job, not the job"). The rule was written down and never
        # applied here: `Produktionsarbetare med truckkort`, `Industriarbetare med truckkort`
        # and `Montör med truckkort` are all manufacturing work.
        #
        # **Guarded rather than deleted, and the answer key is why.** Deleting it outright cost
        # one graded SSYK row — `Operatör med truckkort | Lernia | Halmstad`, which the
        # publisher codes as logistics_transport, not manufacturing. A bare `Operatör` with a
        # forklift licence genuinely is warehouse work, so the register is right and the
        # tempting wider rule was wrong. Naming the three production heads keeps the register's
        # answer and still moves the rows it says nothing about: 0 graded losses. Settled by the
        # publisher's own coding rather than by argument, the way `miljøarbeider` was.
        r"^(?!.*(?:produktionsarbetare|industriarbetare|montör)).*truckkort|"
        r"\blager(?!haus)(?!löf)|chaufför|orderplockare|terminalarbetare|godsmottag|"
        # `förare` as a suffix: buss-, taxi-, lastbils-, skjutstativ-, motvikts-, båt-.
        # Everything a building site drives was claimed by `construction` one pattern up.
        # Two Swedish words end in -förare and drive nothing: **marknadsförare** is a marketer
        # (14 postings, and Arbetsförmedlingen codes "Digital marknadsförare" as
        # *Marknadsanalytiker och marknadsförare* — the register settles it, not an argument)
        # and **bokförare** is a bookkeeper (2). Positional, not whole-title: the compound is
        # the whole point, so only the two stems that precede it are excluded.
        r"(?<!marknads)(?<!bok)förare|brevbärare|paketbud|distributör|"
        # `\btaxi` names a company or a product as often as a vehicle: **TaxiCaller** is a
        # Swedish dispatch-software vendor whose web developers and support agents are not
        # drivers, and a **Taxi-app** is software (7 postings between them). Both lookaheads
        # are positional and read only the token itself — a title-level scan would also have
        # to decide about "Taxiförare till app-baserad åkeri", which is a driver.
        r"\btaxi(?!caller)(?!-app)(?! ?app\b)|bärgare|bärgning|"
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
        # `expediti` -> `expeditie`, the actual Dutch spelling. The language-agnostic stem
        # reintroduced the exact collision the CZECH fragment above was written to avoid — its
        # comment says "`expedic`/`expedien` (NOT bare `expedi`, which would eat the Swedish
        # retail 'Expedit')" — and it reached Swedish `expedition`, an office/registry, filing
        # a military registrar and a ministry's permanent-secretary assistant as logistics.
        # 2 of 2 wrong; no Dutch row is lost, the branch holds none today. (2026-08-26)
        #
        # `vrachtwagen` -> `vrachtwagenchauffeur`, bound to the driver exactly as `chauffeur`
        # and `buschauffeur` beside it already are: all three bare rows were the Dutch truck-
        # TOLL programme (`Vrachtwagenheffing`), every one an administrative post. 3 of 3.
        r"logistiek|expeditie|vrachtwagenchauffeur|trambestuurder|buschauffeur|"
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
        # **The English half needed the guard the French half already had.** The `fr-2`
        # `production` binding below carries `(?![^|]{0,24}(?:marketing|communication|
        # audiovisuel))` because "Chargé de production marketing" is not factory work — and the
        # English fragment on this line was taking the identical class of title with nothing:
        # "Touring Production Manager, Europe", "Sr. Esports Production Manager", "Video
        # Production Manager", "Event Production Manager", "Postproduction Manager" — 6 corpus
        # postings of media, live-events and campaign work in a shop-floor category. Anchored
        # rather than whole-title, on the `safety engineer` precedent a few hundred lines down:
        # "Production Manager Assembly & Test CMDS" and "Cables Production Manager" have to
        # survive, and they do — the guard names the media word and requires it to sit ON
        # `production`, so a plant title is untouched.
        r"^(?!.*(?:video|esports|touring|post|content|audio|film|campaign|creative|media|"
        r"broadcast|podcast|music|studio|theat|event)[\s-]?production)"
        r".*production (?:operator|technician|planner|manager|associate|supervisor|worker)|"
        r"machine operator|manufacturing (?:technician|associate|operator)|"
        # `assembly` shipped bare, ~150 lines ahead of `engineering` and ~700 ahead of
        # `product`, so it answered first for every engineering and product title that names
        # the shop floor as its DOMAIN: "Assembly Process Engineer", "Manufacturing Engineer
        # Assembly", "Ingénieur Process Assembly", "Device Assembly Process Engineer" —
        # 18 of the branch's 64 postings carry an engineer head — plus four rows of
        # "Mycronic PCB Assembly Solutions", which is a PRODUCT LINE, not a workplace
        # ("Product Manager to …", "Produktchef till …", "Senior Software Engineer to …").
        # Exactly the `analytics` verdict of 2026-08-22: a domain word does not outrank a
        # role head. The operators, technicians and supervisors this category is for name no
        # engineer, so the guard costs none of them.
        r"^(?!.*(?:engineer|engr\b|ingénieur|ingenjör|product manager|produktchef))"
        r".*assembly|"
        r"\bassembler\b|equipment installer|quality (?:inspector|technician)|cnc|"
        # **A certification auditor is quality assurance, not accountancy.** `audit(or)?` and
        # `revisor` live in one 60-alternative `finance_accounting` branch, and ~30 of that
        # branch's 209 postings audit a *management system* rather than a ledger: IATF 16949,
        # ISO 13485, IVDR, GMP, GFSI, PED/ASME, Global Gap, medical devices, food and beverage,
        # and the Swedish `kvalitetsrevisor` / `livsmedelsrevisor` that split the word exactly
        # the same way. It is fixed HERE rather than by guarding finance, because
        # `manufacturing_production` runs 370 lines earlier: giving the rows a destination is
        # the whole point, and a guard on finance alone would only have made them decline.
        # Bounded to the standard or the word `quality` sitting ON the audit word — "Senior
        # Vice President, Audit Leader, Audit Practice and Quality" is a bank's audit function
        # and stays where it is, which an unbounded `quality` would have broken.
        r"(?:quality|kvalit|\bqms\b|\bqes\b|iatf|ivdr|\bgmp\b|\bgfsi\b|iso ?\d|global gap|"
        r"medical device|food safety|livsmedels)[^|]{0,24}?(?:auditor|revisor)|"
        r"auditor[^|]{0,26}?(?:quality|kvalit|\bqms\b)|"
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
        # **The `obsluha` comment above was wrong, and the ISCO key is where it shows.** It
        # claimed the word is safe here "only because hospitality's `občerstven` and logistics'
        # `manipulačn` read their own senses of it first". They do not: `obsluha` is the ordinary
        # Czech word for *attending to* anything, and it steals **20 keyed rows** across four
        # other categories — sales 8 (`Obsluha čerpací stanice` ×3, `Obsluha zmrzliny`,
        # `pokladní, obsluha na čerpací stanici`, `PRODAVAČ…, obsluha grilu`, `Prodej zmrzliny a
        # obsluha zákazníků`, `Obsluha - koordinátor/ka prodeje`), construction 7 (`Obsluha
        # jeřábů`, `Obsluha zemních a příbuzných strojů` ×4, `Strojník, obsluha zemních…`,
        # `obsluha autojeřábu`), hospitality 3 (`Obsluha baru`, `Obsluha kavárny`, `Obsluha
        # kebabu`) and logistics 2. That is the largest single steal in this pattern, and it was
        # sitting under a comment asserting the opposite — verify a "what makes this safe" note
        # against a number, the `laborant` prefix lesson again.
        #
        # The OBJECT of `obsluha` decides, so the guard names the objects the register puts
        # elsewhere and leaves the word otherwise intact: `Obsluha CNC`, `Obsluha Laseru`,
        # `Obsluha balící linky`, `Obsluha speciálních strojů`, `obsluha betonárky` and
        # `Strojmistr - obsluha strojů` all still classify here. Declining is most of the win —
        # `sales` runs 500 lines later and construction/hospitality read none of these objects,
        # so 16 of the 18 rescued rows land `uncategorised`, which the AI matcher can still
        # save. A misfile it cannot. `bar[ou]\b` is bounded so it cannot reach `barvírna` (a dye
        # house); `recykla` is deliberately NOT listed — "obsluha recyklační linky" is a line
        # operator on either reading, and declining it would cost a right row to win an
        # arguable one.
        r"operátor|seřizovač|montážní|dělní|"
        # `výrob` is one stem over THREE Czech words and only one of them is this category:
        # *výroba* (production — right), *výrobek/výrobky* (a PRODUCT, so the title is usually
        # a salesman's) and *živočišná/rostlinná výroba* (animal and crop husbandry, which is
        # agriculture — a field `CATEGORIES` does not contain and must therefore DECLINE, not
        # approximate). 196 postings, ~29 of them one of the other two senses.
        # The guard names role heads and the two agricultural adjectives, and every one of them
        # is answered by something further down or by nothing: `finance_accounting` and `design`
        # both run after this pattern, `sales` runs 1 100 lines after it, and the husbandry rows
        # land `uncategorised`, which is the honest answer for work with no category.
        r"^(?!.*(?:obchodní|prodej|\bsales\b|živočišn|rostlinn|zemědělsk|zootechn|"
        r"grafi[kc]|ekonom))"
        r".*výrob|"
        r"^(?!.*obsluha[^|]{0,30}?(?:bar[ou]\b|kavárn|kebab|zmrzlin|čerpací|gril|"
        r"zákazník|prodej|jeřáb|zemních))"
        r".*obsluha|švadlen|šičk|nástroja|tiskař|lakovn|"
        # `obr[áa]běč`: the register writes it BOTH ways and the unaccented "Obraběč/ka kovů"
        # was landing uncategorised — found 2026-08-10 while mutation-checking the Spanish
        # `\bobras?\b` boundary, which is the collision this same row causes in the other
        # direction. The accented-only spelling is the near-miss this file's comments keep
        # warning about, one more time.
        r"obr[áa]běč|frézař|soustružník|brusič|lisař|balič|"
        r"truhlář|řezník|karosář|strojírensk|"
        r"produktionstekniker|produktionsmedarbetare|produktionspersonal|"
        # **Two unbounded stems that match INSIDE a longer, unrelated word.**
        # `(?<!mobil)operatör`: a *mobiloperatör* is a telecoms COMPANY, not a machine
        # operator, and "Sommarjobb hos Mobiloperatör! Fast lön & Rörlig" is a sales job — 6
        # corpus postings and **3 of 3 SSYK-keyed rows, all truth `sales`**. Same mechanism as
        # `ekonom` reading the employer *Mekonomen*: the word names who is hiring.
        # `(?<!be)ställare`: a *beställare* is a PURCHASER. "Verksamhetsutvecklare till
        # beställarenheten funktionsnedsättning" is a disability-services procurement post, and
        # `ställare` (a machine setter) reached it through the middle of the word.
        # Fixed-width lookbehinds, so both stay positional — nothing that genuinely names the
        # role is touched, which whole-title equality could not promise.
        r"produktionsarbetare|(?<!mobil)operatör|(?<!be)ställare|"
        # `svářeč` (CZ welder) sits here, not in skilled_trades, for the same reason `svetsare`
        # does: SSYK/ISCO file welders under industrial manufacturing (factory production), not
        # trades. The Swedish decision (2026-08-09) was never applied to the Czech word until now
        # — it cost 43 rows on the ISCO key that the classifier called skilled_trades. `zámečník`
        # stays a trade (fitter/locksmith), so this is `svářeč` alone, not the whole stem list.
        r"svářeč|"
        r"svetsare|\bsvets\b|montör|montør|"  # montør: Norwegian fitter
        # `montage`/`montering` are the Mekonomen mechanism in a second costume: Swedish
        # employers put them INSIDE their own names — "Dalarnas Takmontage", "Telos
        # Telemontage", "Centralmontage", "Boetsbildemontering" — so a salesman, an
        # administrator and an electrical designer were all filed as assembly workers.
        # A leading `\b` was the obvious fix and is the WRONG one: it also refuses
        # "Elmontage", "ytmontering" and "naturstensmontering", which are the real thing,
        # and it costs about as many rows as it wins. The employer name is not the signal —
        # the ROLE HEAD is, and this file already applies that rule to `construction`
        # ("construction is also a SECTOR") and to `logistik`. Named heads only; each one is
        # answered correctly by a category further down.
        r"^(?!.*(?:säljare|ordermottagare|administratör|konstruktör))"
        r".*mont(?:age|ering)|"
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
        # Dutch `datamodelleur` / `data modelleur`, both spellings. `engineering` used to
        # take them through a bare `modelleur` meant for a mechanical modelmaker; that
        # fragment is now guarded, and this is where the rows were supposed to land.
        r"datamodelleur|data modelleur|"
        # `dataingenjör` (SE) explicitly, so it is not swept up by engineering's broad
        # `ingenjör` a few patterns down — data_engineering is more specific and comes first.
        # **`dataingeniør` (NO) is the identical defect and was simply missed**: `engineering`
        # carries a broad `ingeniør`, so until 2026-08-11 a Norwegian data engineer filed as
        # generic engineering — the exact outcome the line above exists to prevent, one
        # vowel away. `[øo]` because a de-accented feed writes the bare o, the `obr[áa]běč`
        # lesson. Measured at 0 titles moved across 7 172 corpus and answer-key rows: the
        # compound is specific enough that it can only ever add.
        r"datov[ýá] inžen|dátový inžinier|data inžinier|dataingenjör|dataingeni[øo]r|"
        # The French form of the identical defect. `engineering` runs after this pattern and
        # carries a broad `ing[ée]nieur` whose software lookahead lists `logiciel|software|web|
        # développement|devops|full stack` — and not `data`, so "Ingénieur Data (H/F)",
        # "Ingénieur Datas H/F" and "INGÉNIEUR BIG DATA SENIOR F/H" (4 postings) filed as
        # physical engineering. Claimed HERE rather than added to that lookahead, because a
        # lookahead only declines: this is the pattern that should say yes. `datas?` — the
        # French ads pluralise it.
        r"ing[ée]nieur\w*\s+(?:big\s+)?datas?\b|"
        r"data architect|data modell?er", re.I)),
    ("machine_learning", re.compile(
        r"machine learning|\bml engineer|\bai engineer|data scientist|mlops|"
        # `applied scientist` is what Amazon, Adobe and Uber call an ML engineer/researcher
        # ("Staff Applied Scientist - VLLM Inference", "Applied Scientist, Ads
        # Optimization"). 20 live postings, all of which were filing as
        # `science_research` on its bare `\bscientist\b` — the modifier is not "AI", so
        # `\bai\b[^|]{0,20}(?:architect|scientist)` could never reach them. Named here
        # rather than guarded there, because this pattern runs first.
        r"applied scientist|"
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
        #
        # **Guarded 2026-08-26, and the guard is a copy — see `analytics` in `data_analysis`.**
        # `GenAI`, `generative AI` and `LLM` are DOMAIN words, not role words, and
        # `machine_learning` runs 300+ lines above `software_engineering`, `product` and
        # `marketing`, so the domain beat the profession and the profession was never asked.
        # That is verbatim the failure `data_analysis` measured and fixed for `analytics` four
        # days earlier, down to the example shape: `Senior Software Engineer, Data Analytics`
        # classified `software_engineering` while `Senior Software Engineer GenAI Enterprise
        # Applications` (7 postings) classified `machine_learning`. 85 of 152 postings were
        # wrong — `GenAI Product Manager`, `Head of Field Marketing and Events, Gen AI`,
        # `Java Backend Engineer (AI/LLM Chatbot, Customer Service)`, `Cloud Engineer GenAI`.
        #
        # Zero collateral, the same profile the `analytics` guard measured: every excluded
        # title names its own profession outright, so each lands on a named neighbour rather
        # than on `uncategorised`. The ML-systems senses are kept POSITIVELY on the line below
        # rather than by weakening the guard — `LLM inference` and `GenAI platform` are ML
        # infrastructure whoever writes them.
        r"llm (?:inference|infrastructure|platform|training|serving)|"
        r"gen ?ai (?:platform|infrastructure|inference)|"
        r"^(?!.*(?:software engineer|fullstack|full[- ]stack|backend|back[- ]end|"
        r"cloud engineer|product manag|product owner|program manager|marketing|"
        r"business analyst)).*(?:large language model|\bllms?\b|generative ai|\bgen ?ai\b)|"
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
        # **Security is an INDUSTRY VERTICAL as well as a profession, and this guard closes
        # that (2026-08-26). It is a copy** — `finance_accounting` carries
        # `\bfinanc(?:e|ial)\b(?!\s+services?\b)` for exactly the same reason, and the proof
        # that the lesson had not travelled sat side by side: `Senior Financial Services
        # Analyst` classified `uncategorised`, while `Senior Account Executive - Cybersecurity`
        # classified as a security engineer.
        #
        # This pattern runs 11 ahead of `product`, `sales`, `marketing`, `partnerships` and
        # `legal`, so a security vendor's account executive, product manager, product marketer
        # and general counsel all landed here and the function their title names was never
        # asked. Measured over the live corpus: 38 wrong, and with the pattern removed they go
        # product 20, sales 12, marketing 3, legal 2, partnerships 1 — and exactly ONE to
        # `uncategorised`. Near-zero collateral, because each names its own profession outright.
        # The French branch was 8 of 9 wrong (89%) on one repeated deputy-product-manager ad.
        #
        # **`solutions engineer` and `sales engineer` are deliberately NOT in the guard**:
        # `Field Security Specialist (Cyber Security Solutions Engineer)` and `Sales Engineer
        # (Application Security)` are genuine security pre-sales and must stay.
        r"^(?!.*\b(?:account (?:executive|director|manager)|accountmanager|"
        r"product (?:manager|owner|marketing)|product manager adjoint|"
        r"business development|general counsel|territory manager|partner development)\b).*"
        r"(?:"
        # `cyb(?:er)? sec` — one Workday tenant abbreviates the discipline as well as the head
        # ("Advanced Cyber Sec Archt/Engr", "Sr Advanced Cyb Sec Archt/Engr"), which
        # `cyber ?security` and `security (?:engineer|architect)` both miss, so 27 postings of
        # security architecture were filed as SOFTWARE by the bare `\bengineer\b` catch-all.
        # Same lesson as `engr` in `engineering`: an abbreviation defeats every guard built on
        # the spelled-out word.
        r"cyber ?security|\bcyb(?:er)?\s+sec\b|"
        r"information security|infosec|\bappsec\b|application security|"
        r"security (?:engineer|analyst|architect|specialist|consultant|operations|engineering)|"
        r"penetration test|pentest|red team|blue team|\bsoc analyst\b|"
        r"threat (?:intelligence|hunting|detection)|vulnerability (?:management|analyst)|"
        # `(?<!ng-)` guards ONE vendor's product line: CrowdStrike's "NG-SIEM" is a product,
        # and the jobs on it are platform and data engineering. Six of eleven postings were
        # wrong and five said "DevOps" outright in the title — the misfile was legible in the
        # title itself. Cost zero: no correct SIEM row in the corpus carries the prefix.
        r"(?<!ng-)\bsiem\b|security operations cent|"
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
        # nl. Added 2026-08-26 alongside the `analist` narrowing in `data_analysis`: that
        # fragment shipped bare and was shadowing this category in Dutch, so binding it there
        # has to be paired with the named sense here, or `Security Analist` becomes a decline
        # instead of a security role. Same construction as the French line above it.
        r"security analist|beveiligingsanalist|"
        r"ciberseguridad"                                               # es-2
        r")", re.I)),
    # Science / R&D — the applied, industry science the ATS boards carry (pharma, life sciences,
    # labs), added 2026-08-10 (~300 uncategorised). AFTER machine_learning so "Data Scientist"
    # stays ML; academic research (`forskare`, `doktorand`) stays in education on purpose — this
    # is the industry bench, not the university. `\bscientist\b` is safe here because every
    # data/ML sense of it was already claimed above.
    ("science_research", re.compile(
        # **Two senses of `scientist` were NOT "already claimed above", and the comment
        # said they were.** Measured on the live corpus 2026-08-22: `computer scientist`
        # 13 postings — an Indian-IT / US-federal way of writing *software developer*
        # ("Computer Scientist 2 (Full Stack)", "Computer Scientist - Java,
        # Microservices") — and `decision scientist` 9, which is marketing/CX analytics.
        # (`applied scientist`, 20, is the third and is claimed by machine_learning
        # above, which runs first — positive detection wherever a home exists.)
        # A negative lookbehind, not whole-title equality: "Computational Biology
        # Scientist" and "Beamline Scientist at Bloch beamline" must keep matching.
        r"(?<!computer )(?<!decision )\bscientist\b|research scientist|"
        r"clinical research (?:associate|scientist|coordinator)|"
        r"\bbiologist\b|\bchemist\b|physicist|microbiolog|biochemist|pharmacolog|toxicolog|"
        r"bioinformatic|laboratory scientist|lab scientist|"
        # `biomedicinsk analytiker` is the Swedish LICENSED hospital-laboratory profession, and
        # it is the same word as `analytiker` — so `data_analysis`, 86 lines later, was filing
        # clinical-chemistry and blood-bank staff as data analysts: 62 postings across 57
        # titles, every one of them a lab ("till Klinisk kemi", "Blodcentralen", "till klinisk
        # patologi"). The publisher's own SSYK group is "Biomedicinska analytiker m.fl." in the
        # field *Naturvetenskap*, which `categorization_score.py` holds OUT_OF_SCOPE — so the
        # answer key is silent here and the corpus is the evidence, exactly as expected for an
        # internal boundary. `\w*\s*` covers all three live spellings: `Biomedicinsk
        # analytiker`, `Biomedicinska analytiker` (plural) and `Biomedicinskanalytiker` (one
        # word). This is a lab bench, so it belongs with the other lab professions here rather
        # than in `healthcare` — the same call `laboratory scientist` above already records.
        r"biomedicinsk\w*\s*analytiker|"
        r"vědecký pracovník|výzkumný pracovník|"
        # --- wave 2 ---
        r"clinical (?:research|trials?)|medical science liaison|"       # en
        # **A TEST lab is not a science lab.** Nine of this fragment's 22 postings are
        # engineering test benches — "OSV Reliability Lab Technician", "Hardware & Testing
        # Lab Technician", "Mechanical and Thermal Laboratory Technician", "Prototyping Lab
        # Technician, Robotics", "Quality Lab Technician" — where the lab is where you break
        # the product, not where you research one. **These rows DECLINE rather than land
        # anywhere** — checked, not assumed: `manufacturing_production` runs earlier and its
        # `quality (inspector|technician)` needs the two words adjacent, and `engineering`'s
        # discipline arms all require an `engineer` head. Uncategorised is still the cheaper
        # error, since the AI matcher sees an uncategorised row and never sees a misfiled one.
        r"^(?!.*(?:reliabilit|\brel \blab|hardware|testing|thermal|prototyping|"
        r"quality|mechanical)).*lab(?:oratory)? technician|regulatory affairs", re.I)),
    # Before `data_analysis` on purpose. The original reason was that `data_analysis` ended in
    # a bare `\banalyst\b`, so "Financial Analyst" landed in data analysis — a subscriber asking
    # for data work got finance roles, and one asking for finance got nothing. **That bare
    # fragment is gone (2026-08-22), so the ordering now only matters for the block below**
    # (`accounts payable`, `general ledger`, `investment analyst`), which is still load-bearing
    # and still needs to run first.
    #
    # **This comment used to cite "Credit Analyst" as its second example and that was false the
    # whole time** — found independently by two of the 2026-08-22 passes, which is itself the
    # evidence that prose drifts from behaviour unread. Ordering can only decide a title that
    # BOTH patterns match, and nothing in this block read a bare `credit analyst` at all (only
    # `credit control` and `credit risk`), so all 11 such postings sat in `data_analysis`
    # whatever the order was. Corrected, and the term added below. The general lesson is worth
    # more than the fix: **an ordering claim is only true about titles two patterns can both
    # reach, and a comment asserting one is a claim that can rot silently.**
    #
    # **A finance word can name the DOMAIN of somebody else's job, and this pattern runs 13th —
    # ahead of `product`, `design`, `engineering`, `software_engineering` and `sales` — so it
    # wins those titles outright.** Measured over the whole live corpus on 2026-08-22: 306
    # postings were filed as finance work that is not finance work, and none of the five answer
    # keys could see it (all five slices are flat over this change, so the corpus is the only
    # evidence there is). The two guards below are the same lesson as the `\bengineer\b` misfile,
    # arriving from the opposite direction: there a broad ROLE word swallowed a sector, here a
    # narrow DOMAIN word swallows a role.
    #
    #   engineer/developer/architect   119 postings — "Software Engineer, Stripe Tax",
    #                                  "Finance Systems Engineer", "Solution Architect SAP
    #                                  Finance", "Backend Engineer, Billing/Tax". 106 of them
    #                                  land in software_engineering, 1 in uncategorised.
    #   product manager/owner/…         62 postings — "Treasury Product Manager", "Staff Product
    #                                  Manager, Stripe Tax", "Product Owner Finance IT". 58 land
    #                                  in product, 3 in design, 0 in uncategorised.
    #
    # **The cost is 3 postings and it is stated rather than hidden**: `Strategic Finance Lead,
    # Product & Engineering`, `Finance & Strategy Partner, Central Engineering` and `Financial
    # Engineer, truView, Vice President` are finance roles that name engineering, and they leave.
    # 3 against 174 is the trade, and no anchored form separates them — a finance role *reporting
    # into* engineering and an engineer *working on* finance wear the same words in the same order.
    #
    # **Two things about the shape are load-bearing.** The guard is anchored (`^(?!.*…)`) because
    # the role head sits on either side of the domain word: "Finance Systems Engineer" puts it
    # after, "Engineering Manager, Tax Platform" before. An unanchored lookahead — the version
    # that keeps `m.group(0)` equal to the matched term — reads forward only and catches 45 of
    # the 177 postings, measured. The `.*(?:…)` wrap is the price of anchoring: the match now
    # starts at position 0, so span attribution by `m.group(0)` reports "Redovisningsekonom"
    # where it used to report "ekonom". That is a diagnostic cost, not a classification one, and
    # the instrument is what should change — group by alternation branch, not by `group(0)`.
    #
    # English word forms only, deliberately. Swedish `ingenjör`, Czech `inženýr` and German
    # `Ingenieur` are NOT here: those titles were never the failure, and a guard that reads a
    # language nobody measured it against is the `_FR_ROLE` compound hazard again.
    ("finance_accounting", re.compile(
        r"^(?!.*\b(?:engineer|engineers|engineering|developer|developers|architect|architects)\b)"
        r"(?!.*\bproduct\s+(?:manager|managers|owner|owners|lead|leads|management|"
        r"design|designer|designers)\b)"
        # **The product guard was English-only AND space-separated, and German does neither.**
        # "Lohnexperte Produktmanagement - Payroll Software / Jira (m/w/d)" is 16 postings — the
        # largest single title in this category after "Senior Accountant" — and it is a product
        # manager for payroll software, reached through `payroll`. `\bproduct\s+…` cannot see a
        # one-word compound, so the German and Swedish forms are named outright. Exactly the
        # shape the `analytics` guard was fixed for on 2026-08-22: a guard that reads one
        # language is a guard the other languages walk past.
        r"(?!.*(?:produktmanage|produktchef|produktägare|produktsjef))"
        # Three more heads that outrank a finance domain word, all measured on the `payroll`
        # rows: legal counsel (3), pre-sales solutions consulting (4) and customer care (4).
        # `customer success` is deliberately absent — a CSM at an accountancy vendor is still
        # not finance, but the phrase also appears on genuine finance-operations titles here.
        r"(?!.*(?:managing counsel|solutions? consultant|customer care))"
        r".*(?:"
        # `\bfinancial\b` alone was the single largest misfile in this category: **"Financial
        # Services" is an industry vertical, not a finance function**, and 101 of the 103 corpus
        # titles carrying it are somebody else's role sold into that vertical — 129 postings, led
        # by `Account Executive, Emerging Enterprise, Financial Services` (8) and `Sr. Forward
        # Deployed Engineer (FDE) - Financial Services` (9). 56 postings land in `sales`, 30 in
        # `software_engineering`, only 22 in `uncategorised`. The lookahead is positional rather
        # than whole-title on purpose: the 2 real finance titles in the set are `Financial
        # Services Tax, Aircraft Leasing Senior Manager` and its Director twin, and they are kept
        # by `\btax\b` further down. A whole-title `financial services` exclusion would lose them.
        # Only `services?` is excluded — `financial institutions` (4) and `financial markets` (3)
        # were checked and are genuine investment-banking titles.
        r"\bfinanc(?:e|ial)\b(?!\s+services?\b)|account(?:ant|ing)|"
        # **`\bcontroller\b` is a finance word in Sweden and a warehouse word in English.** The
        # lookbehinds are the `georgia` rule for a job title: `Document Controller` (6 postings,
        # a construction/BIM records role), `Stock Controller` (5, retail inventory — two of them
        # `Customer Assistant - Stock Controller`), `Material(s) Controller` (3), plus one each of
        # quality, fleet and flow. 17 postings, and none of them shares a word with the 250 real
        # ones (`Business Controller` 67, `Financial Controller` 48, `Project Controller` 33).
        # `Production Controller` and `Process Controller` are deliberately NOT here: one of the
        # two `Process Controller` rows is `Process Controller, Global Functions Finance Tower`,
        # so the prefix does not decide it and 1 posting is not evidence.
        r"(?<!document )(?<!documentation )(?<!stock )(?<!material )(?<!materials )"
        r"(?<!quality )(?<!fleet )(?<!flow )\bcontroller\b|bookkeep|\btreasury\b|"
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
        # `credit analyst` is here because **the ordering comment above cited it as an example
        # and nothing in this block could reach it.** `credit control` and `credit risk` were
        # both present; the bare analyst form was not, so `Senior Credit Analyst` (3),
        # `Credit Analyst, New Verticals` (2), `Structured Products Credit Analyst` and 4 more
        # — 11 postings, 7 titles — sat in `data_analysis` no matter where this pattern ran.
        # Order was never the fix for them; a term was. Bound to `credit` on purpose: the bare
        # `\banalyst\b` this block is ordered ahead of is `data_analysis`'s, and widening here
        # would re-fight that boundary instead of closing this hole.
        r"\btax\b|\bfp&a\b|credit risk|credit analyst|"
        r"collections specialist|collections manager|"
        # ES. `contable` carries a lookbehind: `conciliación contable` is a sales-ops
        # reconciliation title, not an accountant.
        r"n[óo]minas?\b|\bfinanzas?\b|(?<!conciliaci[óo]n )\bcontable|" # es-2
        # --- Norwegian, 2026-08-11 (N30, N31, N32) — graded against NAV STYRK-08 ---
        r"regnskap|økonomi(?:rådgiver|rådgjevar|medarbeider|konsulent|sjef|leder|ansvarlig|avdeling|styring)|lønns(?:medarbeider|konsulent|ansvarlig|rådgiver|sjef|kontor)|seksjon for lønn|finanssjef|"
        # --- wave 3 (2026-08-17) ---
        # en-13  en — roles-en-w3.md
        r"investor relations|"
        # The securities back office, added 2026-08-22. `operations` ran last and swallowed it:
        # "Investment Operations Officer" (13 postings), "Senior Specialist, Investor Services
        # Operations" (5), "Specialist - Fund Operations, Private Equity", "Officer, Cash
        # Securities Operations" — 42 postings over 22 titles filed as business operations, so
        # a supply-chain subscriber was offered fund administration. **Bound to `operations`,
        # never bare**: bare `fund` is a verb and a charity, bare `securities` and `trading` are
        # sector words that appear on engineering and sales titles all over this corpus. The
        # generic "Client Operations" family (~71 postings, State Street) is deliberately NOT
        # here — client operations exists in every industry and only the CLO/structured-debt
        # suffix makes those finance, which is thinner evidence than a fragment should rest on.
        r"(?:investment|investor\s+services|fund|securities|trading|custody|settlement|"
        r"middle\s+office|treasury)\s+(?:data\s+)?operations|"
        # de-01  de — roles-de-w3.md
        r"buchhalt|"
        # de-05  de — roles-de-w3.md
        r"rechnungswesen|"
        # The two German tax professions the block could not read at all (0 occurrences before
        # 2026-08-22). `steuerberater` is the licensed tax adviser and it is a clean win —
        # 19 postings, every one of them `uncategorised`, 17 of them one employer's town-by-town
        # series. It is deliberately the PERSON (`-berater`) and not the practice
        # (`-beratung`): `Team Lead Sales - Steuerberatungen (m/w/d)` sells TO tax firms and is
        # correctly `sales`, and finance runs first, so the wider stem would have taken it.
        # `steuerfachangestell|steuerfachwirt` is the tax clerk — 6 postings out of
        # `uncategorised`, plus 4 titles already reached by `buchhalt`. The forward lookahead is
        # the whole reason this is safe to add: `Steuerfachangestellter - IT-Support /
        # Kanzleisoftware (m/w/d)` is **8 postings and it is already correctly
        # `customer_support`** — the tax-clerk word is the required qualification and IT support
        # is the job. German puts the profession first, so a forward-only guard reaches it;
        # without the guard this term would have been a net 8-posting MISFILE dressed as a
        # 14-posting win.
        r"steuerberater|steuerfach(?:angestell|wirt)\w*(?![^|]*it[- ]?support)|"
        r"nale[żz]no[śs]ci"                                             # pl-2 (receivables)
        r")",
        re.I)),                                                                 # fr-2
    ("data_analysis", re.compile(
        # `\bdata\b[^|\w]{0,3}analyst` rather than a literal `data analyst`: the NL and DE
        # boards write `Data-Analyst` and one board writes `Junior (Data) Analyst`, and the
        # bare-space form never matched either. 10 postings, all of them real data analysts.
        r"\bdata\b[^|\w]{0,3}analyst|bi analyst|business intelligence|power bi|"
        # The named data functions whose title puts a word between "data" and "analyst" —
        # governance, quality, management, operations. 16 postings, all genuine. Enumerated
        # rather than a `\bdata\b[^|]{0,22}\banalyst\b` window, because that window also reads
        # `Data Privacy Analyst` (legal) and `Maintenance Analyst, Data Center Delivery`.
        r"data (?:governance|quality|management|operations)[^|\w]{0,3}analyst|"
        r"\binsights? analyst|"
        # `tableau` is a TOOL NAME, and a tool name alone never names a role — bound to a role
        # noun 2026-08-22. The bare `\btableau\b` won 13 postings and **all 13 were Salesforce
        # advertising its own product**: three `Tableau Account Executive`, two `Account
        # Director`, an `Enterprise Account Director`, a Customer Success Manager, a Product
        # Manager, two Software Engineering MTS, a Technical Architect and a Lead Researcher.
        # Zero data analysts, so 13 of 13 damage and no collateral. Same shape as the
        # `Mekonomen` guard one pattern up — the collision is an EMPLOYER'S PRODUCT NAME, and
        # it is inside the classifier, not inside `search_tsv`. `power bi` above needs no such
        # guard: Microsoft does not sell Power BI through titled account executives here.
        r"tableau\s+(?:analyst|developer|utvecklare|consultant|specialist|expert)|"
        # **A bare `\banalyst\b` was the single largest misfile in the corpus, and it is gone**
        # (measured 2026-08-22). `analyst` is a job-SHAPE word — every function has one — not a
        # data word, and the data senses of it are all named explicitly above and below. As the
        # residual of those named senses it won **1 778 postings across 1 379 titles**, of which
        # only 218 postings so much as mentioned data, reporting, BI or analytics *anywhere* in
        # the title. The qualifier census is what settles it rather than argument: 294 distinct
        # words appear immediately before `analyst`, the commonest being `business` 165,
        # `operations` 120, `support` 58, `systems` 46, `product` 45, `it` 42 — a distribution
        # no blocklist can ever close.
        #
        # It was also making a decision this file has already made elsewhere UNREACHABLE:
        # `other_tech_function` carries `business analyst` at the bottom of `PATTERNS`, so the
        # taxonomy had already ruled that a business analyst is not a data analyst, and this
        # fragment 459 lines earlier meant that ruling could never fire. 726 of the 1 778
        # postings are claimed by a *named* neighbour the moment this stops shadowing them —
        # other_tech_function 196, operations 180, sales 95, software_engineering 75,
        # marketing 54, hr_recruiting 46, legal 31 (`Paralegal - Data Privacy Analyst`, 18
        # postings, was in *data analysis*), product 19, customer_support 18 — and the
        # remaining 1 052 go `uncategorised`, which is the honest answer and the one the AI
        # matcher can still rescue. A misfile it cannot: it puts the job in a stranger's
        # digest. Do not restore the bare form to make the category look bigger.
        # `analist` is the Dutch/loan spelling that arrives via the NL boards ("Data Analist").
        #
        # **BOUND 2026-08-26, and the reason is that the deletion above was applied to English
        # and to nothing else.** The bare form did in Dutch precisely what `\banalyst\b` did in
        # English: 29 of its 80 postings were not data work, and the clearest proof is that it
        # made a ruling this file had already made unreachable one language over —
        # `Business Analyst` classifies `other_tech_function`, while `Business Analist`
        # classified `data_analysis`. `Security Analist` shadowed `cybersecurity` the same way.
        # Every one of the 51 correct rows carries the literal `data` token
        # (`Data Analist`, `Data-analist`, `Junior Data Analist`, `Technisch Data Analist`,
        # `HR Data Analist`), so binding costs nothing measurable.
        r"quantitative (?:researcher|analyst)|\bdata[- ]?analist\b|"
        # `analityk` (pl) is a separate string from the Czech `analytik` — the i/y is exactly
        # the kind of near-miss that looks already-covered and matches nothing.
        # `analytics` is a DOMAIN word, not a role, and `data_analysis` runs 30–170 lines above
        # `product`, `software_engineering` and `sales` — so the domain beat the profession and
        # the profession never got asked. `classify` returns the first *pattern* that matches
        # anywhere, so string position does not save it: "Senior Software Engineer, Events
        # Analytics Platform" was data analysis. Guarded 2026-08-22 in the same shape as
        # `product design` below: 42 postings move, 22 to product ("Senior Product Manager -
        # User and Lifecycle Analytics"), 17 to software_engineering, 3 to sales ("Account
        # Executive - Analytics, Federal Civilian"), and **nothing moves to uncategorised** —
        # zero collateral, because each of these titles names its own profession outright.
        # Deliberately NOT widened to a bare `\bengineer`: "Engineering Manager, Data &
        # Analytics" is a data-engineering line manager, and guessing there would trade a
        # measured win for an argued one.
        r"^(?!.*(?:software engineer|product manag|product owner|"
        r"account (?:executive|director))).*analytics|"
        # **The Nordic/Czech `analytik` family, bound 2026-08-26 for the same reason as
        # `analist` above.** Bare, it was the largest surviving instance of the very misfile
        # the 2026-08-22 `\banalyst\b` deletion was written to end — 113 wrong of 140. *Analyst*
        # is a job-SHAPE word in these registers exactly as it is in English: `Kravanalytiker`
        # (requirements, 31 postings), `1st Line SOC Analytiker` (6), `Etterretningsanalytiker`
        # (intelligence), `KYC-analytiker`, `Riskanalytiker`, `Verksamhetsanalytiker`,
        # `Senior analytiker reaktorsäkerhet`, `SW Analytik / Tester`.
        #
        # That two upstream categories already carry guards written solely to claw professions
        # back out of this fragment — `säkerhetsanalytiker` in `cybersecurity` and
        # `biomedicinsk\w*\s*analytiker` in `science_research`, the latter worth 62 postings —
        # is itself the evidence: it was being closed one profession at a time instead of at the
        # root. Those two guards stay; they run earlier and cost nothing.
        #
        # Bound to the data sense, ~7 genuinely analytical but data-less titles are lost to
        # `uncategorised` (`PRV söker analytiker till statistikprojekt`) against ~113 recovered
        # — and an `uncategorised` row still reaches the AI matcher, which a misfiled one never
        # does. `analityk`/`analitycz` (pl) keep their own spelling: the i/y is exactly the kind
        # of near-miss that looks already-covered and matches nothing.
        r"data[\s-]?analytik|datov[ýá] analytik|datov[áé] analytič|"
        r"analytik\w*\s+(?:dat|bi\b)|\bbi[\s-]analytik|"
        r"analityk|analitycz|"   # pl
        # --- wave 2 --- both bound: bare `datos` takes `Centro de Datos` and
        # `Protección de Datos` (legal), bare `análisis` takes `Análisis Clínicos` (a
        # hospital lab).
        # --- wave 3 (2026-08-17) ---
        # fr-04  fr — roles-fr-w3.md. Bound 2026-08-26 with the other two spellings; the model
        # for it was already in this file, one category up — `cybersecurity` carries the bound
        # French form `analyste\w*\s+(?:en\s+)?(?:s[ée]curit[ée]|cybers)`.
        r"analyste\w*\s+(?:de\s+)?donn[ée]es|data[\s-]?analyste|"
        r"an[áa]lisis\s+de\s+datos|gobierno\s+del?\s+dato", re.I)),     # es-2
    ("devops_platform", re.compile(
        r"devops|platform engineer|site reliability|\bsre\b|cloud engineer|"
        r"infrastructure engineer|cloud architect|"
        # **A bare TOOL NAME, guarded 2026-08-26.** `kubernetes` was the only tool name in this
        # pattern (no docker, terraform or ansible sits beside it), and `data_analysis` already
        # records the verdict it needed: "a tool name alone never names a role" (`tableau`).
        # `devops_platform` runs ahead of `software_engineering`, so any title merely LISTING
        # Kubernetes in its stack was filed as platform work: `Kubernetes Software Developer`
        # (6 postings), `Software Engineer - Kubernetes & Go`, `Senior Javautvecklare — Spring
        # Boot, Microservices, Kubernetes`. 26 of 42 wrong.
        #
        # Measured: 21 postings move, ALL to `software_engineering`, none to `uncategorised`.
        # **`utvecklare` is deliberately UNBOUNDED here**, and that is measured rather than
        # sloppy: `\butvecklare\b` leaves `Senior Javautvecklare — Spring Boot, Microservices,
        # Kubernetes` in this category, because the Swedish compound has no word boundary in
        # front of the head. Same compound-from-the-inside problem this file records for
        # `-bouw`, `montage` and `marknad`, and the safe direction here is the wide one: this is
        # a negative lookahead, so an over-match only sends a row on to `software_engineering`.
        # Kept: `Senior Kubernetes Engineer`, the OpenShift rows, `Kubernetes Administrator`,
        # `Infrastrukturingenjör Kubernetes`. Deleting the branch would cost ~12 correct rows;
        # the guard costs 0.
        r"^(?!.*(?:\bsoftware (?:engineer|developer)\b|\bdeveloper\b|utvecklare))"
        r".*\bkubernetes\b|"
        r"správce systém|správca systémov|systémov[ýá] administr|"
        r"administrátor (?:is|it|systém|sít|server)|síťov[ýá] administr|"
        r"database administrator|\bdba\b|"
        r"nätverkstekniker|systemtekniker|systemförvaltare|infrastrukturarkitekt|"
        r"systeembeheerder|netwerkbeheerder|applicatiebeheer|"                  # nl
        r"functioneel beheerder|"
        # --- wave 2 ---
        # --- wave 3 (2026-08-17) ---
        # sv-20  sv — roles-sv-w3.md. Both spellings: `skilled_trades` refuses an
        # IT-prefixed drift(s)tekniker in either language, and a refusal there has to
        # hand off to somewhere, not drop — "IT-driftstekniker" was uncategorised.
        r"drifts?tekniker|"
        # de-03  de — roles-de-w3.md
        r"systemadministra|"
        # `systems? administrator` is an unbound generic head, and this pattern is the only one
        # in the file with no explanatory comment on any of its 23 branches — a correlation the
        # 2026-08-26 audit noted and this line is the first repayment of. A GTM, sales-enablement,
        # People/Workday or LMS "Systems Administrator" administers a business APPLICATION and
        # owns no infrastructure; `erp` is deliberately not in the list, because an ERP admin
        # plausibly is IT and no evidence here settles it.
        r"^(?!.*(?:\bgtm\b|go-to-market|sales enablement|people systems|learning management))"
        r".*systems? administrator|\bsysadmin\b", re.I)),               # en
    ("product", re.compile(
        r"product manager|product owner|product lead|product management|"
        # `Director of Product` / `Head of Product` were a genuine hole: the category read only
        # the `product <head>` word order, so the `of` forms fell through to whatever ran later
        # — `Director of Product, Growth/AI` was MARKETING, via `growth`. Added 2026-08-26
        # alongside the `growth` list extension, because narrowing that list without this would
        # have turned a misfile into a decline rather than into a correct answer.
        # `(?!\s+marketing)` because "Director of Product Marketing" is marketing's.
        r"\b(?:director|head|vp|svp|evp)\s+of\s+product\b(?!\s+marketing)|"
        # The one program-management shape that IS product: the qualifier says so. Keeps the
        # 3 postings the move below would otherwise cost, and it has to run before the
        # `other_tech_function` fragment, which it does by sitting here.
        r"product program manager|"
        r"produktov\w*\s+manaž|produktov\w*\s+vlastník|"
        # --- wave 2 ---
        # --- Norwegian, 2026-08-11 (N36) — graded against NAV STYRK-08 ---
        r"produktsjef|"
        # Swedish and Danish, 2026-08-26. The category read Czech and Norwegian product
        # words and not the ones from the corpus's LARGEST language: 19 postings of
        # "Produktchef" and "Produktägare" sat uncategorised while `produktsjef`, one
        # letter away in Norwegian, was read. Found by narrowing `assembly` — "Produktchef
        # till Mycronic PCB Assembly Solutions" went from manufacturing to *nothing*,
        # which is the paired-widening rule: a narrowing that turns a misfile into a
        # decline has not finished.
        # `produktansvarig` is deliberately NOT here. It is a duty, not a head
        # ("Produktansvarig Teknisk Säljare" is a salesman, "Miljö- och produktansvarig"
        # is an environment officer), and `product` runs 500 lines ahead of `sales`.
        r"produktchef|produktägare|"
        # German writes the whole thing as one word, so `product management` cannot read it.
        # 16 postings of "Lohnexperte Produktmanagement - Payroll Software" were classifying as
        # `finance_accounting` via `payroll` until the guard there learned the compound; this is
        # the destination half of that fix, without which the narrowing is just a decline.
        # Guarded against `design`, which `product` runs 30 lines ahead of: "Werkstudent (m/w/d)
        # App UI/UX Design & Produktmanagement" (5 postings) names the design first and the
        # product second, and taking it here would trade one misfile for another.
        r"^(?!.*(?:ui/ux|ux/ui|\bdesign\b)).*produktmanage|"
        # --- wave 3 (2026-08-17) ---
        # `program manager` / `programme (manager|lead|director)` / `\btpm\b` lived here until
        # 2026-08-22 and moved to `other_tech_function`. Program management is a delivery
        # discipline, not product management, and the fragment was a BARE role head: because
        # `product` runs ahead of software_engineering, marketing, sales, hr_recruiting, legal,
        # customer_support and operations, the function the title actually names lost to it.
        # 545 postings over 433 titles, of which 127 postings / 109 titles were claimed by a
        # later category that names the function outright — "Recruiting Program Manager",
        # "Marketing Program Manager, France", "Legal Program Manager, Contracts and
        # Governance", "Supply Chain Program Manager". The 418-posting residual is genuine
        # program/project delivery across silicon, data centres, plants, medicine and defence
        # ("Program Manager Plant A", "Senior Program Manager, Sports Medicine"), which is not
        # product either. Product subscribers keep reaching it through recall —
        # `SHORTLIST_KEYWORDS["product"]` carries "program manager", where breadth belongs and
        # the AI matcher does the precision.
        r"scrum master", re.I)),                                        # en
    ("design", re.compile(
        # The lookbehind is one employer's job title and 15 postings of it: a "Business Process
        # Designer" is a BPM analyst, and `other_tech_function` names `business process`
        # outright — it just runs 400 lines later, so bare `designer` won on ordering alone.
        # Positional, not whole-title, so "Senior Business Process Designer" is caught too.
        # **The engineering-discipline guard, added 2026-08-26 — and this file had already
        # written it, three lines below, for the wrong half of the pair.** `design lead` was
        # bound to a domain word precisely because "Mechanical Design Leader" took 20 of its 29
        # postings; the bare `designer` sitting directly above it never got the same treatment,
        # and it is the bigger branch by 26×. `design` runs ahead of `engineering` and
        # `software_engineering`, so every discipline that calls its design engineer a
        # "Designer" landed in digital product design: measured at ~98 wrong of 769 — 63
        # hardware/electrical/mechanical/civil (`Senior PCB Designer`, `Digital IC Designer`,
        # `Cable Harness Routing Designer`, `Senior Structural Steel Designer`), 18 software
        # solution/system designers, 17 instructional designers (an L&D job).
        #
        # Whole-title and anchored, because the discipline word sits on either side of the head
        # ("Design Engineer Mechanics", "Senior PCB Designer"). `industrial`/`industri` is
        # deliberately absent from the guard: an Industrial Designer IS a designer.
        r"^(?!.*\b(?:cad|bim|pcb|fpga|asic|ic designer|integrated circuit|harness|hvac|"
        r"structural|electrical|mechanical|instructional|curriculum)\b)"
        r".*(?<!business process )designer|"
        # `\bui\b`, `\bux\b` and "user experience" are DOMAIN qualifiers, not role nouns, so
        # bare they read a frontend engineer's title as a designer's — `design` runs ahead of
        # `software_engineering`, so "UI Engineer", "Staff Backend Engineer - UI Platform",
        # "Principal UX Engineer, Ads" and "Software Engineer, User Experience" all landed
        # here: 55 postings over 68 titles, measured 2026-08-22. The lookahead is the same
        # shape the `product design` fragment below already uses, and it is safe because the
        # bare `designer` above still fires first — "Frontend Developer & UI/UX Designer" and
        # "Lead UX Designer & Frontend Engineer" stay in `design`, "UI Engineer" leaves.
        r"^(?!.*\b(?:engineer|engineering|developer|programmer)\b)"
        r".*(?:\bux\b|\bui\b|user experience|user interface)|"
        # "Design Lead" bare is any discipline's design lead, and the automotive/aerospace
        # boards write it constantly: "Mechatronics Discipline Design Leader", "Mechanical
        # Design Leader (LV products)", "Group Design Leader - Interior/Exterior/BIW" — 20 of
        # the 29 postings the bare form held. Bound to a digital/brand domain word instead,
        # the same construction `_DE_ROLE` and `_IT_ROLE` use for the same reason.
        r"(?:brand|content|visual|experience|service|graphic|digital|web|product|"
        r"creative|motion|\bux\b|\bui\b)[\s/&-]+design lead|"
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

        # **The discipline used to have to be ADJACENT to "engineer", and that is what leaked.**
        # `mechanical engineer` cannot read "Mechanical Design Engineer" (14 postings), and only
        # `electrical` had ever been given the gap it needed — so 220 postings of mechanical,
        # electronics, manufacturing, process and structural work carried a discipline word in
        # the title and *still* fell through to `software_engineering`'s bare `\bengineer\b`.
        # Binding the whole discipline list the way `electrical` was already bound closes it;
        # measured on the NAV key it also *gains* a row (`Hardware Test Engineer/Technician`,
        # STYRK 2149), and SE/CZ/CZ-majors stay flat row for row.
        # `engr\b` because Workday and Oracle tenants abbreviate the head as well as the
        # discipline ("Mech Design Engr II", "Chemical Engr I") — 28 postings that every
        # discipline guard in this pattern was blind to, `mech\b` being the other half of it.
        # The guard is `software engineer|developer`, not bare `software`: bare also released
        # `Smart Manufacturing Engineer, Software`, which belongs here.
        rf"{_NOT_SOFTWARE}.*{_ENG_DISCIPLINE}\b[^|]{{0,40}}?\b(?:engineer|engr\b)|"
        # **The other half of the same leak is WORD ORDER, and it was still open.** Workday
        # and Oracle tenants write the head first and the discipline after it — "Engineer
        # Mechanical" (10 postings), "Engineer Electrical & Electronics", "Sr. Engineer
        # Electrical (CAD)", "Engineer, Manufacturing", "Packaging Engineer, Automotive" —
        # and the forward arm above cannot see a discipline that comes second, so ~65 postings
        # of the same electrical and mechanical work fell through to `software_engineering`'s
        # bare `\bengineer\b` exactly as the adjacent-only version did. Mirrored rather than
        # loosened: same guard, same discipline list, same 40-character gap, reversed. The
        # `software engineer|developer` guard is what keeps "Backend Software Engineer,
        # Automotive" and "Embedded Software Engineer Automotive" out of it, and
        # `data_engineering`, `devops_platform` and `logistics_transport` all run BEFORE this
        # pattern, so "DevOps Engineer Aerospace" and "Data Engineer, Industrial IoT" are
        # already answered by the time it is reached.
        rf"{_NOT_SOFTWARE}{_HEAD_ALREADY_BOUND}"
        rf".*\b(?:engineer|engr\b)[^|]{{0,40}}?{_ENG_DISCIPLINE}\b|"
        r"mechatronic|"
        # `(?<!försäljnings)` — see the sales-engineer note on the French `ingénieur` below.
        # A *Försäljningsingenjör* sells; the title's head is the selling, not the engineering.
        r"(?<!försäljnings)ingenjör|ingeniör|ingeniør|"   # SE -ingenjör / -ingeniör, NO -ingeniør
        # `konstruktör` (SE) as well as `konstruktér` (CZ): mechanical and electrical designers
        # are the single largest group inside the register's technical field, and until
        # 2026-08-09 the Swedish spelling was the one missing.
        r"strojní inžen|strojní inžinier|elektroinžen|konstruktér|konštruktér|konstruktör|"
        # Bare `inženýr` (CZ), which only ever appeared with a discipline in front of it —
        # "Inženýr kvality" and "Průmyslový inženýr" were the common shapes and both missed.
        # `data_engineering` reads `datový inžen` several patterns earlier, so it keeps those.
        # `technolog\b` is bounded on purpose: "informačních technologií" is not an engineer.
        # `inžený`, not `inženýr`, since 2026-08-26: the Czech plural is *inženýři* with a **ř**,
        # so the singular spelling cannot read it, and the register writes whole ISCO group
        # names in the plural — "Inženýři elektrotechnici a energetici ve výzkumu a vývoji",
        # "Chemičtí inženýři a specialisté v příbuzných oborech", "ML inženýři". Same lesson as
        # `sjuksköterska` not matching *Sjuksköterskor* and `mechani[kc]` replacing the -k
        # singulars: in a register corpus the plural is the common form, not the exception.
        r"inžený|inžinier|projektant|technolog\b|"
        r"kvalitetstekniker|"
        # **Polish `projektant → design` is deliberately absent.** `projektant` is already here
        # as the Czech *design engineer*; in Polish the same string means *designer*. Moving it
        # to `design` (which runs earlier) costs the Czech key 14 rows, 77.70% -> 77.01%, and no
        # ordering resolves it — the two languages disagree about the word. Cut, not deferred.
        #
        # Three of the four Romance/Slavic engineer words need a software lookahead because
        # `engineering` runs BEFORE `software_engineering`; German does not, because German
        # software work advertises as `entwickler`, which the next pattern claims.
        # **A sales engineer is a salesperson, and three languages spell it with the engineer
        # word.** French `ingénieur commercial / d'affaires / avant-vente / technico-commercial`,
        # German `Vertriebsingenieur` and Swedish `försäljningsingenjör` are quota-carrying field
        # sales roles; `sales` runs 200 lines after this pattern, so only a decline here can
        # release them. `commercial` joins the same lookahead the software words already use;
        # German and Swedish take fixed-width lookbehinds because the sales word is a compound
        # PREFIX there, not a following noun.
        #
        # `(?<!vertriebs)` is on BOTH this token and the German `ingenieur` below, and the
        # duplication is deliberate: `ing[ée]nieur` reads the bare-e German spelling too, so
        # guarding only the German copy left `Vertriebsingenieur im Innendienst` in engineering.
        # That is the `farmaceut(?!yczn)` note on this file's healthcare pattern, verbatim — a
        # bare copy anywhere in the alternation still matches, so a guard on one copy fixes
        # nothing. Measured: guarding the German token alone moved 0 postings.
        r"(?<!vertriebs)ing[ée]nieur(?:e|es|s)?\b(?![^|]{0,40}"                 # fr
        r"(?:logiciel|logicel|software|web|d[ée]veloppement|devops|full ?stack|"
        r"commercial|d'affaires|avant[- ]vente|technico))|"
        r"ing[ée]nierie|"
        r"progettis|progettazion|"                                              # it
        r"(?<!vertriebs)ingenieur|konstrukteur|"                                # de
        r"ingenier(?![\w/()@.]*\s+(?:de\s+)?"                                   # es
        r"(?:software|backend|front[- ]?end|full[- ]?stack))|"
        # `(?<!data)(?<!data )modelleur` — every corpus posting of this fragment is
        # "Data modelleur" / "Datamodelleur", which is data modelling, not mechanical
        # draughting. `data_engineering` runs 570 lines earlier and now names both spellings,
        # so this narrowing hands off rather than declines.
        r"werktuigbouw|constructeur|\btekenaar|(?<!data)(?<!data )modelleur|"
        r"technisch tekenaar|"                                          # nl
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
        # **`data`/`AI` quality is not industrial quality, and the fold above did not guard it.**
        # `data_analysis` runs earlier and correctly keeps "Data Quality Analyst", so the leak is
        # exactly the titles that name no analyst: "Junior Data Quality Specialist", "Senior AI
        # Quality Engineer", "AI Agent Quality Engineer", "Multilingual AI Quality Specialist".
        # The guard requires the word to sit ON `quality` (`[^|]{0,8}`, not anywhere in the
        # title) so "Quality Engineer, Data Center" and a plant title mentioning AI keep their
        # category — the same anchored shape as the `safety engineer` guard below.
        rf"{_NOT_DOMAIN_QUALITY}"
        r".*quality engineer|qualitätsingenieur|ingénieur\w*\s+qualité|"
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
        # **The catch half of unbinding `hvac` in `skilled_trades`.** HVAC is an industry, not a
        # job, and while that bare stem shipped it answered first for every role in the sector.
        # Releasing it there is only safe with this rule here, because `software_engineering`'s
        # bare `\bengineer\b` runs next and would take **59 of those 60 postings** — the same
        # misfile one category along, which is the 718-posting `\bengineer\b` failure of
        # 2026-08-17 arriving through a new door. Mutation-checked by deleting these two lines:
        # 59 postings move straight to `software_engineering`. Building-services and controls
        # engineering is `engineering` on the same footing as the `hse|ehs` line above (STYRK
        # 3119 / ISCO 2149).
        #
        # Two directions because the corpus writes both ("HVAC Controls Application Engineer",
        # "Field Service Engineer - HVAC Controls & Building Automation"), and `36`/`30` are
        # measured widths, not round numbers: at 36 the leading form reaches across "Building
        # Controls Application " (31 chars) — at 30 it is one character short and those 5
        # postings go to software. `hvacr?` catches the refrigeration spelling. The anchored
        # `software|full[- ]?stack` guard is the `safety engineer` shape. **`\bsales\b` is
        # deliberately NOT in that guard**: `sales` runs AFTER `software_engineering`, so
        # declining a sales-engineer title here cannot reach `sales` — it only hands the title
        # to software's bare `\bengineer\b`, which is the very misfile this pair exists to
        # prevent. Measured on "HVAC Pre Sales Field Engineer, GCOE".
        r"^(?!.*(?:software|full[- ]?stack)).*\bhvacr?\b[^|]{0,36}?engineer|"
        r"^(?!.*(?:software|full[- ]?stack)).*engineer[^|]{0,30}?\bhvacr?\b|"
        r"^(?!.*(?:software|application)).*\bmaintenance engineer|"
        # Silicon, EE and physical-discipline engineers — the same defect as the quality,
        # safety and maintenance folds above, in the vocabulary this file never learned.
        # `\bengineer\b` was filing 187 postings of chip and hardware work as SOFTWARE:
        # `Staff Analog Design Engineer` (6), `Staff Digital Design Engineer` (4),
        # `Senior ASIC Design Engineer` (4), `Thermal Engineer` (4), and 146 more titles.
        # These names are unambiguous — nobody advertises a web role as an ASIC engineer —
        # which is why they may be *bare* discipline words where `design engineer` may not
        # (see the discipline-bound rule at the top of this pattern: the SSYK key files a
        # bare "Design Engineer" as SOFTWARE, so bare `design engineer` must NOT ship).
        # `\bsoc\b` is deliberately absent and only `soc design` ships: `SOC Analyst` is a
        # security-operations title and `cybersecurity` does not read a bare `soc`.
        # The `software engineer|developer` guard is what keeps `Software Engineer – Hardware
        # Software Integration Engineer` and `Senior Low Latency Electronic Trading Software
        # Engineer` in software; the narrower `software engineer` (not bare `software`) is
        # measured — bare `software` also released `Smart Manufacturing Engineer, Software`,
        # which is an engineering row.
        r"^(?!.*(?:software (?:engineer|developer)|full[- ]?stack))"
        r".*(?:analog(?:ue)?|\basic\b|\brtl\b|\bfpga\b|\bmems\b|silicon|semiconductor|"
        r"wafer|photonic|\bemc\b|\brf\b|thermal|hydraulic|pneumatic|solenoid|actuator|"
        r"avionic|propulsion|powertrain|"
        r"(?:digital|physical|logical|circuit|board|package|cell|mask|layout|chip|soc|"
        r"\bic|product|tool)\s+design)\b[^|]{0,32}?\bengineer|"
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
        # **`ontwerper` is CUT, and it is the Polish `projektant` verdict in Dutch.** Bare
        # `ontwerper` is the ordinary Dutch word for *designer*, not for a design engineer, and
        # every one of its 7 corpus postings is something else: "Technisch Ontwerper Privileged
        # Access Management" and "Netwerk Security Ontwerper, Checkpoint, F5, Fortinet" (IT
        # security), "Opleidingsontwerper" and "Trainingsontwerper" (instructional design —
        # education), "Ruimtelijk ontwerper Wonen, werken en bereikbaarheid" (spatial planning),
        # "Senior Functioneel Ontwerper" (business analysis). 0 right, 7 wrong — even the
        # `Technisch` prefix, which is what a narrowing would have kept, is IT both times it
        # appears. `technisch tekenaar` and `constructeur` on this same line still carry the
        # Dutch design-engineer sense, so the category loses no real Dutch coverage.
        # `civiel(?![\s-]?recht)` — Dutch *civiel recht* is civil LAW, and both of this
        # fragment's corpus postings are it ("Medewerker(s) wetenschappelijk bureau civiel
        # recht", "Coördinator administratie team Civiel recht"). The civil ENGINEERS the
        # comment above describes are not in the corpus today, so the fragment is kept
        # guarded rather than deleted — the `provision` precedent: a rule with no wrong rows
        # left and a named mechanism is worth keeping bound, not throwing away.
        r"civiel(?![\s-]?recht)|geotechniek|geohydrolo|kunstwerken|"       # nl-2
        # --- wave 3 (2026-08-17) ---
        # en-15  en — roles-en-w3.md
        # Same guard as on `quality engineer` above — this wave-3 fragment took "Junior Data
        # Quality Specialist" and "Multilingual AI Quality Specialist" by the same route.
        rf"{_NOT_DOMAIN_QUALITY}"
        r".*quality (?:manager|assurance manager|systems? (?:manager|specialist|engineer)|specialist)|"
        r"telecomunicaciones|redes\s+el[ée]ctricas", re.I)),            # es-2
    ("software_engineering", re.compile(
        r"software engineer|software developer|back[- ]?end|front[- ]?end|full[- ]?stack|"
        # `programmer(?!ing)` — the English agent noun sits inside the Swedish and Dutch
        # ACTIVITY noun (*programmering*), so a university lecturer in interface programming, a
        # Bravida security technician who also programmes alarms, a statistician "med
        # programmeringsinriktning" and a children's coding coach were all software developers:
        # 7 postings, 7 titles, none of them a dev job. The Swedish AGENT noun
        # (*programmerare*) is unaffected, which is the whole point of the lookahead.
        r"web developer|mobile developer|\bios\b|android|"
        # **`(?<!business )` — Business Development is a SALES title, and this was the
        # untreated English twin of a boundary this file has already drawn twice.** `sales`
        # owns the phrase `business development` (which cannot reach the *-er* form), and
        # wave 3 moved Swedish `affärsutvecklare` into `sales` while the Swedish `utvecklare`
        # fragment carries `(?<!affärs)` for exactly this collision. English had neither
        # guard, so `Business Development Manager` and `Affärsutvecklare` were sales while
        # `Business Developer` was a programmer. 50 postings, and not one of the 37 distinct
        # titles meant a programmer. (2026-08-26)
        r"(?<!business )(?<!business-)\bdeveloper\b|"
        # `(?<!clinical )(?<!statistical )` — a clinical or statistical programmer is a
        # biostatistician writing SAS for trial submissions: a pharma data profession, not
        # software engineering. 7 postings, all wrong, all unambiguous. A second false friend
        # in the same fragment as the `-ing` one below. (2026-08-26)
        r"(?<!clinical )(?<!statistical )programmer(?!ing)|"
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
        #
        # **Four more heads decline, on the same mechanism and for the same reason** (measured
        # 2026-08-22 over every active posting). `legal engineer` — 88 postings, 42 titles, one
        # legal-AI employer's whole job board ("Legal Engineer (Law Firm, Litigation)",
        # "Head of Legal Engineering") — is a lawyer's job, and `legal`'s `\blegal\b` was dead
        # for every title containing "Engineer". `value engineer` (95) and `customer engineer`
        # (92, over half of them Oracle data-centre thermal/UPS field roles) name no software
        # work; they land as honest declines, exactly like `support engineer` above.
        # `customer success engineer` (44) reaches `operations`, which owns `customer success`.
        # These are LOOKBEHINDS for the same reason the `sales`/`support` pair is: a whole-title
        # guard on `customer` would take "Customer Data Platform Engineer" with it.
        # This changes `test_f`, which pins Legal/Value/Customer Success Engineer as software —
        # a 2026-08-17 verdict on a *bare* `sales|support` whole-title guard, re-decided here on
        # the named phrases with 0 postings moving the wrong way.
        r"(?<!sales )(?<!sales-)(?<!support )(?<!support-)"
        r"(?<!legal )(?<!legal-)(?<!value )(?<!value-)(?<!customer )(?<!success )"
        r"\bengineer(?:ing)?\b|\bengr\b|qa engineer|\bsdet\b|"
        r"solutions? architect|enterprise architect|"
        r"vývojá[řr]|vývojárk|programátor|programátork|softwarov|softvérov|"
        # Any Swedish -utvecklare compound, except the ones that are not software. Two were
        # excluded (`affärs`, `verksamhets`) and **the Norwegian twin of this fragment already
        # excluded five** (`forretnings`, `produkt`, `elektronikk`, `organisasjons`,
        # `tjeneste`) — the same word in two Nordic languages with two different guard lists,
        # and Swedish had the shorter one. Measured: 69 postings of `-utvecklare` titles that
        # are not software at all, led by `Processutvecklare lärande & förbättring` (9),
        # `Fastighetsutvecklare` (real estate, 7), `Produktutvecklare` (8),
        # `Elektronikutvecklare` (4), `Näringslivsutvecklare` and `Måltidsutvecklare` (3 each).
        # `mjukvaru` is NOT here and must never be: *mjukvaruutvecklare* is a software
        # developer, and it is the reason this list is prefixes rather than a rule about
        # compounds. Grouped by length because Python lookbehinds must be fixed-width; a
        # whole-title `^(?!...)` variant measures **byte-identical** on all five slices and on
        # the corpus, and the lookbehind is preferred only because it keeps `matchspans.py`'s
        # span attribution readable (`group(0)` stays "utvecklare", not the whole title).
        r"(?<!(?:vård|kund))(?<!(?:språk|metod|plats|lokal|givar|klubb|ledar))"
        r"(?<!(?:affärs|region|ombuds|trafik|energi|kommun))"
        r"(?<!(?:process|produkt|närings|måltids|projekt|ungdoms|tjänste))"
        r"(?<!(?:hårdvaru|företags|industri))(?<!(?:kvalitets|kvalitéts))"
        r"(?<!(?:fastighets|elektronik|mekatronik))"
        r"(?<!(?:verksamhets|produktions|näringslivs|innovations))(?<!organisations)"
        r"utvecklare|"
        r"lösningsarkitekt|systemarkitekt|dataarkitekt|it-arkitekt|integrationsarkitekt|"
        r"solution architect|software architect|"
        r"\btestare\b|testledare|systemtestare|"
        # `entwickler` needs both lookbehinds: German *Produktentwickler* is product development
        # and *Elektronikentwickler* is hardware. `logicel` is not a typo here — Thales
        # publishes it live, and a real corpus contains real misspellings.
        r"logiciel|logicel|d[ée]veloppeur|d[ée]veloppeuse|"                     # fr
        r"architecte\s+(?:logiciel|syst[èe]me|technique|solution|d'entreprise)|"
        r"sviluppator|"                                                         # it
        # Three more prefixes on `entwickler`, all the same class the two existing lookbehinds
        # were added for and all present on the SWEDISH twin already: `Projektentwickler` is a
        # real-estate/renewables project developer, `Organisationsentwickler` is OD/HR, and
        # `Schnittentwicklerin` is a garment pattern cutter. (2026-08-26)
        r"(?<!produkt)(?<!elektronik)(?<!projekt)(?<!schnitt)(?<!organisations)entwickler|"
        # The German apprenticeship has two named Fachrichtungen and only one is software:
        # **Anwendungsentwicklung** is application development, **Systemintegration** is
        # sysadmin/infrastructure — 10 of the fragment's 15 postings. It read the umbrella word
        # and could not see the qualifier that decides the job. (2026-08-26)
        r"fachinformatiker(?![^|]{0,30}systemintegration)|"             # de
        # --- wave 2 ---
        r"systems? architect|integration architect|"                    # en
        # PT. Portuguese for `developer`; Spanish is `desarrollador`, so no collision.
        # --- Norwegian, 2026-08-11 (N34, N35) — graded against NAV STYRK-08 ---
        r"(?<=[a-zæøå]{3})(?<!forretnings)(?<!produkt)(?<!elektronikk)(?<!organisasjons)(?<!tjeneste)utvikl(?:er|ar)\b|testleder|testansvarlig|"
        # --- wave 3 (2026-08-17) ---
        # cs-08  cs — roles-cs-w3.md
        r"(?:it|enterprise|solution|software|datov\w+|cloud\w*|síťov\w+|technick\w+)[- ]?\s*architekt|"
        # de-07  de — roles-de-w3.md
        # `\binformatiker` — the German role noun was added unbounded and immediately read
        # Swedish compounds it was never measured against: `Bioinformatiker` (5 postings, a
        # research profession), `Hälsoinformatiker` and `Forvaltningsinformatiker`, 8 postings
        # in all. The boundary keeps bare `Informatiker` (and `Fachinformatiker` /
        # `wirtschaftsinformatik`, which ship separately) and reaches none of them.
        r"softwareentwickl|wirtschaftsinformatik|\binformatiker|"
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
        r"marketing|marketingov|marketér|\bseo\b|\bsem\b|brand manager|"
        # `growth` DECLINES when the title also names a sales role. `sales` runs AFTER this
        # pattern, so it cannot out-compete `growth` — the catch-all has already answered, the
        # same reason `\bengineer\b` has to decline rather than lose. In "Enterprise Account
        # Executive, Growth" and "Account Manager, SME & Growth" the word names a customer
        # SEGMENT, not the work: measured at **173 postings moving marketing -> sales and none
        # to uncategorised**, led by `Enterprise Account Executive, Growth` (22) and `Customer
        # Growth Sales Account Executive` (20). NAV grades `Growth Manager` sales too. Collateral
        # is nil because the `marketing` alternative above still claims anything that says
        # marketing at all — "Business Development/Sales & Growth Marketing" stays here on that
        # word rather than on `growth`, which is why 4 of the 177 candidates did not move.
        # All five answer-key slices are byte-identical, so this is a corpus verdict.
        # **Extended 2026-08-26, and the reason is structural rather than a new measurement:
        # an enumerated decline list goes stale, and this one shipped in #76 on the SAME DAY
        # `partnerships` and `strategy` were split out of the residual in #83.** It was written
        # when two of the categories its members now belong to did not exist. Measured today:
        # 91 wrong of 413 — partnerships 25 (`GTM Partnerships Manager, SME & Growth`), product
        # 25 (`Director of Product, Growth/AI`), account management 19, customer success 16,
        # hr_recruiting 6 (`Growth Recruiter, High Volume - Contract`).
        #
        # The mechanism is unchanged; only the list grows. Anything naming marketing outright
        # still stays here on that word rather than on `growth`, which is why the original's
        # collateral was nil and remains so.
        r"^(?!.*(?:\bsales\b|account executive|account manager|account director|"
        r"account manage|business development|customer success|partnership|"
        r"product manag|product owner|of product|"
        r"\brecruiter\b|\bsdr\b|\bbdr\b)).*growth|"
        # `\bpr\b` is `(?<!\d/)\bhr\b` in a second costume, and it needs the same two guards.
        # It reads the Norwegian preposition ("2-3 dager **pr** uke") and the US immigration
        # status ("Sr Technical Project Manager (Citizen/**PR** only)"). 2 postings against 25
        # real PR titles, and no title in the corpus writes "<word>/PR" or "pr <period>" any
        # other way, so both guards are free. The Puerto Rico postal code
        # ("... Outlets Barceloneta **PR**") is the same mechanism and is NOT caught — a bare
        # trailing token cannot be told from a role word without a place list.
        r"copywriter|content marketing|(?<!/)\bpr\b(?!\s+(?:uke|uker|veke|week|month))|"
        r"public relations|kampan|"
        # `marknad` needs its LEADING boundary — this is the `georgia` rule in Swedish. Bare, it
        # reads the tail of compounds that are not marketing at all: **arbetsmarknad** (the
        # labour market — 43 postings of municipal employment services,
        # "Arbetsmarknadskonsulent", "Arbetsmarknadssekreterare", "Handläggare till
        # Arbetsmarknadsenheten"), **eftermarknad** 11 (aftermarket/after-sales service),
        # **apoteks-/friskole-/privatmarknad** 9, **stormarknad** 7 (a hypermarket, so "Lönechef
        # till EKO Stormarknad" was marketing), **elmarknad/energimarknad** 6 and
        # **servicemarknad** 4. 81 postings match, **79 leave** (`kommunikatör` rescues two), and
        # 17 of them are sales titles marketing was stealing ("Fältsäljare ... på
        # Apoteksmarknaden" ×6, "Utesäljare – Service & Eftermarknad"). Collateral is nil: the
        # corpus holds no marketing compound of the shape `<word>marknad` —
        # online/digital/webb/content/handels all return zero — because Swedish writes the
        # modifier the other way round ("digital marknadsföring"), which the boundary keeps. The
        # two SSYK key rows in this family are graded OUT OF SCOPE, i.e. the register itself says
        # they are not marketing, and `uncategorised` is the honest answer for 56 of them.
        # **A SECOND mechanism survives that leading boundary, and it is a different one
        # (2026-08-26).** The `<word>marknad` compound problem is fixed; what is left keeps the
        # leading boundary intact and is still not marketing — `marknadsföring` naming the
        # EMPLOYER'S SECTOR ("B2B-säljare till ett snabbväxande bolag inom digital
        # marknadsföring", "Mötesbokare till marknadsföringsbyrå"), and `marknadsledande` /
        # `marknaden` used as an ordinary adjective and noun ("Innesäljare till marknadsledande
        # 1KOMMA5°", "Winback säljare mot norska Marknaden"). 31 wrong of 81, and `marketing`
        # runs before `sales`, so it wins them outright — the same asymmetry the note above
        # invokes. The seller-naming guard is what does the work; `marknadssäljare` and
        # `Marknadskoordinator` are untouched because they name no separate seller.
        r"^(?!.*(?:\bsäljare\b|innesäljare|utesäljare|mötesbokare|account manager))"
        r".*\bmarknad(?!sledande|en\b)|"
        r"kommunikatör|kommunikationsansvarig|kommunikationschef|"
        # NO. The Swedish forms above cannot reach the Norwegian spelling (`-sjon-`), so 16
        # postings of the register's commonest communications title were declined.
        # `kommunikasjonssjef` and `kommunikasjonsleiar` are deliberately absent: both are
        # plausible Norwegian and both fire on 0 corpus rows and 0 key rows today.
        r"kommunikasjons(?:rådgiver|rådgjevar|leder|ansvarlig|direktør)|"
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
        # **PPC is also the *Penitentiair Psychiatrisch Centrum*** — the Dutch prison
        # psychiatric service — and five of this fragment's postings are its psychiatrists,
        # a security officer and a care-team manager, filed as pay-per-click. The `georgia`
        # rule in three letters. `healthcare` already rescues the four rows whose title also
        # names a psychologist; the guard is what releases the rest.
        r"^(?!.*(?:business development|penitentiair|psychiat|forensisch|zorgteam|nifp))"
        r".*\bppc\b|"
        # **Buying MEDIA is advertising; buying PARTS is procurement.** `operations` reads a
        # bare `\bbuyer\b` and runs later, so six postings — "Media Buyer / Meta Ads",
        # "Senior Media Buyer, Programmatic & Native", "Art Buyer - H&M Studios" — were
        # procurement. Named here, where they belong, and lookbehind-declined over there:
        # a narrowing without this line would only have made them uncategorised.
        r"media buyer|art buyer|"
        # en-01  en — roles-en-w3.md
        r"(?:director|head|vp|vice president)[^|]{0,24}?\bcommunications?\b|"
        # en-02  en — roles-en-w3.md
        r"demand gen(?:eration)?\b|"
        # en-05  en — roles-en-w3.md
        r"^(?!.*recruit).*\bevents?\s+(?:manager|coordinator|producer|specialist|lead|director|associate|marketer)|"
        r"public affairs|government affairs|corporate affairs", re.I)),
    ("sales", re.compile(
        # `business develop` rather than `business development`: the *-er* form is the same job
        # and `software_engineering`'s bare `\bdeveloper\b` was claiming it (50 postings, none
        # of them a programmer). Narrowing there without widening here would have turned a
        # misfile into a decline. Paired change, 2026-08-26.
        r"\bsales\b|account executive|account manager|key account|business develop|"
        # NO. An insurance adviser sells insurance — ISCO/STYRK 3321 files it under sales,
        # and 18 postings of it were declined. Bound to the compound, never bare `rådgiver`:
        # that word alone is 253 declined NAV postings spread over eleven categories with a
        # 27% plurality, which is a coin flip, not a gap.
        r"forsikringsr[åa]dgi|"
        # NO. An insurance adviser sells insurance — ISCO/STYRK 3321 files it under sales,
        # and 18 postings of it were declined. Bound to the compound, never bare
        # `rådgiver`: that word alone is 253 declined NAV postings spread over eleven
        # categories with a 27% plurality, which is a coin flip, not a gap.
        r"forsikringsr[åa]dgi|"
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
        # A *Visual* Merchandiser dresses the shop and a "Merchandiser/Godisplockare" fills
        # the sweet display — both are this category. A Junior, Assistant, Collection,
        # Online or Omni Merchandiser sits in the head-office buying team planning what the
        # shop will carry, which is `operations` (16 postings, one UK retailer's grades).
        # The grade word is the whole signal, and it is the only one the title gives.
        r"^(?!.*(?:assistant|asst|junior|collection|online|omni|interim)\s+merchandiser)"
        r".*merchandiser|"
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
        r"human resources|(?<!\d/)\bhr\b|"
        # `rekryter` leaks in two directions and neither is vocabulary.
        #
        # **It is a Swedish VERB before it is a noun.** "Vi rekryterar Tågtekniker till
        # Göteborg!" is an advertisement FOR a train technician, written by whoever is hiring
        # one — five of those, plus "Essge-Plast i Östersund rekryterar Produktionschef",
        # "Modine rekryterar Chef Produktionsteknik" and "Strategisk inköpare rekryteras till
        # Region Östergötland". `rekryterar(?!e)` is the split: the recruiter is *rekryterare*,
        # one letter longer, and the participle *rekryterande* ("Rekryterande konsultchef") is
        # a real HR title and does not match either.
        #
        # **In a pipe-delimited title the role is the FIRST segment and the rest is the
        # agency's branding**: "Driftkoordinator VVS | Lernia Rekrytering & Bemanning |
        # Stockholm" is a plumbing coordinator's job at a client, and "General Operatives |
        # Lernia Bemanning & Rekrytering | Gävle" is warehouse work. `^[^|]*rekryter` reads the
        # segment the employer put the job in. That is the Mekonomen mechanism wearing a
        # staffing agency's name, and the same rule the `montage` employer compounds needed.
        r"^(?!.*(?:rekryterar(?!e)|rekryteras|direktrekryter))[^|]*rekryter|"
        # `nábor` is the Czech twin of the `$80/hr` bug: a **náborový příspěvek** is a signing
        # bonus advertised in the title of the job it is offered for, so "Stříkač - náborový
        # příspěvek 30 000 Kč" (a sprayer) and "Laminátník - …" (a laminator) are factory work
        # that the ISCO key codes as manufacturing_production. Money in a title is never the
        # job. 0 live postings today only because healthcare and manufacturing run first; 2
        # rows on the Czech answer key, and the mechanism is what recurs.
        r"personalist|nábor(?!ov\w{0,3}\s*(?:příspěv|bonus|prémi))|náborář|mzdov[áý] účetní|"
        # French `recrut` is NOT reachable from the existing English `recruit`: recrutement has
        # no i. The two words diverge at the fifth letter — the same near-miss as
        # `skladník`/`Skladníci`. `\brh\b` is a two-letter token and the weakest term here;
        # `ressources humaines` alone is the safe subset if it ever misbehaves.
        r"recrut|ressources humaines|\brh\b|"                                   # fr
        r"risorse\s+umane|"                                                     # it
        r"recursos humanos|\brrhh\b|"                                   # es
        # --- wave 2 ---
        # `\bpeople\b` is the department; "2 People Only From Vancouver … Data Entry" and "We
        # want more people in the kitchen" are the ordinary English noun, counted. The
        # lookbehinds are positional for the same reason `(?<!\d/)\bhr\b` is: the HR sense
        # never has a number or "more" in front of it, and a whole-title rule would discard
        # "Working Student - Culture & People". 7 postings, six of them one multiplying
        # data-entry template.
        r"\bhrbp\b|(?<!\d )(?<!more )\bpeople\b|"                       # en
        r"learning (?:&|and) development|\bl&d\b|"
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
        # `counsel` is a SUBSTRING and it reaches into `counsellor`/`counselor`/`counselling` —
        # the same shape as Dutch `recht` inside *Utrecht*, three lines down. Three corpus
        # postings, all of them filed as lawyers: `100% Permanent School Counsellor`,
        # `Affiliate EAP Counsellor (Network Partnership)` and `Banamex Investment Counselor B
        # Puebla`. `[oi]` covers both the agent noun and the gerund in both spellings; the
        # lookahead cannot reach `Legal Counsel` or `Commercial Counsel` (25 postings), which is
        # why it is a lookahead and not a whole-title exclusion.
        r"\blegal\b|\blawyer\b|\battorney\b|counsel(?!l?[oi])|paralegal|compliance officer|"
        # **`koncipient` is not a legal word — it is Czech for a TRAINEE in a licensed
        # profession**, and the qualifier in front names the profession: `advokátní` (lawyer),
        # `notářský` (notary), `finanční` (finance), `realitní` (real estate). Bare, it filed 2
        # estate agents as lawyers — `Realitní makléř/ka, realitní koncipient/ka` and its longer
        # twin — so it is BOUND, the `_IT_ROLE` construction in Czech.
        #
        # **A negative lookbehind on `realitní` was tried first and it only moved 1 of the 2**,
        # which is the transferable half of this: `Realitní makléř / makléřka, realitní koncipient
        # / koncipientka` writes the feminine form too, and `koncipientka` contains `koncipient`
        # preceded by `/ `, not by the qualifier. **A lookbehind guards one occurrence; a title
        # that names the role twice defeats it.** Binding cannot be defeated that way.
        # `Finanční koncipient/ka / Finance Trainee` was already finance — that pattern runs
        # first — so the finance qualifier needs no branch here.
        r"právník|právnik|advokát|jurist|(?:advok[áa]tn|not[áa][řr]sk)\w*\s+koncipient|"
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
        # A *Service* or *Support* Contract Manager renews maintenance agreements — that is
        # aftermarket account work, not legal — and a *Sub-Contract Manager* is the
        # commercial side of a construction package. Eight postings, all of which DECLINE
        # rather than move — `sales` and `customer_support` both run later but neither reads a
        # contract word, and that is the intended outcome, not an oversight.
        #
        # The generic "Contracts Manager" is left alone. In-house contract management genuinely
        # is this category, the construction sense wears the identical title, and no answer key
        # covers English — the same reason `Solutions Engineer` is still open in the ledger.
        r"^(?!.*(?:service contract|support contract|sub-?contract))"
        r".*\bcontracts?\s+(?:manager|specialist|administrator|analyst|lead|director)\b|"
        r"jur[íi]dic", re.I)),                                          # pt
    ("customer_support", re.compile(
        r"customer (?:support|service|care)|help ?desk|technical support|"
        # Bare `support specialist` read "Application Development & Support Specialist" (22
        # postings, one employer) as a support job when the head of the compound is
        # *development*. Excluded at title level, the same shape as `drifttekniker` above:
        # a "… Development … Support Specialist" is whoever built the thing, and
        # `software_engineering` runs 246 lines earlier with the better claim on it. Nothing
        # correct in the corpus pairs the two words — the 55 other support-specialist titles
        # are Product/Technical/IT/Client, none of them Development.
        r"^(?!.*\bdevelopment\b).*support specialist|"
        r"it[- ]?support|service desk|kundtjänst|kundservice|podpora zákazn|"
        # Bare `zákaznick` is a Czech ADJECTIVE modifying a department, not a job: the
        # profession in "Vzorkař/vzorkařka - oddělení zákaznického servisu" is *vzorkař*,
        # a sampler, and "TECHNIK ZÁKAZNICKÉ KVALITY" is a customer-quality technician on a
        # production line. `podpora zákazn` on the same line reads the real support title.
        r"^(?!.*(?:vzorkař|kvalit|podnikových projekt)).*zákaznick|"
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
        # An office manager runs *an* office; a "Front Office Manager" runs a hotel reception,
        # a "Middle Office Manager" a bank's trade support, a "Post Office Manager" a postal
        # branch, and a "Project Management Office Manager" a PMO. Every one of the 8 postings
        # `office manager` bare was holding under those five qualifiers was wrong (6 titles,
        # zero collateral, measured 2026-08-22) — and hospitality and finance both run BEFORE
        # this entry, so neither could rescue its own.
        r"(?<!post )(?<!middle )(?<!front )(?<!back )(?<!management )office manager|"
        # `provozn` is the Czech stem for "operational" and it reads two different jobs.
        # "Provozní manažer / ředitel / oddělení" is this category; "provozní technik" is a
        # plant/maintenance technician and "provozní zootechnik" a livestock keeper — 12
        # postings over 9 titles, and skilled_trades and manufacturing_production both run
        # BEFORE this entry without naming either, so nothing else could take them back.
        # **A lookbehind-style guard on one adjacency guards ONE occurrence**, the `koncipient`
        # lesson: `provozn\w*[\s/–-]+…technik` requires the technical word IMMEDIATELY after the
        # separator, so an interposed adjective walks straight through — "Provozní/investiční
        # technik/technička" — and the guard reads the adjective *provozní* but not the adverb
        # *provozně*, so "PROVOZNĚ TECHNICKÝ PRACOVNÍK" was never in scope at all. `[^|]{0,14}?`
        # closes the first and `\w*` already covers the second once the separator can be
        # crossed. `energetik` and `mechanizátor` join `technik`: a plant energy engineer and a
        # farm machinery operator are no more operations managers than the technician is.
        r"^(?!.*provozn\w*[\s/–-][^|]{0,14}?(?:zoo)?(?:techni[kc]|energetik|mechanizátor))"
        r"(?!.*provozn\w*\s+pizzerie).*provozn|"
        r"nákupčí|nákupca|inköpare|"
        r"acheteu|approvisionn|\bachats?\b|"                                    # fr
        r"\bacquisti\b|"                                                        # it
        r"eink[äa]uf|"                                                          # de
        r"operacion|operaci[óo]n|compras|comprador|"                            # es
        r"inkoop|inkoper|"                                                      # nl
        r"zakup|"                                                               # pl
        r"am[ée]lioration continue|excellence op[ée]rationnelle|"        # fr-2
        # --- wave 2 --- English words the file already knows in other languages.
        # `(?<!media )(?<!art )` — see the `media buyer` line in `marketing`, which now
        # answers these first anyway; the lookbehinds are the belt to that braces, and cost 0.
        # `(?:assistant|junior|…) merchandiser` is the retail BUYING office: a Junior or
        # Assistant Merchandiser plans assortment and stock, which is the same buy side this
        # line already names as `buyer` and `inköpare`. `sales` runs earlier and keeps the
        # in-store sense — Visual Merchandisers and Cloetta's "Merchandiser/Godisplockare".
        r"(?<!media )(?<!art )\bbuyer\b|purchasing|facilit(?:y|ies) manager|"   # en
        r"(?:assistant|asst|junior|collection|online|omni|interim)\s+merchandiser|"
        r"continuous improvement|operational excellence|\blean\b|"
        # --- wave 3 (2026-08-17) ---
        # sv-01  sv — roles-sv-w3.md
        r"verksamhetsutvecklare|"
        # sv-03  sv — roles-sv-w3.md
        r"upphandl|"
        r"(?:demand|supply|material|capacity) plann?(?:er|ing)", re.I)),
    # Residual for a business function at a tech company that none of the above names.
    # Deliberately last of the business group and much narrower than it was.
    # Program/project-delivery management — its own category since 2026-08-23, promoted out of
    # `other_tech_function` (where no chip could select it, so a program-management subscriber
    # could not filter for it). It sits LAST among the real categories, immediately before the
    # residual, because any category naming the actual function ("Marketing Program Manager",
    # "Legal Program Manager") must answer first — the reason this fragment lived in the residual.
    # Relabel-only: the exact fragment that landed here before, now reachable.
    ("program_management", re.compile(
        # `\btpm\b` was carried over verbatim in the 2026-08-23 promotion and nothing
        # re-examined it. Removed 2026-08-26: in Czech manufacturing **TPM is Total
        # Productive Maintenance**, a maintenance discipline, and one row names the
        # department next to a draughtsman's job (`Technický kreslič oddělení TPM`); a
        # third sense is Third Party Management. 6 of 9 wrong, and the whole positive
        # yield was 3 titles that also spell the words out, which the two fragments
        # beside it already claim.
        r"program manager|programme (?:manager|lead|director)", re.I)),
    # Partnerships / alliances — promoted from the residual 2026-08-23. Same relabel-only move:
    # the `partnership` fragment that used to fall to `other_tech_function`.
    ("partnerships", re.compile(
        r"partnership", re.I)),
    # Corporate / GTM / business strategy — promoted from the residual 2026-08-23. Runs after
    # every function category, so a "Product Strategy" / "Content Strategy" title is only claimed
    # here when nothing more specific did — exactly what the residual did before. Matches the
    # literal `strategy` only, NOT `strateg`ist/`strateg`ic, so the matched set is unchanged.
    ("strategy", re.compile(
        r"strategy", re.I)),
    ("other_tech_function", re.compile(
        r"business analyst|business analist|"   # nl — see the `analist` note in data_analysis
        # **`\bcontent\b` and `community` shipped bare until 2026-08-26, and a misfile HERE is
        # the most expensive kind in the file.** No chip maps to this bucket
        # (`ROLE_ID_FOR_CATEGORY` is display-only, one way), so a claim is a coverage hole
        # rather than a wrong digest; and `ingest.role_category` consults the categoriser cache
        # **only on a decline**, so a confident wrong answer keeps the row out of
        # `store.uncategorised_titles()` for good and it is never re-asked. Wrong here is
        # permanent in a way wrong anywhere else is not.
        #
        # Both were untouched since the original 23-category split and both are the exact class
        # the 2026-08-22 pass narrowed one block below (bare `assistent`/`koordinator`) — it
        # fixed those two and stopped. Measured: `\bcontent\b` claimed 123 postings of which ~81
        # are content *marketing*, editorial and brand work (`Content Manager` ×5,
        # `Senior Content Marketer`, `Brand and Content Strategist`, `Social Content Producer`);
        # `community` claimed 66 of which ~40 are wrong, and **21 of those are not jobs at all**
        # but ATS talent-pool sign-up pages (`Join our Talent Community`), with the rest naming
        # their own profession elsewhere in the string (`Federal Account Director, Intelligence
        # Community`, a gastroenterologist, a security researcher).
        #
        # Removed rather than guarded, on the 2026-08-22 argument: a decline hands the row to
        # the one mechanism that can still read it. `marketing` and `social_media` own the
        # senses worth keeping (`content marketing`, `copywriter`, `content creator`,
        # `community manager`), and they run earlier. The ~42 genuinely-residual rows
        # (content moderation, technical content) go `uncategorised` — which costs no
        # reachability, because nothing could reach them here either.

        # Clerical data entry ("Data Entry Clerk", "Remote Data Entry", ~226 postings): no data
        # pattern above reads it (they all want engineer/analyst/scientist/science), so it falls
        # here to the admin residual, which is what it is.
        r"data entry|"
        r"administrativ|"
        # `assistent` / `asistent` / `koordinator` / `koordinátor` shipped BARE here until
        # 2026-08-22, and they are generic ROLE HEADS — exactly what `_DE_ROLE`'s docstring
        # forbids ("NONE of these may ship bare"), for exactly the reason it gives. This was
        # the last block in the file not following its own rule, and `koordinator` is *in*
        # `_DE_ROLE`. Bare, they read the Scandinavian and Czech compound from the inside.
        #
        # Measured over every active posting (2026-08-22): bare `assistent` won 393 postings
        # across 353 titles, of which ~350 are LSS/BPA personal assistance, `lärarassistent`,
        # `forskningsassistent`, `laboratorieassistent`, `rehabassistent`, `tandläkarassistent`
        # — care, teaching and lab work, not admin. Bare `koordinator` won 238 (`Pasientkoordinator`,
        # `Vårdprocesskoordinator`, `Barnekoordinator`, `Transportkoordinator`,
        # `Bemanningskoordinator`), bare `koordinátor` 42 (BOZP, montáže, realizace). On all
        # three publisher answer keys the four fragments earn **zero** correct rows, and cost
        # 31 wrong ones on the Norwegian key alone — the whole of its
        # `healthcare -> other_tech_function` column, which this change removes outright.
        #
        # **The damage was not a wrong digest, it was a permanent one.** No chip maps to
        # `other_tech_function` (`ROLE_ID_FOR_CATEGORY` is display-only — see the note in
        # `web/lib/options.ts`), so a claim here is a coverage hole; and `ingest.role_category`
        # consults the weekly categoriser's cache **only on a decline**, so a confident wrong
        # answer keeps the row out of `store.uncategorised_titles()` for good. That is why the
        # `personlig assistent` note in `social_care` — "residual … left as declines" — was not
        # true when it was written: these fragments went on claiming the residual, so it was
        # never asked about. Narrowing them hands 695 postings to the one mechanism that can
        # still read them.
        #
        # Bound to an admin domain the head is right, and every binder below is a form the live
        # corpus publishes — `Kontorsassistent`, `Ledningskoordinator`, `Managementassistent`,
        # `Arkivassistent`, `Asistent/ka ředitele` — not a guess. It keeps 58 postings a bare
        # deletion would have lost, at flat accuracy on all five slices.
        r"(?:kontors|förvaltnings|lednings|verksamhets|samordnings|bolags|enhets|arkiv|"
        r"chefs|konsultchefs|management\s?|office\s|back[\s-]?office[\s-]?)"
        r"(?:ass?istent|koordin[aá]tor)|"
        r"\bvd[\s-]?ass?istent|"
        r"asistent[^|]{0,20}?\s+(?:ředitel|jednatel|vedení|kancelář|ceo|generáln|"
        r"back\s?office)|"
        # English admin titles: the CZ/SE spellings above never matched "Executive Assistant".
        # **The `office manager` lookbehinds, carried across 2026-08-26.** That fragment in
        # `operations` guards `(?<!post )(?<!middle )(?<!front )(?<!back )(?<!management )` and
        # its comment says why: "a 'Front Office Manager' runs a hotel reception, a 'Middle
        # Office Manager' a bank's trade support". The lesson was applied to the manager form
        # and not to the assistant form — and the assistant form's damage is LARGER than the
        # 8 postings that motivated the manager fix: `Front Office Assistant` is 23 postings
        # from one Oracle tenant, plus `Back Office Assistant`. The proof sat side by side —
        # `Front Office Manager` classified `uncategorised` while `Front Office Assistant`
        # classified into this unreachable bucket.
        #
        # A front-desk job cannot be resolved from the title (hotel reception or bank front
        # office, depending on employer), and that unresolvability is itself the argument
        # against claiming it confidently into a bucket no chip can select. Cost: zero — the
        # corpus's real office assistants are `Executive & Office Assistant`, `Junior Office
        # Assistant`, `Regional Office Assistant`, `Remote Office Assistant`, none of which
        # carries a front/back qualifier.
        r"(?:executive|administrative|(?<!front )(?<!back )(?<!back-)office|personal) assistant|"
        r"assistant\w*[ .]?e?\s+de\s+direction|"                                # fr
        r"segretari|segreteria|"                                                # it
        r"sachbearbeiter|kaufmann|kauffrau|kaufleute|"                  # de
        # --- wave 2 ---
        # (`partnership` was promoted to its own `partnerships` category 2026-08-23)
        r"business process|"                                            # en
        # --- Norwegian, 2026-08-11 (N38) — graded against NAV STYRK-08 ---
        r"\bsekretær|"
        # --- wave 3 (2026-08-17) ---
        # sv-05  sv — roles-sv-w3.md
        r"projektadministratör|"
        # en-16  en — roles-en-w3.md
        r"implementation (?:consultant|manager|specialist|lead)|"
        # en-17  en — roles-en-w3.md
        r"chief of staff|"
        # (`program manager|programme …|\btpm\b` was promoted to its own `program_management`
        #  category 2026-08-23 — see that entry above, which preserves the function-first order.)
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

#: Spans that name a WORKPLACE or a SECTOR rather than a profession, and must therefore lose
#: to any *profession* word in the same title. Keyed by category; each pattern is matched with
#: `fullmatch` against the span a category's own alternation produced.
#:
#: **This is "specific before general" one level below pattern order.** `healthcare` runs first
#: so that a nurse is never taken by `\banalyst\b`, and that is right — but the same first
#: position also let `sjukvård`, `äldreomsorg`, `tandvård`, `hemtjänst`, `vårdcentral` and Czech
#: `zdravotn` classify the *employer* instead of the job: `Jurist inom Hälso- och
#: sjukvårdsjuridik`, `Kock till Äldreomsorgen Alingsås`, `Data scientist, Folktandvården`,
#: `Rekryterare till äldreomsorgen`, `Full-stack vývojář ... ve zdravotnictví` and
#: `Sociální pracovníci ... (kromě péče o zdravotně postižené)` were all healthcare. Order
#: cannot fix that: a *profession* word is specific and a *sector* word is general, and one
#: alternation cannot be both first and last.
#:
#: The list enumerates OUR OWN vocabulary, not other people's professions, which is why it
#: cannot rot the way `^(?!.*(?:receptionist|jurist|kock|...))` would — every new intruder
#: profession is handled by the category that owns the word, with no edit here.
#:
#: Deferral is per *title*, not per span: a title where the category also matches something
#: non-sector (`Dental Turism AB söker erfarna tandsköterskor`, `Folktandvården söker
#: övertandläkare`) is not deferred at all. Only a title whose EVERY match is a sector span is.
_SECTOR_ONLY: dict[str, "re.Pattern[str]"] = {
    "healthcare": re.compile(
        r"sjukvård|äldreomsorg|tandvård|hemtjänst|zdravotn|.*vårdcentral", re.I),
}


#: A sector word yields to a *profession*, never to the residual bucket. `other_tech_function`
#: is not a claim that the job is anything — no chip maps to it (`ROLE_ID_FOR_CATEGORY` is
#: display-only, one way), so a row there is reachable by nobody and `ingest._classify` will
#: never re-ask about it either, because the model cache is consulted only on a decline.
#:
#: **The 15 postings that motivated this guard no longer need it, and the number here is the
#: corrected one: 4.** When it was measured, the deferral pushed `Rehabkoordinator till
#: Vårdcentralen Ryd`, `Klinikassistent till Vårdcentral Malung` and `Zdravotní asistentka v
#: oční ambulanci` into the residual bucket — but that was against a tree where
#: `other_tech_function` still shipped a bare `koordinator`/`assistent`. The misfile pass
#: narrowed those heads, so it no longer claims those titles at all: the intruder was fixed at
#: the source, which is the better fix. **Mutation-checking this guard is what found that** —
#: emptying it changed nothing, because the case it was written for had already been repaired
#: one commit earlier. Same shape as the wave-3 proposal a committed fix made inert
#: mid-measurement.
#:
#: What survives is narrower and worth stating honestly, because it points the other way: the
#: 4 remaining postings are `Vedoucí administrativní pracovník/ce ve zdravotnictví` (x2),
#: `Odborní administrativní pracovníci v oblasti zdravotnictví` and `Administrativ assistent
#: med erfarenhet av hemtjänst` — titles where the sector is healthcare and the profession
#: really *is* administration, so the mechanism's own logic says `other_tech_function` is the
#: correct answer. The guard overrides it anyway, and that is a **reachability** choice rather
#: than a correctness one: a subscriber can select healthcare and cannot select the residual
#: bucket, so a soft misfile beats a row nobody can reach. Stated rather than implied, because
#: the next person to read this should be able to disagree with it on the number.
_DEFER_NEVER_TO: frozenset[str] = frozenset({"other_tech_function"})


def _sector_only(category: str, pattern: "re.Pattern[str]", text: str) -> bool:
    """True when every match `category` found in `text` is a workplace/sector word."""
    sector = _SECTOR_ONLY.get(category)
    if sector is None:
        return False
    spans = [m.group(0) for m in pattern.finditer(text)]
    return bool(spans) and all(sector.fullmatch(s) for s in spans)


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
    deferred: str | None = None
    for category, pattern in PATTERNS:
        if not pattern.search(text):
            continue
        if deferred is not None and category in _DEFER_NEVER_TO:
            continue
        if _sector_only(category, pattern, text):
            # A workplace, not a profession — let a later category's profession word win.
            # First one seen is remembered, so pattern order still decides the fallback.
            if deferred is None:
                deferred = category
            continue
        return category
    if deferred is not None:
        return deferred
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
    # "program manager" is kept here as the recall half for *product* subscribers even though it
    # now has its own `program_management` category (2026-08-23): a product subscriber plausibly
    # wants programme roles offered too, so it retrieves and the AI matcher decides. Breadth in
    # the keywords, precision in the regex.
    "product": ["product manager", "product owner", "produktový manažer", "produktový vlastník",
                "program manager"],
    "program_management": ["program manager", "programme manager", "delivery manager", "pmo",
                           "program management"],
    "partnerships": ["partnerships", "partnership manager", "partner manager", "alliances",
                     "strategic partnerships"],
    "strategy": ["strategy", "corporate strategy", "gtm", "go-to-market", "business strategy"],
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
    "program_management": "program management",
    "partnerships": "partnerships",
    "strategy": "strategy",
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
