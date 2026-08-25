# One image, two processes.
#
# The web service and the worker run the same code with different commands. That
# is deliberate: they share models, migrations, config validation and the job
# handlers, so building them separately would mean two images that can drift
# apart in exactly the ways that are hardest to debug — a worker running last
# week's understanding pipeline against this week's schema.

FROM python:3.13-slim AS base

# ffmpeg is a soft dependency: the vision stage extracts frames with it and
# records `skipped: ffmpeg unavailable` when it is missing, so the image works
# without it — but then video understanding silently degrades, which is worse
# than a bigger image.
#
# libpq5 is what psycopg2-binary needs at runtime. curl is for the container
# healthcheck below.
RUN apt-get update && apt-get install --no-install-recommends -y \
        ffmpeg \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies before source, so a code change does not reinstall the world.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY api ./api
COPY tests ./tests

# Never run as root. If the process is compromised, this is the difference
# between "read the application directory" and "own the container".
RUN useradd --create-home --uid 10001 sava \
    && chown -R sava:sava /app
USER sava

EXPOSE 8000

# Deep health, not liveness: this asks whether the database, storage and queue
# are actually working, so an orchestrator replaces a container that is up but
# useless. `/livez` exists for platforms that want a shallow probe instead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8000}/health || exit 1

# Overridden by the worker service. `sh -c` so ${PORT} expands — hosting
# platforms assign it at runtime.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips='*'"]
