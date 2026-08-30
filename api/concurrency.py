"""Primitives for work that two workers can reach at the same time.

Sava's queue is intentionally simple — poll, claim, run — and everything in it
is safe right up until two threads reach the same row. Then three separate
things go wrong, and they compound:

  1. **Check-then-insert.** `SELECT ... ; if None: INSERT` is a read followed by
     a write with a gap in between. Both workers read "nothing there", both
     insert, and the loser hits a UNIQUE violation on a constraint that was
     doing exactly its job.
  2. **A failed statement poisons the session.** SQLAlchemy marks the
     transaction as needing rollback; every later `commit()` on that session
     raises `PendingRollbackError`, so the *real* error is buried under a
     second, more confusing one — including the one raised while trying to
     record the first failure.
  3. **Duplicated expensive work.** Long before the UNIQUE violation, both
     workers already downloaded the video and paid for ASR. The constraint
     caught the double *write*; nothing caught the double *spend*.

This module is the answer to all three: writes that cannot collide, a session
that is always usable afterwards, and a lease that keeps two workers off the
same content in the first place.

Everything here works identically on SQLite and PostgreSQL. `ON CONFLICT` is
supported by both (SQLite since 3.24), and the lease is a compare-and-swap
`UPDATE` — no advisory locks, no `SELECT FOR UPDATE`, nothing that exists on
only one of them.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Sequence

from sqlalchemy import update

from .config import IS_POSTGRES

logger = logging.getLogger(__name__)

#: How long a processing lease is honoured before another worker may steal it.
#: Deliberately longer than the slowest realistic pipeline run (download plus
#: ASR plus frames) and shorter than the job lease, so a crashed worker's
#: content becomes available again on the retry rather than staying stuck.
CONTENT_LEASE_SECONDS = 1800


def _now() -> datetime:
    return datetime.now(timezone.utc)


def worker_identity() -> str:
    """Who is asking, precisely enough to be a lease owner.

    Thread id included, not just the pid: the worker runs several threads in one
    process, and they contend with each other exactly as two processes would. A
    per-process id would let a second thread believe it already held the lease.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"[:64]


class ContentBusy(Exception):
    """Another worker holds the processing lease for this content.

    Not an error: the work *is* being done, just not by this caller. The queue
    treats it the way it treats a throttled platform — park the job, give the
    attempt back, come back later — because burning a retry on "somebody else
    got there first" is how a perfectly healthy item ends up dead.
    """

    def __init__(self, canonical_id: int, retry_after: float = 30.0,
                 holder: Optional[str] = None):
        self.canonical_id = canonical_id
        self.retry_after = retry_after
        self.holder = holder
        super().__init__(
            f"canonical {canonical_id} is being processed by "
            f"{holder or 'another worker'}")


# ── Session hygiene ─────────────────────────────────────────────────────────

def safe_rollback(db) -> None:
    """Return a session to a usable state, whatever state it is in.

    The rule this enforces: **a session is rolled back before it is reused.**
    After a failed statement SQLAlchemy will refuse every subsequent operation
    with `PendingRollbackError` until this happens, which turns one handled
    error into an unhandled cascade — most painfully in the error handler that
    was trying to record the original failure.

    Swallows its own failure on purpose. This is called *while already handling
    an error*; a rollback that fails (a dropped connection, usually) must not
    replace the exception the caller is about to raise with a less useful one.
    """
    try:
        db.rollback()
    except Exception as e:  # pragma: no cover - only on a dead connection
        logger.warning("rollback failed: %s", e)


# ── Writes that cannot collide ──────────────────────────────────────────────

def _insert_stmt(model):
    """A dialect-specific INSERT that understands ON CONFLICT."""
    if IS_POSTGRES:
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(model.__table__)


def insert_or_ignore(db, model, values: Dict[str, Any], *,
                     index_elements: Sequence[str]) -> bool:
    """INSERT, or do nothing if the row already exists. Returns True if written.

    The idempotent form of "write this if nobody has". The UNIQUE constraint is
    still there and still enforced — this asks the database to apply it as a
    decision instead of an exception, which is the difference between a race
    that is handled and a race that crashes a worker.

    Use where losing the race means the other worker's row is just as good: a
    transcript for the same content, language and source is the same transcript.
    """
    # RETURNING, not `rowcount`.
    #
    # `INSERT ... ON CONFLICT DO NOTHING` correctly skips the duplicate on both
    # engines, but psycopg3 reports `rowcount` as 1 for the skipped insert, so
    # the return value said "I wrote it" when the database had written nothing.
    # Caught by running the suite against Postgres; SQLite reported 0 and the
    # test passed there, which is exactly the shape of bug that survives a
    # single-engine test run.
    #
    # RETURNING is unambiguous: zero rows back means the conflict fired. Both
    # SQLite (3.35+) and Postgres support it.
    stmt = (_insert_stmt(model).values(**values)
            .on_conflict_do_nothing(index_elements=list(index_elements))
            .returning(model.__table__.c.id))
    try:
        result = db.execute(stmt)
        inserted = result.first() is not None
        db.commit()
        return inserted
    except Exception:
        # Includes the genuinely concurrent case on SQLite, where two writers
        # can still collide at the file lock rather than at the constraint.
        safe_rollback(db)
        raise


def insert_or_update(db, model, values: Dict[str, Any], *,
                     index_elements: Sequence[str],
                     update_columns: Optional[Iterable[str]] = None) -> None:
    """INSERT, or overwrite the conflicting row. Idempotent by construction.

    Use where the new value supersedes the old one — a recomputed embedding is
    the same vector however many times it is derived, so last writer wins is
    both correct and cheap.

    `update_columns` defaults to everything except the conflict key.
    """
    stmt = _insert_stmt(model).values(**values)
    columns = list(update_columns) if update_columns is not None else [
        k for k in values if k not in set(index_elements)
    ]
    if columns:
        stmt = stmt.on_conflict_do_update(
            index_elements=list(index_elements),
            set_={c: getattr(stmt.excluded, c) for c in columns},
        )
    else:  # pragma: no cover - a row that is only its key
        stmt = stmt.on_conflict_do_nothing(index_elements=list(index_elements))
    try:
        db.execute(stmt)
        db.commit()
    except Exception:
        safe_rollback(db)
        raise


# ── Keeping two workers off the same content ────────────────────────────────

def acquire_content_lease(db, canonical_id: int, *, owner: str,
                          ttl_seconds: int = CONTENT_LEASE_SECONDS) -> bool:
    """Claim the exclusive right to process one canonical item.

    A compare-and-swap `UPDATE`: the lease is taken only if it is currently free
    or expired, and the database decides that, atomically, in one statement.
    `rowcount` is the answer — 1 means this caller won, 0 means somebody else
    holds it. There is no window between checking and taking, because there is
    no check.

    An expired lease is stealable on purpose. A worker that is SIGKILLed mid-run
    cannot release anything, and content that can never be processed again is a
    worse failure than processing it twice after half an hour.
    """
    from .models import CanonicalContent

    now = _now()
    stale = now - timedelta(seconds=ttl_seconds)
    stmt = (
        update(CanonicalContent.__table__)
        .where(CanonicalContent.id == canonical_id)
        .where(
            (CanonicalContent.processing_lock_owner.is_(None))
            | (CanonicalContent.processing_lock_at.is_(None))
            | (CanonicalContent.processing_lock_at < stale)
        )
        .values(processing_lock_owner=owner, processing_lock_at=now)
    )
    try:
        result = db.execute(stmt)
        # Committed immediately, and that is the whole point: a lease nobody
        # else can see is not a lease.
        db.commit()
        return bool(result.rowcount)
    except Exception as e:
        safe_rollback(db)
        logger.warning("could not take processing lease for %s: %s", canonical_id, e)
        return False


def release_content_lease(db, canonical_id: int, *, owner: str) -> None:
    """Give the lease back, but only if it is still ours.

    The owner check matters after a lease expires and is stolen: releasing
    unconditionally would clear the *new* holder's lease and let a third worker
    in while the second is still running.
    """
    from .models import CanonicalContent

    # Whatever went wrong upstream, the session has to be usable to release.
    safe_rollback(db)
    try:
        db.execute(
            update(CanonicalContent.__table__)
            .where(CanonicalContent.id == canonical_id)
            .where(CanonicalContent.processing_lock_owner == owner)
            .values(processing_lock_owner=None, processing_lock_at=None)
        )
        db.commit()
    except Exception as e:  # pragma: no cover - lease expiry covers this
        safe_rollback(db)
        logger.warning("could not release processing lease for %s: %s",
                       canonical_id, e)


def lease_holder(db, canonical_id: int) -> Optional[str]:
    """Who currently holds the lease, for logging and tests."""
    from .models import CanonicalContent

    row = db.query(CanonicalContent.processing_lock_owner).filter(
        CanonicalContent.id == canonical_id).first()
    return row[0] if row else None


@contextmanager
def content_lease(db, canonical_id: int, *, owner: str,
                  ttl_seconds: int = CONTENT_LEASE_SECONDS,
                  retry_after: float = 30.0):
    """Hold the processing lease for the duration of a block.

    Raises `ContentBusy` rather than returning a flag, so the expensive path
    below it cannot be entered by forgetting to check a boolean.
    """
    if not acquire_content_lease(db, canonical_id, owner=owner,
                                 ttl_seconds=ttl_seconds):
        raise ContentBusy(canonical_id, retry_after=retry_after,
                          holder=lease_holder(db, canonical_id))
    try:
        yield
    finally:
        release_content_lease(db, canonical_id, owner=owner)
