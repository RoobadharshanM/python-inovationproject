# syntax=docker/dockerfile:1
#
# Production image for the Stump AI IPL analytics service.
#
# Design notes
#   * Two stages keep compilers out of the runtime layer.
#   * The app runs as an unprivileged user.
#   * Trained ML models are NOT baked in.  They are rebuilt on first boot by the
#     entrypoint (or `run.py --train`) so the artifacts always match the
#     scikit-learn version inside the image.
#   * The 18 MB CSV dataset IS baked in so a fresh container can self-import.

# ---------------------------------------------------------------------------
# Stage 1 - build wheels for every dependency
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY requirements.txt .
RUN pip wheel --wheel-dir=/wheels --requirement requirements.txt

# ---------------------------------------------------------------------------
# Stage 2 - runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    PORT=8000

# libpq is required by psycopg; curl backs the container healthcheck.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels --requirement requirements.txt \
    && rm -rf /wheels

# Application source
COPY . /app

# Unprivileged runtime user; /app/data must stay writable because the
# entrypoint writes model artifacts and the import manifest there.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/models /app/data/processed /app/staticfiles /app/media \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=5 \
    CMD curl --fail --silent http://127.0.0.1:8000/health/ || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "--config", "/app/docker/gunicorn.conf.py", "config.wsgi:application"]