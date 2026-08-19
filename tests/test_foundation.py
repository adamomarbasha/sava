"""Behavioural tests for the intelligence foundation.

These assert the properties the architecture exists to guarantee:
  * the same content saved by two users is processed once,
  * URL variants collapse to one canonical item,
  * AI questions never re-acquire media,
  * long content is fully indexed rather than truncated,
  * failures retry with backoff instead of duplicating expensive work.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from conftest import FakeRouter, make_bookmark, make_user

from api.content.identity import resolve_identity, strip_tracking
from api.models import (
    Bookmark, CanonicalContent, ContentChunk, ContentEmbedding, ContentFrame,
    ContentTranscript, ContentUnderstanding, Job, UsageEvent,
)


# ─── Identity & deduplication ────────────────────────────────────────────────

class TestIdentity:
    @pytest.mark.parametrize("urls,expected", [
        ([
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ?si=abc",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
            "https://youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
        ], "youtube:dQw4w9WgXcQ"),
        ([
            "https://www.tiktok.com/@a/video/7234567890123456789",
            "https://www.tiktok.com/@b/video/7234567890123456789?is_from_webapp=1",
            "https://m.tiktok.com/v/7234567890123456789",
        ], "tiktok:7234567890123456789"),
        ([
            "https://www.instagram.com/reel/DPMnXPeEoIi/",
            "https://instagram.com/p/DPMnXPeEoIi/?igshid=x",
        ], "instagram:DPMnXPeEoIi"),
    ])
    def test_variants_collapse(self, urls, expected):
        assert {resolve_identity(u).content_key for u in urls} == {expected}

    def test_distinct_content_does_not_collide(self):
        a = resolve_identity("https://youtu.be/dQw4w9WgXcQ").content_key
        b = resolve_identity("https://youtu.be/aQw4w9WgXcZ").content_key
        assert a != b

    def test_short_link_marked_unresolvable(self):
        ident = resolve_identity("https://vm.tiktok.com/ZMhqK1abc/")
        assert ident.platform == "tiktok"
        assert ident.is_resolvable is False

    def test_tracking_params_stripped(self):
        out = strip_tracking(
            "https://www.tiktok.com/@u/video/123?is_from_webapp=1&utm_source=x")
        assert "utm_source" not in out and "is_from_webapp" not in out


class TestCanonicalReuse:
    def test_two_users_share_one_canonical_row(self, clean_db):
        from api.pipeline.ingest import resolve_or_create_canonical
        db = clean_db
        alice, bob = make_user(db, "a@x.com"), make_user(db, "b@x.com")

        url_a = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        url_b = "https://youtu.be/dQw4w9WgXcQ?si=different"

        bm_a = make_bookmark(db, alice.id, url_a)
        bm_b = make_bookmark(db, bob.id, url_b)

        cc_a, created_a = resolve_or_create_canonical(db, bm_a.url, "youtube")
        cc_b, created_b = resolve_or_create_canonical(db, bm_b.url, "youtube")

        assert created_a is True, "first save should create canonical content"
        assert created_b is False, "second save must REUSE, not create"
        assert cc_a.id == cc_b.id
        assert db.query(CanonicalContent).count() == 1

    def test_processing_is_skipped_when_already_ready(self, clean_db, monkeypatch):
        from api.pipeline import ingest
        db = clean_db
        cc = CanonicalContent(
            content_key="youtube:ALREADYDONE", platform="youtube",
            platform_content_id="ALREADYDONE",
            canonical_url="https://youtube.com/watch?v=ALREADYDONE",
            media_kind="video", processing_state="ready", processing_level=4,
            pipeline_version=1, title="Already processed",
        )
        db.add(cc)
        db.commit()

        calls = {"metadata": 0}

        def _boom(*a, **k):
            calls["metadata"] += 1
            raise AssertionError("must not re-acquire ready content")

        monkeypatch.setattr(ingest.acquire, "fetch_metadata", _boom)
        result = ingest.process_content(cc.id, db)
        assert result["ok"] is True
        assert result.get("cache_hit") is True
        assert calls["metadata"] == 0


# ─── Chunking: no silent truncation ──────────────────────────────────────────

class TestChunking:
    def _long_transcript(self, seconds=1800):
        import random
        random.seed(3)
        words = ("the chef adds olive oil garlic tomatoes basil to the pan at four "
                 "hundred degrees for twenty minutes").split()
        segs, t = [], 0.0
        while t < seconds:
            segs.append({"text": " ".join(random.choice(words) for _ in range(10)),
                         "start": t, "duration": 4.0})
            t += 4.0
        return segs

    def test_long_video_fully_covered(self):
        from api.pipeline.chunking import MAX_EMBED_TOKENS, chunk_transcript
        segs = self._long_transcript(1800)
        chunks = chunk_transcript(segs)
        assert chunks
        assert max(c.token_count for c in chunks) <= MAX_EMBED_TOKENS
        assert chunks[0].start_s == 0
        assert chunks[-1].end_s >= 1790, "tail of a 30-minute video was dropped"

    def test_no_content_is_discarded(self):
        from api.pipeline.chunking import chunk_transcript
        segs = self._long_transcript(600)
        joined = " ".join(c.text for c in chunk_transcript(segs))
        for w in {w for s in segs for w in s["text"].split()}:
            assert w in joined

    def test_oversized_segment_is_split_not_truncated(self):
        from api.pipeline.chunking import MAX_EMBED_TOKENS, chunk_transcript
        chunks = chunk_transcript([{"text": "word " * 9000, "start": 0, "duration": 10}])
        assert len(chunks) > 1
        assert max(c.token_count for c in chunks) <= MAX_EMBED_TOKENS
        assert sum(len(c.text.split()) for c in chunks) == 9000

    def test_chunks_overlap(self):
        from api.pipeline.chunking import chunk_transcript
        chunks = chunk_transcript(self._long_transcript(300))
        overlaps = sum(1 for a, b in zip(chunks, chunks[1:])
                       if b.start_s is not None and a.end_s is not None
                       and b.start_s < a.end_s)
        assert overlaps > len(chunks) // 2


# ─── Persistence: questions must not re-acquire media ────────────────────────

class TestNoReacquisition:
    def _seed(self, db, monkeypatch, *, platform="tiktok"):
        from conftest import install_fake_router

        user = make_user(db, f"noreacq_{platform}@x.com")
        cc = CanonicalContent(
            content_key=f"{platform}:REACQ1", platform=platform,
            platform_content_id="REACQ1",
            canonical_url=f"https://{platform}.com/video/REACQ1",
            media_kind="video", processing_state="ready", processing_level=4,
            pipeline_version=1, title="Vodka pasta recipe",
            creator_name="chef", content_type="recipe", duration_seconds=45,
        )
        db.add(cc)
        db.commit()
        db.refresh(cc)

        segments = [
            {"text": "preheat the oven to 400 degrees", "start": 0, "duration": 5},
            {"text": "add one cup of heavy cream and san marzano tomatoes",
             "start": 5, "duration": 6},
            {"text": "bake for twenty five minutes until bubbling", "start": 11,
             "duration": 6},
        ]
        db.add(ContentTranscript(
            canonical_content_id=cc.id, source="asr", lang="en",
            text=" ".join(s["text"] for s in segments),
            segments=json.dumps(segments), provider="local-whisper",
            audio_seconds=17, is_complete=True))
        db.add(ContentUnderstanding(
            canonical_content_id=cc.id, schema_version=1, content_type="recipe",
            tl_dr="A vodka pasta recipe baked at 400 degrees.",
            key_points=json.dumps(["bake at 400", "use san marzano tomatoes"]),
            topics=json.dumps(["pasta", "recipe"]),
            entities=json.dumps({"ingredients": ["heavy cream", "san marzano tomatoes"]}),
            typed_data=json.dumps({"recipe": {"temperature": "400 degrees"}}),
            chapters="[]", sources_used=json.dumps(["transcript"])))
        db.commit()

        fake = install_fake_router(
            monkeypatch, FakeRouter(completion_text="The oven was set to 400 degrees."))

        # Embed the chunks so retrieval has something to score.
        from api.vectors import to_storage
        for i, seg in enumerate(segments):
            vec = fake.embed([seg["text"]]).vectors[0]
            db.add(ContentChunk(
                canonical_content_id=cc.id, chunk_index=i, modality="transcript",
                text=seg["text"], start_s=int(seg["start"]),
                end_s=int(seg["start"] + seg["duration"]), token_count=10,
                embedding=to_storage(vec), embed_model="fake", embed_dim=fake.dim))
        db.commit()

        bm = make_bookmark(db, user.id, cc.canonical_url, platform=platform)
        bm.canonical_content_id = cc.id
        db.commit()
        return user, cc, bm, fake

    def test_ask_this_never_touches_the_network(self, clean_db, monkeypatch):
        from api.pipeline import acquire
        from api.services import intelligence
        db = clean_db
        user, cc, bm, fake = self._seed(db, monkeypatch)

        calls = {"n": 0}

        def _forbidden(*a, **k):
            calls["n"] += 1
            raise AssertionError("Ask This must not re-acquire media")

        for fn in ("download_audio", "download_video_lowres", "fetch_metadata",
                   "fetch_native_captions", "transcribe_audio"):
            monkeypatch.setattr(acquire, fn, _forbidden)

        for _ in range(5):
            res = intelligence.ask_this(db, bm, "What temperature was the oven?",
                                        user_id=user.id)
            assert res["ok"] is True
            assert res["grounded_in"] > 0

        assert calls["n"] == 0
        assert res["citations"], "answer should cite transcript moments"

    def test_summary_is_cached_after_first_generation(self, clean_db, monkeypatch):
        from api.services import intelligence
        db = clean_db
        user, cc, bm, fake = self._seed(db, monkeypatch)

        first = intelligence.get_or_create_summary(db, bm, user_id=user.id)
        assert first["available"] is True
        assert first["cached"] is True, "understanding was pre-seeded"
        before = fake.complete_calls

        for _ in range(3):
            again = intelligence.get_or_create_summary(db, bm, user_id=user.id)
            assert again["cached"] is True
        assert fake.complete_calls == before, "cached summary must not call a model"

        hits = (db.query(UsageEvent)
                .filter(UsageEvent.operation == "summary.read",
                        UsageEvent.cache_hit.is_(True)).count())
        assert hits >= 3


# ─── Retrieval ───────────────────────────────────────────────────────────────

class TestRetrieval:
    def _library(self, db, monkeypatch, n_saves=60):
        from conftest import install_fake_router
        from api.vectors import to_storage

        fake = install_fake_router(monkeypatch, FakeRouter())

        user = make_user(db, "lib@x.com")
        themes = [
            ("vodka pasta recipe san marzano tomatoes cream", "recipe", "pasta"),
            ("best ramen restaurant in new york city lower east side", "restaurant", "nyc"),
            ("bmw m3 versus m4 track comparison review", "product", "cars"),
            ("kai cenat mafiathon stream highlights clip", "entertainment", "streams"),
            ("tokyo japan travel itinerary shibuya shrine", "travel", "japan"),
            ("python api scraping tutorial requests pagination", "coding", "python"),
        ]
        for i in range(n_saves):
            text, ctype, topic = themes[i % len(themes)]
            cc = CanonicalContent(
                content_key=f"youtube:VID{i:05d}", platform="youtube",
                platform_content_id=f"VID{i:05d}",
                canonical_url=f"https://youtube.com/watch?v=VID{i:05d}",
                media_kind="video", processing_state="ready", processing_level=4,
                pipeline_version=1, title=f"{text} episode {i}",
                creator_name=f"creator{i % 7}", content_type=ctype,
                duration_seconds=300)
            db.add(cc)
            db.commit()
            db.refresh(cc)
            db.add(ContentUnderstanding(
                canonical_content_id=cc.id, schema_version=1, content_type=ctype,
                tl_dr=f"About {text}.", key_points=json.dumps([text]),
                topics=json.dumps([topic]), entities="{}", typed_data="{}",
                chapters="[]", sources_used="[]"))
            vec = fake.embed([f"{text} {topic}"]).vectors[0]
            db.add(ContentEmbedding(canonical_content_id=cc.id,
                                    embedding=to_storage(vec),
                                    model="fake", dim=fake.dim))
            db.add(ContentChunk(
                canonical_content_id=cc.id, chunk_index=0, modality="transcript",
                text=text, start_s=0, end_s=30, token_count=12,
                embedding=to_storage(vec), embed_model="fake", embed_dim=fake.dim))
            db.commit()
            bm = make_bookmark(db, user.id, cc.canonical_url, title=cc.title)
            bm.canonical_content_id = cc.id
            db.commit()
        return user, fake

    def test_semantic_search_finds_the_right_theme(self, clean_db, monkeypatch):
        from api.services import retrieval
        db = clean_db
        user, _ = self._library(db, monkeypatch)
        results = retrieval.search_library(db, user.id, "pasta recipe tomatoes", limit=10)
        assert results
        assert results[0].content_type == "recipe", \
            f"top hit should be a recipe, got {results[0].content_type}"

    def test_search_is_scoped_to_the_user(self, clean_db, monkeypatch):
        from api.services import retrieval
        db = clean_db
        user, _ = self._library(db, monkeypatch)
        stranger = make_user(db, "stranger@x.com")
        assert retrieval.search_library(db, stranger.id, "pasta recipe", limit=10) == []

    def test_search_stays_fast_on_a_large_library(self, clean_db, monkeypatch):
        import time
        from api.services import retrieval
        db = clean_db
        user, _ = self._library(db, monkeypatch, n_saves=600)
        start = time.monotonic()
        results = retrieval.search_library(db, user.id, "tokyo travel itinerary", limit=20)
        elapsed = (time.monotonic() - start) * 1000
        assert results
        assert elapsed < 2500, f"search took {elapsed:.0f}ms on 600 saves"

    def test_related_saves_use_no_model(self, clean_db, monkeypatch):
        from api.services import retrieval
        db = clean_db
        user, fake = self._library(db, monkeypatch)
        cc = db.query(CanonicalContent).first()
        before = fake.complete_calls
        related = retrieval.related_saves(db, user.id, cc.id, limit=5)
        assert related
        assert all(r.canonical_id != cc.id for r in related)
        assert fake.complete_calls == before, "related saves must not call an LLM"

    def test_ask_sava_only_sends_retrieved_context(self, clean_db, monkeypatch):
        from api.services import intelligence
        db = clean_db
        user, fake = self._library(db, monkeypatch, n_saves=120)

        captured = {}
        original = fake.complete

        def spy(task, **kw):
            captured["prompt"] = kw.get("prompt", "")
            return original(task, **kw)

        fake.complete = spy
        res = intelligence.ask_sava(db, user.id, "What ramen restaurants have I saved?")
        assert res["ok"] is True
        assert res["grounded_in"] <= 10, "must not dump the library into context"
        assert len(res["sources"]) <= 10
        assert len(captured["prompt"]) < 20000


# ─── Collections ─────────────────────────────────────────────────────────────

class TestCollections:
    def test_manual_collection_matches_by_name(self, clean_db, monkeypatch):
        from api.services import collections as cs
        db = clean_db
        user, fake = TestRetrieval()._library(db, monkeypatch, n_saves=36)
        coll = cs.create_collection(db, user.id, "Kai Cenat")
        assert coll.id
        suggestions = cs.suggest_for_collection(db, coll.id, limit=20)
        assert suggestions, "should find the streaming saves"
        titles = " ".join((s["title"] or "").lower() for s in suggestions)
        assert "kai cenat" in titles

    def test_auto_collections_reflect_actual_saves(self, clean_db, monkeypatch):
        from api.services import collections as cs
        db = clean_db
        user, fake = TestRetrieval()._library(db, monkeypatch, n_saves=48)
        fake.completion_text = '{"name":"Pasta Night","description":"Recipes"}'
        stats = cs.rebuild_auto_collections(db, user.id)
        assert stats["status"] == "ok"
        assert stats["clusters"] >= 2, f"expected multiple clusters, got {stats}"

    def test_no_auto_collections_for_a_tiny_library(self, clean_db, monkeypatch):
        from api.services import collections as cs
        db = clean_db
        user, _ = TestRetrieval()._library(db, monkeypatch, n_saves=4)
        stats = cs.rebuild_auto_collections(db, user.id)
        assert stats["status"] == "not_enough_saves"


# ─── Job queue ───────────────────────────────────────────────────────────────

class TestJobs:
    def test_enqueue_is_idempotent(self, clean_db):
        from api.jobs import enqueue
        db = clean_db
        a = enqueue(db, "content.process", {"canonical_id": 42},
                    idempotency_key="content.process:42")
        b = enqueue(db, "content.process", {"canonical_id": 42},
                    idempotency_key="content.process:42")
        assert a.id == b.id
        assert db.query(Job).filter(Job.kind == "content.process").count() == 1

    def test_failure_retries_with_backoff_then_dies(self, clean_db):
        from api.jobs import HANDLERS, claim_next, enqueue, execute
        db = clean_db
        attempts = {"n": 0}

        def flaky(payload, session):
            attempts["n"] += 1
            raise RuntimeError("transient")

        HANDLERS["test.flaky"] = flaky
        job = enqueue(db, "test.flaky", {}, idempotency_key="test.flaky:1",
                      max_attempts=3)

        for _ in range(3):
            job.run_after = job.created_at
            db.commit()
            claimed = claim_next(db)
            assert claimed is not None
            execute(db, claimed)

        db.refresh(job)
        assert attempts["n"] == 3
        assert job.state == "dead"
        assert "transient" in (job.last_error or "")

    def test_retry_does_not_duplicate_expensive_work(self, clean_db, monkeypatch):
        """A retried job must resume, not repeat completed stages."""
        from api.pipeline import ingest
        db = clean_db
        cc = CanonicalContent(
            content_key="youtube:RETRY1", platform="youtube",
            platform_content_id="RETRY1",
            canonical_url="https://youtube.com/watch?v=RETRY1",
            media_kind="video", processing_state="ready", processing_level=4,
            pipeline_version=1, title="Done")
        db.add(cc)
        db.commit()
        counter = {"n": 0}
        monkeypatch.setattr(ingest.acquire, "fetch_metadata",
                            lambda *a, **k: counter.__setitem__("n", counter["n"] + 1))
        for _ in range(3):
            ingest.process_content(cc.id, db)
        assert counter["n"] == 0


# ─── Telemetry ───────────────────────────────────────────────────────────────

class TestTelemetry:
    def test_events_are_recorded_and_summarised(self, clean_db):
        from api.ai import telemetry
        db = clean_db
        user = make_user(db, "tel@x.com")
        telemetry.record(db, operation="asr", user_id=user.id, platform="tiktok",
                         audio_seconds=45, estimated_usd=0.0005, proxy_bytes=2_500_000)
        telemetry.record(db, operation="summary.generate", user_id=user.id,
                         platform="tiktok", input_tokens=800, output_tokens=200,
                         estimated_usd=0.0009)
        telemetry.record(db, operation="content.cache_hit", user_id=user.id,
                         platform="tiktok", cache_hit=True)

        s = telemetry.summarize(db, user_id=user.id, days=1)
        assert s["events"] == 3
        assert s["estimated_usd"] > 0
        assert s["audio_seconds"] == 45
        assert s["proxy_bytes"] == 2_500_000
        assert s["proxy_usd"] > 0
        assert 0 < s["cache_hit_rate"] <= 1
        assert {r["operation"] for r in s["by_operation"]} == {
            "asr", "summary.generate", "content.cache_hit"}


# ─── Frame selection ─────────────────────────────────────────────────────────

class TestFrameSelection:
    def test_short_clips_get_few_frames(self, monkeypatch):
        from api.pipeline import frames as fm
        monkeypatch.setattr(fm, "_scene_timestamps", lambda *a, **k: [])
        monkeypatch.setattr(fm, "ffmpeg_available", lambda: True)
        assert len(fm.select_timestamps("x.mp4", duration=8)) <= 3
        assert len(fm.select_timestamps("x.mp4", duration=25)) <= 5
        assert len(fm.select_timestamps("x.mp4", duration=600)) <= 8

    def test_scene_changes_are_preferred(self, monkeypatch):
        from api.pipeline import frames as fm
        scenes = [1.0, 3.5, 7.2, 11.0, 14.5]
        monkeypatch.setattr(fm, "_scene_timestamps", lambda *a, **k: scenes)
        monkeypatch.setattr(fm, "ffmpeg_available", lambda: True)
        picked = fm.select_timestamps("x.mp4", duration=20)
        assert all(p in scenes for p in picked)

    def test_duplicate_frames_are_dropped(self, tmp_path):
        from PIL import Image
        from api.pipeline.frames import Frame, deduplicate

        def textured(seed: int):
            """Structured image — a flat colour has no gradients, so dHash of
            one solid block is all zeros and every solid image looks identical."""
            img = Image.new("RGB", (64, 64))
            px = img.load()
            for y in range(64):
                for x in range(64):
                    px[x, y] = ((x * 4 + seed * 37) % 256,
                                (y * 4 + seed * 91) % 256,
                                ((x + y) * 2 + seed * 53) % 256)
            return img

        paths = []
        for i, seed in enumerate([1, 1, 9]):     # two identical, one different
            p = tmp_path / f"f{i}.jpg"
            textured(seed).save(p, quality=95)
            paths.append(str(p))
        kept = deduplicate([Frame(ts_ms=i * 1000, path=p) for i, p in enumerate(paths)])
        assert len(kept) == 2, "identical frames should collapse to one"


# ─── Platform strategy ───────────────────────────────────────────────────────

class TestPlatformStrategy:
    def test_youtube_does_not_use_vision_by_default(self):
        from api.pipeline.ingest import YOUTUBE
        assert YOUTUBE.try_native_captions is True
        assert YOUTUBE.wants_vision(visual_dependency=0.9, has_transcript=True,
                                    media_kind="video") is False

    def test_tiktok_escalates_to_vision_when_visual(self):
        from api.pipeline.ingest import TIKTOK
        assert TIKTOK.try_native_captions is False
        assert TIKTOK.wants_vision(visual_dependency=0.85, has_transcript=True,
                                   media_kind="video") is True
        assert TIKTOK.wants_vision(visual_dependency=0.2, has_transcript=True,
                                   media_kind="video") is False

    def test_silent_content_always_escalates(self):
        from api.pipeline.ingest import TIKTOK
        assert TIKTOK.wants_vision(visual_dependency=0.1, has_transcript=False,
                                   media_kind="video") is True

    def test_image_posts_require_vision(self):
        from api.pipeline.ingest import INSTAGRAM
        assert INSTAGRAM.wants_vision(visual_dependency=0.0, has_transcript=False,
                                      media_kind="carousel") is True
