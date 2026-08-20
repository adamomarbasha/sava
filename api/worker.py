"""Background worker.

Run alongside the API:

    python -m api.worker                # long-running
    python -m api.worker --once         # drain the queue and exit
    python -m api.worker --concurrency 4

Deployment: one unfiltered worker process is the right shape until a specific
workload needs different hardware. Claiming is atomic, so running several is
safe — on Postgres via FOR UPDATE SKIP LOCKED, on SQLite via serialised writes.

Pools, when scale demands them:

    python -m api.worker --platforms youtube          # captions-heavy, cheap
    python -m api.worker --platforms tiktok           # egress-heavy
    python -m api.worker --kinds content.comments     # optional enrichment only

Same binary, same queue, different slice. Nothing needs to be re-architected to
split the workload — that is the point of the filter existing before it is
needed.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from .config import WORKER_CONCURRENCY, WORKER_POLL_SECONDS
from .db import SessionLocal
from .jobs import WORKER_ID, claim_next, execute, queue_stats
from .pipeline import handlers  # noqa: F401  (registers handlers)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(name)s: %(message)s",
)
logger = logging.getLogger("sava.worker")

_stop = threading.Event()


def _handle_signal(signum, _frame):
    logger.info("received signal %s, finishing current job then exiting", signum)
    _stop.set()


def run_loop(worker_index: int, poll_seconds: float,
             kinds=None, platforms=None) -> None:
    ident = f"{WORKER_ID}#{worker_index}"
    idle = 0
    while not _stop.is_set():
        db = SessionLocal()
        try:
            job = claim_next(db, worker_id=ident, kinds=kinds, platforms=platforms)
            if job is None:
                idle += 1
                # Back off when idle so an empty queue is not a busy loop.
                _stop.wait(min(poll_seconds * min(idle, 5), 15.0))
                continue
            idle = 0
            logger.info("claimed job %s (%s) attempt %s", job.id, job.kind, job.attempts)
            started = time.monotonic()
            ok = execute(db, job)
            logger.info("job %s %s in %.1fs", job.id,
                        "OK" if ok else "FAILED", time.monotonic() - started)
        except Exception:
            logger.exception("worker loop error")
            _stop.wait(poll_seconds)
        finally:
            db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Sava background worker")
    parser.add_argument("--once", action="store_true",
                        help="drain the queue and exit")
    parser.add_argument("--concurrency", type=int, default=WORKER_CONCURRENCY)
    parser.add_argument("--poll", type=float, default=WORKER_POLL_SECONDS)
    parser.add_argument(
        "--kinds", default=None,
        help="comma-separated job kinds this worker will take "
             "(e.g. content.process). Omit to take everything.")
    parser.add_argument(
        "--platforms", default=None,
        help="comma-separated platforms this worker will take (e.g. youtube,tiktok)")
    args = parser.parse_args()

    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()] if args.kinds else None
    platforms = ([p.strip() for p in args.platforms.split(",") if p.strip()]
                 if args.platforms else None)
    if kinds or platforms:
        logger.info("worker pool: kinds=%s platforms=%s", kinds or "all", platforms or "all")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    db = SessionLocal()
    try:
        logger.info("queue at start: %s", queue_stats(db))
    finally:
        db.close()

    if args.once:
        from .jobs import drain
        stats = drain(limit=1000)
        logger.info("drained: %s", stats)
        return 0 if stats["failed"] == 0 else 1

    threads = [
        threading.Thread(target=run_loop, args=(i, args.poll, kinds, platforms),
                         daemon=True, name=f"sava-worker-{i}")
        for i in range(max(1, args.concurrency))
    ]
    for t in threads:
        t.start()
    logger.info("worker started with %d threads", len(threads))

    try:
        while not _stop.is_set():
            _stop.wait(1.0)
    except KeyboardInterrupt:
        _stop.set()

    for t in threads:
        t.join(timeout=30)
    logger.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
