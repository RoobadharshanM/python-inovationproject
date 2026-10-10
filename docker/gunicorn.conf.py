"""Gunicorn configuration for the containerised service.

Sizing rule of thumb for gunicorn: ``workers = 2 * cores + 1``.  The web tier is
mostly I/O bound (database aggregates plus JSON rendering), so the default stays
conservative and WEB_CONCURRENCY can be raised from the environment.
"""

import multiprocessing
import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

workers = int(os.getenv("WEB_CONCURRENCY", max(2, multiprocessing.cpu_count() * 2 + 1)))

# Analytics and dashboard responses are large JSON payloads; allow enough time
# for the aggregate queries but still fail fast rather than hanging a worker.
timeout = int(os.getenv("WEB_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("WEB_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("WEB_KEEPALIVE", "5"))

# Recycle workers to bound memory growth from the ML/sklearn imports.
max_requests = int(os.getenv("WEB_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("WEB_MAX_REQUESTS_JITTER", "100"))

accesslog = os.getenv("WEB_ACCESS_LOG", "-")
errorlog = os.getenv("WEB_ERROR_LOG", "-")
loglevel = os.getenv("WEB_LOG_LEVEL", "info")

# Trust the reverse proxy / compose network for scheme and client IP.
forwarded_allow_ips = os.getenv("WEB_FORWARDED_ALLOW_IPS", "*")


def when_ready(server):  # pragma: no cover - runtime hook
    server.log.info("Stump AI ready on %s with %s workers", bind, workers)