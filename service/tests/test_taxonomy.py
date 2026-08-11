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
    ("Affärsutvecklare", "uncategorised"),     # ...but business development is not software
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
    ("Magazine Content Editor", "other_tech_function",
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
    ("Sr. Product Cybersecurity Architect for Advanced Hearing Aid Platform", "cybersecurity",
     "the hearing-aid INDUSTRY employs engineers; the term is for the audiology PROFESSION"),
])
def test_multilingual_stems_stay_inside_their_own_language(title, expected, trap):
    """The cross-language collisions the 2026-08-10 pass had to defuse, one case each.

    `PATTERNS` is a single ordered list shared by ten languages, so a stem added for one of
    them reads every other language's titles too. Every string here classified *wrongly*
    before its guard existed — these are not hypotheticals, they are the measured failures,
    and each one is a lookahead, a word boundary or a binding that a later simplification
    would remove without any other test noticing."""
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
    ("engineering", "Laborant/ka",
     "DE `laborant` must be compound-prefix-bound: the Czech key writes it standalone, "
     "and the bare term took both of those rows in wave 1"),
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
