"""Health and readiness.

`GET /` previously returned a hardcoded string — `{"message": "Sava API is
running 🚀"}` — which is not a health check. It returns 200 while the database is
unreachable, the worker is dead, and every save is failing silently. An uptime
monitor pointed at it would have stayed green through a total outage.

This module answers the question that actually matters operationally:

> If saves stopped processing at 2 AM, would anything know?

The signal for that is not "is the process alive" but **the age of the oldest
queued job**. A dead worker does not make the API unhealthy — requests still
succeed, rows are still written — it just means nothing ever drains. Queue age is
the one number that goes wrong in every version of that failure: worker crashed,
worker OOM-killed, worker deployed with a bad image, database lock held, platform
circuit stuck open.

Two endpoints, because they answer different questions and want different
callers:

  * `/health` — deep. For a human, an uptime monitor, and an alert rule.
  * `/livez`  — shallow. For a platform's own restart probe, which must not
    restart the API because the *database* is briefly unavailable.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Past this, the queue is not merely busy — something is wrong. Chosen against
# observed behaviour: a cold TikTok resolve plus understanding runs in tens of
# seconds, so fifteen minutes of no progress is not a backlog, it is a stall.
QUEUE_STALL_SECONDS = int(os.getenv("SAVA_QUEUE_STALL_SECONDS", str(15 * 60)))

_STARTED_AT = time.time()


def _age_seconds(value: Any) -> float | None:
    """Seconds since `value`, tolerating the several shapes SQL returns."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - value).total_seconds())


def _check_database(db) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        db.execute(text("SELECT 1")).scalar()
        return {"ok": True, "latency_ms": round((time.monotonic() - started) * 1000, 1)}
    except Exception as e:
        # The class name, never the message: a connection error string carries
        # the host, the username and sometimes the password.
        return {"ok": False, "error": type(e).__name__}


def _check_storage() -> Dict[str, Any]:
    """Is object storage configured and constructible?

    Deliberately does not perform a round trip. A health check that writes an
    object on every poll is a health check that costs money and creates garbage;
    this reports configuration, and a real failure surfaces at startup because
    production storage now fails closed.
    """
    try:
        from .storage import get_storage
        provider = get_storage()
        return {"ok": True, "backend": provider.name}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def _check_queue(db) -> Dict[str, Any]:
    try:
        by_state = {row["state"]: int(row["n"]) for row in db.execute(text(
            "SELECT state, COUNT(*) n FROM jobs GROUP BY state")).mappings()}
        oldest_queued = db.execute(text(
            "SELECT MIN(created_at) FROM jobs WHERE state = 'queued'")).scalar()
        age = _age_seconds(oldest_queued)

        queued = by_state.get("queued", 0)
        running = by_state.get("running", 0)
        stalled = age is not None and age > QUEUE_STALL_SECONDS

        return {
            "ok": not stalled,
            "queued": queued,
            "running": running,
            "failed": by_state.get("failed", 0),
            "oldest_queued_age_seconds": None if age is None else round(age),
            "stall_threshold_seconds": QUEUE_STALL_SECONDS,
            "stalled": stalled,
        }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def _optional_providers() -> Dict[str, Any]:
    """Which optional ingestion providers loaded, and why the others did not."""
    try:
        from .ingestors.registry import provider_status
        return provider_status()
    except Exception as e:  # never let a diagnostic break the health endpoint
        return {"error": type(e).__name__, "detail": str(e)}


def health_report(db) -> Dict[str, Any]:
    """The full picture. `ok` is false if anything a user depends on is broken."""
    from .config import ENVIRONMENT

    checks = {
        "database": _check_database(db),
        "storage": _check_storage(),
        "queue": _check_queue(db),
    }
    return {
        "ok": all(c.get("ok") for c in checks.values()),
        "environment": ENVIRONMENT,
        "uptime_seconds": round(time.time() - _STARTED_AT),
        "checks": checks,
        # Reported, deliberately not part of `ok`. These providers depend on
        # packages the production image does not install on purpose, so their
        # absence is a configuration fact rather than an outage — and the whole
        # point of isolating them is that a missing one does not make Sava
        # unhealthy. It is here so "why is TikTok not extracting" is answerable
        # without reading container logs.
        "optional_providers": _optional_providers(),
    }
