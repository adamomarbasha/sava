"""Scale, throttling, and failure-mode tests.

Every external provider is mocked. Nothing here touches YouTube, TikTok, or
Instagram — the point is to prove Sava's *own* behaviour under load, not to
generate abusive traffic.

What these assert:
  * 5,000 saves do not become 5,000 external requests,
  * duplicate content collapses to one job regardless of URL shape,
  * concurrency caps actually hold under parallel workers,
  * 429 / Retry-After parks work instead of burning retries,
  * a platform outage opens a circuit and isolates that platform only,
  * a worker crash mid-job recovers without duplicating expensive work,
  * partial extraction failure preserves the save.
"""
from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest

from conftest import install_fake_router, FakeRouter, make_user

from api.models import Bookmark, CanonicalContent, Job, UsageEvent
from api.platform_budget import (
    Outcome, PlatformPolicy, PlatformRequestManager, PlatformUnavailable,
    classify, parse_retry_after,
)


# ─── Error classification ────────────────────────────────────────────────────

class TestClassification:
    @pytest.mark.parametrize("text,expected", [
        ("HTTP Error 429: Too Many Requests", Outcome.RATE_LIMITED),
        ("Rate limit reached. Please wait a few minutes", Outcome.RATE_LIMITED),
        ("ERROR: Sign in to confirm you're not a bot", Outcome.FORBIDDEN),
        ("HTTP Error 403: Forbidden", Outcome.FORBIDDEN),
        ("Video unavailable", Outcome.NOT_FOUND),
        ("This video has been removed by the uploader", Outcome.NOT_FOUND),
        ("Private video", Outcome.NOT_FOUND),
        ("The read operation timed out", Outcome.TIMEOUT),
        ("something else entirely", Outcome.ERROR),
    ])
    def test_classify(self, text, expected):
        assert classify(text) == expected

    def test_retry_after_is_honoured(self):
        assert parse_retry_after("429 Too Many Requests, Retry-After: 120") == 120.0
        assert parse_retry_after("no header here") is None

    def test_deleted_content_is_not_a_platform_failure(self):
        """A wave of deleted videos must not look like an outage."""
        mgr = PlatformRequestManager({"youtube": PlatformPolicy(
            name="youtube", max_concurrency=4, requests_per_minute=0,
            min_interval_s=0, failure_threshold=2, open_seconds=60,
            max_open_seconds=60)})
        for _ in range(20):
            with mgr.acquire("youtube") as slot:
                slot.failed("Video unavailable")
        available, _, _ = mgr.availability("youtube")
        assert available is True, "not_found must never trip the breaker"
        assert mgr.snapshot()["youtube"]["not_found"] == 20


# ─── Concurrency and rate budget ─────────────────────────────────────────────

class TestConcurrency:
    def test_concurrency_cap_holds_under_parallel_load(self):
        mgr = PlatformRequestManager({"tiktok": PlatformPolicy(
            name="tiktok", max_concurrency=3, requests_per_minute=0,
            min_interval_s=0, failure_threshold=99, open_seconds=60,
            max_open_seconds=60)})

        peak = {"n": 0}
        current = {"n": 0}
        lock = threading.Lock()

        def worker():
            try:
                with mgr.acquire("tiktok", timeout=10) as slot:
                    with lock:
                        current["n"] += 1
                        peak["n"] = max(peak["n"], current["n"])
                    time.sleep(0.05)
                    with lock:
                        current["n"] -= 1
                    slot.ok()
            except PlatformUnavailable:
                pass

        threads = [threading.Thread(target=worker) for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert peak["n"] <= 3, f"concurrency cap breached: peak {peak['n']}"

    def test_min_interval_paces_requests(self):
        mgr = PlatformRequestManager({"instagram": PlatformPolicy(
            name="instagram", max_concurrency=1, requests_per_minute=0,
            min_interval_s=0.15, failure_threshold=99, open_seconds=60,
            max_open_seconds=60)})
        start = time.monotonic()
        for _ in range(5):
            with mgr.acquire("instagram") as slot:
                slot.ok()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.15 * 3, f"requests not paced: {elapsed:.2f}s"

    def test_rate_budget_blocks_the_burst(self):
        mgr = PlatformRequestManager({"youtube": PlatformPolicy(
            name="youtube", max_concurrency=8, requests_per_minute=5,
            min_interval_s=0, failure_threshold=99, open_seconds=60,
            max_open_seconds=60)})
        for _ in range(5):
            with mgr.acquire("youtube") as slot:
                slot.ok()
        # The 6th would have to wait out the 60s window.
        st = mgr._state("youtube")
        assert len(st.recent) == 5


# ─── Circuit breaker ─────────────────────────────────────────────────────────

class TestCircuitBreaker:
    def _mgr(self):
        return PlatformRequestManager({
            "instagram": PlatformPolicy(
                name="instagram", max_concurrency=2, requests_per_minute=0,
                min_interval_s=0, failure_threshold=3, open_seconds=60,
                max_open_seconds=120),
            "youtube": PlatformPolicy(
                name="youtube", max_concurrency=2, requests_per_minute=0,
                min_interval_s=0, failure_threshold=3, open_seconds=60,
                max_open_seconds=120),
        })

    def test_repeated_failures_open_the_circuit(self):
        mgr = self._mgr()
        for _ in range(3):
            with mgr.acquire("instagram") as slot:
                slot.failed("HTTP Error 403: Forbidden")
        available, wait, reason = mgr.availability("instagram")
        assert available is False
        assert reason == "circuit_open"
        assert wait > 0
        with pytest.raises(PlatformUnavailable):
            with mgr.acquire("instagram"):
                pass

    def test_outage_is_isolated_to_one_platform(self):
        """An Instagram outage must not stop YouTube processing."""
        mgr = self._mgr()
        for _ in range(5):
            try:
                with mgr.acquire("instagram") as slot:
                    slot.failed("HTTP Error 403: Forbidden")
            except PlatformUnavailable:
                break   # circuit opened — further calls are refused by design

        assert mgr.availability("instagram")[0] is False
        assert mgr.availability("youtube")[0] is True
        with mgr.acquire("youtube") as slot:
            slot.ok()
        assert mgr.snapshot()["youtube"]["ok"] == 1

    def test_rate_limit_respects_retry_after(self):
        mgr = self._mgr()
        with mgr.acquire("youtube") as slot:
            slot.failed("429 Too Many Requests. Retry-After: 90")
        available, wait, reason = mgr.availability("youtube")
        assert available is False
        assert reason == "throttled"
        assert 80 <= wait <= 95, f"expected ~90s cooldown, got {wait}"

    def test_probe_reopens_after_recovery(self):
        mgr = self._mgr()
        for _ in range(3):
            with mgr.acquire("instagram") as slot:
                slot.failed("timeout")
        st = mgr._state("instagram")
        # Wind the clock to just inside the probe window.
        st.open_until = time.time() + 10
        available, _, reason = mgr.availability("instagram")
        assert available is True and reason == "probe"
        with mgr.acquire("instagram") as slot:
            slot.ok()
        assert mgr.availability("instagram")[0] is True
        assert mgr.snapshot()["instagram"]["state"] == "closed"


# ─── Backpressure and deduplication at scale ─────────────────────────────────

class TestSaveStorm:
    def _mock_platform(self, monkeypatch, calls):
        """Count every would-be external call without making one."""
        from api.pipeline import acquire

        def _meta(url, *a, **k):
            calls.append(("metadata", url))
            return acquire.AcquisitionResult(
                True, "metadata", bytes_moved=2048,
                metadata={"title": f"Video {url[-6:]}", "duration": 60,
                          "uploader": "creator"})

        def _caps(url, *a, **k):
            calls.append(("captions", url))
            return acquire.AcquisitionResult(False, "metadata", error="no captions")

        def _dl(url, *a, **k):
            calls.append(("download", url))
            return acquire.AcquisitionResult(False, "audio", error="mocked")

        monkeypatch.setattr(acquire, "fetch_metadata", _meta)
        monkeypatch.setattr(acquire, "fetch_captions_via_ytdlp", _caps)
        monkeypatch.setattr(acquire, "fetch_native_captions", _caps)
        monkeypatch.setattr(acquire, "download_audio", _dl)
        monkeypatch.setattr(acquire, "download_video_lowres", _dl)

    @pytest.mark.parametrize("n_saves,n_unique", [(100, 20), (1000, 200), (5000, 250)])
    def test_saves_do_not_map_one_to_one_onto_jobs(self, clean_db, monkeypatch,
                                                   n_saves, n_unique):
        """The core economic property: N saves of M unique items => M jobs."""
        from api.services.save import create_save
        db = clean_db
        install_fake_router(monkeypatch, FakeRouter())

        users = [make_user(db, f"storm{i}@x.com") for i in range(20)]
        video_ids = [f"VID{i:08d}" for i in range(n_unique)]

        created = 0
        for i in range(n_saves):
            vid = video_ids[i % n_unique]
            user = users[i % len(users)]
            # Rotate through URL shapes so dedup is genuinely exercised.
            shape = i % 4
            url = {
                0: f"https://www.youtube.com/watch?v={vid}",
                1: f"https://youtu.be/{vid}?si=x{i}",
                2: f"https://m.youtube.com/watch?v={vid}&feature=share",
                3: f"https://www.youtube.com/embed/{vid}",
            }[shape]
            try:
                create_save(db, url=url, user_id=user.id)
                created += 1
            except Exception:
                pass  # duplicate for this user — expected

        canonical = db.query(CanonicalContent).count()
        jobs = db.query(Job).filter(Job.kind == "content.process").count()

        assert canonical == n_unique, \
            f"{n_saves} saves produced {canonical} canonical rows, expected {n_unique}"
        assert jobs <= n_unique, \
            f"{n_saves} saves produced {jobs} jobs, expected at most {n_unique}"
        assert created > 0

    def test_save_makes_no_external_call(self, clean_db, monkeypatch):
        """The API must accept a save without touching the network."""
        from api.services.save import create_save
        db = clean_db
        install_fake_router(monkeypatch, FakeRouter())
        calls: list = []
        self._mock_platform(monkeypatch, calls)

        user = make_user(db, "fast@x.com")
        for i in range(50):
            create_save(db, url=f"https://www.youtube.com/watch?v=FAST{i:07d}",
                        user_id=user.id)

        assert calls == [], f"save path made {len(calls)} external calls"
        assert db.query(Bookmark).count() == 50

        # Every save landed, and none of them failed. Fifty YouTube videos at
        # 2 units each is well past a Free account's 30-unit monthly allowance,
        # so the later ones are stored with their AI processing withheld — which
        # is the point: running out of AI budget costs you the *analysis*, never
        # the save. Nothing is rejected, nothing is deleted, nothing is FAILED.
        states = {b.processing_state for b in db.query(Bookmark).all()}
        assert states <= {"queued", "limit_reached"}, states
        assert "failed" not in states
        assert all(b.url for b in db.query(Bookmark).all())

    def test_save_latency_is_flat(self, clean_db, monkeypatch):
        from api.services.save import create_save
        db = clean_db
        install_fake_router(monkeypatch, FakeRouter())
        calls: list = []
        self._mock_platform(monkeypatch, calls)
        user = make_user(db, "latency@x.com")

        start = time.monotonic()
        for i in range(200):
            create_save(db, url=f"https://www.youtube.com/watch?v=LAT{i:08d}",
                        user_id=user.id)
        per_save_ms = (time.monotonic() - start) * 1000 / 200
        assert per_save_ms < 50, f"{per_save_ms:.1f}ms per save is too slow for an API path"

    def test_viral_content_is_processed_once(self, clean_db, monkeypatch):
        """1,000 users save the same TikTok -> one processing run."""
        from api.jobs import drain
        from api.services.save import create_save
        from api.pipeline import ingest
        db = clean_db
        install_fake_router(monkeypatch, FakeRouter())
        calls: list = []
        self._mock_platform(monkeypatch, calls)

        viral = "https://www.tiktok.com/@creator/video/7234567890123456789"
        users = [make_user(db, f"viral{i}@x.com") for i in range(50)]
        for i, u in enumerate(users):
            create_save(db, url=f"{viral}?is_from_webapp=1&sender_device={i}",
                        user_id=u.id)

        assert db.query(CanonicalContent).count() == 1
        assert db.query(Job).filter(Job.kind == "content.process").count() == 1

        drain(limit=10)
        metadata_calls = [c for c in calls if c[0] == "metadata"]
        assert len(metadata_calls) <= 1, \
            f"viral content fetched {len(metadata_calls)} times"

        # Every user still has their own private save.
        assert db.query(Bookmark).count() == 50
        assert len({b.user_id for b in db.query(Bookmark).all()}) == 50


# ─── Queue behaviour under throttling ────────────────────────────────────────

class TestQueueUnderThrottle:
    def test_throttled_platform_is_skipped_not_failed(self, clean_db, monkeypatch):
        """Jobs for a throttled platform stay queued; others still run."""
        from api.jobs import claim_next, enqueue
        db = clean_db

        enqueue(db, "content.process", {"canonical_id": 1},
                idempotency_key="k-ig", platform="instagram")
        enqueue(db, "content.process", {"canonical_id": 2},
                idempotency_key="k-yt", platform="youtube")

        # Instagram is blocked; YouTube is fine.
        job = claim_next(db, skip_platforms={"instagram": 300.0})
        assert job is not None
        assert job.platform == "youtube", "should have skipped the Instagram job"

        # Nothing else is claimable while Instagram stays blocked.
        assert claim_next(db, skip_platforms={"instagram": 300.0}) is None

        ig = db.query(Job).filter(Job.platform == "instagram").first()
        assert ig.state == "queued", "throttled job must not be failed"
        assert ig.attempts == 0, "throttled job must not burn an attempt"

    def test_platform_unavailable_parks_without_burning_attempts(self, clean_db):
        from api.jobs import HANDLERS, claim_next, enqueue, execute
        db = clean_db

        def handler(payload, session):
            raise PlatformUnavailable("instagram", "circuit_open", 120.0)

        HANDLERS["test.parked"] = handler
        job = enqueue(db, "test.parked", {}, idempotency_key="parked-1",
                      platform="instagram", max_attempts=3)

        claimed = claim_next(db, skip_platforms={})
        assert claimed is not None
        assert claimed.attempts == 1
        execute(db, claimed)

        db.refresh(job)
        assert job.state == "queued", "parked job must remain queued"
        assert job.attempts == 0, "parking must return the attempt"
        assert "parked" in (job.last_error or "")

    def test_worker_crash_recovers_via_lease(self, clean_db):
        """A crashed worker's in-flight job is reclaimed, not lost."""
        from api.config import JOB_LEASE_SECONDS
        from api.jobs import _now, claim_next, enqueue
        db = clean_db

        job = enqueue(db, "content.process", {"canonical_id": 9},
                      idempotency_key="crash-1", platform="youtube")
        claimed = claim_next(db, skip_platforms={})
        assert claimed.state == "running"

        # Simulate the worker dying: lease goes stale, nothing released it.
        claimed.locked_at = _now() - timedelta(seconds=JOB_LEASE_SECONDS + 60)
        db.commit()

        reclaimed = claim_next(db, skip_platforms={})
        assert reclaimed is not None and reclaimed.id == job.id
        assert reclaimed.attempts == 2, "reclaim should count as a new attempt"

    def test_duplicate_enqueue_during_storm_is_one_job(self, clean_db):
        from api.jobs import enqueue
        db = clean_db
        for _ in range(500):
            enqueue(db, "content.process", {"canonical_id": 42},
                    idempotency_key="content.process:42", platform="youtube")
        assert db.query(Job).count() == 1


# ─── Graceful degradation ────────────────────────────────────────────────────

class TestDegradation:
    def test_partial_failure_preserves_the_save(self, clean_db, monkeypatch):
        """Metadata succeeds, everything else fails -> content still usable."""
        from api.pipeline import acquire, ingest
        db = clean_db
        install_fake_router(monkeypatch, FakeRouter())

        cc = CanonicalContent(
            content_key="tiktok:PARTIAL1", platform="tiktok",
            platform_content_id="PARTIAL1",
            canonical_url="https://tiktok.com/@i/video/PARTIAL1",
            media_kind="video", processing_state="queued", pipeline_version=1)
        db.add(cc)
        db.commit()

        monkeypatch.setattr(acquire, "fetch_metadata", lambda *a, **k:
                            acquire.AcquisitionResult(
                                True, "metadata", bytes_moved=1024,
                                metadata={"title": "Recipe", "duration": 30}))
        monkeypatch.setattr(acquire, "fetch_captions_via_ytdlp", lambda *a, **k:
                            acquire.AcquisitionResult(False, "metadata", error="none"))
        monkeypatch.setattr(acquire, "download_audio", lambda *a, **k:
                            acquire.AcquisitionResult(False, "audio", error="blocked"))
        monkeypatch.setattr(acquire, "download_video_lowres", lambda *a, **k:
                            acquire.AcquisitionResult(False, "video", error="blocked"))

        result = ingest.process_content(cc.id, db)
        db.refresh(cc)

        assert result["ok"] is True
        assert cc.title == "Recipe", "acquired metadata must be preserved"
        assert cc.processing_state in ("partial", "ready")
        assert cc.processing_state != "failed"

    def test_user_never_sees_a_platform_error(self, clean_db, monkeypatch):
        """A throttled platform still returns a successful save to the user."""
        from api.platform_budget import get_manager
        from api.services.save import create_save
        db = clean_db
        install_fake_router(monkeypatch, FakeRouter())

        mgr = get_manager()
        st = mgr._state("instagram")
        st.open_until = time.time() + 600      # Instagram fully down

        user = make_user(db, "degraded@x.com")
        result = create_save(
            db, url="https://www.instagram.com/reel/DPMnXPeEoIi/", user_id=user.id)

        assert result["id"] > 0
        assert result["processing_state"] == "queued"
        assert db.query(Bookmark).count() == 1, "the save must survive"
        st.open_until = 0.0

    def test_user_data_never_leaks_across_users(self, clean_db, monkeypatch):
        """Shared canonical content, strictly private user data."""
        from api.services.save import create_save
        db = clean_db
        install_fake_router(monkeypatch, FakeRouter())

        alice = make_user(db, "alice-priv@x.com")
        bob = make_user(db, "bob-priv@x.com")
        url = "https://www.youtube.com/watch?v=SHARED00001"

        a = create_save(db, url=url, user_id=alice.id, note="alice private note")
        b = create_save(db, url=url + "&feature=share", user_id=bob.id,
                        note="bob private note")

        assert a["canonical_id"] == b["canonical_id"], "content should be shared"
        assert a["id"] != b["id"], "saves must be distinct"

        alice_bm = db.query(Bookmark).get(a["id"])
        bob_bm = db.query(Bookmark).get(b["id"])
        assert alice_bm.note == "alice private note"
        assert bob_bm.note == "bob private note"
        assert alice_bm.user_id != bob_bm.user_id


# ─── Cache versioning ────────────────────────────────────────────────────────

class TestCacheVersioning:
    def test_upgrade_plan_targets_only_stale_content(self, clean_db):
        from api.content.upgrade import plan_upgrade
        db = clean_db
        for i, version in enumerate([1, 1, 2, 2, 2]):
            db.add(CanonicalContent(
                content_key=f"youtube:VER{i}", platform="youtube",
                canonical_url=f"https://youtube.com/watch?v=VER{i}",
                media_kind="video", processing_state="ready",
                pipeline_version=version))
        db.commit()

        plan = plan_upgrade(db, limit=10, target_version=2)
        assert plan["eligible_total"] == 2, "only v1 content is stale"
        assert all(i["from_version"] == 1 for i in plan["items"])

    def test_upgrade_is_batched_and_idempotent(self, clean_db):
        from api.content.upgrade import plan_upgrade, queue_upgrade
        db = clean_db
        for i in range(20):
            db.add(CanonicalContent(
                content_key=f"tiktok:UP{i}", platform="tiktok",
                canonical_url=f"https://tiktok.com/@i/video/UP{i}",
                media_kind="video", processing_state="ready", pipeline_version=1))
        db.commit()

        plan = plan_upgrade(db, limit=5, target_version=2)
        assert plan["batch_size"] == 5, "batch must respect the limit"

        first = queue_upgrade(db, plan)
        assert first["queued"] == 5
        assert first["remaining"] == 15

        # Re-running the same plan must not duplicate jobs.
        queue_upgrade(db, plan)
        assert db.query(Job).count() == 5

    def test_current_content_is_never_reprocessed(self, clean_db):
        from api.config import PIPELINE_VERSION
        from api.content.upgrade import plan_upgrade
        db = clean_db
        db.add(CanonicalContent(
            content_key="youtube:CURRENT", platform="youtube",
            canonical_url="https://youtube.com/watch?v=CURRENT",
            media_kind="video", processing_state="ready",
            pipeline_version=PIPELINE_VERSION))
        db.commit()
        assert plan_upgrade(db)["eligible_total"] == 0


# ─── Telemetry ───────────────────────────────────────────────────────────────

class TestScaleTelemetry:
    def test_dedup_ratio_is_reported(self, clean_db, monkeypatch):
        from api.ai import telemetry
        from api.services.save import create_save
        db = clean_db
        install_fake_router(monkeypatch, FakeRouter())

        users = [make_user(db, f"econ{i}@x.com") for i in range(10)]
        for u_idx, user in enumerate(users):
            for v_idx in range(10):          # every user saves all 10 videos
                try:
                    create_save(
                        db, url=f"https://www.youtube.com/watch?v=ECON{v_idx:07d}",
                        user_id=user.id)
                except Exception:
                    pass

        econ = telemetry.dedup_economics(db, days=1)
        assert econ["unique_content"] == 10
        assert econ["user_saves"] > econ["unique_content"]
        assert econ["dedup_ratio"] > 1.0, "dedup ratio should exceed 1"
        assert econ["saves_avoided"] > 0

    def test_queue_health_reports_depth_and_age(self, clean_db):
        from api.ai import telemetry
        from api.jobs import enqueue
        db = clean_db
        for i in range(5):
            enqueue(db, "content.process", {"canonical_id": i},
                    idempotency_key=f"depth-{i}", platform="youtube")
        health = telemetry.queue_health(db)
        assert health["depth"] == 5
        assert health["oldest_queued_age_s"] is not None
        assert any(r["platform"] == "youtube" for r in health["by_platform"])

    def test_platform_health_exposes_rates(self, clean_db):
        from api.ai import telemetry
        from api.platform_budget import get_manager
        db = clean_db
        mgr = get_manager()
        mgr.reset()
        with mgr.acquire("youtube") as slot:
            slot.ok(bytes_moved=1000)
        with mgr.acquire("youtube") as slot:
            slot.failed("429 Too Many Requests")

        health = telemetry.platform_health(db, days=1)
        yt = health["platforms"]["youtube"]
        assert yt["requests"] == 2
        assert yt["ok"] == 1
        assert yt["rate_limited"] == 1
        assert yt["success_rate"] == 0.5
        assert yt["p50_latency_ms"] >= 0
        mgr.reset()
