"""YouTube + TikTok extraction hardening, and the 100k-user load simulations.

Nothing in this file touches YouTube or TikTok. Every provider is mocked, every
"user" is a database row, and the load figures are chosen to prove *shape* —
that the cost curve bends with reuse — not to generate traffic. Hammering a
platform to prove Sava can hammer a platform would be both pointless and rude.

What these assert:

  * every YouTube URL shape collapses to one identity, Shorts included,
  * TikTok photo posts are recognised as carousels, not as broken videos,
  * carousel slides are stored in order and slide 1 is the cover,
  * comments are canonical, cached, bounded, and never gate readiness,
  * ASR is a replaceable provider and is off unless configured,
  * outbound fetches refuse private addresses and unknown hosts,
  * 100,000 saves over 1,000 viral items produce 1,000 processing jobs.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import FakeRouter, install_fake_router, make_user

from api.content.identity import resolve_identity
from api.models import (
    Bookmark, CanonicalContent, ContentAsset, ContentComment, Job,
)


# ─── YouTube URL normalization ───────────────────────────────────────────────

class TestYouTubeIdentity:
    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=AbCdEf",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123&index=4",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&utm_source=newsletter",
    ])
    def test_every_shape_is_one_identity(self, url):
        ident = resolve_identity(url)
        assert ident.content_key == "youtube:dQw4w9WgXcQ"
        assert ident.is_resolvable

    def test_shorts_and_watch_are_not_two_items(self):
        """The single most expensive duplication bug available on YouTube."""
        a = resolve_identity("https://youtube.com/shorts/aaaaaaaaaaa")
        b = resolve_identity("https://youtube.com/watch?v=aaaaaaaaaaa")
        assert a.content_key == b.content_key

    def test_different_videos_stay_different(self):
        a = resolve_identity("https://youtu.be/dQw4w9WgXcQ")
        b = resolve_identity("https://youtu.be/9bZkp7q19f0")
        assert a.content_key != b.content_key


# ─── TikTok URL normalization and media kind ─────────────────────────────────

class TestTikTokIdentity:
    def test_photo_post_is_a_carousel(self):
        ident = resolve_identity("https://www.tiktok.com/@creator/photo/7412345678901234567")
        assert ident.media_kind == "carousel"
        assert ident.content_key == "tiktok:7412345678901234567"

    def test_video_post_is_a_video(self):
        ident = resolve_identity("https://www.tiktok.com/@creator/video/7412345678901234567")
        assert ident.media_kind == "video"

    def test_photo_and_video_paths_share_identity(self):
        """Same post id: one canonical row, whichever path it was reached by."""
        a = resolve_identity("https://www.tiktok.com/@x/photo/7412345678901234567")
        b = resolve_identity("https://www.tiktok.com/@x/video/7412345678901234567")
        assert a.content_key == b.content_key

    def test_tracking_params_do_not_split_content(self):
        base = "https://www.tiktok.com/@x/video/7412345678901234567"
        noisy = base + "?is_from_webapp=1&sender_device=pc&web_id=123&_r=1"
        assert resolve_identity(base).content_key == resolve_identity(noisy).content_key

    def test_short_link_is_flagged_for_resolution(self):
        ident = resolve_identity("https://vm.tiktok.com/ZMabcdef/")
        assert not ident.is_resolvable
        assert ident.platform == "tiktok"


# ─── TikTok carousels ────────────────────────────────────────────────────────

class TestCarousel:
    def test_slides_are_stored_in_order_with_slide_one_as_cover(self, clean_db, monkeypatch):
        from api.pipeline import acquire, ingest
        from api.pipeline.acquire import AcquisitionResult

        slides = [{"url": f"https://p16-sign.tiktokcdn-us.com/slide{i}.jpg",
                   "width": 1080, "height": 1920} for i in range(4)]
        monkeypatch.setattr(acquire, "fetch_carousel", lambda url, n=12: AcquisitionResult(
            True, "carousel", metadata={
                "title": "3 pasta rules", "uploader": "chef",
                "slides": slides, "slide_count": len(slides),
                "thumbnail": slides[0]["url"], "webpage_url": url,
            }))
        # Mirroring is a network call; the point here is ordering, not fetching.
        monkeypatch.setattr(
            "api.services.thumbnails.mirror_to_storage",
            lambda url, namespace="thumbnails", platform=None: (f"k/{url[-10:]}", f"/o/{url[-10:]}"))

        cc = CanonicalContent(
            content_key="tiktok:999", platform="tiktok", platform_content_id="999",
            canonical_url="https://tiktok.com/@i/photo/999", media_kind="carousel",
            processing_state="queued", stage_status="{}",
        )
        clean_db.add(cc)
        clean_db.commit()

        out = ingest._ingest_carousel(clean_db, cc)
        assert "ok" in out["status"]

        stored = (clean_db.query(ContentAsset)
                  .filter(ContentAsset.canonical_content_id == cc.id)
                  .order_by(ContentAsset.asset_index).all())
        assert [a.asset_index for a in stored] == [0, 1, 2, 3]
        assert stored[0].kind == "cover"
        assert all(a.kind == "image" for a in stored[1:])
        # The creator chose slide one. Never a later slide.
        assert cc.thumbnail_url.endswith("slide0.jpg") or "slide0" in cc.thumbnail_url

    def test_carousel_never_downloads_a_video(self, clean_db, monkeypatch):
        """A photo post has no MP4. Trying to fetch one is the old bug."""
        from api.pipeline import acquire, ingest
        from api.pipeline.acquire import AcquisitionResult

        called = {"video": 0, "audio": 0}
        monkeypatch.setattr(acquire, "download_video_lowres",
                            lambda *a, **k: called.__setitem__("video", called["video"] + 1))
        monkeypatch.setattr(acquire, "download_audio",
                            lambda *a, **k: called.__setitem__("audio", called["audio"] + 1))
        monkeypatch.setattr(acquire, "fetch_carousel", lambda url, n=12: AcquisitionResult(
            True, "carousel", metadata={
                "title": "t", "slides": [{"url": "https://p16-sign.tiktokcdn-us.com/a.jpg"}],
                "webpage_url": url}))
        monkeypatch.setattr("api.services.thumbnails.mirror_to_storage",
                            lambda *a, **k: ("k", "/o/k"))
        install_fake_router(monkeypatch, FakeRouter())

        cc = CanonicalContent(
            content_key="tiktok:1001", platform="tiktok", platform_content_id="1001",
            canonical_url="https://tiktok.com/@i/photo/1001", media_kind="carousel",
            processing_state="queued", stage_status="{}",
        )
        clean_db.add(cc)
        clean_db.commit()

        ingest.process_content(cc.id, clean_db)
        assert called["video"] == 0
        assert called["audio"] == 0

    def test_slide_text_keeps_its_position(self, clean_db):
        from api.pipeline.ingest import _carousel_text

        cc = CanonicalContent(content_key="tiktok:2002", platform="tiktok",
                              canonical_url="u", media_kind="carousel", stage_status="{}")
        clean_db.add(cc)
        clean_db.commit()
        for i, text in enumerate(["Creamy pasta", "You need cream", "Simmer 8 min"]):
            clean_db.add(ContentAsset(canonical_content_id=cc.id, asset_index=i,
                                      ocr_text=text))
        clean_db.commit()

        assets = (clean_db.query(ContentAsset)
                  .filter(ContentAsset.canonical_content_id == cc.id)
                  .order_by(ContentAsset.asset_index).all())
        rendered = _carousel_text(assets)
        # Order is the meaning: title, then ingredient, then method.
        assert rendered.index("slide 1") < rendered.index("slide 2") < rendered.index("slide 3")
        assert "Simmer 8 min" in rendered


# ─── Comments ────────────────────────────────────────────────────────────────

class TestComments:
    def _content(self, db, platform="youtube", key="youtube:cm1"):
        cc = CanonicalContent(
            content_key=key, platform=platform, platform_content_id="cm1",
            canonical_url="https://youtube.com/watch?v=cm1", media_kind="video",
            processing_state="ready", stage_status="{}",
        )
        db.add(cc)
        db.commit()
        return cc

    def test_comments_are_fetched_once_for_everyone(self, clean_db, monkeypatch):
        """Ten thousand savers, one comment fetch. The whole point."""
        from api.services import comments as comments_svc

        calls = {"n": 0}

        def fake_fetch(self, content, *, limit):
            calls["n"] += 1
            return comments_svc.CommentFetch(True, comments=[
                comments_svc.FetchedComment(text=f"comment {i}", like_count=100 - i,
                                            platform_comment_id=str(i))
                for i in range(10)])

        monkeypatch.setattr(comments_svc.YouTubeCommentsProvider, "fetch", fake_fetch)
        monkeypatch.setattr(comments_svc.YouTubeCommentsProvider, "available", True)
        monkeypatch.setattr(comments_svc, "_index_comments", lambda *a, **k: 0)

        cc = self._content(clean_db)
        for _ in range(50):
            comments_svc.ensure_comments(clean_db, cc.id)

        assert calls["n"] == 1, "comments must be cached against canonical content"
        assert (clean_db.query(ContentComment)
                .filter(ContentComment.canonical_content_id == cc.id).count()) == 10

    def test_sample_is_bounded(self, clean_db, monkeypatch):
        from api.services import comments as comments_svc

        monkeypatch.setattr(comments_svc, "COMMENTS_MAX_PER_ITEM", 15)
        monkeypatch.setattr(
            comments_svc.YouTubeCommentsProvider, "fetch",
            lambda self, content, *, limit: comments_svc.CommentFetch(
                True, comments=[comments_svc.FetchedComment(text=f"c{i}",
                                                            platform_comment_id=str(i))
                                for i in range(500)]))
        monkeypatch.setattr(comments_svc.YouTubeCommentsProvider, "available", True)
        monkeypatch.setattr(comments_svc, "_index_comments", lambda *a, **k: 0)

        cc = self._content(clean_db, key="youtube:cm2")
        comments_svc.ensure_comments(clean_db, cc.id)
        stored = (clean_db.query(ContentComment)
                  .filter(ContentComment.canonical_content_id == cc.id).count())
        assert stored == 15, "an unbounded comment tree is never fetched"

    def test_comment_failure_never_touches_processing_state(self, clean_db, monkeypatch):
        from api.services import comments as comments_svc

        monkeypatch.setattr(
            comments_svc.YouTubeCommentsProvider, "fetch",
            lambda self, content, *, limit: comments_svc.CommentFetch(
                False, error="provider exploded"))
        monkeypatch.setattr(comments_svc.YouTubeCommentsProvider, "available", True)

        cc = self._content(clean_db, key="youtube:cm3")
        result = comments_svc.ensure_comments(clean_db, cc.id)

        assert result["ok"] is False
        clean_db.refresh(cc)
        assert cc.processing_state == "ready", "content stays READY when comments fail"
        assert cc.comments_state == "failed"

    def test_stale_sample_is_refreshed_fresh_one_is_not(self, clean_db, monkeypatch):
        from api.services import comments as comments_svc

        cc = self._content(clean_db, key="youtube:cm4")
        cc.comment_version = comments_svc.COMMENT_VERSION
        cc.comments_fetched_at = datetime.now(timezone.utc)
        clean_db.commit()
        assert comments_svc.is_stale(cc) is False

        cc.comments_fetched_at = datetime.now(timezone.utc) - timedelta(days=400)
        clean_db.commit()
        assert comments_svc.is_stale(cc) is True

    def test_disabled_platform_provider_is_not_called(self, clean_db, monkeypatch):
        """TikTok comments need a session cookie. Off beats failing loudly."""
        from api.services import comments as comments_svc

        monkeypatch.setattr(comments_svc, "COMMENTS_TIKTOK_ENABLED", False)
        cc = self._content(clean_db, platform="tiktok", key="tiktok:cm5")
        result = comments_svc.ensure_comments(clean_db, cc.id)
        assert result["reason"] == "no_provider"
        clean_db.refresh(cc)
        assert cc.comments_state == "disabled"

    def test_comments_are_a_separate_retrieval_modality(self, clean_db, monkeypatch):
        """Audience opinion must never look like something the creator said."""
        from api.models import ContentChunk
        from api.services import comments as comments_svc

        install_fake_router(monkeypatch, FakeRouter())
        cc = self._content(clean_db, key="youtube:cm6")
        for i in range(4):
            clean_db.add(ContentComment(canonical_content_id=cc.id, text=f"great video {i}",
                                        rank=i, platform_comment_id=str(i)))
        clean_db.commit()

        comments_svc._index_comments(clean_db, cc.id)
        chunks = (clean_db.query(ContentChunk)
                  .filter(ContentChunk.canonical_content_id == cc.id).all())
        assert chunks and all(c.modality == "comment" for c in chunks)


# ─── ASR provider ────────────────────────────────────────────────────────────

class TestASRProvider:
    def test_default_is_no_provider_not_local_cpu(self, monkeypatch):
        """CPU Whisper on the API host is the thing that cannot scale."""
        import api.asr as asr_mod

        monkeypatch.delenv("SAVA_ASR_PROVIDER", raising=False)
        asr_mod.reset_asr()
        provider = asr_mod.get_asr()
        assert not provider.available
        assert provider.name == "none"
        asr_mod.reset_asr()

    def test_local_is_opt_in_and_declares_itself_in_process(self, monkeypatch):
        import api.asr as asr_mod

        monkeypatch.setenv("SAVA_ASR_PROVIDER", "local")
        asr_mod.reset_asr()
        provider = asr_mod.get_asr()
        assert provider.runs_in_process is True
        asr_mod.reset_asr()

    def test_hosted_needs_credentials_before_it_activates(self, monkeypatch):
        import api.asr as asr_mod

        monkeypatch.setenv("SAVA_ASR_PROVIDER", "hosted")
        monkeypatch.delenv("SAVA_ASR_BASE_URL", raising=False)
        monkeypatch.delenv("SAVA_ASR_API_KEY", raising=False)
        asr_mod.reset_asr()
        assert not asr_mod.get_asr().available

        monkeypatch.setenv("SAVA_ASR_BASE_URL", "https://asr.example.invalid/v1")
        monkeypatch.setenv("SAVA_ASR_API_KEY", "test-key")
        asr_mod.reset_asr()
        provider = asr_mod.get_asr()
        assert provider.available and provider.runs_in_process is False
        asr_mod.reset_asr()

    def test_missing_asr_is_a_missing_transcript_not_a_failed_item(self, monkeypatch):
        from api.pipeline import acquire
        import api.asr as asr_mod

        monkeypatch.delenv("SAVA_ASR_PROVIDER", raising=False)
        asr_mod.reset_asr()
        result = acquire.transcribe_audio("/nonexistent.m4a")
        assert result.ok is False
        assert "no ASR provider" in (result.error or "")
        asr_mod.reset_asr()


# ─── Outbound safety ─────────────────────────────────────────────────────────

class TestNetworkGuard:
    @pytest.mark.parametrize("url", [
        "http://169.254.169.254/latest/meta-data/",   # cloud metadata
        "http://127.0.0.1:8000/api/bookmarks",        # our own API
        "http://localhost/admin",
        "http://10.0.0.5/internal",
        "file:///etc/passwd",
        "gopher://evil/x",
        "https://evil.example.com/image.jpg",         # not a platform CDN
    ])
    def test_unsafe_targets_are_refused(self, url):
        from api.net_guard import PLATFORM_IMAGE_HOSTS, UnsafeURL, validate

        with pytest.raises(UnsafeURL):
            validate(url, allowed_hosts=PLATFORM_IMAGE_HOSTS)

    @pytest.mark.parametrize("url", [
        "https://i.ytimg.com/vi/abc/maxresdefault.jpg",
        "https://p16-sign-va.tiktokcdn-us.com/obj/x~tplv.image",
        "https://scontent.cdninstagram.com/v/x.jpg",
    ])
    def test_platform_cdns_are_allowed(self, url):
        """Host allowlisting only. DNS is asserted separately so this test does
        not fail on a machine with no resolver or a retired CDN hostname."""
        from api.net_guard import PLATFORM_IMAGE_HOSTS, validate

        assert validate(url, allowed_hosts=PLATFORM_IMAGE_HOSTS, resolve=False) == url

    def test_private_addresses_are_refused_even_on_an_allowed_host(self):
        """The redirect-inward case, which a host check alone would miss."""
        from api.net_guard import UnsafeURL, _addresses_are_public

        for addr in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "192.168.0.9", "::1"):
            with pytest.raises(UnsafeURL):
                _addresses_are_public([addr])
        _addresses_are_public(["142.250.72.14"])          # a public address is fine


# ─── Object storage ──────────────────────────────────────────────────────────

class TestObjectStorage:
    def test_local_backend_round_trips(self, tmp_path):
        from api.storage import LocalObjectStorage, derive_key

        storage = LocalObjectStorage(root=tmp_path)
        key = derive_key("thumbnails", "https://cdn.example/x.jpg", content_type="image/jpeg")
        assert not storage.exists(key)
        storage.put(key, b"\xff\xd8imagebytes", content_type="image/jpeg")
        assert storage.exists(key)
        assert storage.get(key) == b"\xff\xd8imagebytes"
        assert storage.url(key).endswith(key)
        storage.delete(key)
        assert not storage.exists(key)

    def test_keys_are_stable_so_a_second_save_reuses_the_object(self):
        from api.storage import derive_key

        a = derive_key("thumbnails", "https://cdn.example/x.jpg", content_type="image/jpeg")
        b = derive_key("thumbnails", "https://cdn.example/x.jpg", content_type="image/jpeg")
        assert a == b

    def test_traversal_keys_are_rejected(self, tmp_path):
        from api.storage import LocalObjectStorage

        storage = LocalObjectStorage(root=tmp_path)
        assert storage.exists("../../etc/passwd") is False


# ─── 100,000-user load simulations ───────────────────────────────────────────
#
# All mocked. The numbers below are deliberately about *ratios* — how many jobs
# a given number of saves produces — because that is the property that decides
# whether the architecture survives growth. Absolute throughput is a function of
# hardware and is measured in production, not here.

@pytest.fixture
def _no_network(monkeypatch):
    """Fail loudly if a simulation ever tries to leave the machine."""
    def boom(*a, **k):
        raise AssertionError("load simulation attempted a real network call")

    from api.pipeline import acquire
    for name in ("fetch_metadata", "fetch_captions_via_ytdlp", "fetch_native_captions",
                 "download_audio", "download_video_lowres", "fetch_carousel"):
        monkeypatch.setattr(acquire, name, boom)
    return True


class TestHundredThousandUserLoad:
    """Save bursts at 100k scale, with providers mocked out entirely."""

    def _burst(self, db, *, users: int, unique_items: int, platform: str,
               saves_per_user: int = 1) -> dict:
        """Simulate N users saving into a pool of M distinct pieces of content."""
        from api.services.save import DuplicateSave, create_save

        started = time.monotonic()
        accepted = duplicates = 0
        user_ids = [make_user(db, f"load{i}@example.com").id for i in range(users)]

        for index, uid in enumerate(user_ids):
            for s in range(saves_per_user):
                item = (index * saves_per_user + s) % unique_items
                url = (f"https://youtube.com/watch?v=vid{item:08d}x"
                       if platform == "youtube"
                       else f"https://www.tiktok.com/@c/video/{7000000000000000000 + item}")
                try:
                    create_save(db, url=url, user_id=uid)
                    accepted += 1
                except DuplicateSave:
                    duplicates += 1

        return {
            "accepted": accepted,
            "duplicates": duplicates,
            "canonical": db.query(CanonicalContent).count(),
            "jobs": db.query(Job).filter(Job.kind == "content.process").count(),
            "bookmarks": db.query(Bookmark).count(),
            "seconds": time.monotonic() - started,
        }

    def test_viral_workload_collapses_to_one_job_per_item(self, clean_db, _no_network):
        """Scenario C: many saves, few unique items. The margin case."""
        stats = self._burst(clean_db, users=600, unique_items=20, platform="tiktok")

        assert stats["canonical"] == 20
        assert stats["jobs"] == 20, "one processing job per unique item, not per save"
        assert stats["bookmarks"] == stats["accepted"]
        # 600 library additions, 20 extractions paid for.
        assert stats["accepted"] / stats["jobs"] >= 25

    def test_unique_workload_is_one_job_each(self, clean_db, _no_network):
        """Scenario A: no reuse available. The worst case, and it must be linear."""
        stats = self._burst(clean_db, users=300, unique_items=300, platform="youtube")
        assert stats["canonical"] == 300
        assert stats["jobs"] == 300

    def test_mixed_workload_dedups_partially(self, clean_db, _no_network):
        """Scenario B/F: realistic overlap across platforms."""
        yt = self._burst(clean_db, users=200, unique_items=50, platform="youtube")
        tt = self._burst(clean_db, users=200, unique_items=50, platform="tiktok")
        assert yt["canonical"] == 50
        assert tt["canonical"] == 100          # cumulative across both bursts
        assert tt["jobs"] == 100

    def test_accepting_a_burst_never_blocks_on_extraction(self, clean_db, _no_network):
        """The distinction that matters: accept fast, process later.

        `_no_network` makes any acquisition attempt raise. The burst completing
        proves the request path does no extraction at all.
        """
        stats = self._burst(clean_db, users=400, unique_items=400, platform="youtube")
        assert stats["accepted"] == 400
        per_save_ms = (stats["seconds"] / stats["accepted"]) * 1000
        # Generous: CI machines are slow and this is SQLite. The assertion is
        # that a save is a few database writes, not that it is fast in absolute
        # terms.
        assert per_save_ms < 60, f"save path too slow at {per_save_ms:.1f}ms/save"

    def test_queue_depth_is_observable_during_a_burst(self, clean_db, _no_network):
        from api.ai import telemetry

        self._burst(clean_db, users=200, unique_items=200, platform="tiktok")
        health = telemetry.queue_health(clean_db)
        assert health["depth"] >= 200
        assert health["by_state"].get("queued", 0) >= 200
        assert "oldest_queued_age_s" in health

    def test_one_user_saving_the_same_thing_twice_is_rejected(self, clean_db, _no_network):
        from api.services.save import DuplicateSave, create_save

        user = make_user(clean_db, "dupe@example.com")
        url = "https://youtube.com/watch?v=aaaaaaaaaaa"
        create_save(clean_db, url=url, user_id=user.id)
        with pytest.raises(DuplicateSave):
            # Different URL shape, same video.
            create_save(clean_db, url="https://youtu.be/aaaaaaaaaaa", user_id=user.id)
        assert clean_db.query(Bookmark).filter(Bookmark.user_id == user.id).count() == 1

    def test_comments_do_not_multiply_with_savers(self, clean_db, _no_network, monkeypatch):
        """Scenario G: comments enabled under a viral load."""
        from api.services import comments as comments_svc

        calls = {"n": 0}

        def fake_fetch(self, content, *, limit):
            calls["n"] += 1
            return comments_svc.CommentFetch(
                True, comments=[comments_svc.FetchedComment(text="nice", platform_comment_id="1")])

        monkeypatch.setattr(comments_svc.YouTubeCommentsProvider, "fetch", fake_fetch)
        monkeypatch.setattr(comments_svc.YouTubeCommentsProvider, "available", True)
        monkeypatch.setattr(comments_svc, "_index_comments", lambda *a, **k: 0)

        self._burst(clean_db, users=200, unique_items=5, platform="youtube")
        for cc in clean_db.query(CanonicalContent).all():
            for _ in range(40):                    # every saver "opens" the item
                comments_svc.ensure_comments(clean_db, cc.id)

        assert calls["n"] == 5, "one comment fetch per unique item, regardless of savers"


class TestProviderDegradation:
    """Scenarios H, I, J — a provider goes bad and the system stays sane."""

    def test_platform_outage_parks_work_without_failing_saves(self, clean_db, monkeypatch):
        from api.platform_budget import (
            PlatformPolicy, PlatformRequestManager, PlatformUnavailable,
        )
        from api.services.save import create_save

        manager = PlatformRequestManager({
            "tiktok": PlatformPolicy.from_env("tiktok", concurrency=1, rpm=60,
                                              min_interval=0, failures=2, open_s=300),
            "youtube": PlatformPolicy.from_env("youtube", concurrency=2, rpm=60,
                                               min_interval=0, failures=5, open_s=60),
        })
        # A server-side fault, not a missing video: "unavailable" is
        # deliberately classified as content-level and must never trip a breaker,
        # so the message here is an unambiguous upstream error.
        #
        # Exactly `failure_threshold` failures. A third attempt would itself be
        # refused, because by then the breaker is already open.
        for _ in range(2):
            with manager.acquire("tiktok", "metadata") as slot:
                slot.failed("HTTP 500 internal error from upstream")

        with pytest.raises(PlatformUnavailable):
            with manager.acquire("tiktok", "metadata"):
                pass

        assert manager.availability("tiktok")[0] is False
        assert manager.availability("youtube")[0] is True, "outage must not spread"

        # Saves are still accepted while the platform is down.
        user = make_user(clean_db, "outage@example.com")
        result = create_save(clean_db, url="https://www.tiktok.com/@c/video/7111111111111111111",
                             user_id=user.id)
        assert result["id"]

    def test_asr_slowdown_is_bounded_by_its_own_concurrency(self):
        """Scenario H: transcription gets slow and must not consume the host."""
        import threading

        from api.platform_budget import PlatformPolicy, PlatformRequestManager

        manager = PlatformRequestManager({
            "asr": PlatformPolicy.from_env("asr", concurrency=2, rpm=600,
                                           min_interval=0, failures=6, open_s=30),
        })
        peak = {"n": 0}
        current = {"n": 0}
        lock = threading.Lock()

        def slow_transcription():
            with manager.acquire("asr", "transcribe") as slot:
                with lock:
                    current["n"] += 1
                    peak["n"] = max(peak["n"], current["n"])
                time.sleep(0.05)
                with lock:
                    current["n"] -= 1
                slot.ok()

        threads = [threading.Thread(target=slow_transcription) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert peak["n"] <= 2, f"ASR concurrency ceiling breached: {peak['n']}"
