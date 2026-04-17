"""Run all ingestion sources and load into Snowflake."""

from __future__ import annotations

import logging
import sys

from ingestion.base import JobPosting
from ingestion.load import load_postings
from ingestion.sources.adzuna import AdzunaSource
from ingestion.sources.greenhouse import GreenhouseSource
from ingestion.sources.remotive import RemotiveSource
from ingestion.sources.startupjobs import StartupJobsSource
from ingestion.sources.weworkremotely import WeWorkRemotelySource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


ALL_SOURCES = [
    RemotiveSource,
    WeWorkRemotelySource,
    StartupJobsSource,
    AdzunaSource,
    GreenhouseSource,
]


def main() -> None:
    all_postings: list[JobPosting] = []

    for source_cls in ALL_SOURCES:
        try:
            source = source_cls()
            postings = source.run()
            all_postings.extend(postings)
            logger.info("%s: %d postings collected", source.source_name, len(postings))
        except Exception:
            logger.exception("Source %s failed, skipping.", source_cls.__name__)

    logger.info("Total collected: %d postings", len(all_postings))

    if all_postings:
        inserted = load_postings(all_postings)
        logger.info("Load complete: %d new rows inserted.", inserted)
    else:
        logger.warning("No postings collected from any source.")


if __name__ == "__main__":
    main()
