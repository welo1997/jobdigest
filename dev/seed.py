"""Fill the local database with synthetic postings, a confirmed subscriber and matches.

    . dev\\env.ps1
    python dev\\seed.py

Why this is part of a working dev environment rather than an extra: an empty `postings`
table makes /matches, /hidden, the digest and every filter untestable, and the three things
most likely to be wrong in a change here — the location gate, `work_mode`, `education_min` —
are only visible against rows that vary.

**This file lives outside `service/` on purpose.** The Dockerfile does `COPY service/`, and a
script whose job is to create a *confirmed* subscriber with a live `manage_token` must not be
able to ship in the production image.

**Never copy the production database down instead.** It holds real addresses and
`manage_token`s, each of which is full control of a subscription (security rules 1, 2, 5).
Synthetic rows cost nothing and cannot leak.

The AI matcher is deliberately not in the loop: the live matcher is the claude.ai routine, and
`service/matcher.py --match` needs an API key this box does not have. The seeded scores stand
in for it. To exercise the file-exchange path locally, run `python -m service.matcher --export`
and `--import` against `exchange/`.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingestion.base import JobPosting                    # noqa: E402
from service import links, store                         # noqa: E402
from service.ingest import build_row                     # noqa: E402

SOURCE = "devseed"
#: `example.com` and not a `.test` address: /subscribe validates through `email-validator`,
#: which refuses reserved special-use TLDs, so a `.test` seed address would be one the signup
#: form itself could never accept — the seeded subscriber and a hand-made one would then
#: differ in a way that only shows up much later. example.com is reserved for documentation
#: and passes validation. Nothing is ever sent locally regardless (MAIL_BACKEND=file).
DEFAULT_EMAIL = "dev@example.com"

#: Columns added by migrations 012 (work_mode), 013 (language) and 014 (education). The
#: local volume is created once and `schema.sql` runs only on an empty data directory, so a
#: volume older than a migration keeps the old schema for ever — and the failure that
#: produces looks like an application bug, not a stale database. Checked before anything is
#: written, because the seed itself is the first thing that would hit it.
REQUIRED_COLUMNS = {
    "postings": ["work_mode", "education_min", "search_tsv"],
    "profiles": ["language", "education_levels", "work_modes", "countries", "cities"],
    "matches": ["status"],
    "digest_runs": ["shortlist_n"],
}

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


# --------------------------------------------------------------------- guards ---

def assert_local_database() -> str:
    """Refuse to run against anything that is not a local Postgres.

    The seed writes a confirmed subscriber, dismissed matches and 40 fake postings. Against
    production that is not a mess to clean up — it is fabricated rows in a real person's
    digest. `DATABASE_URL` is an environment variable, and the shell that has the production
    one exported is the same shell you would run this in.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL is not set — dot-source dev\\env.ps1 first.")
    host = (urlparse(dsn).hostname or "").lower()
    if host not in LOCAL_HOSTS:
        sys.exit(f"refusing to seed a non-local database (host {host!r}). "
                 "This script only ever writes to localhost.")
    return dsn


def assert_schema_current() -> None:
    with store.cursor() as cur:
        cur.execute("select table_name, column_name from information_schema.columns "
                    "where table_schema = 'public'")
        have = {(r["table_name"], r["column_name"]) for r in cur.fetchall()}
    missing = [f"{t}.{c}" for t, cols in REQUIRED_COLUMNS.items() for c in cols
               if (t, c) not in have]
    if missing:
        sys.exit("This database predates the current schema — missing: "
                 + ", ".join(missing)
                 + "\nPostgres applies schema.sql only to an EMPTY volume, so nothing will "
                   "migrate it in place.\nRun:  dev\\db.ps1 reset")


# ------------------------------------------------------------------- postings ---
# (title, company, location, description, salary_raw)
#
# The descriptions are short but not arbitrary: each one carries the evidence a classifier
# reads. `geo.work_mode` scans them for a named arrangement, `education.classify_requirement`
# for a binding qualification. Most say nothing about either — which is the realistic case
# (~97% of production rows have a null education_min, and 70% of the corpus has no
# description at all) and the one the SQL gates get wrong, since `x = any(...)` on a NULL
# column is NULL and WHERE discards NULL exactly as it discards false.

POSTINGS: list[tuple[str, str, str, str, str | None]] = [
    # --- data engineering ---
    ("Junior Data Engineer", "Rohlik Group", "Prague, Czechia",
     "Build and maintain ETL pipelines in Python and dbt on Snowflake. Fully remote within "
     "the EU.", "60 000 - 75 000 CZK / month"),
    ("Data Engineer", "Productboard", "Prague, Czechia",
     "You will own our data warehouse. Hybrid work model of 2 days in the office per week.",
     None),
    ("Analytics Engineer", "Kiwi.com", "Brno, Czechia",
     "dbt, BigQuery, Looker. On-site in our Brno office.", "70 000 - 90 000 CZK"),
    ("Senior Data Engineer", "Zalando SE", "Berlin, Germany",
     "Spark, Kafka, AWS. A Master's degree in Computer Science or a related field is "
     "required, and 5 years of professional data engineering experience.",
     "€75,000 - €95,000"),
    ("Data Platform Engineer", "Bolt", "Tallinn, Estonia",
     "Terraform, Airflow, Snowflake. This role is fully remote.", None),

    # --- data analysis ---
    ("Junior Data Analyst", "Alza.cz", "Prague, Czechia",
     "SQL and Power BI reporting for the ecommerce team. Středoškolské vzdělání s maturitou.",
     "45 000 CZK"),
    ("Data Analyst", "Notino", "Brno, Czechia", "", None),          # no description at all
    ("Business Intelligence Analyst", "Erste Group", "Vienna, Austria",
     "Tableau, SQL, stakeholder reporting. Bachelor's degree in economics or statistics "
     "required.", "€52,000"),
    ("Marketing Data Analyst", "Allegro", "Warsaw, Poland",
     "GA4, SQL, dashboards. We work three days a week in the office.", None),
    ("Data Analyst (Fraud)", "Wise", "Remote, Europe",
     "Fully remote role, anywhere in the EU. SQL and Python.", "€55,000 - €70,000"),

    # --- machine learning ---
    ("Machine Learning Engineer", "Seznam.cz", "Prague, Czechia",
     "Recommender systems at scale. PhD in machine learning or equivalent research "
     "experience preferred.", None),
    ("Data Scientist", "Mall Group", "Prague, Czechia",
     "Forecasting and pricing models in Python.", "80 000 - 100 000 CZK"),
    ("Junior ML Engineer", "Gymbeam", "Košice, Slovakia",
     "Vysokoškolské vzdelanie druhého stupňa je podmienkou. PyTorch, MLflow.", None),

    # --- software engineering ---
    ("Backend Developer (Python)", "Socialbakers", "Prague, Czechia",
     "FastAPI, Postgres, Docker.", "90 000 CZK"),
    ("Frontend Developer", "STRV", "Prague, Czechia",
     "React, TypeScript, Next.js. Home office possible occasionally.", None),
    ("Full-stack Engineer", "Qonto", "Paris, France",
     "This is a fully remote position, open across the EU.", "€60,000 - €80,000"),
    ("Junior Java Developer", "Ness Digital", "Ostrava, Czechia",
     "Spring Boot, Oracle. Vyučen v oboru IT nebo praxe.", None),
    ("Senior Software Engineer", "GitLab", "Remote, Worldwide",
     "All-remote company. Ruby, Go. Requirements: at least 8 years of experience building "
     "production systems.", "$140,000 - $180,000"),
    ("QA Engineer", "Y Soft", "Brno, Czechia (hybrid)",
     "Test automation in Playwright.", None),

    # --- devops / platform ---
    ("DevOps Engineer", "CN Group", "Prague, Czechia",
     "Kubernetes, Terraform, GitLab CI.", "100 000 - 130 000 CZK"),
    ("Site Reliability Engineer", "Adform", "Copenhagen, Denmark",
     "Observability, incident response. Fully remote within Europe.", None),
    ("Platform Engineer", "Delivery Hero", "Berlin, Germany",
     "AWS, EKS, ArgoCD. Hybrid working model.", "€80,000"),

    # --- product ---
    ("Product Manager", "Twisto", "Prague, Czechia",
     "Own the lending roadmap end to end.", None),
    ("Junior Product Owner", "Pipedrive", "Prague, Czechia",
     "Work with engineering and design on the CRM core. Bachelor's degree or equivalent "
     "practical experience.", "70 000 CZK"),
    ("Technical Program Manager", "SAP", "Walldorf, Germany",
     "Cross-team delivery. We use a hybrid work model of 3 days in the office per week.",
     None),

    # --- design ---
    ("UX Designer", "Livesport", "Prague, Czechia",
     "Figma, prototyping, research for the flagship app.", "75 000 CZK"),
    ("Senior Product Designer", "Miro", "Amsterdam, Netherlands",
     "Fully remote, EU time zones.", "€70,000 - €85,000"),
    ("Grafik / Graphic Designer", "Footshop", "Prague, Czechia",
     "Vizuály pro kampaně, Adobe CC. Práce z kanceláře v Praze.", None),

    # --- social media ---
    ("Social Media Specialist", "Dr. Max", "Prague, Czechia",
     "Instagram, TikTok, paid social for the pharmacy chain.", "45 000 - 55 000 CZK"),
    # Stays `null`, on purpose: a bare "hybrid" in body copy is never enough (it appears in
    # "hybrid cloud" and German Hybrid-DRG). Only a *named* schedule or policy classifies, or
    # the word in the location field. Keeping a row that misses is what makes the rule visible.
    ("Community Manager", "Bohemia Interactive", "Prague, Czechia",
     "Discord, Reddit, player communications. Hybrid.", None),

    # --- other tech function ---
    ("Growth Marketing Manager", "Shoptet", "Prague, Czechia",
     "Performance campaigns and lifecycle email.", None),
    ("Recruiter (Tech)", "Avast", "Brno, Czechia",
     "Full-cycle hiring for engineering. Part-time possible, 0.6 FTE.", "50 000 CZK"),
    ("Financial Controller", "Rohlik Group", "Prague, Czechia",
     "Monthly close, reporting. University degree in finance required.", None),
    ("Customer Success Manager", "Productboard", "Remote, EU",
     "Fully remote. Own a portfolio of enterprise accounts.", "€45,000"),

    # --- uncategorised / no category the taxonomy models ---
    ("Cybersecurity Specialist", "O2 Czech Republic", "Prague, Czechia",
     "SOC monitoring, incident response, SIEM tuning.", None),
    ("IT Support Technician", "Škoda Auto", "Mladá Boleslav, Czechia (on-site)",
     "L1/L2 support, Windows, Active Directory. Práce na pracovišti.", "40 000 CZK"),
    ("Sales Representative", "Kentico", "Brno, Czechia",
     "Outbound to mid-market accounts in DACH.", None),

    # --- non-EU, to exercise the country gate ---
    ("Data Engineer", "Workday Inc.", "Bengaluru, India",
     "Spark and Scala on the analytics platform.", None),
    ("Senior Data Analyst", "Stripe", "Austin, TX",
     "SQL, dbt, growth analytics.", "$150,000"),
    ("Backend Engineer", "Shopify", "Toronto, Canada",
     "Ruby on Rails at scale. Digital by design — work remotely.", None),
]


def seed_postings() -> list[dict]:
    today = date.today()
    rows = []
    for n, (title, company, location, description, salary) in enumerate(POSTINGS, start=1):
        pid = f"{SOURCE}-{n:03d}"
        posting = JobPosting(
            posting_id=pid,
            source=SOURCE,
            title=title,
            company=company,
            url=f"https://example.com/jobs/{pid}",
            description=description or None,
            location=location,
            # No source-level country constant, deliberately — the resolver reads the
            # posting's own text and unknown stays unknown, which is what the AI matcher is
            # for. A constant here would be the Arbeitnow bug reproduced in the fixtures.
            country_code=None,
            remote_signal=None,
            salary_raw=salary,
            currency=None,
            # Spread over three weeks so ordering by first_seen_at/posted_at has something
            # to order.
            posted_at=today - timedelta(days=n % 21),
        )
        rows.append(build_row(posting))
    store.upsert_postings(rows)
    return rows


# ------------------------------------------------------------------ subscriber ---

PROFILE_DATA = {
    "label": "Dev digest",
    "stack": ["python", "sql", "dbt"],
    "seniorities": ["junior", "mid"],
    "countries": ["CZ", "DE"],
    "cities": ["prague", "brno", "berlin"],
    "remote_scope": "eu",
    # Empty on purpose in both cases: "no preference", which `clean_work_modes` and
    # `clean_levels` widen to all of them. Tick boxes on /preferences to narrow it and watch
    # the shortlist change — that is the interesting local experiment.
    "work_modes": [],
    "education_levels": [],
    "education_field": "computer science",
    "role_categories": ["data_engineering", "data_analysis"],
    "work_types": ["permanent", "freelance/contract"],
    "part_time_only": False,
    "eligible_only": True,
    # Pre-filled so /preferences opens with the industry chips already selected — a soft signal
    # for the matcher, editable on the page.
    "sectors": ["ecommerce", "finance"],
    "min_score": 6,
    "frequency": "daily",
    "language": "en",
}


def seed_subscriber(email: str, language: str) -> dict:
    """A confirmed subscriber, or the existing one. Idempotent by address.

    `confirmed=True` skips the double opt-in so /matches is reachable on the first run. The
    signup flow itself is still worth walking — that is what service/outbox/ is for.
    """
    existing = store.get_live_profile_by_email(email)
    if existing:
        return existing
    profile = store.create_email_subscription(
        email, {**PROFILE_DATA, "language": language}, confirmed=True)
    if not profile:
        sys.exit(f"could not create a subscription for {email} — a live row already exists "
                 "with a status this script does not touch (paused?).")
    return profile


# --------------------------------------------------------------------- matches ---
# Scores are chosen against the two thresholds that decide where a match is visible:
#   >= EMAIL_MIN_SCORE (6)  emailed AND on /matches
#   >= MATCH_FLOOR    (4)  stored, /matches only — never emailed
# and two are hidden, so /hidden is not an empty page the first time it is opened.

MATCH_PLAN: list[tuple[int, int, str, str]] = [
    (1,  9, "new",       "Junior, Python + dbt on Snowflake, Prague, remote — a direct hit."),
    (6,  8, "new",       "Junior analyst in Prague; SQL and Power BI match the stated stack."),
    (2,  7, "new",       "Warehouse ownership with dbt; hybrid in Prague, two days on site."),
    (3,  7, "new",       "Analytics engineering on dbt, Brno — on-site, which may not suit."),
    (5,  6, "new",       "Fully remote EU data platform role; Airflow and Snowflake line up."),
    (10, 6, "new",       "Remote EU analyst role, SQL + Python; fraud domain is new ground."),
    (4,  5, "new",       "Senior scope and a Master's requirement — above the stated seniority."),
    (12, 5, "new",       "Data science rather than engineering; modelling-heavy."),
    (9,  4, "new",       "Marketing analytics in Warsaw — outside the selected countries."),
    (14, 4, "new",       "Backend Python; adjacent to the data stack but not a data role."),
    (11, 7, "dismissed", "Recommender systems at Seznam — strong match, already applied."),
    (20, 6, "dismissed", "DevOps in Prague — Kubernetes and Terraform, not interested."),
]


def seed_matches(profile_id: str) -> tuple[int, int]:
    hidden = 0
    for n, score, status, summary in MATCH_PLAN:
        posting_id = f"{SOURCE}-{n:03d}"
        store.upsert_match(profile_id, posting_id, score, summary)
        if status != "new":
            # Set separately, exactly as the product does: `upsert_match` never writes
            # `status`, which is what stops the nightly re-score resurrecting a hidden job.
            store.set_match_status(profile_id, posting_id, status)
            hidden += 1
    return len(MATCH_PLAN) - hidden, hidden


# ----------------------------------------------------------------------- report ---

def report(rows: list[dict]) -> None:
    def dist(key: str) -> str:
        c = Counter(r[key] if r[key] is not None else "null" for r in rows)
        return ", ".join(f"{k}={v}" for k, v in c.most_common())

    print(f"\npostings          {len(rows)}")
    print(f"  role_category   {dist('role_category')}")
    print(f"  work_mode       {dist('work_mode')}")
    print(f"  education_min   {dist('education_min')}")
    print(f"  experience_min  {dist('experience_min')}")
    print(f"  country_code    {dist('country_code')}")
    print(f"  city            {dist('city')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--email", default=DEFAULT_EMAIL,
                    help=f"subscriber address to create (default {DEFAULT_EMAIL})")
    ap.add_argument("--language", default="en",
                    help="locale the subscriber signed up under — decides the language of "
                         "every email to them and the locale of their emailed links")
    args = ap.parse_args()

    dsn = assert_local_database()
    print(f"database          {dsn}")
    assert_schema_current()

    rows = seed_postings()
    report(rows)

    profile = seed_subscriber(args.email, args.language)
    visible, hidden = seed_matches(profile["id"])
    lang = profile.get("language") or args.language

    print(f"\nsubscriber        {profile['email']}  ({profile['status']}, lang={lang})")
    print(f"  matches         {visible} visible, {hidden} hidden")
    print("\nNo email is sent locally — MAIL_BACKEND=file writes to service\\outbox\\.")
    print("Open these directly (the token in the URL is the login):\n")
    print(f"  matches         {links.matches_link(profile['manage_token'], lang)}")
    print(f"  preferences     {links.preferences_link(profile['manage_token'], lang)}")
    print(f"  hidden          {links.site_page('hidden', lang)}"
          "   (sign in via either link above first)")
    print(f"  unsubscribe     {links.unsubscribe_link(profile['manage_token'])}")


if __name__ == "__main__":
    main()
