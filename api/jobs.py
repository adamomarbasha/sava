"""Database-backed job queue.

Why not Celery/RQ/Dramatiq/Temporal: they all require infrastructure this
product does not otherwise need (a broker, a result backend, a scheduler), and
Sava's workflow is a short linear pipeline per item — not a long-running
orchestration with human-in-the-loop steps or multi-day timers. Temporal in
particular would be sophistication for its own sake here.

A transactional queue inside the database we already run gives us:
  * durability and crash-safety for free,
  * idempotency as a UNIQUE constraint rather than application bookkeeping,
  * an atomic claim with no second system to agree with — see `claim_next`,
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

from .concurrency import ContentBusy, safe_rollback
from .config import INLINE_JOBS, IS_POSTGRES, JOB_LEASE_SECONDS, JOB_MAX_ATTEMPTS
from .db import SessionLocal
from .models import Job
from .platform_budget import PlatformUnavailable

logger = logging.getLogger(__name__)

HANDLERS: Dict[str, Callable[[Dict[str, Any], Any], Any]] = {}

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

#: How many candidate rows to try before giving up a claim round. Bounded so a
#: heavily contended queue cannot spin here; the caller polls again shortly.
_CLAIM_ATTEMPTS = 5


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
    user_id: Optional[int] = None,
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
        # Reviving is a fresh request by a possibly different user. Take the
        # better priority rather than keeping the original: if a Pro subscriber
        # asks for content a Free user queued last week, it is now Pro work.
        existing.priority = min(int(existing.priority or 100), int(priority))
        if user_id is not None:
            existing.user_id = user_id
        db.commit()
        if INLINE_JOBS:
            run_job_now(existing.id)
        return existing

    job = Job(
        kind=kind, idempotency_key=key[:200], payload=json.dumps(payload, default=str),
        platform=(platform or None), state="queued", priority=priority,
        max_attempts=max_attempts, user_id=user_id,
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


def _saturated_users(db) -> List[int]:
    """Users who already have as many jobs running as their plan allows.

    Concurrency is a plan limit (1 job for Free, 3 for Pro), and this is where
    it is enforced — at claim time rather than at enqueue time. Enqueue-time
    enforcement would have to reject or delay the save itself, whereas here the
    work simply waits its turn: nothing is lost, nothing is failed, and the
    queue drains in plan order.

    The count is bounded by total worker concurrency (a couple of processes
    times `WORKER_CONCURRENCY`), so this is a handful of rows and one plan
    lookup each, not a scan.
    """
    try:
        rows = db.execute(text(
            "SELECT user_id, COUNT(*) AS n FROM jobs "
            "WHERE state = 'running' AND user_id IS NOT NULL GROUP BY user_id"
        )).mappings().all()
    except Exception as e:
        # Never let fairness accounting stop work from being claimed.
        logger.debug("concurrency check skipped: %s", e)
        return []

    if not rows:
        return []

    from .entitlements import for_user

    saturated = []
    for row in rows:
        user_id = int(row["user_id"])
        running = int(row["n"] or 0)
        try:
            allowed = for_user(db, user_id).limits.concurrent_jobs
        except Exception:
            allowed = 1
        if running >= max(1, int(allowed)):
            saturated.append(user_id)
    return saturated


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
    busy = _saturated_users(db) or None

    if IS_POSTGRES:  # pragma: no cover - needs live Postgres
        # Every array parameter is CAST explicitly.
        #
        # Postgres infers a parameter's type from how it is used, and `NULL`
        # inside `= ANY($n)` gives it nothing to work from. With four such
        # parameters it could still resolve them; adding the fifth (`busy`)
        # tipped it into `AmbiguousParameter: could not determine data type of
        # parameter $4` and the whole queue stopped claiming — on Postgres only,
        # which is to say in production only. The casts remove the inference
        # problem rather than relying on it happening to succeed.
        row = db.execute(text("""
            UPDATE jobs SET state='running', locked_by=:w, locked_at=:now, attempts=attempts+1
            WHERE id = (
                SELECT id FROM jobs
                WHERE ((state='queued' AND run_after <= :now)
                    OR (state='running' AND locked_at < :stale))
                  AND (CAST(:skip AS text[]) IS NULL OR platform IS NULL
                       OR NOT (platform = ANY(CAST(:skip AS text[]))))
                  AND (CAST(:kinds AS text[]) IS NULL
                       OR kind = ANY(CAST(:kinds AS text[])))
                  AND (CAST(:plats AS text[]) IS NULL
                       OR platform = ANY(CAST(:plats AS text[])))
                  AND (CAST(:busy AS integer[]) IS NULL OR user_id IS NULL
                       OR NOT (user_id = ANY(CAST(:busy AS integer[]))))
                ORDER BY priority ASC, run_after ASC, id ASC
                FOR UPDATE SKIP LOCKED LIMIT 1
            ) RETURNING id
        """), {"w": worker_id, "now": now, "stale": stale,
               "skip": blocked_names, "kinds": kinds or None,
               "plats": platforms or None, "busy": busy}).first()
        db.commit()
        return db.query(Job).get(row.id) if row else None

    # ── SQLite (and any backend without SKIP LOCKED): compare-and-swap ──────
    #
    # The previous implementation read a candidate row, mutated the ORM object,
    # and committed. That is a read and a write with a gap in between, and
    # SQLite does not take a write lock at the SELECT — it takes one at the
    # first write. Two threads therefore both read the same queued row, both set
    # `state='running'`, and both commit successfully. The second commit
    # overwrites the first, no error is raised anywhere, and the same job runs
    # twice. The module docstring claimed an IMMEDIATE transaction made this
    # safe; nothing ever issued one, and adding one would only have serialised
    # this process's own threads, not a second worker process.
    #
    # So the claim is now a conditional UPDATE and the database decides. The
    # candidate SELECT only proposes; the UPDATE succeeds for exactly one caller
    # because it re-asserts, in the same statement that writes, everything the
    # SELECT believed. `rowcount` is the verdict.
    #
    # `attempts` doubles as the version counter: every successful claim
    # increments it, so a row that was claimed and released between our SELECT
    # and our UPDATE fails the guard even though it is queued again.
    for _ in range(_CLAIM_ATTEMPTS):
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
            if busy:
                q = q.filter((Job.user_id.is_(None)) | (~Job.user_id.in_(busy)))
            candidate = q.order_by(Job.priority.asc(), Job.run_after.asc(),
                                   Job.id.asc()).first()
            if candidate is None:
                return None

            job_id = candidate.id
            seen_state = candidate.state
            seen_attempts = int(candidate.attempts or 0)
            seen_locked_at = candidate.locked_at

            guard = [Job.id == job_id, Job.state == seen_state,
                     Job.attempts == seen_attempts]
            # NULL never equals NULL, so an unlocked row needs IS NULL rather
            # than `= None` — which would silently match nothing and make every
            # claim of a fresh job fail.
            if seen_locked_at is None:
                guard.append(Job.locked_at.is_(None))
            else:
                guard.append(Job.locked_at == seen_locked_at)

            claimed = db.query(Job).filter(*guard).update(
                {Job.state: "running", Job.locked_by: worker_id,
                 Job.locked_at: now, Job.attempts: seen_attempts + 1},
                synchronize_session=False)
            db.commit()

            if claimed:
                # Re-read rather than trusting the in-session object: the UPDATE
                # bypassed the identity map, and the caller is about to run a
                # handler against these values.
                db.expire_all()
                return db.query(Job).get(job_id)

            # Lost this row to another worker. Try the next candidate — the
            # queue is not empty just because this one is gone.
            logger.debug("lost claim race on job %s", job_id)
        except Exception as e:
            # On SQLite two writers can also collide at the file lock. That is
            # contention, not corruption: roll back and let the loop retry.
            safe_rollback(db)
            logger.debug("claim contention: %s", e)
            return None
    return None


def _release_units(db, job: Job) -> None:
    """Hand Processing Units back for work that died without costing anything.

    Called only when a job is declared dead — never on an intermediate failure,
    because a job that is going to be retried is still going to do the work it
    was paid for. `billing.refund` applies the refund rule (see its docstring);
    if any money was already spent on this content the units stay spent.
    """
    if job.kind != "content.process":
        return
    try:
        payload = json.loads(job.payload or "{}")
        canonical_id = payload.get("canonical_id")
        user_id = job.user_id or payload.get("user_id")
        if not (canonical_id and user_id):
            return
        from .billing import refund
        refund(db, user_id=int(user_id), canonical_content_id=int(canonical_id),
               reason="job_dead")
    except Exception as e:
        # A refund failing must never stop the queue from moving on.
        logger.warning("could not release units for job %s: %s", job.id, e)


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
            _release_units(db, job)
        else:
            job.state = "queued"
            # Exponential backoff: 30s, 2m, 8m, 32m
            job.run_after = _now() + timedelta(seconds=30 * (4 ** (job.attempts - 1)))
    job.locked_by = None
    job.locked_at = None
    db.commit()


def _park(db, job: Job, *, retry_after: float, reason: str) -> None:
    """Re-queue a job without spending an attempt.

    For the cases where nothing is wrong with the job: the platform is
    throttled, or another worker is already doing this exact work. Both are
    "come back later", and neither should count against the retry budget.
    """
    safe_rollback(db)
    job.attempts = max(0, (job.attempts or 1) - 1)
    job.state = "queued"
    job.run_after = _now() + timedelta(seconds=retry_after)
    job.last_error = f"parked: {reason}"[:2000]
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

    # Read the identifiers now, while the session is definitely healthy.
    #
    # `job` is an ORM object on the session the handler is about to use. If the
    # handler leaves that session needing a rollback, every attribute access
    # becomes a lazy load that raises PendingRollbackError — so even *logging*
    # `job.id` in the error path fails, before anything gets a chance to
    # recover. Plain locals cannot be invalidated by a failed transaction.
    job_id, job_kind = job.id, job.kind
    try:
        fn(payload, db)
        finish(db, job, ok=True)
        return True
    except PlatformUnavailable as e:
        # Not a failure of the job — the platform is throttled. Park it and
        # give the attempt back so throttling never exhausts the retry budget.
        _park(db, job, retry_after=e.retry_after, reason=str(e))
        logger.info("job %s parked for %.0fs (%s)", job.id, e.retry_after, e.reason)
        return False
    except ContentBusy as e:
        # Another worker holds the processing lease for this content. Same
        # treatment as a throttled platform: the work is happening, just not
        # here, so park without spending an attempt. Failing instead would
        # eventually declare a perfectly healthy item dead for the crime of
        # being popular.
        _park(db, job, retry_after=e.retry_after, reason=str(e))
        logger.info("job %s deferred: %s", job.id, e)
        return False
    except Exception as e:
        # Rollback FIRST — before logging, before `finish`, before any attribute
        # of `job` is read.
        #
        # A handler that died on a failed statement (an IntegrityError from a
        # racing writer, most often) leaves the session needing a rollback, and
        # SQLAlchemy then refuses every later operation with
        # PendingRollbackError. That includes the lazy load behind `job.id` in a
        # log line, which is how the recovery path itself used to raise: the job
        # stayed 'running' until its lease expired and the log showed a rollback
        # complaint instead of the cause. The error is already captured in `e`.
        detail = f"{e}\n{traceback.format_exc()[:1200]}"
        safe_rollback(db)
        logger.exception("job %s (%s) failed", job_id, job_kind)
        finish(db, job, ok=False, error=detail)
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
