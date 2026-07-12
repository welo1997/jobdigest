"""Send Telegram notifications for high-scoring personal matches."""

from __future__ import annotations

import logging
import os
import sys
import time
from urllib.parse import urlparse

import requests
import snowflake.connector
from dotenv import load_dotenv

from enrichment.curator import curate

load_dotenv()
logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Max postings to send per run. Curation trims the live shortlist down to this.
MAX_ALERTS = int(os.environ.get("PERSONAL_MAX_ALERTS", "6"))

LIVENESS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

# Lowercase substrings that signal a closed/filled posting even when the
# host returns HTTP 200. Czech and English variants seen on jobs.cz,
# startupjobs.cz, greenhouse, lever.
CLOSURE_MARKERS = (
    # startupjobs.cz serves HTTP 200 for expired postings with this banner:
    # "Nabídce vypršela platnost nebo již není aktuální."
    "vypršela platnost",
    "již není aktuální",
    "nabídka byla již obsazena",
    "nabídka již byla obsazena",
    "tato nabídka již není aktivní",
    "tato nabídka zde již není",
    "neexistuje nebo již byla obsazena",
    "this job is no longer available",
    "this position is no longer available",
    "this position has been filled",
    "this role is no longer",
    "this job is no longer open",
    "this opening is no longer",
    "the job you're looking for",       # greenhouse "…is no longer available"
    "we are no longer accepting applications",
    "no longer accepting applications",
    "job posting not found",
    "this job has expired",
    "this listing is no longer active",
    "position closed",
    "page not found",
    "stránka neexistuje",
)

# HTTP codes that mean the posting is definitively gone.
DEAD_STATUS = frozenset({404, 410})
# Codes that indicate bot-blocking / rate-limiting rather than a dead posting.
# We cannot verify these hosts, and the user prefers a maybe-stale link over a
# missed live one, so we send them (verdict "live") with a note.
BOT_BLOCKED_STATUS = frozenset({401, 403, 406, 451, 999})

# ATS hosts whose job path disappears (redirects to the board root) when a
# posting is filled. If the final URL no longer contains the job segment we
# treat it as dead.
ATS_JOB_PATH_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com")


def _redirected_off_job_page(requested_url: str, final_url: str) -> bool:
    """True if an ATS job link redirected away from its job detail path."""
    host = urlparse(requested_url).netloc.lower()
    if not any(h in host for h in ATS_JOB_PATH_HOSTS):
        return False
    req_path = urlparse(requested_url).path.rstrip("/")
    final_path = urlparse(final_url).path.rstrip("/")
    # Job detail pages carry a numeric/slug id segment (…/jobs/123, …/job/abc).
    # A redirect to the board root or careers landing page drops it.
    if req_path == final_path:
        return False
    return ("/jobs/" in req_path or "/job/" in req_path) and (
        "/jobs/" not in final_path and "/job/" not in final_path
    )


def check_url_liveness(url: str) -> tuple[str, str]:
    """Probe a job URL. Returns (verdict, note).

    verdict is one of:
      - "live"    : 2xx/3xx with no closure marker, OR a bot-blocked host we
                    cannot verify → send alert (better a stale link than a miss)
      - "dead"    : 404/410, closure marker, or ATS redirect-to-root → skip + mark notified
      - "unknown" : 429, 5xx, timeout, network error → skip and retry next run
    """
    if not url:
        return ("dead", "empty url")
    try:
        resp = requests.get(url, headers=LIVENESS_HEADERS, timeout=15, allow_redirects=True)
    except requests.RequestException as exc:
        return ("unknown", f"network error: {exc.__class__.__name__}")
    if resp.status_code == 429:
        time.sleep(5)
        try:
            resp = requests.get(url, headers=LIVENESS_HEADERS, timeout=15, allow_redirects=True)
        except requests.RequestException as exc:
            return ("unknown", f"network error after retry: {exc.__class__.__name__}")
        if resp.status_code == 429:
            return ("unknown", "429 rate limited")

    code = resp.status_code
    if code in DEAD_STATUS:
        return ("dead", f"HTTP {code}")
    if code in BOT_BLOCKED_STATUS:
        # Can't inspect the body; assume live so we don't drop real postings
        # behind Cloudflare/LinkedIn bot walls (notably Adzuna redirects).
        return ("live", f"HTTP {code} (bot-blocked, unverifiable — sending)")
    if code >= 500:
        return ("unknown", f"HTTP {code} (server error)")
    if code >= 400:
        return ("dead", f"HTTP {code}")

    if _redirected_off_job_page(url, resp.url):
        return ("dead", f"redirected to {urlparse(resp.url).path or '/'}")

    body_lower = resp.text.lower()
    for marker in CLOSURE_MARKERS:
        if marker in body_lower:
            return ("dead", f"closure marker: {marker[:30]}")
    return ("live", f"HTTP {code}")


def get_snowflake_connection() -> snowflake.connector.SnowflakeConnection:
    kwargs = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        role=os.environ.get("SNOWFLAKE_ROLE", "SYSADMIN"),
    )
    if key_path := os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
        kwargs["private_key_file"] = os.path.expanduser(key_path)
    else:
        kwargs["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    return snowflake.connector.connect(**kwargs)


def fetch_matches(conn: snowflake.connector.SnowflakeConnection) -> list[dict]:
    """Fetch unnotified personal matches."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            posting_id, source, title, company, url,
            country_code, posted_at, skills_csv,
            salary_raw, personal_score, personal_summary
        FROM raw_marts_personal.fct_personal_matches
        ORDER BY personal_score DESC, posted_at DESC
    """)
    rows = cur.fetchall()
    cur.close()
    return [
        {
            "posting_id": r[0],
            "source": r[1],
            "title": r[2],
            "company": r[3],
            "url": r[4],
            "country_code": r[5],
            "posted_at": r[6],
            "skills_csv": r[7],
            "salary_raw": r[8],
            "personal_score": r[9],
            "personal_summary": r[10],
        }
        for r in rows
    ]


def format_message(match: dict) -> str:
    """Format a single match as a Telegram message."""
    score = match["personal_score"]
    title = match["title"] or "Unknown"
    company = match["company"] or "Unknown"
    source = match["source"] or "?"
    country = match["country_code"] or "?"
    posted = match["posted_at"] or "?"
    skills = match["skills_csv"] or "none extracted"
    salary = match["salary_raw"] or "not listed"
    # Prefer the curator's "why today" note; fall back to the per-posting score summary.
    summary = match.get("curation_note") or match["personal_summary"] or ""
    url = match["url"] or ""

    emoji = "\U0001f7e2" if score >= 8 else "\U0001f7e1"  # green or yellow circle

    return (
        f"{emoji} Personal match  \u00b7  score: {score}/10\n"
        f"\n"
        f"\U0001f4bc {title} \u2014 {company}\n"
        f"\U0001f310 {source}  \u00b7  {country}  \u00b7  posted {posted}\n"
        f"\U0001f6e0 {skills}\n"
        f"\U0001f4b0 {salary}\n"
        f"\n"
        f"\U0001f916 {summary}\n"
        f"\n"
        f"\U0001f517 {url}"
    )


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    """Send a message via Telegram Bot API."""
    url = TELEGRAM_API.format(token=token)
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=10,
    )
    if resp.status_code == 200:
        return True
    logger.warning("Telegram send failed: %s %s", resp.status_code, resp.text[:200])
    return False


def mark_notified(
    conn: snowflake.connector.SnowflakeConnection, posting_ids: list[str]
) -> None:
    """Batch-update notified = TRUE for sent posting IDs."""
    if not posting_ids:
        return
    cur = conn.cursor()
    placeholders = ", ".join(["%s"] * len(posting_ids))
    cur.execute(
        f"""
        UPDATE raw.job_postings
        SET notified = TRUE, notified_at = CURRENT_TIMESTAMP()
        WHERE posting_id IN ({placeholders})
        """,
        posting_ids,
    )
    cur.close()
    logger.info("Marked %d postings as notified.", len(posting_ids))


def main() -> None:
    conn = get_snowflake_connection()
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.warning("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set. Printing to stdout instead.")

    matches = fetch_matches(conn)
    logger.info("Found %d candidate matches.", len(matches))

    if not matches:
        conn.close()
        return

    # 1. Liveness probe — keep only confirmed-live postings.
    live: list[dict] = []
    dead_ids: list[str] = []
    unknown_count = 0
    for match in matches:
        verdict, note = check_url_liveness(match["url"])
        if verdict == "dead":
            logger.info("Dead: %s @ %s (%s)", match["title"], match["company"], note)
            dead_ids.append(match["posting_id"])
        elif verdict == "unknown":
            logger.warning(
                "Liveness unknown: %s @ %s (%s) — will retry next run",
                match["title"], match["company"], note,
            )
            unknown_count += 1
        else:
            live.append(match)

    logger.info("Live=%d dead=%d unknown=%d", len(live), len(dead_ids), unknown_count)

    # 2. Curation — let Claude pick the few genuinely worth sending. On any failure
    # curate() returns None and we fall back to the top matches by score (already
    # ordered by fetch_matches).
    selected = curate(live, MAX_ALERTS) if live else []
    if selected is None:
        logger.info("Falling back to top %d live matches by score.", MAX_ALERTS)
        selected = live[:MAX_ALERTS]

    # 3. Send the selected picks.
    sent_ids: list[str] = []
    for match in selected:
        msg = format_message(match)
        if token and chat_id:
            if send_telegram(token, chat_id, msg):
                sent_ids.append(match["posting_id"])
                logger.info(
                    "Sent: %s @ %s (score=%s)",
                    match["title"], match["company"], match["personal_score"],
                )
            else:
                logger.warning("Failed to send: %s", match["posting_id"])
        else:
            print(msg)
            print("---")
            sent_ids.append(match["posting_id"])

    # Mark sent + confirmed-dead as notified so they leave the queue. Live-but-not-
    # selected and unknown-liveness rows stay unnotified: they get reconsidered next
    # run and otherwise age out via the mart's freshness filter.
    mark_notified(conn, sent_ids + dead_ids)
    conn.close()
    logger.info(
        "Notification complete. sent=%d dead=%d unknown=%d live_held=%d",
        len(sent_ids), len(dead_ids), unknown_count, len(live) - len(sent_ids),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    main()
