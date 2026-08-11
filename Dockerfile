# JobDigest backend — serves the public API (service.webapp) and runs the daily pipeline
# (service.pipeline). One lean image, no Snowflake and no dbt beyond the one seed file
# GreenhouseSource reads at runtime (see below). Build context = repo root.
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
# Not a dbt dependency despite the path: GreenhouseSource loads its curated board tokens
# from this seed at runtime, resolved relative to ingestion/sources/. Without it the
# largest source fetches nothing and says so only in a warning — which is how it
# contributed 0 rows to production while every check stayed green.
COPY dbt/seeds/target_companies.csv ./dbt/seeds/target_companies.csv

# Bake the embedding model into the image rather than fetching it on first use.
#
# fastembed downloads ~250MB of ONNX weights the first time a model is instantiated. Left to
# run time that is a source which fetches on demand, and this repo has been bitten by that
# shape more than once: it fails on a network hiccup, inside a container with no terminal, at
# 03:00, and the symptom is an absence rather than an error. Baking it also means the image is
# the whole artifact — `docker run` needs no egress to Hugging Face.
#
# FASTEMBED_CACHE_PATH is set explicitly because the default is a temp directory: it would be
# wiped, silently re-downloaded, and the build-time download would have bought nothing.
#
# The model name comes from service/embed.py rather than being repeated here. It is pinned
# alongside the fastembed version as part of the embedding identity (EMBEDDING_MODEL_ID), and a
# second copy in a Dockerfile is exactly how an image ends up baking one model while the code
# requests another — then fetching the difference at run time, at 03:00, quietly.
ENV FASTEMBED_CACHE_PATH=/app/.fastembed_cache
RUN python -c "from service import embed; embed._load(); print('baked', embed.EMBEDDING_MODEL_ID)"

# Run as non-root. The chown covers .fastembed_cache above, so the app user can read the
# weights without being able to replace them.
RUN useradd -m app && chown -R app /app
# `/state` is NAV's persisted mirror (compose mounts the `nav_state` named volume here).
# **It must exist in the IMAGE, owned by app, or the volume is unwritable.** Docker
# initialises a fresh named volume from the image's directory — including its ownership — but
# if the path does not exist in the image it creates the mountpoint root-owned 0755 instead,
# and this container runs non-root. The failure is silent by construction: `nav._save` catches
# OSError, logs a warning and carries on, so the adapter would cold-start ~1 200 detail calls
# every single night, never converge, and never alert. Verified on the box before this line
# existed: `os.access("/state", os.W_OK)` was False.
RUN mkdir -p /state && chown app /state
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "service.webapp:app", "--host", "0.0.0.0", "--port", "8000"]
