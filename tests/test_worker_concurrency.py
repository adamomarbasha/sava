"""Two workers, one row.

Every test here reproduces something that actually happened in the worker logs:
the same job claimed twice by a 2-thread worker, UNIQUE violations on transcript
and embedding inserts, `PendingRollbackError` from a session reused after a
failed statement, and retries that "succeeded" only because a competing worker
had already written the data.

These are concurrency tests, so they use real threads and a real database rather
than mocks. A mocked lock proves the mock locks. What has to be proven is that
*the database* refuses the second writer, because that is the only referee two
worker processes share.

Threading note: each thread gets its own `SessionLocal()`. Sharing a session
across threads would be its own bug and would test nothing about the schema.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError, PendingRollbackError

from api import concurrency, jobs
from api.concurrency import (
    ContentBusy, acquire_content_lease, insert_or_ignore, insert_or_update,
    release_content_lease, safe_rollback, worker_identity,
)
from api.db import SessionLocal
from api.models import (
    CanonicalContent, ContentEmbedding, ContentTranscript, ContentUnderstanding,
    Job,
)

from conftest import make_user


def _canonical(db, key: str) -> CanonicalContent:
    cc = CanonicalContent(
        content_key=key, platform="youtube", canonical_url=f"https://y.test/{key}",
        title="Test item",
    )
    db.add(cc)
    db.commit()
    db.refresh(cc)
    return cc


def _queued_job(db, kind: str, key: str, **payload) -> Job:
    import json
    job = Job(kind=kind, idempotency_key=key, payload=json.dumps(payload),
              state="queued", run_after=datetime.now(timezone.utc) - timedelta(seconds=1))
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _run_concurrently(fn, n: int):
    """Run `fn(i)` in n threads, collecting results and exceptions."""
    results: list = [None] * n
    errors: list = [None] * n
    barrier = threading.Barrier(n)

    def target(i):
        try:
            # Line every thread up so they hit the contended statement together
            # rather than one after another, which would test nothing.
            barrier.wait(timeout=10)
            results[i] = fn(i)
        except Exception as e:  # noqa: BLE001 - recorded and asserted on
            errors[i] = e

    threads = [threading.Thread(target=target, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results, errors


class TestJobClaimIsAtomic:
    """One queued job, many workers, exactly one claim."""

    def test_two_threads_cannot_both_claim_the_same_job(self, clean_db):
        _queued_job(clean_db, "content.process", "claim-race-1", canonical_id=1)

        def claim(i):
            db = SessionLocal()
            try:
                job = jobs.claim_next(db, worker_id=f"worker-{i}", skip_platforms={})
                return job.id if job else None
            finally:
                db.close()

        results, errors = _run_concurrently(claim, 2)

        assert errors == [None, None], f"claiming raised: {errors}"
        claimed = [r for r in results if r is not None]
        assert len(claimed) == 1, (
            f"both workers claimed the job: {results}. The claim is not atomic.")

    def test_eight_threads_one_job(self, clean_db):
        """The 2-thread case can pass by luck. Eight cannot."""
        _queued_job(clean_db, "content.process", "claim-race-2", canonical_id=1)

        def claim(i):
            db = SessionLocal()
            try:
                job = jobs.claim_next(db, worker_id=f"worker-{i}", skip_platforms={})
                return job.id if job else None
            finally:
                db.close()

        results, _ = _run_concurrently(claim, 8)
        assert len([r for r in results if r is not None]) == 1, results

    def test_each_of_two_jobs_goes_to_exactly_one_worker(self, clean_db):
        """Contention must not cost throughput: two jobs, two workers, both run."""
        _queued_job(clean_db, "content.process", "pair-a", canonical_id=1)
        _queued_job(clean_db, "content.process", "pair-b", canonical_id=2)

        def claim(i):
            db = SessionLocal()
            try:
                job = jobs.claim_next(db, worker_id=f"worker-{i}", skip_platforms={})
                return job.id if job else None
            finally:
                db.close()

        results, _ = _run_concurrently(claim, 2)
        claimed = sorted(r for r in results if r is not None)
        assert len(claimed) == 2 and claimed[0] != claimed[1], (
            f"two workers should have taken one job each, got {results}")

    def test_the_claim_marks_the_job_running_and_counts_the_attempt(self, clean_db):
        job = _queued_job(clean_db, "content.process", "claim-state", canonical_id=1)

        db = SessionLocal()
        try:
            claimed = jobs.claim_next(db, worker_id="w1", skip_platforms={})
            assert claimed is not None and claimed.id == job.id
            assert claimed.state == "running"
            assert claimed.locked_by == "w1"
            assert claimed.attempts == 1
        finally:
            db.close()

    def test_a_running_job_is_not_reclaimed_before_its_lease_expires(self, clean_db):
        _queued_job(clean_db, "content.process", "lease-held", canonical_id=1)

        db = SessionLocal()
        try:
            assert jobs.claim_next(db, worker_id="w1", skip_platforms={}) is not None
            assert jobs.claim_next(db, worker_id="w2", skip_platforms={}) is None
        finally:
            db.close()

    def test_a_stale_lease_is_reclaimable_by_exactly_one_worker(self, clean_db):
        """A worker that died must not strand the job — nor let two in at once."""
        job = _queued_job(clean_db, "content.process", "lease-stale", canonical_id=1)
        job.state = "running"
        job.locked_by = "dead-worker"
        job.locked_at = datetime.now(timezone.utc) - timedelta(days=1)
        clean_db.commit()

        def claim(i):
            db = SessionLocal()
            try:
                claimed = jobs.claim_next(db, worker_id=f"worker-{i}", skip_platforms={})
                return claimed.id if claimed else None
            finally:
                db.close()

        results, _ = _run_concurrently(claim, 4)
        assert len([r for r in results if r is not None]) == 1, results


class TestIdempotentWrites:
    """The UNIQUE constraints stay. Writing through them stops crashing."""

    def test_duplicate_transcript_write_does_not_raise(self, clean_db):
        cc = _canonical(clean_db, "yt:transcript-dupe")
        values = {"canonical_content_id": cc.id, "source": "captions", "lang": "en",
                  "text": "hello world", "segments": "[]", "is_complete": True}

        first = insert_or_ignore(clean_db, ContentTranscript, dict(values),
                                 index_elements=["canonical_content_id", "lang", "source"])
        second = insert_or_ignore(clean_db, ContentTranscript, dict(values),
                                  index_elements=["canonical_content_id", "lang", "source"])

        assert first is True, "the first write should insert"
        assert second is False, "the second write should be a no-op, not an error"
        assert clean_db.query(ContentTranscript).filter(
            ContentTranscript.canonical_content_id == cc.id).count() == 1

    def test_the_unique_constraint_still_exists(self, clean_db):
        """Proof the race was fixed rather than hidden by dropping the constraint."""
        cc = _canonical(clean_db, "yt:constraint-intact")
        clean_db.add(ContentTranscript(
            canonical_content_id=cc.id, source="captions", lang="en",
            text="one", segments="[]"))
        clean_db.commit()

        clean_db.add(ContentTranscript(
            canonical_content_id=cc.id, source="captions", lang="en",
            text="two", segments="[]"))
        with pytest.raises(IntegrityError):
            clean_db.commit()
        safe_rollback(clean_db)

    def test_concurrent_transcript_writers_produce_one_row(self, clean_db):
        cc = _canonical(clean_db, "yt:transcript-threads")

        def write(i):
            db = SessionLocal()
            try:
                return insert_or_ignore(
                    db, ContentTranscript,
                    {"canonical_content_id": cc.id, "source": "asr", "lang": "en",
                     "text": f"from worker {i}", "segments": "[]", "is_complete": True},
                    index_elements=["canonical_content_id", "lang", "source"])
            finally:
                db.close()

        _results, errors = _run_concurrently(write, 4)

        # SQLite can still return "database is locked" under real contention;
        # that is a retryable condition, not the UNIQUE violation being tested.
        integrity = [e for e in errors if isinstance(e, IntegrityError)]
        assert not integrity, f"a UNIQUE violation escaped: {integrity}"
        assert clean_db.query(ContentTranscript).filter(
            ContentTranscript.canonical_content_id == cc.id).count() == 1

    def test_duplicate_embedding_write_does_not_raise(self, clean_db):
        cc = _canonical(clean_db, "yt:embedding-dupe")

        for model_name in ("model-a", "model-b"):
            insert_or_update(
                clean_db, ContentEmbedding,
                {"canonical_content_id": cc.id, "model": model_name, "dim": 8},
                index_elements=["canonical_content_id"])

        rows = clean_db.query(ContentEmbedding).filter(
            ContentEmbedding.canonical_content_id == cc.id).all()
        assert len(rows) == 1, "an upsert must not create a second row"
        assert rows[0].model == "model-b", "the later write should win"

    def test_concurrent_embedding_writers_produce_one_row(self, clean_db):
        cc = _canonical(clean_db, "yt:embedding-threads")

        def write(i):
            db = SessionLocal()
            try:
                insert_or_update(
                    db, ContentEmbedding,
                    {"canonical_content_id": cc.id, "model": f"m{i}", "dim": 8},
                    index_elements=["canonical_content_id"])
            finally:
                db.close()

        _results, errors = _run_concurrently(write, 4)
        integrity = [e for e in errors if isinstance(e, IntegrityError)]
        assert not integrity, f"a UNIQUE violation escaped: {integrity}"
        assert clean_db.query(ContentEmbedding).filter(
            ContentEmbedding.canonical_content_id == cc.id).count() == 1

    def test_duplicate_understanding_write_does_not_raise(self, clean_db):
        from api.pipeline.ingest import _upsert_understanding

        cc = _canonical(clean_db, "yt:understanding-dupe")
        _upsert_understanding(clean_db, cc.id, {"tl_dr": "first"})
        _upsert_understanding(clean_db, cc.id, {"tl_dr": "second"})

        rows = clean_db.query(ContentUnderstanding).filter(
            ContentUnderstanding.canonical_content_id == cc.id).all()
        assert len(rows) == 1
        assert rows[0].tl_dr == "second"


class TestSessionHealthAfterIntegrityError:
    """A failed statement must not poison everything that follows."""

    def test_session_is_usable_after_an_integrity_error(self, clean_db):
        cc = _canonical(clean_db, "yt:session-health")
        clean_db.add(ContentTranscript(canonical_content_id=cc.id, source="captions",
                                       lang="en", text="one", segments="[]"))
        clean_db.commit()

        clean_db.add(ContentTranscript(canonical_content_id=cc.id, source="captions",
                                       lang="en", text="two", segments="[]"))
        with pytest.raises(IntegrityError):
            clean_db.commit()

        safe_rollback(clean_db)

        # The proof: an ordinary write works immediately afterwards. Without the
        # rollback this raises PendingRollbackError instead.
        cc.title = "still writable"
        clean_db.commit()
        assert clean_db.query(CanonicalContent).get(cc.id).title == "still writable"

    def test_without_rollback_the_session_is_poisoned(self, clean_db):
        """Documents the failure mode, so the fix cannot be quietly removed."""
        cc = _canonical(clean_db, "yt:session-poison")
        clean_db.add(ContentTranscript(canonical_content_id=cc.id, source="captions",
                                       lang="en", text="one", segments="[]"))
        clean_db.commit()
        clean_db.add(ContentTranscript(canonical_content_id=cc.id, source="captions",
                                       lang="en", text="two", segments="[]"))
        with pytest.raises(IntegrityError):
            clean_db.commit()

        with pytest.raises(PendingRollbackError):
            cc.title = "doomed"
            clean_db.commit()
        safe_rollback(clean_db)

    def test_a_handler_that_raises_integrityerror_still_records_the_failure(
            self, clean_db):
        """The queue's own error path, which is where this bit hardest.

        `execute` catches the handler's exception and then commits the job's new
        state. On a poisoned session that commit raised PendingRollbackError,
        the real error was never recorded, and the job sat 'running' until its
        lease expired.
        """
        cc = _canonical(clean_db, "yt:handler-integrity")
        clean_db.add(ContentTranscript(canonical_content_id=cc.id, source="captions",
                                       lang="en", text="one", segments="[]"))
        clean_db.commit()

        @jobs.handler("test.integrity_failure")
        def _boom(payload, db):  # noqa: ANN001
            db.add(ContentTranscript(
                canonical_content_id=payload["canonical_id"], source="captions",
                lang="en", text="duplicate", segments="[]"))
            db.commit()

        job = _queued_job(clean_db, "test.integrity_failure", "integrity-job",
                          canonical_id=cc.id)

        db = SessionLocal()
        try:
            claimed = jobs.claim_next(db, worker_id="w1", skip_platforms={})
            assert claimed is not None
            ok = jobs.execute(db, claimed)
            assert ok is False
            db.expire_all()
            row = db.query(Job).get(job.id)
            assert row.state in ("queued", "dead"), row.state
            assert row.last_error and "UNIQUE" in row.last_error.upper(), (
                f"the real error was not recorded: {row.last_error!r}")
            assert "PendingRollbackError" not in (row.last_error or "")
        finally:
            jobs.HANDLERS.pop("test.integrity_failure", None)
            db.close()


class TestContentLease:
    """Only one worker may run the expensive path for a canonical item."""

    def test_one_of_two_workers_gets_the_lease(self, clean_db):
        cc = _canonical(clean_db, "yt:lease-race")

        def take(i):
            db = SessionLocal()
            try:
                return acquire_content_lease(db, cc.id, owner=f"worker-{i}")
            finally:
                db.close()

        results, errors = _run_concurrently(take, 2)
        assert errors == [None, None], errors
        assert sum(1 for r in results if r) == 1, (
            f"both workers took the lease: {results}")

    def test_eight_workers_one_lease(self, clean_db):
        cc = _canonical(clean_db, "yt:lease-race-8")

        def take(i):
            db = SessionLocal()
            try:
                return acquire_content_lease(db, cc.id, owner=f"worker-{i}")
            finally:
                db.close()

        results, _ = _run_concurrently(take, 8)
        assert sum(1 for r in results if r) == 1, results

    def test_the_lease_is_reusable_once_released(self, clean_db):
        cc = _canonical(clean_db, "yt:lease-cycle")
        assert acquire_content_lease(clean_db, cc.id, owner="a") is True
        assert acquire_content_lease(clean_db, cc.id, owner="b") is False
        release_content_lease(clean_db, cc.id, owner="a")
        assert acquire_content_lease(clean_db, cc.id, owner="b") is True

    def test_releasing_someone_elses_lease_does_nothing(self, clean_db):
        """A stolen expired lease must not be cleared by its previous holder."""
        cc = _canonical(clean_db, "yt:lease-not-yours")
        acquire_content_lease(clean_db, cc.id, owner="holder")
        release_content_lease(clean_db, cc.id, owner="impostor")
        assert acquire_content_lease(clean_db, cc.id, owner="other") is False

    def test_an_expired_lease_can_be_stolen(self, clean_db):
        """A SIGKILLed worker must not strand content forever."""
        cc = _canonical(clean_db, "yt:lease-expired")
        acquire_content_lease(clean_db, cc.id, owner="dead-worker")
        cc.processing_lock_at = datetime.now(timezone.utc) - timedelta(hours=2)
        clean_db.commit()
        assert acquire_content_lease(clean_db, cc.id, owner="live-worker") is True

    def test_worker_identity_distinguishes_threads(self):
        """Two threads in one process contend exactly as two processes do."""
        seen = []
        _run_concurrently(lambda i: seen.append(worker_identity()), 4)
        assert len(set(seen)) == 4, f"thread ids not distinguished: {seen}"


class TestOnlyOneExpensivePath:
    """The point of all of the above: the money is spent once."""

    def test_concurrent_processing_runs_the_expensive_path_once(self, clean_db,
                                                                monkeypatch):
        """Two workers, one canonical item, one trip through the pipeline body."""
        from api.pipeline import ingest

        cc = _canonical(clean_db, "yt:one-expensive-path")

        entered = []
        gate = threading.Event()

        def slow_router(*_a, **_k):
            # Stand in for the whole expensive ladder. Held open so the second
            # worker is guaranteed to arrive while the first is still inside.
            entered.append(threading.get_ident())
            gate.wait(timeout=5)
            raise RuntimeError("stop here — the lease is what is under test")

        monkeypatch.setattr(ingest, "get_router", slow_router, raising=False)
        monkeypatch.setattr("api.ai.router.get_router", slow_router)

        busy = []

        def run(i):
            db = SessionLocal()
            try:
                return ingest.process_content(cc.id, db)
            except ContentBusy as e:
                busy.append(e)
                return "busy"
            except Exception:
                return "error"
            finally:
                db.close()

        def release_later():
            # Let the first worker out once the second has had its chance.
            threading.Event().wait(1.0)
            gate.set()

        releaser = threading.Thread(target=release_later, daemon=True)
        releaser.start()
        _results, errors = _run_concurrently(run, 2)
        gate.set()
        releaser.join(timeout=5)

        assert not [e for e in errors if e is not None], errors
        assert len(entered) <= 1, (
            f"the expensive path ran {len(entered)} times; the lease did not hold")
        assert len(busy) == 1, (
            "the second worker should have been told the content was busy, "
            f"got {busy}")

    def test_the_lease_is_released_after_a_failure(self, clean_db, monkeypatch):
        """A crash must not leave content locked until the TTL expires."""
        from api.pipeline import ingest

        cc = _canonical(clean_db, "yt:lease-released-on-error")

        def boom(*_a, **_k):
            raise RuntimeError("pipeline exploded")

        monkeypatch.setattr("api.ai.router.get_router", boom)

        result = ingest.process_content(cc.id, clean_db)
        assert result.get("ok") is False

        clean_db.expire_all()
        assert clean_db.query(CanonicalContent).get(cc.id).processing_lock_owner is None

    def test_a_cache_hit_never_takes_the_lease(self, clean_db):
        """Reads stay lock-free — a ready item must not queue behind a worker."""
        from api.models import ProcessingState
        from api.pipeline.ingest import PIPELINE_VERSION

        cc = _canonical(clean_db, "yt:cache-hit-no-lease")
        cc.processing_state = ProcessingState.READY
        cc.pipeline_version = PIPELINE_VERSION
        clean_db.commit()

        # Somebody else holds the lease; a cache hit must not care.
        acquire_content_lease(clean_db, cc.id, owner="another-worker")

        from api.pipeline import ingest
        result = ingest.process_content(cc.id, clean_db)
        assert result.get("cache_hit") is True


class TestRetriesReuseCachedWork:
    """A retry must not re-buy what the first attempt already paid for."""

    def test_a_second_run_does_not_re_acquire_an_existing_transcript(
            self, clean_db, monkeypatch):
        """The stale-read bug: the transcript existed and was fetched again."""
        from api.pipeline import acquire, ingest

        cc = _canonical(clean_db, "yt:transcript-cached")
        cc.duration_seconds = 120
        clean_db.add(ContentTranscript(
            canonical_content_id=cc.id, source="captions", lang="en",
            text="a transcript that already exists", segments='[]'))
        clean_db.commit()

        downloads = []
        for name in ("download_video_lowres", "download_audio", "transcribe_audio",
                     "fetch_native_captions"):
            monkeypatch.setattr(
                acquire, name,
                lambda *a, _n=name, **k: downloads.append(_n) or (_ for _ in ()).throw(
                    AssertionError(f"{_n} called for content that is already cached")),
                raising=False)

        # The run may fail downstream for want of an AI provider; what matters
        # is that nothing tried to buy the transcript again.
        try:
            ingest.process_content(cc.id, clean_db)
        except Exception:
            pass

        assert downloads == [], f"re-acquired cached work: {downloads}"

    def test_enqueue_is_idempotent_for_the_same_work(self, clean_db):
        """Two saves of the same content schedule one job, not two."""
        user = make_user(clean_db, "queue-idem@test.dev")
        cc = _canonical(clean_db, "yt:enqueue-idem")

        first = jobs.enqueue(clean_db, "content.process",
                             {"canonical_id": cc.id}, user_id=user.id)
        second = jobs.enqueue(clean_db, "content.process",
                              {"canonical_id": cc.id}, user_id=user.id)

        assert first is not None and second is not None
        assert first.id == second.id
        assert clean_db.query(Job).filter(Job.kind == "content.process").count() == 1

    def test_a_busy_job_is_parked_without_spending_an_attempt(self, clean_db):
        """ContentBusy is 'come back later', not a failure worth a retry."""
        cc = _canonical(clean_db, "yt:busy-park")

        @jobs.handler("test.busy")
        def _busy(payload, db):  # noqa: ANN001
            raise ContentBusy(cc.id, retry_after=5)

        _queued_job(clean_db, "test.busy", "busy-job", canonical_id=cc.id)

        db = SessionLocal()
        try:
            claimed = jobs.claim_next(db, worker_id="w1", skip_platforms={})
            assert claimed is not None
            assert claimed.attempts == 1
            assert jobs.execute(db, claimed) is False

            db.expire_all()
            row = db.query(Job).filter(Job.idempotency_key == "busy-job").first()
            assert row.state == "queued", "a busy job must be retried, not failed"
            assert row.attempts == 0, "parking must give the attempt back"
        finally:
            jobs.HANDLERS.pop("test.busy", None)
            db.close()


class TestBothDatabasesAreCovered:
    """The primitives must not be SQLite-only or Postgres-only."""

    def test_on_conflict_is_available_for_the_configured_dialect(self, clean_db):
        cc = _canonical(clean_db, "yt:dialect-check")
        # Compiles and runs against whichever database the suite is pointed at,
        # which is SQLite by default and Postgres when SAVA_TEST_DATABASE_URL is
        # set. Both support ON CONFLICT; neither needs a code path of its own.
        assert insert_or_ignore(
            clean_db, ContentTranscript,
            {"canonical_content_id": cc.id, "source": "captions", "lang": "en",
             "text": "x", "segments": "[]"},
            index_elements=["canonical_content_id", "lang", "source"]) is True

    def test_the_claim_guard_uses_is_null_not_equals_null(self, clean_db):
        """A fresh job has locked_at NULL, and `= NULL` matches nothing.

        Getting this wrong makes every claim of a never-run job silently fail
        and the queue stops moving — with no error anywhere.
        """
        _queued_job(clean_db, "content.process", "null-locked-at", canonical_id=1)
        db = SessionLocal()
        try:
            assert jobs.claim_next(db, worker_id="w1", skip_platforms={}) is not None
        finally:
            db.close()
