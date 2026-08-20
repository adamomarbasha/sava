"""Database-backed job queue.

Why not Celery/RQ/Dramatiq/Temporal: they all require infrastructure this
product does not otherwise need (a broker, a result backend, a scheduler), and
Sava's workflow is a short linear pipeline per item — not a long-running
orchestration with human-in-the-loop steps or multi-day timers. Temporal in
particular would be sophistication for its own sake here.

A transactional queue inside the database we already run gives us:
  * durability and crash-safety for free,
  * idempotency as a UNIQUE constraint rather than application bookkeeping,
  * exactly one obvious place to inspect stuck work (`SELECT * FROM jobs`),
  * zero new services to deploy, secure, or pay for.

The tradeoff is throughput: this design polls, and tops out in the low
thousands of jobs/minute on Postgres. Sava's ceiling is one job per save, so
that headroom is enormous. If it is ever exhausted, the handler registry stays
the same and only `claim_next` changes.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import text

from .config import INLINE_JOBS, IS_POSTGRES, JOB_LEASE_SECONDS, JOB_MAX_ATTEMPTS
from .db import SessionLocal
from .models import Job
from .platform_budget import PlatformUnavailable

logger = logging.getLogger(__name__)

HANDLERS: Dict[str, Callable[[Dict[str, Any], Any], Any]] = {}

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def handler(kind: str):
    """Register a job handler: @handler("content.process")."""
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    db,
    kind: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    idempotency_key: Optional[str] = None,
    platform: Optional[str] = None,
    priority: int = 100,
    delay_seconds: int = 0,
    max_attempts: int = JOB_MAX_ATTEMPTS,
    force: bool = False,
) -> Optional[Job]:
    """Enqueue idempotently.

    The idempotency key is what makes retries safe: re-saving the same content
    or replaying a webhook cannot schedule the same expensive work twice.
    Returns the existing job when one is already queued or running.
    """
    payload = payload or {}
    key = idempotency_key or f"{kind}:{json.dumps(payload, sort_keys=True, default=str)}"

    existing = db.query(Job).filter(Job.idempotency_key == key).first()
    if existing:
        if existing.state in ("queued", "running") and not force:
            return existing
        if existing.state in ("done",) and not force:
            return existing
        # failed / dead -> revive rather than create a duplicate row
        existing.state = "queued"
        existing.attempts = 0
        existing.run_after = _now() + timedelta(seconds=delay_seconds)
        existing.last_error = None
        existing.locked_by = None
        existing.locked_at = None
        db.commit()
        if INLINE_JOBS:
            run_job_now(existing.id)
        return existing

    job = Job(
        kind=kind, idempotency_key=key[:200], payload=json.dumps(payload, default=str),
        platform=(platform or None), state="queued", priority=priority,
        max_attempts=max_attempts,
        run_after=_now() + timedelta(seconds=delay_seconds),
    )
    db.add(job)
    try:
        db.commit()
        db.refresh(job)
    except Exception:
        # Lost a race on the unique key — return whoever won.
        db.rollback()
        return db.query(Job).filter(Job.idempotency_key == key).first()

    if INLINE_JOBS:
        run_job_now(job.id)
    return job


def _unavailable_platforms() -> Dict[str, float]:
    """Platforms we should not claim work for right now, with wait seconds."""
    from .platform_budget import get_manager
    blocked: Dict[str, float] = {}
    manager = get_manager()
    for name in ("youtube", "tiktok", "instagram", "other"):
        available, wait_s, _reason = manager.availability(name)
        if not available:
            blocked[name] = wait_s
    return blocked


def claim_next(db, *, worker_id: str = WORKER_ID,
               skip_platforms: Optional[Dict[str, float]] = None,
               kinds: Optional[List[str]] = None,
               platforms: Optional[List[str]] = None) -> Optional[Job]:
    """Atomically take the next runnable job.

    Skips jobs whose platform is throttled or circuit-open, so an Instagram
    outage cannot starve YouTube work of worker threads. Those jobs stay
    queued — nothing is lost or failed.

    `kinds` and `platforms` are the pool seam. One process with no filters is
    the right shape today. When transcription needs GPUs and acquisition needs
    residential egress, the same binary runs twice with different filters and the
    workloads separate — no new services, no queue migration, no code change.
    """
    now = _now()
    stale = now - timedelta(seconds=JOB_LEASE_SECONDS)
    blocked = _unavailable_platforms() if skip_platforms is None else skip_platforms
    blocked_names = [p for p in blocked] or None

    if IS_POSTGRES:  # pragma: no cover - needs live Postgres
        row = db.execute(text("""
            UPDATE jobs SET state='running', locked_by=:w, locked_at=:now, attempts=attempts+1
            WHERE id = (
                SELECT id FROM jobs
                WHERE ((state='queued' AND run_after <= :now)
                    OR (state='running' AND locked_at < :stale))
                  AND (:skip IS NULL OR platform IS NULL OR NOT (platform = ANY(:skip)))
                  AND (:kinds IS NULL OR kind = ANY(:kinds))
                  AND (:plats IS NULL OR platform = ANY(:plats))
                ORDER BY priority ASC, run_after ASC, id ASC
                FOR UPDATE SKIP LOCKED LIMIT 1
            ) RETURNING id
        """), {"w": worker_id, "now": now, "stale": stale,
               "skip": blocked_names, "kinds": kinds or None,
               "plats": platforms or None}).first()
        db.commit()
        return db.query(Job).get(row.id) if row else None

    # SQLite: a short IMMEDIATE transaction is sufficient — writes serialise.
    try:
        q = db.query(Job).filter(
            ((Job.state == "queued") & (Job.run_after <= now))
            | ((Job.state == "running") & (Job.locked_at < stale))
        )
        if blocked_names:
            q = q.filter(
                (Job.platform.is_(None)) | (~Job.platform.in_(blocked_names))
            )
        if kinds:
            q = q.filter(Job.kind.in_(kinds))
        if platforms:
            q = q.filter(Job.platform.in_(platforms))
        job = q.order_by(Job.priority.asc(), Job.run_after.asc(), Job.id.asc()).first()
        if not job:
            return None
        job.state = "running"
        job.locked_by = worker_id
        job.locked_at = now
        job.attempts = (job.attempts or 0) + 1
        db.commit()
        return job
    except Exception as e:
        db.rollback()
        logger.debug("claim contention: %s", e)
        return None


def finish(db, job: Job, *, ok: bool, error: Optional[str] = None) -> None:
    if ok:
        job.state = "done"
        job.last_error = None
    else:
        job.last_error = (error or "")[:2000]
        if job.attempts >= (job.max_attempts or JOB_MAX_ATTEMPTS):
            job.state = "dead"
            logger.error("job %s (%s) dead after %s attempts: %s",
                         job.id, job.kind, job.attempts, job.last_error)
        else:
            job.state = "queued"
            # Exponential backoff: 30s, 2m, 8m, 32m
            job.run_after = _now() + timedelta(seconds=30 * (4 ** (job.attempts - 1)))
    job.locked_by = None
    job.locked_at = None
    db.commit()


def execute(db, job: Job) -> bool:
    """Run one job's handler. Returns success."""
    fn = HANDLERS.get(job.kind)
    if fn is None:
        finish(db, job, ok=False, error=f"no handler registered for '{job.kind}'")
        return False
    try:
        payload = json.loads(job.payload or "{}")
    except Exception:
        payload = {}
    try:
        fn(payload, db)
        finish(db, job, ok=True)
        return True
    except PlatformUnavailable as e:
        # Not a failure of the job — the platform is throttled. Park it and
        # give the attempt back so throttling never exhausts the retry budget.
        job.attempts = max(0, (job.attempts or 1) - 1)
        job.state = "queued"
        job.run_after = _now() + timedelta(seconds=e.retry_after)
        job.last_error = f"parked: {e}"[:2000]
        job.locked_by = None
        job.locked_at = None
        db.commit()
        logger.info("job %s parked for %.0fs (%s)", job.id, e.retry_after, e.reason)
        return False
    except Exception as e:
        logger.exception("job %s (%s) failed", job.id, job.kind)
        finish(db, job, ok=False, error=f"{e}\n{traceback.format_exc()[:1200]}")
        return False


def run_job_now(job_id: int) -> bool:
    """Execute a specific job synchronously (INLINE_JOBS / tests)."""
    db = SessionLocal()
    try:
        job = db.query(Job).get(job_id)
        if not job or job.state == "done":
            return False
        job.state = "running"
        job.attempts = (job.attempts or 0) + 1
        job.locked_by = WORKER_ID
        job.locked_at = _now()
        db.commit()
        return execute(db, job)
    finally:
        db.close()


def drain(limit: int = 100) -> Dict[str, int]:
    """Run queued jobs until empty. Used by tests and by `worker.py --once`."""
    stats = {"ran": 0, "ok": 0, "failed": 0}
    db = SessionLocal()
    try:
        while stats["ran"] < limit:
            job = claim_next(db)
            if job is None:
                break
            stats["ran"] += 1
            if execute(db, job):
                stats["ok"] += 1
            else:
                stats["failed"] += 1
    finally:
        db.close()
    return stats


def queue_stats(db) -> Dict[str, Any]:
    rows = db.execute(text(
        "SELECT state, COUNT(*) n FROM jobs GROUP BY state"
    )).mappings().all()
    out = {r["state"]: int(r["n"]) for r in rows}
    out["total"] = sum(out.values())
    return out
