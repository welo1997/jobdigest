# JobDigest backend — serves the public API (service.webapp) and runs the daily pipeline
# (service.pipeline). One lean image, no Snowflake/dbt. Build context = repo root.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app

# psycopg2-binary ships wheels, so no libpq build deps needed. curl for the healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install deps first for layer caching.
COPY requirements.txt ./
COPY service/requirements.txt service/requirements.txt
COPY service/requirements-worker.txt service/requirements-worker.txt
RUN pip install -r service/requirements-worker.txt

# App code (only what the API + pipeline need).
COPY service/ ./service/
COPY ingestion/ ./ingestion/
COPY search_jobs.py ./search_jobs.py

# Run as non-root.
RUN useradd -m app && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "service.webapp:app", "--host", "0.0.0.0", "--port", "8000"]
