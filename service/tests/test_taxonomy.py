"""The role taxonomy has one Python definition — these tests keep the rest in step.

`service/taxonomy.py` is imported by ingest, the shortlist builder, the digest subject
line and the CV parser, so those four cannot drift. Two consumers can't import Python:

  - ``dbt/models/staging/stg_job_postings.yml`` — an `accepted_values` test that runs in
    Snowflake. If a category is added here and not there, `dbt test` fails in CI on real
    data, which is a slow and confusing way to find out.
  - ``web/lib/options.ts`` — the vocabulary both signup forms offer, mapping each role chip
    to a category. A typo or a renamed category here silently produces a profile that
    matches nothing.

So these tests read those two files as text and assert they agree with `CATEGORIES`.

The web side moved twice and the paths below record where it landed. The pages now live
under ``web/app/(site)/[locale]/`` because the site is exported once per language, and the
chip → category maps moved out of the pages into ``web/lib/options.ts`` because a chip is
now keyed by a stable id rather than by its English label — a label that eight catalogues
rewrite cannot also be a lookup key. That refactor is precisely the kind of change these
tests exist to catch, so they follow it rather than being relaxed around it.
"""

import re
from pathlib import Path

import pytest

from service import taxonomy

ROOT = Path(__file__).resolve().parents[2]
DBT_SCHEMA = ROOT / "dbt" / "models" / "staging" / "stg_job_postings.yml"
WEB_OPTIONS = ROOT / "web" / "lib" / "options.ts"
WEB_PAGE = ROOT / "web" / "app" / "(site)" / "[locale]" / "page.tsx"

#: A title no pattern in PATTERNS can read, so the hint is the only thing left to decide the
#: answer — which is exactly the path that was writing raw source labels into the column.
OPAQUE = "Något Oklassificerbart"


# ------------------------------------------------------------------ classifier ---

@pytest.mark.parametrize("title,expected", [
    ("Analytics Engineer", "data_engineering"),
    ("Senior Data Scientist", "machine_learning"),
    ("BI Analyst", "data_analysis"),
    ("Site Reliability Engineer", "devops_platform"),
    ("Product Owner", "product"),
    ("Product Designer", "design"),            # design must win over product
    ("Java vývojář", "software_engineering"),  # CZ title
    ("Účetní", "finance_accounting"),          # CZ title — was other_tech_function until
                                               # the 2026-08-09 split
    # The sectors added when the taxonomy stopped being tech-only. Each must beat a broad
    # tech pattern that would otherwise swallow it.
    ("Všeobecná sestra", "healthcare"),        # CZ nurse
    ("Sjuksköterska", "healthcare"),           # SE nurse
    ("Učitelé na 1. stupni základních škol", "education"),
    ("Financial Analyst", "finance_accounting"),  # NOT data_analysis: analyst is broad
    ("Elektrikář", "skilled_trades"),
    ("Stavbyvedoucí", "construction"),
    ("Skladník", "logistics_transport"),
    ("Produktionstekniker", "manufacturing_production"),
    ("Restaurangchef", "hospitality"),         # SE: "chef" here means manager, not cook
    ("IT-chef", "uncategorised"),              # ...and the same word must NOT mean hospitality

    # Swedish terms, written from the titles the scorer showed being declined (2026-08-09).
    # The register is the largest non-English source and every one of these was landing in
    # `uncategorised` — an honest answer, but a needlessly common one.
    ("Systemutvecklare", "software_engineering"),
    ("Senior testare", "software_engineering"),
    ("Servicetekniker", "skilled_trades"),
    ("Pizzabagare", "hospitality"),
    ("Restaurangbiträde", "hospitality"),
    ("Köksbiträde", "hospitality"),
    ("Anläggningsarbetare", "construction"),
    ("Plattsättare", "construction"),
    ("Orderplockare", "logistics_transport"),
    ("Lagermedarbetare", "logistics_transport"),
    # SSYK files welders under industrial manufacturing, not trades, and the boundary it
    # draws is coherent: factory production is manufacturing, install/repair/service is a
    # trade. Following it costs nothing and stops the scorer reporting a definitional
    # disagreement as a classifier error (41 confusions on 2026-08-09).
    ("Svetsare", "manufacturing_production"),
    ("Verkstadsmontör", "manufacturing_production"),
    ("Backend Engineer", "software_engineering"),
    # Non-software engineering (added 2026-08-09), and the boundary against software that its
    # ordering exists to hold. A discipline qualifier -> engineering; a software title stays put.
    ("Mechanical Engineer", "engineering"),
    ("Electrical Engineer", "engineering"),
    ("Maskiningenjör", "engineering"),         # SE mechanical engineer
    ("Automationsingenjör", "engineering"),    # SE — the case the sector comment calls out
    ("Strojní inženýr", "engineering"),        # CZ mechanical engineer
    ("Software Engineer", "software_engineering"),   # NOT engineering — no discipline qualifier
    ("Dataingenjör", "data_engineering"),      # SE — data_engineering wins over broad ingenjör
    # The Swedish vocabulary pass of 2026-08-09-b. Every title here is real register text the
    # classifier declined, and the words behind them are stems and occupation nouns, not the
    # ads' own phrasing — measured on a *holdout* slice the patterns were not written from
    # (51.1% -> 71.6%), because scoring new words against the sample they were read out of
    # only measures memorisation. See `scripts/categorization_score.py --before`.
    ("Undersköterskor till hemtjänsten", "healthcare"),   # plural: the stem, not the word
    ("Leg. Fysioterapeut sökes för uppdrag", "healthcare"),
    ("Psykolog habilitering", "healthcare"),
    ("Fastighetsskötare", "skilled_trades"),   # ...and `skötersk` must NOT reach `skötare`
    ("Elevassistent till Bosgårdsskolan", "education"),
    ("Timvikarie förskola - Innovitaskolan", "education"),
    ("Sushikock", "hospitality"),              # `kock` as a suffix, with its Swedish endings
    ("Serverings- och barpersonal sökes", "hospitality"),
    ("Butiksmedarbetare, Willys Helsingborg Berga", "sales"),
    ("Säljarjobb utan lönetak!", "sales"),     # the stem: "säljare" alone missed this
    ("Träarbetare Mariestad", "construction"),
    ("Hjullastarförare till Stockholm-Spånga", "construction"),  # NOT logistics' `förare`
    ("Taxiförare / färdtjänstförare i Uppsala kommun", "logistics_transport"),
    ("Brevbärare/paketbud - Solna", "logistics_transport"),
    ("Ställare inom formsprutning", "manufacturing_production"),
    ("Bilplåtslagare till Werksta Falkenberg", "manufacturing_production"),
    ("Mekanikkonstruktör till Nord-Lock", "engineering"),   # SE spelling of konstruktér
    ("Elkonstruktör", "engineering"),          # was skilled_trades before engineering existed
    ("Senior Java-utvecklare", "software_engineering"),
    # Was pinned `uncategorised` until 2026-08-17, and the pin's point was the one kept below:
    # business development is not SOFTWARE, which is what `(?<!affärs)utvecklare` guarantees.
    # Wave 3 (sv-06) gave it a home in `sales` rather than leaving it homeless — the file had
    # carved the word out of software and never put it anywhere, so 60+ live rows were
    # uncategorised by construction. `uncategorised` was the absence of a decision, not one.
    ("Affärsutvecklare", "sales"),
    ("It-supporttekniker | Skövde", "customer_support"),
    ("Löneadministratör", "finance_accounting"),
    ("Senior ekonom", "finance_accounting"),   # moved out of the other_tech_function residual
    ("Grafisk Formgivare till Newport", "design"),
    ("Kommunikatör vid Kunskapscentrum", "marketing"),
    # The Czech vocabulary pass of 2026-08-09-c, written after the ISCO answer key showed
    # Czech at 42.6% against Swedish 80.8%. Same lesson as the Swedish pass and the same
    # discipline: measured on the disjoint half of the register (43.5% -> 66.6%), which is
    # where these titles are NOT from.
    ("Zedníci", "construction"),                          # plural: the stem, not the word
    ("Zámečníci", "skilled_trades"),                      # ...and Czech declines the í too
    # `svářeč` (welder) follows `svetsare` into manufacturing, not trades — the SSYK/ISCO
    # boundary applied to Czech at last (2026-08-10, 43 rows the classifier had called trades).
    # `zámečník` (fitter) above stays a trade: this is the welder alone, not the whole stem list.
    ("Svářeč", "manufacturing_production"),
    ("Svářeči kovů", "manufacturing_production"),          # plural, the register's own spelling
    ("Dělník / dělnice v kovovýrobě - zámečna", "manufacturing_production"),
                                                          # `zámečna` is the shop floor, not
                                                          # the locksmith
    # Three ordering cases, each a title that carries two categories' words at once.
    ("Montér ve stavebnictví (m/ž)", "construction"),     # site domain beats trade role
    ("Dělníci v oblasti výstavby a údržby budov", "construction"),
    ("Obsluha v zařízeních rychlého občerstvení", "hospitality"),   # not machine tending
    ("Skladníci, obsluha manipulačních vozíků", "logistics_transport"),
    ("Obsluha strojů na výrobu výrobků z plastu", "manufacturing_production"),
    ("Prodavač/ka ovoce", "sales"),
    ("Vedoucí prodejny", "sales"),
    ("Inženýr/ka kvality", "engineering"),                # bare `inženýr`, no discipline word
    ("Datový inženýr", "data_engineering"),               # ...which data work still outranks
    ("Projektant měřících systémů", "engineering"),
    ("Specialista informačních technologií", "uncategorised"),  # `technolog\b` is bounded:
                                                          # IT is not an engineering technolog
    ("Mechanici a opraváři osobních automobilů", "skilled_trades"),
    # The English pass of 2026-08-09-d. Written from what production actually holds: with the
    # registers drained, 22 488 of the uncategorised rows are English ATS postings, and these
    # are the systematic shapes inside them. Measured on the disjoint half of that block
    # (18.7% -> 32.1% of the residue classified), which is where these titles are NOT from.
    ("Solutions Architect", "software_engineering"),      # the plural is the commoner form
    ("Enterprise Architect", "software_engineering"),
    ("Data Architect", "data_engineering"),               # ...but data work still wins
    ("Cloud Architect", "devops_platform"),               # ...and so does platform work
    ("Large Language Model Architect", "machine_learning"),   # the single commonest one
    ("Software Engr I", "software_engineering"),          # Workday/Oracle abbreviate the title
    ("Field Service Engr I", "skilled_trades"),           # ...and a field engineer is a trade
    ("Assembler - Level 1", "manufacturing_production"),
    ("Production Supervisor", "manufacturing_production"),
    ("Database Administrator", "devops_platform"),
    ("Quantitative Researcher", "data_analysis"),
    ("People Partner", "hr_recruiting"),
    ("Executive Assistant", "other_tech_function"),
    ("Teaching Assistant", "education"),                  # ...but a school assistant teaches
    ("Customer Assistant - Food - Malvern", "sales"),     # UK retail shop floor
    ("Customer Support Specialist", "customer_support"),  # ...and support is not retail
    ("Agente di commercio", "sales"),                     # IT field sales, via adzuna
    # The 2026-08-10 pass: the head of the still-uncategorised English tail, each an existing
    # category missing a surface form the register actually writes. SE/CZ accuracy unchanged
    # (80.9% / 65.9%), so these are pure coverage on the English residue.
    ("Data Science Manager", "machine_learning"),         # the field + a seniority word, not
    ("Data Science Trainee", "machine_learning"),         # ...the -ist form the pattern had
    ("Immigration Lawyer", "legal"),                      # `lawyer`/`attorney` were both absent
    ("Senior Attorney", "legal"),
    ("Software Development Manager", "software_engineering"),  # "development" != "developer"
    ("Commercial(e) terrain - indépendant", "sales"),     # the literal "(e)" broke the old \s+
    ("Sales Development Representative", "sales"),         # US pipeline titles that never said
    ("SDR - EMEA", "sales"),                               # ...the word "sales"
    ("Data Analist", "data_analysis"),                    # the Dutch/loan spelling, via NL
    # Deliberate declines held from the same pass, kept as tests so they stay decisions: a
    # bare "Quantitative Trader" has no trading category, and "Specialist, Client Processing"
    # is one employer's internal vocabulary, not a profession.
    ("Quantitative Trader", "uncategorised"),
    ("Specialist, Client Processing", "uncategorised"),
    # A second extension pass from mining the uncategorised corpus for recurring role words:
    # each generalises across the noisy tail (one pattern catches every spelling variant).
    ("Sr Estimator", "construction"),
    ("Equipment Technician", "skilled_trades"),
    ("Service Technician", "skilled_trades"),
    ("Data Entry Clerk", "other_tech_function"),
    ("Sr Strategic Sourcing Spec", "operations"),
    ("Technician", "uncategorised"),                      # ...but a *bare* technician still is
    # A deliberate decline, kept as a test so it is a decision rather than an oversight: a
    # bare "Project Manager" is construction, IT, marketing or events depending on the
    # employer, and `uncategorised` is the honest answer for 50 postings rather than a guess
    # that puts them in a stranger's digest.
    ("Project Manager", "uncategorised"),
    ("Social Media Manager", "social_media"),
    ("Specialista sociálních sítí", "social_media"),          # CZ
    ("Náborár pre projekty, Marketing | Sociálne siete", "social_media"),  # SK
    ("Community Manager", "social_media"),
    ("Influencer Marketing Specialist", "social_media"),
    ("Lighthouse Keeper", "uncategorised"),
])
def test_classify(title, expected):
    assert taxonomy.classify(title) == expected


def test_social_media_beats_the_catch_all():
    """Almost every social title also says "marketing" or "content", so without the ordering
    they all land in the broader marketing pattern — and a subscriber who asked for social
    media gets general marketing instead. This is the same class of bug as
    test_specificity_order_holds, one layer down.

    Before 2026-08-09 the bucket underneath was `other_tech_function`, which held marketing,
    sales, finance, HR and legal together; the split made that bucket `marketing`, and the
    ordering requirement is unchanged."""
    assert taxonomy.classify("Social Media Marketing Specialist") == "social_media"
    assert taxonomy.classify("Content Creator") == "social_media"
    assert taxonomy.classify("Marketing Specialist") == "marketing"
    # ...but a designer who also runs the socials is still a designer.
    assert taxonomy.classify("Grafik a správa sociálních sítí") == "design"


def test_monteur_is_a_trade_by_decision_not_by_ordering():
    """`monteur` was wanted by three languages in two different categories.

    The German and French vocabulary passes both proposed it for `manufacturing_production`;
    the Dutch pass proposed `skilled_trades`. Because skilled_trades runs first, shipping all
    three would have resolved the disagreement *by ordering accident* — and the next person to
    reorder the file would silently reclassify three languages without knowing they had.

    It is a trade because the repo already splits the cognates that way (Czech `montér` is a
    trade; Swedish/Norwegian `montör|montør` is manufacturing, on the SSYK boundary) and
    because every collected instance is a field fitter. This test pins the decision, and the
    two cognates on either side of it so the split cannot quietly collapse into one answer."""
    assert taxonomy.classify("Servicemonteur Havenkranen") == "skilled_trades"
    assert taxonomy.classify("Reifenmonteur (m/w/d)") == "skilled_trades"
    assert taxonomy.classify("Monteur-Câbleur Electronique") == "skilled_trades"
    # The two neighbours the decision is defined against.
    assert taxonomy.classify("Montér ve výrobě") == "skilled_trades"        # CZ, unchanged
    assert taxonomy.classify("Montör till fabriken") == "manufacturing_production"  # SE


def test_polish_projektant_never_becomes_design():
    """Polish *projektant* is a designer; Czech *projektant* is a design engineer — and the
    Czech sense is already in `engineering`.

    Filing the string under `design` (which runs earlier) to serve Polish costs the Czech
    answer key 14 rows, 77.70% -> 77.01%. No ordering resolves it, because the two languages
    disagree about what the word means. It was cut for that reason, and this test exists so
    that a future Polish pass re-adding it fails here rather than in the CZ accuracy number,
    where it would read as an unexplained regression."""
    assert taxonomy.classify("Projektant elektro") == "engineering"
    # The category that claims the bare word must be engineering, and it must be the FIRST
    # pattern that matches — an added `design` entry would win on order without changing the
    # line above if `design` ever moved.
    claimants = [c for c, p in taxonomy.PATTERNS if p.search("projektant")]
    assert claimants and claimants[0] == "engineering", claimants


@pytest.mark.parametrize("title,expected,trap", [
    # Each of these is a real production/answer-key string that a bare stem read wrongly.
    ("Przedstawiciel handlowy - branża farmaceutyczna", "sales",
     "PL *farmaceutyczny* is a pharma sales adjective, not a pharmacist"),
    ("Inżynier mechaniczny", "engineering",
     "PL *mechaniczny* is an adjective; bare mechani[kc] filed it as a trade"),
    ("ADDETTO/A VENDITA KIABI PARMA", "sales",
     "IT ads write the gender slash inline; addett\\w+ vendit matches 0 of these without it"),
    ("Addetto/a Assistenza Clienti per Azienda Commerciale", "customer_support",
     "bare IT `commerciale` is a sector adjective in 21 of 28 titles"),
    # Declining is the correct answer here — the point is only that it must NOT be read as a
    # driver. Unbounded, `autist` makes this `logistics_transport`.
    ("Assistenza Autistica - Operatore", "uncategorised",
     "IT `autist` unbounded reads autistic/autism, not a driver"),
    ("Sales Manager Bangkok", "sales",
     "NL `kok` unbounded matches bangkok"),
    ("Adviseur Utrecht", "uncategorised",
     "NL `recht` is inside Utrecht — the georgia rule in Dutch"),
    # Same shape as `Assistenza Autistica` above: declining is the correct answer, and the point
    # is only that IT `magazzin` must not read it as a warehouse. It expected
    # `other_tech_function` until 2026-08-26 purely because the residual's bare `\bcontent\b`
    # happened to claim it; that fragment was removed as a misfile (81 of its 123 postings were
    # content marketing), so the trap is now asserted without borrowing another bucket's answer.
    ("Magazine Content Editor", "uncategorised",
     "IT `magazzin` has a double z and must not reach English magazine"),
    # --- 2026-08-11 wave 2. Each of these misfiled during the JOINT measurement, and none
    # of them was visible to the single-language pass that proposed the term: the offending
    # title is written in a language that pass never looked at.
    ("Délégué Médico-Technique Respiratoire", "sales",
     "ES `médico` is an adjective; a medical-DEVICE sales rep is not a clinician"),
    ("ALTERNANT DELEGUE MEDICO TECHNIQUE RESPIRATOIRE H/F", "sales",
     "the same title unaccented and in caps, which is how the ATS actually writes it"),
    ("Técnico de Mantenimiento en dispositivos médicos", "skilled_trades",
     "ES `médico` on a maintenance role names the DEVICE, not the profession"),
    ("Ejecutivo de Cuentas (Licencia Medica)", "uncategorised",
     "a trailing `(Licencia Médica)` is LEAVE COVER — declining is the right answer"),
    ("Marketing Director - Dental Professionals", "marketing",
     "ES `dental` — selling TO dentists is not practising dentistry"),
    ("Recepcionista clínica dental", "hospitality",
     "the `recepcionista` guard: a clinic receptionist stayed where wave 1 put it"),
    ("Flexmedewerker gehandicaptenzorg", "social_care",
     "disability care is social care — and the word had to LAND there, not just leave "
     "healthcare, or titles whose only signal is that word fall to uncategorised"),
    ("Sr. Product Cybersecurity Architect for Advanced Hearing Aid Platform", "cybersecurity",
     "the hearing-aid INDUSTRY employs engineers; the term is for the audiology PROFESSION"),
    # The German bound-role construction has to read BOTH compound directions.
    ("Leitung (w/m/d) Produktion", "manufacturing_production",
     "German writes the role OPEN as well as closed; reading one halves the coverage"),
    ("Fertigungstechniker", "manufacturing_production",
     "the closed half of the same binding"),
    ("Chemielaborant (m/w/d)", "engineering",
     "a >=4-letter compound prefix is what makes `laborant` safe against the Czech key"),
])
def test_multilingual_stems_stay_inside_their_own_language(title, expected, trap):
    """The cross-language collisions the 2026-08-10 pass had to defuse, one case each.

    `PATTERNS` is a single ordered list shared by ten languages, so a stem added for one of
    them reads every other language's titles too. Every string here classified *wrongly*
    before its guard existed — these are not hypotheticals, they are the measured failures,
    and each one is a lookahead, a word boundary or a binding that a later simplification
    would remove without any other test noticing."""
    assert taxonomy.classify(title) == expected, trap


#: The 2026-08-26 misfile pass. Every one of these is a real corpus title that was categorised
#: **confidently and wrongly**, and each was found by grouping the live corpus under the exact
#: taxonomy fragment that claimed it (`scripts/matchspans.py`).
#:
#: They are grouped here rather than scattered because they share one cause, and it is the
#: finding worth keeping: **almost none of them was a gap in what the taxonomy knew.** In nearly
#: every case the file had already made the decision, written it down in a comment, measured it
#: — and then not applied it to the sibling fragment that needed it. A test that pins the
#: outcomes is the cheap half; the expensive half is noticing the class.
_MISFILE_CASES = [
    # --- an existing guard that was never copied to its sibling -----------------------------
    ("Business Analist", "other_tech_function",
     "NL: `Business Analyst` was already other_tech_function; the bare `analist` made that "
     "ruling unreachable one language over"),
    ("Kravanalytiker", "uncategorised",
     "SE requirements analyst — the bare-`analyst` deletion of 2026-08-22 was applied to "
     "English and to nothing else"),
    ("1st Line SOC Analytiker", "uncategorised",
     "the same fragment shadowing cybersecurity in Swedish"),
    ("Data Analist", "data_analysis",
     "the bound Dutch form must survive the narrowing — every correct row carries `data`"),
    ("Dataanalytiker", "data_analysis", "and the bound Swedish form"),
    ("Senior Software Engineer GenAI Enterprise Applications", "software_engineering",
     "`Senior Software Engineer, Data Analytics` was already software; GenAI is the same "
     "domain-word trap with the guard not fitted"),
    ("GenAI Product Manager", "product", "same fragment, the product half"),
    ("Large Language Model Architect", "machine_learning",
     "the guard must not cost the vocabulary it was added for"),
    ("Front Office Assistant", "uncategorised",
     "`office manager` carries five lookbehinds for exactly this; the assistant form had none, "
     "and its damage was larger"),
    ("Senior Account Executive - Cybersecurity (North)", "sales",
     "`financial services` is guarded as a vertical; cybersecurity is a vertical too"),
    ("Field Security Specialist (Cyber Security Solutions Engineer)", "cybersecurity",
     "security PRE-SALES is genuinely security work and must survive that guard"),
    ("Kubernetes Software Developer", "software_engineering",
     "`a tool name alone never names a role` was recorded for `tableau` and not for k8s"),
    ("Senior Kubernetes Engineer", "devops_platform",
     "the guard must not cost the real platform roles"),
    ("Senior Javautvecklare – Spring Boot, Microservices, Kubernetes", "software_engineering",
     "the Swedish compound has no word boundary before the head, so the guard cannot be bound"),
    ("Business Developer till Eurofins!", "sales",
     "`Business Development Manager` and `Affärsutvecklare` were already sales; the English "
     "*-er* form was a programmer"),
    ("Doktorand i odontologi", "education",
     "four sibling clinical fragments carry the academic guard; `odont[óo]log` was the one "
     "place it was not copied"),
    ("Učitel/ka odborného výcviku oboru Pečovatel/ka", "education",
     "teaching the care trade is not practising it — the same guard, one register over"),
    ("Registrator till Försvarsmaktens internationella expedition - vikariat", "uncategorised",
     "the Czech fragment's comment says NOT bare `expedi`; the Dutch one reintroduced it"),
    ("Produktionsarbetare med truckkort", "manufacturing_production",
     "a forklift LICENCE is a duty attached to another job — the class `med logistikansvar` "
     "already defines"),
    ("Operatör med truckkort | Lernia | Halmstad", "logistics_transport",
     "and the publisher's own SSYK coding says a bare operator with that licence IS logistics: "
     "the wider rule was wrong and the answer key is what caught it"),
    # --- a word that names a place, a material or an employer, not a profession -------------
    ("Kock till Lunda förskola", "hospitality",
     "`Kock till Lunda skola` was already hospitality — one prefix moved the same job"),
    ("Förskollärare", "education", "and the profession itself must not move"),
    ("Zaakbegeleider Commissie Mijnbouwschade", "uncategorised",
     "NL `mijnbouw` is mining; the `-bouw` lookbehind list was two doors short"),
    ("Adviseur Energie Innovatie Gebouwde Omgeving", "uncategorised",
     "`gebouwde` is the adjective *built* — `bouw` as a mid-word substring"),
    ("Administratief medewerker vrachtwagenheffing/Toezicht Wegvervoer", "other_tech_function",
     "the Dutch truck-TOLL programme is administration, not driving"),
    # --- a generic role head with no domain binding -----------------------------------------
    ("Senior PCB Designer", "uncategorised",
     "`design lead` was bound to a domain word after 'Mechanical Design Leader' took 20 of 29; "
     "the bare `designer` above it never was"),
    ("Digital IC Designer", "uncategorised", "integrated-circuit design is engineering"),
    ("Industrial Designer", "design",
     "and an Industrial Designer IS a designer — the guard must not read `industri`"),
    ("Product Designer", "design", "the deliberate product/design boundary is unchanged"),
    # --- an enumerated list that went stale --------------------------------------------------
    ("GTM Partnerships Manager, SME & Growth", "partnerships",
     "the `growth` decline list shipped the same day `partnerships` was created"),
    ("Director of Product, Growth/AI", "product",
     "and `Director of Product` was a hole in `product` itself, so narrowing alone would have "
     "produced a decline rather than a right answer"),
    ("Growth Marketing Manager", "marketing",
     "anything naming marketing outright still stays — the original guard's collateral was nil"),
    ("B2B-säljare till ett snabbväxande bolag inom digital marknadsföring", "sales",
     "`marknadsföring` naming the EMPLOYER'S SECTOR survived the leading-boundary fix"),
]


@pytest.mark.parametrize("title,expected,trap", _MISFILE_CASES)
def test_a_fragment_never_claims_a_job_it_cannot_read(title, expected, trap):
    """The 2026-08-26 misfile pass, one case per guard.

    A misfile is the expensive kind of wrong and the invisible kind: the categorisation
    watchdog counts `uncategorised` and a misfile is the opposite of that; `unmet_demand_terms`
    reads demand, not supply; and the AI matcher never sees the row, because the row is not in
    the shortlist it was retrieved for. Nothing in this repo reports one. Each string below was
    read out of the live corpus and verified wrong before its guard was written.
    """
    assert taxonomy.classify(title) == expected, trap




@pytest.mark.parametrize("category,title,trap", [
    ("sales", "Setra Skinnskatteberg söker sågverkoperatörer",
     "NL `verkoper` unbounded reaches inside the Swedish sågverk|operatörer"),
    # The register's own spelling, WITHOUT the á — that is the row `obra` collides with, and
    # using the accented form here would have made this assertion vacuous.
    ("construction", "Obraběč/ka kovů",
     "ES `obra` unbounded reaches the Czech machinist"),
    # --- 2026-08-11 wave 2. Place names and one foreign profession, all of them the
    # `georgia` rule: a role word that is also somewhere on a map.
    ("hospitality", "Site Lead Kochi India",
     "DE `koch` unbounded reaches KOCHI, an Indian city"),
    ("healthcare", "Delegado de Ventas Andalucia Occidental",
     "ES `dental` unbounded sits inside OCCIDENTAL — a Spanish region"),
    ("healthcare", "Profesor de Higiene Bucodental",
     "ES `dental` unbounded sits inside BUCODENTAL, and this is a TEACHER"),
    ("manufacturing_production", "Projektleiter Infrastruktur",
     "DE `fräs` unbounded sits inside INFRASTRUKTUR"),
    # `("engineering", "Laborant/ka", ...)` lived here from wave 1 until 2026-08-17, asserting
    # that `laborant` stayed compound-prefix-bound so the standalone Czech title could not reach
    # it.  **That guarantee was deliberately withdrawn**, not lost: wave 3 re-tested the verdict
    # under the corrected gate and the collision does not exist against `engineering` — CZ
    # 1586 -> 1588, CZ majors 1-3 256 -> 258, SE and NO flat.  Reaching `Laborant/ka` is now the
    # point.  `test_laborant_reaches_the_standalone_czech_title` asserts the opposite and is its
    # replacement; the reasoning is on the pattern itself.  A term rejected against category A
    # is not rejected, it is untested against B — so this entry is removed rather than relaxed,
    # and the next pass should not re-add a bound without re-running the measurement.
    ("cybersecurity", "Specjalista ds. bezpieczeństwa i higieny pracy",
     "PL bare `bezpiecze` eats BHP — occupational health & safety, which has NO category, "
     "so a safety officer would file as a security engineer"),
])
def test_a_bounded_stem_does_not_reach_into_another_language(category, title, trap):
    """Asserted on the PATTERN, not through `classify()`, and that distinction is the point.

    Both of these titles are claimed by `manufacturing_production`, which runs earlier than
    either category here — so routing them through `classify()` yields the right answer whether
    the boundary is present or not. Mutation-checking the obvious version of this test showed
    exactly that: remove `\\b` and it still passed. It would have been a guard that documents
    nothing, of the precise kind CLAUDE.md warns about, and it would have read as protection.

    What is actually guaranteed is narrower and worth stating on its own: **the category's own
    pattern must not match the foreign title at all.** That holds regardless of ordering, so it
    survives someone reordering `PATTERNS` — which is when the masked version would have
    started silently misfiling."""
    pattern = dict(taxonomy.PATTERNS)[category]
    assert not pattern.search(title), trap


@pytest.mark.parametrize("title,expected", [
    # Italian: reparto = DEPARTMENT. These must keep their category.
    ("TECNICO/A REPARTO ELETTRICO", "manufacturing_production"),
    ("Addetto/a Programmazione di reparto", "manufacturing_production"),
    ("Tecnico Trasfertista Reparto BEND", "manufacturing_production"),
    # Spanish: reparto = DELIVERY ROUND. Declining is correct; factory work is not.
    ("Tecnico de reparto", "uncategorised"),
    ("TECNICO DE REPARTO", "uncategorised"),
    ("Responsable de reparto", "uncategorised"),
])
def test_italian_reparto_is_a_department_and_spanish_reparto_is_a_delivery_round(
        title, expected):
    """One word, two languages, two jobs — and the two proposals could not see each other.

    `_IT_ROLE` contains `tecnic\\w+`, so the Italian binding `_IT_ROLE … reparto` reads the
    Spanish *Técnico de reparto* — a DELIVERY technician — and files it as factory work,
    because `manufacturing_production` runs long before `logistics_transport`. The accented
    `Técnico` is safe on its own (é is not e), but ATS boards write titles unaccented and in
    caps, which is exactly the form that misfires.

    The separator is the fix and it is a real difference in the two languages, not a hack:
    **Italian writes `di reparto`, Spanish writes `de reparto`**, so a `(?<!de )` lookbehind
    drops the Spanish sense and costs no Italian title.

    The Italian pass flagged this collision and could not check it — it had only Italian
    text. It is here because the *joint* measurement is the only place a cross-proposal
    conflict is visible, and because a later simplification of that lookbehind would
    silently start misfiling every Spanish delivery ad."""
    assert taxonomy.classify(title) == expected


def test_a_german_role_head_never_ships_bare():
    """`produktion`/`fertigung` bound to a role head, never loose — measured, not stylistic.

    Bare `produktion|fertigung` FAILS the answer-key gate: it takes 6 SSYK rows, 2 of them
    out of a correct answer, because the words appear as a DOMAIN on titles belonging to
    other categories. Bound to a role head it holds both keys row-for-row and still wins on
    both halves of the board holdout.

    The assertion is on the pattern rather than through `classify()` so it cannot be
    satisfied by pattern ordering: `manufacturing_production` must not claim a bare domain
    word at all."""
    manufacturing = dict(taxonomy.PATTERNS)["manufacturing_production"]

    # the binding fires on a real role, in both compound directions
    assert manufacturing.search("Leitung (w/m/d) Produktion")
    assert manufacturing.search("Fertigungstechniker")

    # ...and not on the bare domain word carrying somebody else's profession
    assert not manufacturing.search("Praktikum Produktion")
    assert not manufacturing.search("Werkstudent Fertigung")


def test_quality_work_splits_three_ways_and_is_not_one_category():
    """Quality is the biggest uncategorised cluster in the corpus and deliberately has no
    category of its own.

    199 non-software quality titles across six languages make a `quality` category look
    obviously right. It was measured both ways (`notes/proposals/category-gaps.md`): a separate
    category scores *negative* at every position in the order, while folding the engineer half
    into `engineering` scores positive — SE 81.1896% -> 81.3383%, CZ flat, both moved
    answer-key rows gains. Both keys already file quality work under engineering (ISCO
    3119/2149) and SSYK has no quality occupation at all.

    What must survive is the three-way split, because no single category is honest across it:
    a quality *engineer* is engineering, a quality *inspector* on a line is production, and
    software QA is software. Collapsing any two of these is the tempting simplification, and
    the middle case is the one that silently regresses — `quality (inspector|technician)` lives
    in `manufacturing_production`, which runs *earlier* than engineering."""
    # These two are the assertions that actually carry the fold: mutation-checking showed that
    # removing it sends "Senior Quality Engineer" to `software_engineering` and drops
    # "Supplier Quality Manager" to `uncategorised`.
    assert taxonomy.classify("Senior Quality Engineer") == "engineering"
    assert taxonomy.classify("Supplier Quality Manager till försvarsindustri") == "engineering"
    # The three below are documentation, not coverage, and it is worth saying so: each is
    # already claimed by its own language's `ingenieur` / `ingénieur` / `inżynier` term from the
    # first multi-language pass, so they pass with or without the quality fold. They are kept
    # because they record what the fold is *for* — the same title in four languages — but a
    # future editor must not read them as protecting it.
    assert taxonomy.classify("Qualitätsingenieur (w/m/d) Defence") == "engineering"
    assert taxonomy.classify("Ingénieur qualité fournisseurs H/F") == "engineering"
    assert taxonomy.classify("Inżynier Jakości") == "engineering"
    # The line roles stay production — this is the assertion a careless merge would break.
    assert taxonomy.classify("Quality Inspector") == "manufacturing_production"
    assert taxonomy.classify("Quality Technician") == "manufacturing_production"
    # ...and software QA stays software.
    assert taxonomy.classify("QA Engineer") == "software_engineering"


def test_french_bound_roles_depend_on_pattern_order():
    """The French bindings are order-dependent by design, and one of them is *only* correct
    because another runs first.

    `(?:chef|responsable) de secteur` -> `sales` is 60% right on its own: a "Chef de secteur"
    can be a territory sales manager or a production area manager. It reaches 100% here only
    because the `_FR_ROLE … production` binding in `manufacturing_production` runs **fourteen
    patterns earlier** and takes the production sense first. Reordering the file, or dropping
    the production binding, silently turns every French production area manager into a
    salesperson — with no other test noticing.

    The `production` lookahead is the same lesson pointing the other way: "Chargé de production
    marketing" is not factory work, and that exclusion came from a real corpus title."""
    assert taxonomy.classify("Chef de secteur") == "sales"
    assert taxonomy.classify("Chef de secteur production") == "manufacturing_production"
    assert taxonomy.classify("Chargé de production H/F") == "manufacturing_production"
    assert taxonomy.classify("Chargé de production marketing") == "marketing"
    # Inclusive-form spellings the bounded gap has to read, all real corpus shapes.
    assert taxonomy.classify("Technicien(ne) de maintenance industriel") == "skilled_trades"
    assert taxonomy.classify("Technicien.ne maintenance") == "skilled_trades"


def test_payroll_is_bound_so_it_cannot_take_an_analyst():
    """`finance_accounting` runs one pattern before `data_analysis`, so a bare payroll term
    outranks every analyst title that happens to name the payroll team.

    The French proposal offered a bare `\\bpaie\\b`. Measured across every corpus it claimed
    five payroll titles correctly and took "Data Analyst H/F - Equipe Paie / Facturation" off
    `data_analysis`. Binding it to a role noun keeps all five and releases the analyst, because
    a data analyst on the payroll team is a data analyst."""
    assert taxonomy.classify("Gestionnaire de Paie F/H") == "finance_accounting"
    assert taxonomy.classify("Responsable Paie et ADP H/F") == "finance_accounting"
    assert taxonomy.classify("Data Analyst H/F - Equipe Paie / Facturation") == "data_analysis"


def test_specificity_order_holds():
    """Data/ML/devops must beat the broad software 'engineer' catch-all. This is the
    ordering bug the single definition exists to prevent — it is invisible until a
    'Data Engineer' starts being filed as software_engineering."""
    assert taxonomy.classify("Data Engineer") == "data_engineering"
    assert taxonomy.classify("Machine Learning Engineer") == "machine_learning"
    assert taxonomy.classify("Cloud Engineer") == "devops_platform"


def test_hint_is_the_fallback_not_an_override():
    assert taxonomy.classify("Data Engineer", "other_tech_function") == "data_engineering"
    assert taxonomy.classify("Něco Divného", "other_tech_function") == "other_tech_function"
    assert taxonomy.classify(None) == "uncategorised"
    assert taxonomy.classify("") == "uncategorised"


def test_a_non_canonical_hint_is_discarded_not_stored():
    """`classify` used to end `return hint or UNCATEGORISED`, handing the source's own string
    straight into `postings.role_category`. Four adapters map their hint through a curated
    table first; five passed a raw third-party string (platsbanken's Swedish SSYK label,
    workable's employer-typed department, startupjobs' field slug, recruitee's category_code,
    oraclecloud's JobFamily).

    Measured on production 2026-08-08: 14 135 of 98 858 active postings (14%) carried a value
    outside CATEGORIES — platsbanken alone 13 561 rows across 954 distinct labels. The recall
    predicate is `role_category = any(...) OR search_tsv @@ (...)`, so every one of those rows
    was unreachable through the category half and survived on keyword alone. Nothing failed:
    the dbt `accepted_values` test runs in Snowflake against stg_job_postings.sql's own SQL
    `case`, which never sees a hint, so it structurally cannot catch this.

    `uncategorised` is the honest answer. It is not better at matching — neither value is
    selectable as a preference — but it means "unknown" to every consumer instead of meaning
    a category that does not exist."""
    assert taxonomy.classify(OPAQUE, "Systemutvecklare/Programmerare") == "uncategorised"
    assert taxonomy.classify(OPAQUE, "Greenvolt Next España, S.L.") == "uncategorised"
    # ...while a canonical hint still rescues a title the regexes cannot read.
    assert taxonomy.classify(OPAQUE, "software_engineering") == "software_engineering"
    # ...and a readable title still beats any hint, canonical or not.
    assert taxonomy.classify("Data Engineer", "Utesäljare") == "data_engineering"


@pytest.mark.parametrize("hint", [
    # platsbanken — occupation.label, the Swedish SSYK leaf label (954 distinct in production)
    "Systemutvecklare/Programmerare", "Utesäljare", "Helpdesktekniker/Supporttekniker",
    "Butikssäljare, dagligvaror/Medarbetare, dagligvaror",
    "Läkarsekreterare/Vårdadmin/Medicinsk sekreterare",
    # workable — job.function/department, employer free text. These are not job functions at all.
    "Greenvolt Next España, S.L.", "Engine by Starling", "FBS", "Wild Card", "NTG Freelancer",
    # startupjobs — the field's parent slug, and it arrives in both cases
    "sales", "Tech", "top-management",
    # recruitee — category_code
    "information_technology", "marketing_pr", "government_nonprofit",
    # oraclecloud — per-tenant HCM JobFamily/JobFunction
    "Integrated Supply Chain", "Health, Safety & Environment", "Customer/Product Support",
])
def test_classify_only_ever_returns_a_canonical_category(hint):
    """Every value here is real production data pulled from the five adapters that passed a
    raw hint. The title is deliberately unclassifiable, so the hint is the only thing that can
    decide the answer — which is exactly the path that was writing junk into the column.

    The literals live in this file rather than being passed through a shell: Swedish and Czech
    diacritics are silently mangled in transit (`Dataingenjör` -> `Datainginjor`), which has
    already produced one wrong conclusion in this repo."""
    assert taxonomy.classify(OPAQUE, hint) in taxonomy.CATEGORIES


# ------------------------------------------------------- internal consistency ----

def test_every_category_has_a_subject_word():
    """A category with no label makes the digest subject fall back to the raw enum."""
    labelled = set(taxonomy.SUBJECT_WORDS) | {taxonomy.UNCATEGORISED}
    assert set(taxonomy.CATEGORIES) - labelled == set()


def test_every_category_has_shortlist_keywords():
    """A category with no keywords silently retrieves nothing for a subscriber who
    selects it — an empty digest with no error anywhere."""
    keyed = set(taxonomy.SHORTLIST_KEYWORDS) | {taxonomy.UNCATEGORISED}
    assert set(taxonomy.CATEGORIES) - keyed == set()


def test_cv_rules_reference_real_categories():
    assert {c for c, _ in taxonomy.CV_RULES} <= set(taxonomy.CATEGORIES)


# ------------------------------------------------------------ cross-language ----

def _dbt_accepted_values() -> set[str]:
    """Pull the role_category accepted_values list out of the dbt schema YAML."""
    text = DBT_SCHEMA.read_text(encoding="utf-8")
    block = text.split("- name: role_category", 1)
    assert len(block) == 2, "role_category column not found in the dbt schema"
    match = re.search(r"values:\s*\[(.*?)\]", block[1], re.S)
    assert match, "accepted_values list not found for role_category"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_dbt_accepted_values_match_the_taxonomy():
    dbt_values = _dbt_accepted_values()
    assert dbt_values == set(taxonomy.CATEGORIES), (
        "dbt accepted_values and service/taxonomy.py disagree.\n"
        f"  only in dbt:      {sorted(dbt_values - set(taxonomy.CATEGORIES))}\n"
        f"  only in taxonomy: {sorted(set(taxonomy.CATEGORIES) - dbt_values)}"
    )


def test_the_answer_keys_name_only_real_categories():
    """`scripts/categorization_score.py` is the gate, and a typo in either of its answer keys
    is the worst kind of failure it can have: the classifier is marked wrong for being right,
    the category looks broken, and the next iteration "fixes" a classifier that was fine.
    Nothing else checks these tables — they are read by a script, not by the service."""
    import sys as _sys

    _sys.path.insert(0, str(ROOT))
    from scripts import categorization_score as scorer

    named = (set(scorer.FIELD_MAP.values()) | set(scorer.GROUP_MAP.values())
             | set(scorer.ISCO_MAP.values()))
    unknown = named - set(taxonomy.CATEGORIES)
    assert not unknown, (
        f"the answer key grades against categories that do not exist: {sorted(unknown)}")


def test_no_isco_prefix_is_both_mapped_and_out_of_scope():
    """The two ISCO tables are read at the same prefix length, so a code in both is a silent
    coin-flip on which rule applies — and the out-of-scope set is only allowed to override a
    map entry at a *longer* prefix (7512 bakers inside 751, 3121 mining inside 312)."""
    from scripts import categorization_score as scorer

    both = set(scorer.ISCO_MAP) & scorer.ISCO_OUT_OF_SCOPE
    assert not both, f"prefixes both mapped and excluded: {sorted(both)}"


def test_the_isco_key_is_read_longest_prefix_first():
    """Without it, a 2-digit sub-major would swallow the unit groups that opt out of it, and
    the exclusions that make the key honest would silently stop applying."""
    from scripts.categorization_score import truth_for_isco

    assert truth_for_isco("2141")[0] == "engineering"      # via "214"
    assert truth_for_isco("2211")[0] == "healthcare"       # via "22"
    assert truth_for_isco("7512") == (None, False)         # bakers opt out of 751
    assert truth_for_isco("7511")[0] == "manufacturing_production"
    assert truth_for_isco("2161") == (None, False)         # architects have no category
    assert truth_for_isco("") == (None, True)              # a hole is not an exclusion


def test_frontend_maps_only_reference_real_categories():
    """The chip vocabulary maps role ids to categories. A stale value here produces a
    profile whose role filter matches nothing, with no error on any side.

    Each block is read with a pattern that picks out only the *category* positions. A blanket
    ``"[a-z_]+"`` sweep would also collect the chip ids and keywords that now sit alongside
    them (``product_manager``, ``data_engineer``) and report every one as an unknown category —
    a test that fails for a reason that isn't real is a test that gets deleted.
    """
    text = WEB_OPTIONS.read_text(encoding="utf-8")
    referenced = set()

    # ROLE_OPTIONS: [{ id, category, keyword }, …] — only `category:` names a category, and
    # `category: null` is the deliberate "no category models this" case, not a value.
    options = re.search(r"export const ROLE_OPTIONS[^=]*=\s*\[(.*?)\n\];", text, re.S)
    assert options, f"ROLE_OPTIONS not found in {WEB_OPTIONS.name}"
    referenced |= set(re.findall(r'category:\s*"([a-z_]+)"', options.group(1)))

    # Both of these are keyed *by* category: CV signals -> chip, and stored slug -> chip.
    for block_name in ("CV_ROLE_ID", "ROLE_ID_FOR_CATEGORY"):
        match = re.search(rf"export const {block_name}[^=]*=\s*\{{(.*?)\n\}};", text, re.S)
        assert match, f"{block_name} not found in {WEB_OPTIONS.name}"
        referenced |= set(re.findall(r"^\s*([a-z_]+):", match.group(1), re.M))

    assert referenced, "no categories extracted — the parse, not the frontend, is broken"
    unknown = referenced - set(taxonomy.CATEGORIES)
    assert not unknown, (
        f"web/lib/options.ts references unknown role_category values: {sorted(unknown)}"
    )


def test_skill_suggestions_are_keyed_by_real_role_ids():
    """`SKILLS_BY_ROLE` drives the skill chips the signup wizard offers for the picked roles.

    It is keyed by role **chip id**, and a key that matches no chip is the quietest possible
    bug: `suggestedSkills` looks the id up, finds nothing, and contributes nothing — so the
    role simply offers no skills and every other role still does. Nothing throws, nothing
    logs, and the column looks plausible. Renaming a chip in `ROLE_OPTIONS` without renaming
    it here does exactly that, which is why the check is on the *keys* rather than on the
    values: the words themselves are editorial, the ids are a contract.

    Not asserted: that every role has an entry. A role with no suggestions is a legitimate
    state — it falls back to handing the question to the visitor, the same way a typed role
    does — so requiring one would be a rule about copy, enforced as a test.
    """
    text = WEB_OPTIONS.read_text(encoding="utf-8")

    options = re.search(r"export const ROLE_OPTIONS[^=]*=\s*\[(.*?)\n\];", text, re.S)
    assert options, f"ROLE_OPTIONS not found in {WEB_OPTIONS.name}"
    role_ids = set(re.findall(r'id:\s*"([a-z_]+)"', options.group(1)))
    assert role_ids, "no role ids extracted — the parse, not the frontend, is broken"

    block = re.search(r"export const SKILLS_BY_ROLE[^=]*=\s*\{(.*?)\n\};", text, re.S)
    assert block, f"SKILLS_BY_ROLE not found in {WEB_OPTIONS.name}"
    keyed = set(re.findall(r"^\s*([a-z_]+):\s*\[", block.group(1), re.M))
    assert keyed, "no keys extracted — the parse, not the frontend, is broken"

    unknown = keyed - role_ids
    assert not unknown, (
        "web/lib/options.ts suggests skills for role chips that do not exist: "
        f"{sorted(unknown)} — these suggestions can never be shown"
    )


def test_every_language_names_every_category_in_its_subject_words():
    """`i18n.SUBJECT_WORDS` is what the digest subject calls a category, per language.

    A category missing from one language falls back to the English word, which produces a
    subject line that is Czech apart from one English noun — readable enough that nobody
    reports it, wrong enough to look machine-made. The English table here must also match
    `taxonomy.SUBJECT_WORDS`, which stays the single Python definition.
    """
    from service import i18n

    assert i18n.SUBJECT_WORDS["en"] == taxonomy.SUBJECT_WORDS, (
        "i18n.SUBJECT_WORDS['en'] and taxonomy.SUBJECT_WORDS disagree — the English subject "
        "word has two definitions and they have drifted."
    )
    # Compared against the English table, not `CATEGORIES`: `uncategorised` is deliberately
    # absent from both, because a digest of unclassified postings should not claim a category
    # in its subject at all. The invariant is that every language names the same set English
    # does — no more, no less.
    expected = set(taxonomy.SUBJECT_WORDS)
    assert expected <= set(taxonomy.CATEGORIES)
    for locale in i18n.LOCALES:
        missing = expected - set(i18n.SUBJECT_WORDS.get(locale, {}))
        assert not missing, f"i18n.SUBJECT_WORDS[{locale!r}] is missing: {sorted(missing)}"
        unknown = set(i18n.SUBJECT_WORDS[locale]) - expected
        assert not unknown, f"i18n.SUBJECT_WORDS[{locale!r}] names unknown categories: {sorted(unknown)}"


SIGNUP_FORMS = [WEB_PAGE, ROOT / "web" / "app" / "(plain)" / "v2" / "page.tsx"]


@pytest.mark.parametrize("form", SIGNUP_FORMS, ids=lambda p: p.parent.name)
def test_part_time_only_is_not_read_straight_off_the_chip(form):
    """The work-type chips are an inclusive multi-select — the form says "tap all that fit"
    and ships with Full-time pre-selected. So tapping Part-time means "this fits too", and
    `part_time_only: work.has("Part-time")` records the opposite of what the user was shown:
    subscribers with Full-time visibly ticked were stored as part-time-only, and the matcher
    then penalised every full-time role in their digest. It is only "only" when Full-time is
    not also selected.

    Asserted as text because this is TSX the test suite cannot import — same approach, and
    same reason, as the role_category drift tests above.

    Two chip vocabularies are accepted because the two forms are on different sides of the
    i18n refactor: the live wizard keys its chips by stable id (`fulltime`/`parttime`), while
    the unlinked `/v2` copy still keys them by English label. What is being asserted is the
    *derivation*, which is identical either way, so matching both spellings tests the same
    property rather than the spelling.
    """
    text = form.read_text(encoding="utf-8")
    assert not re.search(r'part_time_only:\s*work\.has\(', text), (
        f"{form.name} maps part_time_only straight off the chip — a subscriber who also "
        "selected Full-time is recorded as part-time-only."
    )
    assert re.search(
        r'work\.has\("(?:Part-time|parttime)"\)\s*&&\s*!work\.has\("(?:Full-time|fulltime)"\)',
        text,
    ), (
        f"{form.name} must derive part_time_only from Part-time selected AND Full-time not."
    )


ROLE_INPUT_FORMS = SIGNUP_FORMS + [
    ROOT / "web" / "app" / "(site)" / "[locale]" / "preferences" / "page.tsx"
]


@pytest.mark.parametrize("form", ROLE_INPUT_FORMS, ids=lambda p: p.parent.name)
def test_a_typed_role_is_never_silently_dropped(form):
    """"Add another role…" accepts anything, and most of what people type maps to no
    category — Sales, Cybersecurity, IT Support, all real fields the taxonomy does not model.

    Two ways to get this wrong, and both have shipped. Slugifying the label into
    `role_categories` produced `social_media_specialist`, a value no posting carries, so the
    filter matched nothing and no error was raised anywhere. Dropping it with
    `.filter(Boolean)` and nothing else is the same silence one step earlier: not stored, not
    logged, and the chip stays highlighted so the subscriber believes it took effect.

    The correct handling is to carry it into `stack`, which the shortlist full-text query
    searches — the word still steers retrieval even though nothing classified it.

    `ROLE_CAT[...]` and `roleCategory(...)` are the same lookup either side of the i18n
    refactor: the map moved into `web/lib/options.ts` and became a function when chips stopped
    being keyed by their English label.
    """
    text = form.read_text(encoding="utf-8")
    assert re.search(r"filter\(\(?\w+\)? =>\s*!(?:ROLE_CAT\[|roleCategory\()", text), (
        f"{form.name} does not separate role chips that map to no category — a typed role "
        "is either dropped or slugified into a filter that can never match."
    )
    # ...and that leftover has to reach `stack`, not be computed and then discarded. Either
    # named (`freeRoles`) or inlined into the payload — both are in use.
    assert re.search(r"stack:[^\n]*(freeRoles|\[\.\.\.roles\]\.filter)", text), (
        f"{form.name} computes the unmapped chips but does not send them as search keywords."
    )


# --- Norwegian, 2026-08-11 -----------------------------------------------------------------
#
# The first language taught AFTER its answer key existed rather than before, which is why the
# boundary calls below cite STYRK-08 counts rather than an argument.


@pytest.mark.parametrize("title,expected", [
    # The six failures CLAUDE.md and docs/sources.md name by name. Five were uncategorised and
    # one was a live MISFILE; all six now resolve, and this test is what stops them regressing.
    ("Systemutvikler", "software_engineering"),
    ("Produktsjef", "product"),
    ("Testleder", "software_engineering"),
    ("IT-arkitekt", "software_engineering"),
    # THE misfile: a security analyst matched on "analytiker" and filed as data_analysis, so a
    # Norwegian security role landed in data subscribers' shortlists and in no security one.
    # cybersecurity runs before data_analysis, which is what makes this work.
    ("Sikkerhetsanalytiker", "cybersecurity"),
    ("Overvåknings- og sikkerhetsanalytikere", "cybersecurity"),
    # A Norwegian data engineer used to file as generic `engineering`, because that pattern
    # carries a broad `ingeniør` and data_engineering knew only the Swedish `dataingenjör`.
    ("Dataingeniør", "data_engineering"),
])
def test_the_documented_norwegian_failures_are_fixed(title, expected):
    """Every string here is quoted in CLAUDE.md or docs/sources.md as a known Norwegian
    failure. They are pinned rather than merely fixed because a note saying "this is broken"
    is the kind of thing a future pass re-solves from scratch, and because five of the six
    were fixed by vocabulary that a later simplification could remove without any other test
    noticing."""
    assert taxonomy.classify(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("Helsefagarbeider", "healthcare"),
    ("Sjukepleiar", "healthcare"),               # nynorsk; `s[yj]ukeplei` reads both forms
    ("Barnehagelærer", "education"),
    ("Barne- og ungdomsarbeider", "education"),
    ("Sjåfør klasse C", "logistics_transport"),
    ("Selger til vår butikk", "sales"),
    ("Tømrer søkes", "construction"),
    # Was `skilled_trades`, on the comment "a plumber is a trade, not a production line". Half
    # right: it is certainly not a production line, but the registers file it under CONSTRUCTION
    # and this row was disagreeing with its own answer key. NAV codes this exact ad **STYRK
    # 7126** (as it does `VVS Montør` and `Rørlegger - service- og prosjekt`), and SSYK puts
    # `VVS montör` in the group "VVS-montörer m.fl." under the field "Bygg och anläggning" —
    # four keyed rows, two registers, no counter-example. `ISCO_CATEGORIES` maps `71` to
    # construction, so the scorer already believed this; only the pattern did not.
    ("Rørlegger/VVS-Montør", "construction"),
    ("Overlege", "healthcare"),
    ("Tannlege", "healthcare"),
])
def test_norwegian_occupation_nouns_classify(title, expected):
    """The high-frequency national occupation nouns. Norwegian was the worst-served language
    in the file — 68.5% of a live 2 673-ad NAV corpus uncategorised, against ~38% for Polish
    and ~37% for Dutch — because the whole *sector* vocabulary was missing, not just the tech
    vocabulary."""
    assert taxonomy.classify(title) == expected


def test_lege_is_enumerated_because_nynorsk_builds_adjectives_on_it():
    """`lege` is Norwegian for *doctor* and also the nynorsk adjective ending in `-lege`
    (`faglege`, `offentlege`, `kommunale`). A bare `lege` stem is the georgia rule in
    Norwegian, so the fragment enumerates the real compounds instead.

    Asserted on healthcare's own pattern, not through `classify()`, so it holds regardless of
    where healthcare sits in the order — which is what keeps it meaningful after a reorder."""
    healthcare = dict(taxonomy.PATTERNS)["healthcare"]
    for doctor in ("Overlege", "Tannlege", "Fastlege", "Kommunelege", "Sykehjemslege"):
        assert healthcare.search(doctor), f"{doctor} is a doctor"
    for adjective in ("faglege ledere", "offentlege tenester", "generelle vilkår"):
        assert not healthcare.search(adjective), f"{adjective!r} is not a doctor"


def test_miljoarbeider_and_miljoveileder_follow_the_refreshed_nav_key():
    """**A decline reversed by a bigger key — settled by ground truth in both directions.**

    On the small key (session 9), `miljøarbeider`/`miljøveileder` split three ways even after
    dropping titles naming another profession (healthcare 5, social_care 4, education 3), so it
    was declined — the only safe error is a miss. The refreshed NAV STYRK-08 key (2026-08-23,
    ~6 000 live ads) does not reproduce that split: **`miljøarbeider` codes healthcare in 22 of
    27** (5321/5329 pleie-/omsorgsarbeid), and **`miljøveileder` codes social_care in 8 of 10**
    (3412, *Miljøarbeidere innen sosiale fagfelt*). So the two words are now taken, to different
    categories, and the earlier decline stands corrected by the same kind of evidence that made
    it — the STYRK distribution, not an intuition about the word.

    Healthcare runs before social_care, which is what keeps `miljøarbeider` (healthcare) and
    `miljøveileder` (social_care) from colliding despite the shared `miljø` stem."""
    for title in ("Miljøarbeider fast hver 3. helg", "Miljøarbeider- Syketransport."):
        assert taxonomy.classify(title) == "healthcare", (
            f"{title!r} codes healthcare in 22 of 27 on the refreshed NAV key")
    assert taxonomy.classify("Miljøveileder") == "social_care", (
        "Miljøveileder codes social_care (STYRK 3412) in 8 of 10 on the refreshed NAV key")


def test_program_management_partnerships_strategy_are_first_class_categories():
    """Promoted out of `other_tech_function` 2026-08-23 so a chip can select them.

    Relabel-only: the exact fragments that used to land these in the residual now name dedicated
    categories, so the *set* of matched titles is unchanged — only the label moves from a bucket
    no chip could select to one it can. The three properties that make it safe:

    - the fragments classify to the new categories;
    - **function-first order is preserved** — a title naming a real function ("Legal Program
      Manager", "Marketing Manager") is still claimed by that function, because the three new
      entries sit last among the real categories, immediately before the residual;
    - the residual still catches everything else it did (no coverage lost the other way).
    """
    assert taxonomy.classify("Senior Program Manager") == "program_management"
    assert taxonomy.classify("Technical Program Manager") == "program_management"
    assert taxonomy.classify("Head of Partnerships") == "partnerships"
    assert taxonomy.classify("Strategic Partnerships Manager") == "partnerships"
    assert taxonomy.classify("Corporate Strategy Director") == "strategy"
    assert taxonomy.classify("GTM Strategy Manager") == "strategy"
    # Function-first: a category naming the actual job answers before the three new ones.
    assert taxonomy.classify("Legal Program Manager") == "legal"
    assert taxonomy.classify("Marketing Manager") == "marketing"
    # None of the three is the residual any more, and the residual still catches its own.
    for gone in ("Program Manager", "Partnerships Lead", "Corporate Strategy Analyst"):
        assert taxonomy.classify(gone) != "other_tech_function"
    assert taxonomy.classify("Business Analyst") == "other_tech_function"
    assert taxonomy.classify("Executive Assistant") == "other_tech_function"


def test_kitchen_work_in_a_kindergarten_is_education_and_that_is_a_known_loss():
    """The one genuine loss in the Norwegian pass, recorded rather than hidden.

    `barnehage` (education) runs before the `kjøkken…` hospitality binding, so a kitchen
    assistant *in a kindergarten* files as education. Two rows in a 2 673-ad corpus. The
    ordering is right for the far larger set of kindergarten roles and no reordering fixes
    both — the same shape as the accepted `Responsable Contrôle Qualité (FinTech)` residual."""
    assert taxonomy.classify("Kjøkkenassistent søkes til Klokkergaarden Naturbarnehage") == \
        "education"
    # ...while kitchen work anywhere else still reads as hospitality.
    assert taxonomy.classify("Kjøkkenassistent deltidsvikar") == "hospitality"


def test_every_searchable_category_has_a_label_in_every_catalogue():
    """The public `/jobs` filter menu renders a name for each `role_category` it offers, and a
    category with no name anywhere renders as a raw id — `logistics_transport` — on a page
    with no login in front of it.

    Labels come from two places by design, and this test is what keeps that from being a gap:
    `web/lib/options.ts` maps a category to the signup chip whose label already names it (one
    definition, translated eight times), and the `jobs.categories` block in each catalogue
    covers the categories no chip maps to. `categoryLabel` reads the first, then the second.
    So adding a category without adding a chip *or* a `jobs.categories` entry must fail here
    rather than on a stranger's screen.

    `uncategorised` is excluded because it is never offered as a filter — `_SEARCH_CATEGORIES`
    in `service/webapp.py` removes it, and `test_search_sql.py` pins that it stays removed.
    """
    options = WEB_OPTIONS.read_text(encoding="utf-8")
    block = re.search(r"export const ROLE_OPTIONS[^=]*=\s*\[(.*?)\n\];", options, re.S)
    assert block, f"ROLE_OPTIONS not found in {WEB_OPTIONS.name}"
    # Categories a chip names, and can therefore borrow that chip's translated label.
    by_chip = set(re.findall(r'category:\s*"([a-z_]+)"', block.group(1)))

    searchable = set(taxonomy.CATEGORIES) - {taxonomy.UNCATEGORISED}
    messages = ROOT / "web" / "i18n" / "messages"
    catalogues = sorted(messages.glob("*.ts"))
    # A glob, never a list: a ninth language must be covered by adding the file, not by also
    # remembering to add it here. Same rule as CI's SQL-test skip-check.
    assert len(catalogues) >= 8, f"only {len(catalogues)} catalogues found — the glob is broken"

    for path in catalogues:
        text = path.read_text(encoding="utf-8")
        cats = re.search(r"\n    categories:\s*\{(.*?)\n    \},", text, re.S)
        extra = set(re.findall(r"^\s*([a-z_]+):", cats.group(1), re.M)) if cats else set()
        missing = searchable - by_chip - extra
        assert not missing, (
            f"{path.name} has no label for {sorted(missing)} — the /jobs filter menu would "
            f"show the raw category id. Add a chip in options.ts or a jobs.categories entry."
        )
        unknown = extra - set(taxonomy.CATEGORIES)
        assert not unknown, (
            f"{path.name} labels {sorted(unknown)}, which is not a role_category — a label "
            "nothing can ever render."
        )


# --------------------------------------------------------------------------------------
# The `\bengineer\b` catch-all, and the Swedish `assistent` residual.  Added 2026-08-17.
#
# Both are the same shape of defect: a broad pattern answering confidently for titles it
# does not understand.  `software_engineering`'s bare catch-all was claiming 718 active rows
# of sales, IT-support, safety and maintenance engineers, and `other_tech_function`'s bare
# `assistent` was claiming 1 673 Swedish personal-assistant ads.  A misfile is worse than a
# miss: a miss still reaches the AI matcher on the keyword path, a misfile does not.
#
# Every title below is a real production string, from the corpus dump or an answer key —
# `test_education.py`'s rule, and the reason these read oddly specific.
# --------------------------------------------------------------------------------------


def test_a_sales_engineer_is_a_sales_job():
    """The catch-all must DECLINE so `sales`, which runs later, can answer."""
    for title in ("Sales Engineer", "Graduate Sales Engineer",
                  "Senior Sales Engineer Data Center (m/w/d)", "Presales Engineer",
                  "Pre-Sales Engineer (M/Ž)", "Sr Adv Appl/Sys Sales Engineer"):
        assert taxonomy.classify(title) == "sales", title
    # The neighbour that must NOT move: a software engineer working *on* the sales platform.
    assert taxonomy.classify("Senior Software Engineer - Sales Tech") == "software_engineering"


def test_b_support_engineer_reaches_customer_support():
    for title in ("Technical Support Engineer", "IT Support Engineer",
                  "Customer Support Engineer", "Technical Support Engineer (m/w/d)",
                  "Senior Technical Support Engineer, Observe by Snowflake"):
        assert taxonomy.classify(title) == "customer_support", title


def test_c_application_support_engineer_is_a_decline():
    """A decline pinned as a DECISION, not an oversight.

    Adding `support engineer` to `customer_support` would claim these (measured: identical on
    all five answer-key slices).  It was declined on 2026-08-17 because it converts an honest
    miss into a confident answer on the most arguable member of the family.  If that is ever
    reopened, this test is the one that should fail first and be changed deliberately.
    """
    for title in ("Application Support Engineer", "Support Engineer"):
        assert taxonomy.classify(title) == "uncategorised", title


def test_d_safety_engineering_is_engineering():
    for title in ("Functional Safety Engineer", "Safety Engineer", "Sr HSE Engineer",
                  "Jr EHS Engineer", "Environmental Health and Safety Engineer",
                  "Senior Fire Safety Engineer (all genders)"):
        assert taxonomy.classify(title) == "engineering", title
    # The guards, each a real title that must stay in software.
    for title in ("Trust & Safety Engineer", "Fullstack Engineer, Safety Engineering",
                  "Software Functional Safety Engineer – Automotive"):
        assert taxonomy.classify(title) == "software_engineering", title


def test_e_maintenance_engineer_is_engineering_not_software():
    for title in ("Maintenance Engineer", "Predictive Maintenance Engineer Antwerpen",
                  "JUNIOR MECHANICAL MAINTENANCE ENGINEER"):
        assert taxonomy.classify(title) == "engineering", title
    # The title `_FR_ROLE`'s docstring records as having been rescued from this family once
    # already — now pinned by a test rather than by a French stem's shape.
    assert taxonomy.classify(
        "AI Application Operations & Maintenance Engineer (Azure)") == "software_engineering"
    # `skilled_trades` runs earlier and must keep the technicians.
    for title in ("Maintenance Technician", "Senior Underhållstekniker"):
        assert taxonomy.classify(title) == "skilled_trades", title


def test_f_the_catch_all_still_claims_everything_else():
    """The guard is POSITIONAL; this is what a whole-title guard would have broken.

    `^(?!.*(?:sales|support|...))` passes all five answer keys with byte-identical numbers, so
    the keys cannot distinguish it from the lookbehind — only the corpus can.  It moves 147
    further postings the wrong way, led by `Senior Salesforce Engineer`, because "sales" is
    inside SALESFORCE.  That is the `georgia` rule.  This test passes against the unpatched
    file too; it earns its place by failing against the *wrong fix*.
    """
    for title in ("Systems Engineer", "Principal Engineer", "Staff Engineer",
                  "IT Operations Engineer", "Engineering Manager, Growth", "Software Engr I",
                  "Senior Salesforce Engineer", "Salesforce Ads Systems Engineer",
                  "Sr. Systems Engineer, Sales & Marketing",
                  "Desktop Engineer (2nd Line Support)", "Support Operations Engineer",
                  # 2026-08-22: neighbours of the four heads that left (see the test below).
                  # These are what a whole-title guard on those words would have cost.
                  "Senior Engineer, Customer Platform", "Staff Engineer - Customer Onboarding",
                  "Engineering Manager, Customer Experience",
                  "Senior Software Engineer, Legal Products"):
        assert taxonomy.classify(title) == "software_engineering", title


def test_f2_four_more_heads_decline_to_their_real_owners():
    """`Legal`, `Value`, `Customer` and `Customer Success` Engineer, added 2026-08-22.

    **This narrows what `test_f` above pins, and the reason is in the 2026-08-17 commit rather
    than in a new argument.** That session released 718 postings — sales, IT support,
    maintenance, safety — on the stated principle that *a misfile is worse than a miss*, and it
    declined `support engineer` precisely because it "would convert 129 honest declines into
    confident answers on the most arguable member of the family".  Three of the titles `test_f`
    listed were never mentioned in that reasoning: they were examples of the catch-all's
    remaining scope, not a verdict on the phrases.  Measured here, they are the same failure the
    session was fixing, in four more disciplines:

        legal engineer              88 postings, one legal-AI employer's whole board, and
                                    `legal`'s own `\\blegal\\b` was DEAD for every title
                                    containing "Engineer"
        value engineer              95 — a business-value pre-sales consultant.  94 decline,
                                    which is the `support engineer` treatment, not a new claim
        customer engineer           92 — Oracle data-centre field service and pre-sales
        customer success engineer   44 — `operations` owns `customer success` and could never
                                    answer, because the catch-all answered first

    The guard is four LOOKBEHINDS, so the mechanism verdict `test_f` exists to defend is intact:
    the whole-title form still costs 147 postings via SALESFORCE, and the four neighbour titles
    added to `test_f` above are what a whole-title guard on *these* words would have cost.
    Wrong-direction moves across all five answer-key slices: zero.
    """
    for title, expected in (("Legal Engineer", "legal"),
                            ("Lead Legal Engineer", "legal"),
                            ("Legal Engineer - In-House", "legal"),
                            ("Customer Success Engineer", "operations"),
                            ("Senior Manager, Customer Success Engineering", "operations")):
        assert taxonomy.classify(title) == expected, title
    # `value engineer` and `customer engineer` DECLINE — nothing else claims them, and that is
    # the intended outcome.  A decline still reaches the AI matcher on the keyword path; a
    # confident wrong answer puts the job in a stranger's digest.
    for title in ("Value Engineer", "Senior Value Engineer - Public Sector",
                  "Customer Engineer", "Customer Engineer I - Atlanta, GA"):
        assert taxonomy.classify(title) != "software_engineering", title


def test_g_swedish_personal_assistant_is_social_care():
    """45% of the SSYK social field, and it was reaching nobody.

    `other_tech_function` has no chip (`ROLE_ID_FOR_CATEGORY` is display-only, one way), so
    these were invisible rather than misrouted.  A title pattern beats the SSYK hint in
    `classify`, so this could not be fixed from the adapter.
    """
    for title in ("Personlig assistent", "Personliga assistenter",
                  "Personlig assistent till kvinna i Solna",
                  "Personlig assistans till man i Göteborg"):
        assert taxonomy.classify(title) == "social_care", title


def test_h_the_english_personal_assistant_is_not_a_care_worker():
    """The false friend that makes the fix Swedish-only.

    English "Personal Assistant" is an executive admin; Swedish "personlig assistent" is LSS
    disability support.  Two near-identical strings, two unrelated jobs — and the `-assistent`
    compounds must keep their own categories, which is why `assistent` was not simply bounded.
    """
    for title in ("Personal Assistant", "Executive Assistant",
                  "Personal Assistant to the CEO"):
        assert taxonomy.classify(title) == "other_tech_function", title
    assert taxonomy.classify("Ekonomiassistent") == "finance_accounting"
    assert taxonomy.classify("Löneassistent") == "finance_accounting"
    assert taxonomy.classify("Elevassistent") == "education"


# --------------------------------------------------------------------------------------
# Wave-3 integration guards, and two employer/currency collisions.  Added 2026-08-17.
#
# Every title below is a real production row, verified to exist exactly once in a 122 208-row
# corpus dump.  Three of these tests pin the *shape* of a guard rather than merely that it
# works, because in each case the answer keys could not tell the right fix from the wrong one
# — all five slices are identical under both — and only the corpus separates them.  That is
# the same lesson as the `\bengineer\b` lookbehind above, arriving three more times.
# --------------------------------------------------------------------------------------


def test_czech_psycholog_does_not_read_the_english_word_psychology():
    """`healthcare` is pattern #1, so a leak here outranks everything.

    The Czech fragment is `psycholo(?:g(?!i)|ž)`; the `(?!i)` stops Czech *psychologie* but
    English *Psychology* ends `-gy`, so it slipped through.  Guard is `(?![iy])`.
    """
    assert taxonomy.classify(
        "Podcaster and Content Creator Psychology Today") == "social_media"
    assert taxonomy.classify(
        "Assessment Scientist: Masters in Psychology; Psychometrists") == "science_research"
    # The Czech rows the fragment exists for must still land.
    assert taxonomy.classify("Psycholog") == "healthcare"


def test_product_design_guard_reads_the_word_before_not_only_after():
    r"""`\bproduct design\b(?!\s*engineer)` guarded the word AFTER; a Swedish row puts it before."""
    assert taxonomy.classify(
        "Mechanical Engineer  Handheld R&D Product Design  Husqvarna Group") == "engineering"
    assert taxonomy.classify("Product Designer") == "design"


def test_vardcentral_does_not_take_the_clinic_receptionist():
    """Swedish broke a decision the file had already made in Spanish.

    `healthcare`'s Spanish arm carries `^(?!.*(?:recepcionista|professional))` precisely to keep
    a clinic receptionist in `hospitality`.  `vårdcentral` re-broke it in Swedish, so it carries
    the same anchor now.
    """
    assert taxonomy.classify("Receptionist till vårdcentralen Skärvet") == "hospitality"
    assert taxonomy.classify("Distriktssköterska till vårdcentralen") == "healthcare"


def test_hr_is_not_an_hourly_rate():
    """101 active rows were filed `hr_recruiting` because the title quoted a rate.

    `georgia` in a currency string.  Declining lets the right pattern answer, exactly as with
    the engineer catch-all: `hr_recruiting` runs before `legal`, `operations` and
    `other_tech_function`, so 9 rows reach `legal`, 5 `operations`, and 83 become honest
    declines.
    """
    assert taxonomy.classify(
        "Legal Research Specialist - Fully Remote | Upto $120/hr") == "legal"
    for title in ("Voice Narrator - Fully Remote | Upto $50/hr",
                  "AI Safety Specialist - Fully Remote | Upto $70/hr"):
        assert taxonomy.classify(title) == "uncategorised", title


def test_the_hr_guard_is_positional_not_whole_title():
    r"""Pins the SHAPE.  A whole-title `^(?!.*\d\s*/\s*hr\b)` throws away a real HR job that
    happens to quote its own rate, and a bare `(?<!/)` throws away a Swedish compound.  All
    three forms are identical on all five answer-key slices; only these two rows separate them.
    """
    # Dies under the whole-title form.
    assert taxonomy.classify("HR Leader - Fully Remote | Upto $80/hr") == "hr_recruiting"
    # Dies under a bare `(?<!/)`.  U+2011 non-breaking hyphen — copied from production, not typed.
    assert taxonomy.classify("L\u00f6n/HR\u2011administrat\u00f6r - J\u00f6nk\u00f6ping") \
        == "hr_recruiting"


def test_ekonom_does_not_read_the_employer_mekonomen():
    """A role stem matching a company name inside the classifier, not inside `search_tsv`."""
    for title in ("Kundmottagare sökes omgående till Mekonomen Noret Mora",
                  "FORDONSTEKNIKER (MEKONOMEN)"):
        assert taxonomy.classify(title) != "finance_accounting", title


def test_the_ekonom_guard_is_a_lookbehind_not_a_word_boundary():
    r"""Pins the SHAPE, and this is the one where the obvious fix is measurably wrong.

    A leading `\b` moves 28 rows and only 3 are Mekonomen; the 25 casualties are the Swedish
    compounds this pattern exists for.  A prefix census over the corpus is what settles it: `m`
    is the only prefix before `ekonom` that is not a compound morpheme, and all 10 of its
    occurrences are Mekonomen.
    """
    for title in ("Bolagsekonom till TidX Förvaltning AB, Göteborg",
                  "Hälsoekonom som vill arbeta med läkemedel och medicinteknik",
                  "Verksamhetsekonom till Söderhamns kommun"):
        assert taxonomy.classify(title) == "finance_accounting", title
    # A whole-title `mekonomen` exclusion would lose this; the lookbehind keeps it.
    assert taxonomy.classify("Ekonomiassistent till Mekonomen") == "finance_accounting"


def test_laborant_reaches_the_standalone_czech_title():
    """The wave-1 verdict, reversed — reaching this row is now the point, not the hazard.

    A term rejected against category A is not rejected; it is untested against B.
    """
    assert taxonomy.classify("Laborant/ka") == "engineering"
    assert taxonomy.classify("Chemielaborant") == "engineering"


def test_fr_role_heads_do_not_match_inside_foreign_compounds():
    r"""The leading `\b` on `_FR_ROLE`.  `chef` used to reach into Swedish `Sektionschef`."""
    assert not taxonomy._FR_ROLE.startswith("(?:"), (
        "_FR_ROLE lost its leading \b — French heads will match inside compounds in every "
        "other language the corpus contains, which is how a French pattern came to be reading "
        "Swedish morphology."
    )
    assert taxonomy.classify("Chef de Projet") != "manufacturing_production"
