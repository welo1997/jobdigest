"""Teamtailor ATS — public career-site JSON Feed for a curated list of companies (no key).

Public endpoint: ``https://{tenant}.teamtailor.com/jobs.json`` — a JSON Feed (application/
feed+json) that every Teamtailor career site publishes without a key, login, or browser.
Each item carries a schema.org ``_jobposting`` block with a structured ``jobLocation`` (ISO
``addressCountry``), ``baseSalary``, ``hiringOrganization.name`` and ``datePosted`` — so the
company name is read off the feed, never guessed from the slug, and the country resolves
cleanly.

Why this is permitted (settled 2026-08-06 — read this before assuming, per the repo's rule
that a source is settled on its terms, never on robots.txt alone):
  - **There is no anti-scraping term.** Teamtailor's public footer carries only a Privacy
    Policy, Cookie Policy, Security page, Code of Conduct and Modern Slavery statement — no
    Terms of Service governing career-site visitors, and nothing forbidding automated reading.
    (Contrast Alma Career / BambooHR / the Bundesagentur, each of which *did* forbid it in a
    binding document while robots.txt looked fine — the three times that trap has been hit.)
  - **robots.txt affirmatively allows it.** A career site's robots.txt disallows only
    ``/app/``, ``/messages/``, ``/messenger/`` and ``/jobs/internal/``; the public ``/jobs``
    path and the feed are allowed, and it carries ``Content-Signal: search=yes, ai-input=yes``
    (only ``ai-train=no``) — a machine-readable permission for exactly this use: read for
    search / as input to a matcher, not for training.
  - **The feed is published for consumption.** Teamtailor markets AI-agent/aggregator access
    to these feeds; ``jobs.json`` exists to be read.

Personio was evaluated in the same pass and is NOT built: its ``/xml`` feed works, but its
governing website terms could not be read (the marketing site returns 429/403 to every
attempt, as it has every prior time), and the ``/xml`` endpoint answers with a job count for
bogus slugs (``amazon``, ``johnson`` — neither a Personio customer), so identity cannot be
verified the way it can here. Unresolved terms are not permission; it stays skipped.

Tenants are identity-checked against each board's own ``hiringOrganization.name`` and posting
countries, not a live 200 — the same discipline as recruitee/workable. The curated set fills
the thinnest selectable countries (EE/LV/FI/DK) that the Greenhouse/Ashby/Lever passes could
not reach, because those companies run Teamtailor rather than a supported ATS.

No source-level country constant and no source ``remote_signal``: the feed gives a per-posting
country, and it carries no structured remote flag, so remote is left to the downstream text
classifier (``geo.is_fully_remote``) rather than asserted here.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

HEADERS = politeness.HEADERS
_TIMEOUT = 20
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

#: Verified live 2026-08-06 against each board's own jobs.json — company name AND posting
#: countries, not a job count. The subdomain is the exact Teamtailor slug and is not always
#: derivable from the company name (Printful's is `printfulinc`, IQM's is `iqm` not
#: `meetiqm`). Skipped in the same pass: `starship` (0 jobs), `edrone` (3 of 4 Brazil).
TENANTS = [
    "comodule",     #  3 — Comodule, Tallinn (EE) — IoT for e-bikes
    "mintos",       # 21 — Mintos, Riga (LV) 20 + LT/DE — investment marketplace
    "printfulinc",  # 37 — FYUL (ex-Printful), Riga (LV) 12 + ES 9 + PL 7 + EE 4 + spread
    "iqm",          # 18 — IQM Quantum Computers, Espoo (FI) 12 + Munich (DE) 10
    "templafy",     #  1 — Templafy, Copenhagen (DK)
    "queueit",      #  2 — Queue-it ApS, Copenhagen (DK)
    "lightyear",    #  1 — Lightyear, Tallinn (EE) — investing app
    "cybelangel",   #  8 — CybelAngel, Paris (FR) 5 — cyber threat intelligence
    "memobank",     #  1 — Memo Bank, Paris (FR). NOT recruitee:memo, a Belgian impostor.
    "sweep",        #  7 — SWEEP, Paris (FR) 4 + London — carbon accounting
    "podimo",       #  7 — Podimo, Amsterdam (NL) 6 + Berlin — audio (Danish-HQ)
    # Expansion pass 2, 2026-08-06, identity-checked against the postings.
    "infraspeak",   #  9 — Infraspeak, Porto (PT) + Barcelona — maintenance-management SaaS
    # AT pass, 2026-08-07. Two Teamtailor slugs probed for Austrian companies answered live
    # and were **rejected on their own postings**, which is the whole reason this check
    # exists: `medicus` is a gynaecology clinic in Oslo, not Medicus AI of Vienna, and
    # `journi` is eight near-identical "Copy of Fullstack Developer … UK" rows in Banbury,
    # GB — a test board, not Journi of Vienna.
    "tractivegmbh", #  2 — Tractive GmbH, Pasching (AT). Slug is not derivable from the name.
    # IT expansion pass, 2026-08-07, identity-checked against the postings' own cities.
    "weroad",       # 19 — WeRoad, Milano (IT) 6 of 8 + US. Travel; the roles are product,
    #                     data engineering and category management, not tour guiding.
    "unobravointernational",
    #                  4 — Unobravo, Milan (IT) 3 of 4. **The company is an online-therapy
    #                     platform but this board is its operations/growth hiring** (SEM
    #                     manager, Head of Growth, operations) — which is why it is taken and
    #                     `ashby:serenis`, the same sector but all clinician roles, is not.
    # FR expansion pass, 2026-08-07. Teamtailor turned out to be where the French mid-market
    # is, and it produced this repo's cleanest example of why a slug is not an identity:
    # **`sunday` answers live on two ATSes and they are two different companies** — `ashby:sunday`
    # is a Redwood City robotics firm (mechanical/firmware/motor-control engineers) and
    # `teamtailor:sunday` is the French restaurant-payments company. Only the second is taken.
    # Two more live-200 impostors rejected: `teamtailor:air` (74) is an Italian energy call
    # centre in Rome and Tivoli, not Air France-KLM — as `greenhouse:air` was already known
    # not to be Air Liquide; and `recruitee:lvmh` (49) **is genuinely LVMH** and was skipped
    # anyway, being 49 German beauty-consultant and shop-floor roles with no knowledge work in
    # it — the `ashby:serenis` call again.
    "sunday",       # 35 — Sunday, Paris 3 of 8 + Lyon/Marseille/Strasbourg, London, Miami
    "groupepositive",
    #                  19 — Sarbacane's group ("Positive"), Lyon 3 + Hem (FR) + Berlin (DE) 3
    #                     + Wrocław (PL). The slug is the group name, not the product's.
    "botify",       # 14 — Botify, Paris (FR) 3 of 8 + New York 5; SEO/search analytics
    "adeo",         # 10 — ADEO (Leroy Merlin's parent), Ronchin (FR) 7 of 8 + Paris
    "thales",       # 22 — **Thales Norway AS**, Oslo 5 / Trondheim 3, all of it software
    #                     engineering, architecture and DevOps. Not an impostor — a real
    #                     subsidiary board — and Norway held 23 active postings in the whole
    #                     corpus, so this roughly doubles it. Distinct from workday:thales.
    # RO pass, 2026-08-07. Two of the four supported-ATS hits in 64 Romanian employers were
    # impostors, both on tokens that look like a safe abbreviation of the company name:
    # `ashby:amber` (22) is a Köln/Aachen company, not Amber Studio of Bucharest, and
    # `teamtailor:yonder` (3) is a Cardiff/London fintech, not Yonder of Cluj (tss-yonder.com).
    # Also skipped: `workday:genpact` — real, but Noida/Gurgaon/Hyderabad/Bangalore bulk with
    # 3 Iași rows in 60, and it is N+1, which is the Barclays/Nike call; and `bamboohr:fintechos`,
    # a genuine Romanian product company sitting on an ATS this repo refuses on terms.
    "zitec",        # 10 — Zitec, București (RO) 4 of 8 + remote-RO. Digital/product agency,
    #                     and the only unambiguously Romanian board the pass found.
    # GB expansion pass, 2026-08-07. **Teamtailor has an impostor pattern worth naming**: two
    # generic English slugs on it are Italian staffing/consultancy firms — `air` (74) is a Rome
    # and Tivoli energy call centre, not Air France-KLM, and `united` (15) is eight B2B sales
    # and reception roles in **Pavia**, not United Utilities. Both answered 200 with plausible
    # counts. `wellcome` (2) was skipped for the opposite reason: two social-media roles with
    # **no location at all**, so identity could not be checked either way.
    "attest",       #  9 — Attest, London (GB) 7 of 8 + New York. Consumer research SaaS.
    # BE pass, 2026-08-07.
    "ml6",          # 12 — ML6, Gand (BE) 2 + Amsterdam 4 / Eindhoven (NL) + München (DE).
    #                     Belgian AI consultancy with a Dutch-weighted board; taken for both.
    "babcock",      # 13 — **Babcock France**: Le Cannet-des-Maures 4, Paris 2, Mérignac, Dijon.
    #                     Probed for Babcock International (UK) and it is a real subsidiary
    #                     board, not an impostor — French aviation-support inventory. Third
    #                     time this session a subsidiary board served a country nobody searched
    #                     (after Thales Norway and Playtech's Baltics).
    # NO pass, 2026-08-07 — the first deliberate Norwegian sweep, and it confirmed that
    # Teamtailor is where the Nordic mid-market is: 32 of the run's 52 supported boards were
    # Teamtailor, against 5 Lever and 4 Workable. Every one below is identity-checked against
    # its own postings' cities *and* screened for the demo content described at `_DEMO_CONTENT`.
    "schibsted",    # 21 — Schibsted: Oslo 6 of 8 + Stockholm 2. Media/product; Aftenposten and
    #                     Podme roles. The strongest Norwegian board of the run after bekk.
    "vipps",        #  7 — Vipps MobilePay: Oslo 6 of 7. Fintech; analyst, frontend, dev manager.
    "veidekke",     # 10 — Veidekke: 8 of 8 Norwegian, Oslo 5. Construction, and taken on the
    #                     professional half — Prosjektleder Jernbane, Prosjektingeniør
    #                     Jernbaneteknikk, Ytre miljørådgiver. It does also carry trades
    #                     (Montør, Asfalt, Reisemekaniker), so it is the closest call here;
    #                     kept because project engineering and environmental advisory are
    #                     squarely ISCO 1-2, unlike `workday:jlp`'s shop floors.
    "kongsbergdigital",
    #                18 — **self-identifies as "Falkor"**, not Kongsberg Digital, and is kept on
    #                     that name: the postings are Kognitwin (KDI's own product) roles in
    #                     Fornebu (NO) 3 + Bengaluru (IN) 4. Identity comes off the feed, so the
    #                     stored employer is right even though the slug is the old name.
    "volue",        # 13 — Volue: Porsgrunn + Oslo (NO) + München (DE), Gdańsk (PL), Kadıköy (TR).
    "itera",        # 13 — Itera: Fredrikstad (NO) 3 + Oslo + Brno (CZ) + Kyiv/L'viv (UA).
    "nordicsemiconductor",
    #                14 — Nordic Semiconductor: Oslo (NO) 3 + Taiwan 2, Philippines, US 2.
    "vismasoftwareinternationalas",
    #                 5 — Visma Software International: Oslo (NO) 5 of 5.
    "vismaamilias",  #  1 — Visma Amilias: Oslo (NO).
    "vismafinland",  #  3 — Visma Finland: Helsinki (FI) 3 of 3 — FI is one of the thinnest
    #                      selectable countries, which is why a 3-row board is worth a request.
    "attensi",      # 10 — Attensi: Oslo (NO) 3 + London 4 + Boston. Simulation training.
    "ardoq",        #  8 — Ardoq: Oslo (NO) 2 + London 3 + København 2 + New York.
    "noisolation",  # 10 — No Isolation: Oslo (NO) 2 + London 2 + DE 2 + PL.
    "signicat",     #  7 — Signicat (Trondheim-HQ) and **0 Norwegian rows** — Madrid (ES),
    #                     Estoril (PT) 2, București (RO), Vilnius (LT) 2, Enschede (NL). Taken
    #                     for exactly that: four of the five countries are thin ones.
    "easee",        #  4 — Easee: Oslo (NO) + London, Glasgow, Amsterdam.
    "puzzel",       #  3 — Puzzel: London + København + Amsterdam.
    "nelhydrogen",  #  7 — Nel Hydrogen: Wallingford (US) 6 + Oslo (NO) 1. Mostly American, kept
    #                     because US is selectable and the board costs one request.
    "hystar",       #  1 — Hystar: Høvik (NO).
    "techstep",     #  1 — Techstep: Gdańsk (PL).
    "esmart",       #  1 — eSmart Systems (Halden, NO): the one row is in the Netherlands.
    # **Five Norwegian slugs answered live and were rejected**, and they split into two kinds.
    # Two are ordinary slug collisions, caught by the feed's own name: `norr` is
    # "Svensk Markservice AB", eight grounds-maintenance and snow-clearing jobs around Umeå
    # (SE), not Norrøna; `remarkable` is "REMARKABLE RETAIL", Swedish mystery-shopper gigs in
    # Uppsala, Gävle, Mora and Borlänge, not reMarkable of Oslo. **Three are abandoned demo
    # tenants — `akerbp`, `jotun` and `salmar`** — and those are the ones that matter, because
    # the feed titles read "Aker BP", "Jotun" and "SalMar" and the identity check therefore
    # *passes*. See `_DEMO_CONTENT`. `vismaenterpriseab` (Växjö) and `vismacc` were skipped
    # rather than rejected: SE is already flooded by platsbanken, and 2 of vismacc's 3 rows are
    # talent pools that `_TALENT_POOL` drops.
    # SE pass, 2026-08-07. Sweden is the best-covered EEA country (16 588 active, 16 296 of
    # them platsbanken) — but the register does not reach the tech employers: measured today,
    # platsbanken holds **0 rows for Spotify, Northvolt, Truecaller and Epidemic Sound**, 2 for
    # Klarna and 7 for Ericsson. Reporting is voluntary in practice, and that gap is this list.
    # Swedish IT consultancies lead it because their whole inventory is ISCO 1-2 by construction.
    # **Seven Swedish boards found in this pass were dropped again after measuring the overlap,
    # and the reason is not tidiness.** Platsbanken and an ATS name the same employer
    # differently — "NEXER GROUP AB" against "Nexer" — and `digest.dedupe_key` folds only a
    # *trailing legal form*, so it collapses "Tobii AB" ≡ "Tobii" and does **not** collapse
    # "NEXER GROUP AB" ≡ "Nexer", "IVER ACCELERATE AB" ≡ "Iver" or "SECURITAS SVERIGE AB" ≡
    # "Securitas". So a duplicated Swedish employer is not merely two shortlist slots, it is a
    # second *email* for a job already sent — the exact failure dedupe_key exists to prevent,
    # arriving by a route it cannot see (two sources spelling one employer differently, rather
    # than one source relisting). Measured platsbanken rows vs board rows: nexergroup 62/100,
    # iver 21/6, softhouse 9/10, kognity 4/5, mathem 1/1, tobii 4/6, anyfin 4/8 — all dropped
    # on a >=50% rule. Sweden holds 16 588 active postings, so redundant inventory there buys
    # nothing and the duplicate-email risk is the whole cost. The boards kept below are the
    # ones the register genuinely misses (0 platsbanken rows unless noted).
    "hiq",          #  78 — HiQ: Göteborg 4 of 8 + Linköping 3 + Malmö.
    "prevas",       #  45 — Prevas: Västerås 2, Uppsala 2, Stockholm, Linköping.
    "instabee",     #  32 — Instabee (Budbee/Instabox): Stockholm 4 + Amsterdam (NL) 3.
    "polestar",     #  30 — Polestar: Göteborg 3 + Oslo, Brussels, Bicester, Shanghai, Seoul.
    "securitas",    #  29 — Securitas, and taken *because* the board is not guards: Data Domain
    #                     Owner, Financial Controller, HR BP, Junior Business & Data Analyst,
    #                     across Stockholm 2, Dublin 2, Warszawa, Glostrup (DK).
    "paradox-interactive",
    #                  19 — Paradox Interactive: Stockholm 4 + Tampere (FI) 3 + Sitges (ES).
    #                     **Not `ashby:paradox`**, which is Dubai/Paris and a different company.
    "lindex",       #   7 — Lindex, and a genuinely close call kept on its titles: Infrastructure
    #                     Specialist, System Developer (.NET) in Kiruna, Data Scientist, PR
    #                     Project Executive — only 2 of 7 are store roles. Contrast
    #                     `fenixoutdoor` below, which is the same industry and was rejected.
    "storytel",     #   6 — Storytel: Stockholm 5 + København.
    "hemnet",       #   5 — Hemnet: Stockholm 5 of 5.
    "detectify",    #   4 — Detectify: Stockholm 4 of 4.
    "clavister",    #   4 — Clavister: Örnsköldsvik 2, Gothenburg, Stockholm. Network security.
    "starstable",   #   2 — Star Stable: Stockholm 2.
    # **Rejected in the SE pass**, and the two inventory rejections are the instructive ones:
    # `fenixoutdoor` (78) is genuinely Fenix Outdoor but the board is shop floor — Butikssäljare,
    # "Verkäufer (w/m/d) in Teilzeit", Timemedarbejder brand store, Kasse und Kundenservice; and
    # `doktor` (50) is genuinely Doktor.se but the board is clinicians — Sjuksköterska five times
    # over, Distriktssköterska, Specialistläkare. Those are the `workday:jlp` and `ashby:serenis`
    # calls. `axis` (2) is an identity rejection: both rows are in Oslo, so it is not Axis
    # Communications of Lund.
    # DK/CH/FI/IE pass, 2026-08-07 — four countries in one discovery run.
    "lunar",        #  14 — **Lunar (the Danish neobank): Aarhus 3 + København 5.** The slug
    #                     answers live on two ATSes and only this one is the bank —
    #                     `ashby:lunar` is San Francisco/Newcastle/London. Same shape as
    #                     `sunday`, and the reason a live 200 is never an identity.
    "planmecaoy",   #   7 — Planmeca Group (FI): Compliance & Corporate Responsibility Manager,
    #                     Solution Owner Digital CX, technical product specialist. The slug
    #                     carries the `oy`, which no name-derived guess would produce.
    "siili",        #   4 — Siili Solutions (FI): AI Architect + AI Engineer; the other two rows
    #                     are open applications that `_TALENT_POOL` drops.
    # `maersk` (10) was **caught by `_DEMO_CONTENT`**, not by hand: feed titled "Maersk", 5 of 10
    # items carrying Teamtailor's product pitch, and one row literally titled "Copy of iOS
    # developer". A second abandoned demo tenant, in a different country, found by the guard
    # written earlier the same day for `akerbp`/`jotun`/`salmar`. `holcim` (72) is genuinely
    # Holcim and rejected on inventory — Tipper Driver, Mixer Driver, Factory Operative, Plant
    # Operative across GB depots.
    # FI deep pass, 2026-08-07 — a second Finnish list (81 fresh names) after the four-country
    # run. Finland held **210 active postings from 26 employers**, the most concentrated
    # inventory of any selectable country, and every non-ATS route is closed: Työmarkkinatori is
    # gated *and* robots-disallows `/api/`; Jobly.fi is Alma Career; Duunitori 403s the honest
    # agent; Oikotie Työpaikat forbids "säännöllinen, järjestelmällinen tai jatkuva tietojen
    # kerääminen … indeksointi"; Kuntarekry, valtiolle.fi and Aarresaari are client-rendered or
    # moved; and avoindata.fi holds statistics plus one dead CC-BY endpoint.
    "granlund",     #  33 — Granlund: Helsinki 3, Tampere 2, Rovaniemi + Umeå (SE). Building
    #                     services engineering — project managers, sprinkler designers.
    "patria",       #  18 — Patria: Hämeenlinna 5, Jämsä 2, Linnavuori. Defence; LCS and R&D
    #                     managers, weapon systems.
    "insta",        #  14 — Insta Group: Tampere 7, Kuopio. QA lead, embedded software lead.
    "eficode",      #   8 — Eficode: Helsinki + Stockholm 2, København, Malmö, Düsseldorf,
    #                     Zürich. DevOps consultancy, and the widest Nordic spread here.
    "ensto",        #   8 — Ensto: Porvoo (FI) + Villefranche-sur-Saône 3 / Bagnères-de-Bigorre
    #                     (FR) + India, US. Finnish electrification, French operations.
    "vincitoyj",    #   7 — Vincit: Helsinki 4, Hervanta 2, Jyväskylä. The slug carries `oyj`.
    "teleste",      #   6 — Teleste: Tampere 3, Kaarina + Wrocław (PL).
    "sitowise",     #   5 — Sitowise: Espoo 3, Tampere.
    "netum",        #   3 — Netum: Helsinki, Tampere.
    "sympa",        #   1 — Sympa: Espoo. Finnish HR software.
    "nightingalehealth",
    #                   1 — Nightingale Health: Helsinki. Blood-analysis diagnostics.
    # **Three rejections here are the best slug lessons in this file.** `ssh` self-identifies as
    # **"French Express"** — a Stockholm restaurant group whose three rows are *Kock* and
    # *Sommar Bartender Extra*, not SSH Communications Security of Helsinki. `career` (probed for
    # Innofactor) self-identifies as **"Teamtailor"**: it is the ATS vendor's own careers page,
    # which is what a maximally generic slug resolves to. And `wihuri` IS genuinely
    # "Wihuri Oy Tekninen kauppa" and was still rejected — its rows are Piirihuoltomekaanikko
    # (district service mechanic) and an open application, so it fails on inventory, not
    # identity. Elsewhere in the same pass: `greenhouse:remedy` is Missouri City TX, not Remedy
    # Entertainment of Espoo; `greenhouse:nortal` is 8 of 8 "Latin America - Remote";
    # `ashby:atria` is Singapore/UK/China, not Atria the Finnish food group; `ashby:polar`
    # carries no Finnish row at all. `oraclecloud:ecyq` was correctly reported as already
    # carried — Nixu was acquired by DNV, whose tenant was added earlier the same day.
    # GR/HU/BG/HR/SI/LU/MT/CY pass, 2026-08-07.
    "nanobit",      #   1 — Nanobit, Zagreb (HR). One growth-marketing role, and Croatia held
    #                     76 postings, so a single genuine Zagreb row is worth one request.
    # `otp` (6), probed for OTP Bank of Budapest, is a **Spanish occupational-health company**:
    # every row is a "Técnico/a PRL" (prevención de riesgos laborales) in Amposta, Alicante,
    # Almería or Sant Cugat. A three-letter slug is an initialism before it is a company.
    # PT/LT/EE pass (batch B), 2026-08-07. Teamtailor again carried the pass — 10 of the 18
    # boards wired. Portugal, Lithuania and Estonia had never had a country-specific list.
    "xpandit",      #  11 — Xpand IT: Lisboa + Braga (PT). Data engineering and analytics.
    "imaginarycloud",
    #                  10 — Imaginary Cloud: Lisboa (PT) 4 of 4 sampled. .NET/AI.
    "celfocus",     #   6 — Celfocus: Lisboa (PT). Telecom software; AI and PL/SQL.
    "nutrium",      #   6 — Nutrium: Braga (PT) + US/BR. Nutrition software. A partial keep —
    #                      two of six rows are "Nutricionista"/dietitian, i.e. clinical, but the
    #                      rest are product and go-to-market, so it is nowhere near the
    #                      `ashby:serenis` line.
    "adform",       #  12 — Adform: Oslo, Warsaw, Mumbai. Danish-HQ adtech with Baltic
    #                      engineering; kept for the Nordic and Polish rows.
    "adcash",       #   7 — Adcash: Tallinn (EE) 4 of 4 sampled.
    "milrem",       #   4 — Milrem Robotics: Tallinn (EE) 3 + Tartu. Defence robotics.
    "zenitech",     #   6 — Zenitech: Vilnius (LT) + Budapest (HU), evenly split. DevOps.
    "northway",     #   4 — Northway Biotech: Vilnius (LT) 4 of 4.
    "nrd",          #   3 — NRD Companies: Vilnius (LT) + Belmopan. Govtech.
    # CY/LU/MT/IS/LI pass (batch C), 2026-08-07.
    "atnorth",      #  24 — atNorth (Reykjavík-HQ data centres) and the board is Nordic rather
    #                     than Icelandic: DK 9, FI 6, NO 4, SE 4, IS 1. Taken for the spread.
    "mangopay",     #   7 — Mangopay (Luxembourg-HQ): Warsaw (PL) 4 + Berlin. Payments.
    "bsm",          #  44 — Bernhard Schulte Shipmanagement: US 10, IN 8, DE 8, CY 5, GR 4.
    #                     Shore-side corporate roles (fleet personnel, cost control, supply
    #                     chain), NOT seafarer crewing — which is why this one is taken and the
    #                     Maltese ship-management segment was left out of the list entirely.
    #                     Its two sibling tenants `bernhardschulte` (4) and `bsm-bsm-cruise` (5)
    #                     are the same company again and are not taken: one company, one board.
    # `origo`, probed for Origo of Reykjavík, is a Swedish market-research firm hiring
    # "Telefonintervjuare" and "Enkät intervjuare" in Linköping and Göteborg.
    # LV pass, 2026-08-07. Latvia held **68 active postings**; cv.lv is Alma Career, and the
    # NVA register is unresolved on permission (see the note in CLAUDE.md), so curated employers
    # is the whole route. The Latvian telcos turned out to be where the professional hiring is.
    "tet",          #  33 — Tet (ex-Lattelecom), Rīga: product owner, AI services owner, Red
    #                     Team Lead, data-network expert, storage/server admin. **The best
    #                     Latvian board found**, and entirely Riga.
    "bitelatvija",  #  13 — Bite Latvija: Rīga, Jelgava, Rēzekne. A deliberately marginal keep —
    #                     roughly half the board is shop-floor retail ("Pārdošanas speciālists
    #                     salonā"), the other half office roles (B2B sales consultant, HR
    #                     business partner, AI solutions specialist). Kept because the split is
    #                     ~50/50 rather than the >90% that got `rimilatvia` and `fenixoutdoor`
    #                     rejected, and because 13 rows is material in a 68-row country.
    "lmt",          #  10 — Latvijas Mobilais Telefons, Rīga: monitoring-solutions engineer,
    #                     senior systems analyst, IT transformation architect, senior .NET
    #                     developer, AI product technical lead. Entirely professional.
    # **`rimilatvia` (100) was the largest Latvian board found and is rejected on inventory** —
    # dishwashers, cooks, checkout supervisors, bakery sales, central-kitchen production staff.
    # It is genuinely Rimi and it would have been the single biggest LV number in the corpus;
    # taking it would put a supermarket's shop floor into the widened retrieval path. Same call
    # as `workday:jlp` and `oraclecloud` Posten Bring.
    # Rejected on identity: `ashby:maxima` is San Mateo, California, not Maxima Latvija;
    # **`ashby:bite` is a London founding-engineer board while `teamtailor:bitelatvija` is the
    # Latvian operator** — one more slug answering live on two ATSes with only one right, after
    # `sunday` and `lunar`; `recruitee:grid` is an esports betting operation (MOBA Trading
    # Analyst, Live Trader FPS), not Grid Dynamics; `recruitee:accenture` is two Amsterdam rows
    # of which one is "Senior Marketer (Sample)", i.e. Recruitee seed content.
]


def _text(html: Optional[str]) -> Optional[str]:
    if not html:
        return None
    out = _WS.sub(" ", _TAG.sub(" ", html)).strip()
    return out or None


def _first_address(jp: dict) -> dict:
    for loc in jp.get("jobLocation") or []:
        addr = (loc or {}).get("address") or {}
        if addr:
            return addr
    return {}


def _country(addr: dict) -> Optional[str]:
    code = (addr.get("addressCountry") or "").strip().upper()
    return code if len(code) == 2 else None


def _location(addr: dict) -> Optional[str]:
    parts = [addr.get("addressLocality"), addr.get("addressCountry")]
    return ", ".join(p for p in parts if p) or None


def _salary(jp: dict) -> tuple[Optional[str], Optional[str]]:
    """(salary_raw, currency) from a schema.org MonetaryAmount, best-effort."""
    sal = jp.get("baseSalary") or {}
    val = sal.get("value") or {}
    currency = sal.get("currency") or None
    lo, hi, unit = val.get("minValue"), val.get("maxValue"), val.get("unitText")
    if lo is None and hi is None:
        return None, currency
    amount = f"{lo}-{hi}" if (lo is not None and hi is not None and lo != hi) else str(lo if lo is not None else hi)
    raw = amount + (f" {currency}" if currency else "") + (f"/{unit}" if unit else "")
    return raw.strip() or None, currency


def _posted(jp: dict, item: dict) -> Optional[date]:
    raw = jp.get("datePosted") or item.get("date_published")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        return None


#: Talent-pool / spontaneous-application entries carry no real role and would only add noise
#: to a shortlist. Dropped by title, deliberately narrow — a genuine posting never matches.
_TALENT_POOL = re.compile(
    r"submit your cv|didn'?t find|couldn'?t find|open application|spontaneous|"
    r"talent (pool|community|network)|connect with us|future opportunit|"
    r"otevřená pozice|iniciativní", re.I)

#: Teamtailor's own demo job ads, which sit on abandoned trial tenants — and this guard has to
#: exist separately from `_DEMO_TITLE` in the recruitee adapter, for two reasons found the hard
#: way on 2026-08-07.
#:
#: **The titles carry no marker.** Recruitee stamps its seed content "(Sample)"/"(Muster)"/
#: "(voorbeeld)"/"(example)"; Teamtailor's are called "Backend developer", "UX Designer",
#: "Key Account Manager". `akerbp`, `jotun` and `salmar` each returned 11 postings of which
#: **10 titles were identical across all three** — and a title-based rule is not available,
#: because `volue` genuinely advertises "Software Engineer". So the tell must be the body.
#:
#: **And identity-from-feed does not save us here.** The module docstring's guarantee — take
#: the company from `hiringOrganization.name`, never the slug — holds for a slug *collision*
#: (`norr` self-identifies as "Svensk Markservice AB", `remarkable` as "REMARKABLE RETAIL", and
#: both were correctly rejected on that basis). It fails for an abandoned trial registered under
#: the real company's own name: `akerbp.teamtailor.com/jobs.json` is titled **"Aker BP"** and its
#: `content_html` is Teamtailor's sales pitch, dated 2023. A board can be honestly named and
#: still hold nothing but demo content.
#:
#: Measured when written: 5–6 of 11 items on each of the three tenants match, and **0 of 12
#: known-good tenants** match a single item (comodule, mintos, iqm, templafy, podimo, thales,
#: schibsted, vipps, volue, itera, nordicsemiconductor, kongsbergdigital). Deliberately anchored
#: on Teamtailor marketing its own product inside a job ad, which a real employer never does.
_DEMO_CONTENT = re.compile(
    r"Teamtailor is an Employer Branding|"
    r"careers\.eloomi\.com|jobb\.sosalarm\.se|careerseurope\.danielwellington\.com", re.I)


class TeamtailorSource(BaseSource):
    """Teamtailor public career-site JSON feeds across a curated list of tenants."""

    def __init__(self, tenants: Optional[list[str]] = None):
        self._tenants = tenants or TENANTS

    @property
    def source_name(self) -> str:
        return "teamtailor"

    def fetch(self) -> list[dict]:
        out: list[dict] = []
        for tenant in self._tenants:
            url = f"https://{tenant}.teamtailor.com/jobs.json"
            try:
                if not politeness.robots_allows(url):
                    logger.warning("Teamtailor %s: blocked by robots.txt", tenant)
                    continue
                politeness.throttle(url)
                resp = requests.get(url, headers=HEADERS, timeout=_TIMEOUT)
                if resp.status_code != 200:
                    # Loud on purpose: the list is short and hand-curated, so a dark board is
                    # a real loss rather than noise — the silent-zero failure this repo keeps
                    # rediscovering.
                    logger.warning("Teamtailor %s: HTTP %s", tenant, resp.status_code)
                    continue
                items = resp.json().get("items", [])
                for it in items:
                    it["_tenant"] = tenant
                out.extend(items)
                logger.info("Teamtailor %s: %d", tenant, len(items))
            except (requests.RequestException, ValueError) as exc:
                logger.warning("Teamtailor %s failed: %s", tenant, exc)
        return out

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        out: list[JobPosting] = []
        for it in raw_items:
            url = it.get("url")
            title = (it.get("title") or "").strip()
            if not url or not title or _TALENT_POOL.search(title):
                continue
            jp = it.get("_jobposting") or {}
            body = jp.get("description") or it.get("content_html") or ""
            if _DEMO_CONTENT.search(body):
                continue
            addr = _first_address(jp)
            org = (jp.get("hiringOrganization") or {}).get("name")
            salary_raw, currency = _salary(jp)
            out.append(JobPosting(
                posting_id=make_posting_id(url),
                source=self.source_name,
                title=title,
                company=(org or it.get("_tenant") or "").strip() or None,
                url=url,
                description=_text(jp.get("description") or it.get("content_html")),
                location=_location(addr),
                country_code=_country(addr),
                remote_signal=None,
                salary_raw=salary_raw,
                currency=currency,
                posted_at=_posted(jp, it),
            ))
        return out
