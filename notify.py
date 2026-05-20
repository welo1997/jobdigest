"""Send Telegram notifications for high-scoring personal matches."""

from __future__ import annotations

import logging
import os
import sys
import time

import requests
import snowflake.connector
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

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
    "nabídka byla již obsazena",
    "nabídka již byla obsazena",
    "nabídka již není aktuální",
    "tato nabídka již není aktivní",
    "tato nabídka zde již není",
    "neexistuje nebo již byla obsazena",
    "this job is no longer available",
    "this position is no longer available",
    "this position has been filled",
    "we are no longer accepting applications",
    "no longer accepting applications",
    "this job has expired",
    "this listing is no longer active",
    "page not found",
    "stránka neexistuje",
)


def check_url_liveness(url: str) -> tuple[str, str]:
    """Probe a job URL. Returns (verdict, note).

    verdict is one of:
      - "live"    : 2xx/3xx and no closure marker → send alert
      - "dead"    : 4xx or closure marker found → skip + mark notified
      - "unknown" : 429, timeout, network error → skip and try again next run
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
    if resp.status_code >= 400:
        return ("dead", f"HTTP {resp.status_code}")
    body_lower = resp.text.lower()
    for marker in CLOSURE_MARKERS:
        if marker in body_lower:
            return ("dead", f"closure marker: {marker[:30]}")
    return ("live", f"HTTP {resp.status_code}")


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
    summary = match["personal_summary"] or ""
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
    logger.info("Found %d matches to notify.", len(matches))

    if not matches:
        conn.close()
        return

    sent_ids: list[str] = []
    dead_ids: list[str] = []
    unknown_count = 0

    for match in matches:
        verdict, note = check_url_liveness(match["url"])
        if verdict == "dead":
            logger.info(
                "Skipping dead: %s @ %s (%s)",
                match["title"], match["company"], note,
            )
            dead_ids.append(match["posting_id"])
            continue
        if verdict == "unknown":
            logger.warning(
                "Skipping (liveness unknown): %s @ %s (%s) — will retry next run",
                match["title"], match["company"], note,
            )
            unknown_count += 1
            continue

        msg = format_message(match)
        if token and chat_id:
            success = send_telegram(token, chat_id, msg)
            if success:
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

    # Mark both successfully-sent and confirmed-dead so they exit the queue.
    # Unknown (rate-limited / network) stay unnotified to retry next run.
    mark_notified(conn, sent_ids + dead_ids)
    conn.close()
    logger.info(
        "Notification complete. sent=%d dead=%d unknown=%d",
        len(sent_ids), len(dead_ids), unknown_count,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    main()
