"""End-to-end proof that the pipeline stopped downloading videos it doesn't need.

`tests/test_routing.py` tests the decision in isolation. These run the real
`process_content` with the network stubbed out and assert on **what it actually
tried to fetch** — which is the thing that costs money, and the thing that was
silently wrong before.

Every acquisition function is replaced by a recorder. If `download_video_lowres`
appears in the call log, real bytes would have moved in production.
"""
from __future__ import annotations

import json

import pytest

from api import plans
from api.models import (
    CanonicalContent, ContentFrame, ContentTranscript, ProcessingState,
)
from api.pipeline import acquire, ingest
from api.pipeline.acquire import AcquisitionResult

from conftest import FakeRouter, install_fake_router, make_user


class Recorder:
    """Stubs every network-touching acquisition call and logs what was asked for."""

    def __init__(self, monkeypatch, *, captions=None, metadata=None,
                 audio_ok=True, video_ok=True):
        self.calls: list = []
        self._captions = captions
        self._metadata = metadata or {}
        self._audio_ok = audio_ok
        self._video_ok = video_ok

        monkeypatch.setattr(acquire, "fetch_metadata", self._metadata_fn)
        monkeypatch.setattr(acquire, "fetch_captions_via_ytdlp", self._captions_fn)
        monkeypatch.setattr(acquire, "fetch_native_captions", self._captions_fn)
        monkeypatch.setattr(acquire, "download_audio", self._audio_fn)
        monkeypatch.setattr(acquire, "download_video_lowres", self._video_fn)
        monkeypatch.setattr(acquire, "transcribe_audio", self._asr_fn)
        # ffmpeg is not required for these tests and its absence must not be
        # what stops a download — that would make the assertion vacuous.
        monkeypatch.setattr(ingest.frames_mod, "ffmpeg_available", lambda: True)
        monkeypatch.setattr(ingest.frames_mod, "select_timestamps",
                            lambda *a, **k: [1.0, 2.0])
        monkeypatch.setattr(ingest.frames_mod, "extract_frames", lambda *a, **k: [])
        monkeypatch.setattr(ingest, "_mirror_cover", lambda *a, **k: False)
        # ASR is configured, so the audio route is genuinely reachable.
        monkeypatch.setattr(ingest, "_asr_available", lambda: True)

    # -- stubs ---------------------------------------------------------------
    def _metadata_fn(self, url, *a, **k):
        self.calls.append("metadata")
        return AcquisitionResult(True, "metadata", bytes_moved=1200,
                                 duration_s=self._metadata.get("duration", 30),
                                 metadata=self._metadata)

    def _captions_fn(self, url, *a, **k):
        self.calls.append("captions")
        if not self._captions:
            return AcquisitionResult(False, "metadata", error="no caption tracks")
        return AcquisitionResult(
            True, "metadata", bytes_moved=800,
            duration_s=self._metadata.get("duration", 30),
            metadata={"segments": self._captions, "language": "en",
                      "source": "captions", "info": self._metadata})

    def _audio_fn(self, url, *a, **k):
        self.calls.append("download_audio")
        if not self._audio_ok:
            return AcquisitionResult(False, "audio", error="blocked")
        return AcquisitionResult(True, "audio", path="/tmp/sava_fake/audio.m4a",
                                 bytes_moved=1_600_000, duration_s=30)

    def _video_fn(self, url, *a, **k):
        self.calls.append("download_video")
        if not self._video_ok:
            return AcquisitionResult(False, "video", error="blocked")
        return AcquisitionResult(True, "video", path="/tmp/sava_fake/video.mp4",
                                 bytes_moved=7_400_000, duration_s=30)

    def _asr_fn(self, path, *a, **k):
        self.calls.append("asr")
        return AcquisitionResult(
            True, "audio", duration_s=30,
            metadata={"segments": [{"text": "spoken words " * 40, "start": 0,
                                    "duration": 30}],
                      "language": "en", "source": "asr",
                      "provider": "local-whisper", "model": "small"})

    # -- assertions ----------------------------------------------------------
    @property
    def downloaded_video(self) -> bool:
        return "download_video" in self.calls

    @property
    def downloaded_audio(self) -> bool:
        return "download_audio" in self.calls

    @property
    def bytes_class(self) -> str:
        if self.downloaded_video:
            return "video"
        if self.downloaded_audio:
            return "audio"
        return "none"


def _content(db, key, *, platform="tiktok", description=None, duration=30,
             media_kind="video", title="A video"):
    cc = CanonicalContent(
        content_key=key, platform=platform, canonical_url=f"https://x/{key}",
        media_kind=media_kind, duration_seconds=duration, title=title,
        description=description, thumbnail_url=f"https://cdn/{key}.jpg",
        processing_state=ProcessingState.QUEUED, processing_level=0,
        stage_status="{}", metadata_json="{}")
    db.add(cc)
    db.commit()
    db.refresh(cc)
    return cc


def _classify_as(monkeypatch, *, visual_dependency, content_type="other"):
    """Pin the classifier so the routing decision under test is deterministic."""
    monkeypatch.setattr(
        ingest.understanding, "classify",
        lambda **kw: ({"content_type": content_type, "confidence": 0.9,
                       "visual_dependency": visual_dependency}, None))


def _no_cover(monkeypatch):
    monkeypatch.setattr(ingest, "_read_cover", lambda *a, **k: ({}, ""))


def _cover_says(monkeypatch, text):
    monkeypatch.setattr(
        ingest, "_read_cover",
        lambda *a, **k: ({"enough": bool(text)}, text))


@pytest.fixture
def user(clean_db):
    return make_user(clean_db, "pipeline-cost@example.com")


# ─── The headline claims ─────────────────────────────────────────────────────

class TestNoUnnecessaryDownloads:

    def test_a_tiktok_with_a_real_caption_downloads_nothing(self, clean_db,
                                                            monkeypatch, user):
        """The regression that mattered.

        Before routing, this exact item pulled 7.4 MB of video through a paid
        proxy. It has a description and a cover; that is enough.
        """
        install_fake_router(monkeypatch, FakeRouter())
        rec = Recorder(monkeypatch, metadata={"duration": 30})
        _classify_as(monkeypatch, visual_dependency=0.35)
        _cover_says(monkeypatch, "[cover] on-screen: 3 INGREDIENT PASTA")

        cc = _content(clean_db, "tiktok:cheap",
                      description="Full method in the caption. " * 12)
        result = ingest.process_content(cc.id, clean_db, user_id=user.id)

        assert result["ok"] is True
        assert rec.bytes_class == "none", rec.calls
        assert result["route"] in ("text", "cover")
        clean_db.refresh(cc)
        assert plans.units_for_content(cc) == 1

    def test_youtube_with_captions_downloads_nothing(self, clean_db,
                                                     monkeypatch, user):
        install_fake_router(monkeypatch, FakeRouter())
        rec = Recorder(monkeypatch, metadata={"duration": 2400},
                       captions=[{"text": "spoken " * 200, "start": 0,
                                  "duration": 2400}])
        _classify_as(monkeypatch, visual_dependency=0.3)
        _no_cover(monkeypatch)

        cc = _content(clean_db, "youtube:longform", platform="youtube",
                      duration=2400, description="A long talk")
        ingest.process_content(cc.id, clean_db, user_id=user.id)

        assert rec.bytes_class == "none", rec.calls
        assert not rec.downloaded_video

    def test_no_text_takes_audio_not_video(self, clean_db, monkeypatch, user):
        """Speech-driven content: transcribe it, don't download the picture."""
        install_fake_router(monkeypatch, FakeRouter())
        rec = Recorder(monkeypatch, metadata={"duration": 30})
        _classify_as(monkeypatch, visual_dependency=0.3)
        _no_cover(monkeypatch)

        cc = _content(clean_db, "tiktok:speech", description="#fyp")
        result = ingest.process_content(cc.id, clean_db, user_id=user.id)

        assert rec.bytes_class == "audio", rec.calls
        assert not rec.downloaded_video
        assert result["route"] == "audio"
        clean_db.refresh(cc)
        assert plans.units_for_content(cc) == plans.units_for_route("audio")

    def test_a_transcript_is_still_produced_on_the_audio_route(self, clean_db,
                                                               monkeypatch, user):
        """Cheaper must not mean less understood."""
        install_fake_router(monkeypatch, FakeRouter())
        Recorder(monkeypatch, metadata={"duration": 30})
        _classify_as(monkeypatch, visual_dependency=0.3)
        _no_cover(monkeypatch)

        cc = _content(clean_db, "tiktok:speech2", description="#fyp")
        ingest.process_content(cc.id, clean_db, user_id=user.id)

        tr = (clean_db.query(ContentTranscript)
              .filter(ContentTranscript.canonical_content_id == cc.id).first())
        assert tr is not None and len(tr.text) > 100


class TestEscalationStillWorks:

    def test_genuinely_visual_content_reaches_frames(self, clean_db,
                                                     monkeypatch, user):
        """Cheap-first must not mean never."""
        install_fake_router(monkeypatch, FakeRouter())
        rec = Recorder(monkeypatch, metadata={"duration": 30})
        _classify_as(monkeypatch, visual_dependency=0.95, content_type="recipe")
        _no_cover(monkeypatch)

        cc = _content(clean_db, "tiktok:visual", description="")
        result = ingest.process_content(cc.id, clean_db, user_id=user.id)

        assert rec.downloaded_video, rec.calls
        assert result["route"] == "light_vision"
        clean_db.refresh(cc)
        assert plans.units_for_content(cc) == plans.units_for_route("light_vision")

    def test_a_useless_cover_escalates_to_frames(self, clean_db,
                                                 monkeypatch, user):
        install_fake_router(monkeypatch, FakeRouter())
        rec = Recorder(monkeypatch, metadata={"duration": 30})
        _classify_as(monkeypatch, visual_dependency=0.7, content_type="recipe")
        _cover_says(monkeypatch, "")          # cover told us nothing

        cc = _content(clean_db, "tiktok:escalate", description="look")
        result = ingest.process_content(cc.id, clean_db, user_id=user.id)

        assert rec.downloaded_video, rec.calls
        assert "light_vision" in result["route"]

    def test_a_useful_cover_prevents_escalation(self, clean_db,
                                                monkeypatch, user):
        """The saving that makes the whole model work."""
        install_fake_router(monkeypatch, FakeRouter())
        rec = Recorder(monkeypatch, metadata={"duration": 30})
        _classify_as(monkeypatch, visual_dependency=0.7, content_type="recipe")
        _cover_says(monkeypatch,
                    "[cover] on-screen: " + "pasta garlic chilli oil " * 20)

        cc = _content(clean_db, "tiktok:covered", description="look")
        result = ingest.process_content(cc.id, clean_db, user_id=user.id)

        assert not rec.downloaded_video, rec.calls
        assert result["route"] == "cover"

    def test_deep_is_reachable_only_by_asking(self, clean_db, monkeypatch, user):
        install_fake_router(monkeypatch, FakeRouter())
        rec = Recorder(monkeypatch, metadata={"duration": 30})
        _classify_as(monkeypatch, visual_dependency=0.2)
        _no_cover(monkeypatch)

        cc = _content(clean_db, "tiktok:deep", description="x" * 400)
        result = ingest.process_content(cc.id, clean_db, user_id=user.id, deep=True)

        assert result["route"] == "deep_vision"
        assert rec.downloaded_video


class TestReuseAndCaching:

    def test_a_processed_item_is_never_reprocessed(self, clean_db,
                                                   monkeypatch, user):
        install_fake_router(monkeypatch, FakeRouter())
        rec = Recorder(monkeypatch, metadata={"duration": 30})
        _classify_as(monkeypatch, visual_dependency=0.3)
        _cover_says(monkeypatch, "[cover] something")

        cc = _content(clean_db, "tiktok:once", description="y" * 400)
        ingest.process_content(cc.id, clean_db, user_id=user.id)
        first = list(rec.calls)

        ingest.process_content(cc.id, clean_db, user_id=user.id)
        assert rec.calls == first, "second run performed network work"

    def test_an_existing_transcript_is_not_re_fetched(self, clean_db,
                                                      monkeypatch, user):
        install_fake_router(monkeypatch, FakeRouter())
        rec = Recorder(monkeypatch, metadata={"duration": 30})
        _classify_as(monkeypatch, visual_dependency=0.3)
        _no_cover(monkeypatch)

        cc = _content(clean_db, "tiktok:hastranscript", description="#fyp")
        clean_db.add(ContentTranscript(
            canonical_content_id=cc.id, source="asr", lang="en",
            text="already transcribed " * 30, segments="[]", is_complete=True))
        clean_db.commit()

        ingest.process_content(cc.id, clean_db, user_id=user.id)
        assert not rec.downloaded_audio and not rec.downloaded_video, rec.calls

    def test_the_cover_read_is_cached_as_a_frame(self, clean_db,
                                                 monkeypatch, user):
        """So a re-run costs no second vision call."""
        install_fake_router(monkeypatch, FakeRouter())
        Recorder(monkeypatch, metadata={"duration": 30})
        _classify_as(monkeypatch, visual_dependency=0.3)

        cc = _content(clean_db, "tiktok:covercache", description="z" * 400)
        # Real `_read_cover`, with storage returning bytes.
        monkeypatch.setattr(ingest, "_read_cover", ingest.__dict__["_read_cover"])
        monkeypatch.setattr(
            ingest.frames_mod, "analyze_cover",
            lambda blob, **kw: ({"ocr": "HELLO", "caption": "a bowl",
                                 "enough": True}, None))
        monkeypatch.setattr("api.storage.get_storage",
                            lambda: type("S", (), {"get": lambda self, k: b"jpegbytes"})())
        cc.thumbnail_stored_key = "thumbnails/abc.jpg"
        clean_db.commit()

        ingest.process_content(cc.id, clean_db, user_id=user.id)
        frame = (clean_db.query(ContentFrame)
                 .filter(ContentFrame.canonical_content_id == cc.id,
                         ContentFrame.ts_ms == 0).first())
        assert frame is not None and frame.ocr_text == "HELLO"


class TestRouteIsRecorded:

    def test_every_processed_item_records_its_route_and_reason(
            self, clean_db, monkeypatch, user):
        """Without this, the cost model can never be checked against reality."""
        install_fake_router(monkeypatch, FakeRouter())
        Recorder(monkeypatch, metadata={"duration": 30})
        _classify_as(monkeypatch, visual_dependency=0.3)
        _cover_says(monkeypatch, "[cover] text")

        cc = _content(clean_db, "tiktok:audited", description="q" * 400)
        ingest.process_content(cc.id, clean_db, user_id=user.id)

        clean_db.refresh(cc)
        assert cc.route in [r["route"] for r in ingest.route.describe()]
        assert cc.route_reason and len(cc.route_reason) > 5


class TestAskNeverReprocesses:

    def test_reading_a_summary_touches_no_acquisition(self, clean_db,
                                                      monkeypatch, user):
        """Ask and Summary read derived intelligence, never the source video."""
        from api.services import intelligence
        from conftest import make_bookmark

        install_fake_router(monkeypatch, FakeRouter())
        rec = Recorder(monkeypatch, metadata={"duration": 30})
        _classify_as(monkeypatch, visual_dependency=0.3)
        _cover_says(monkeypatch, "[cover] text")

        cc = _content(clean_db, "tiktok:asked", description="w" * 400)
        ingest.process_content(cc.id, clean_db, user_id=user.id)
        before = list(rec.calls)

        bm = make_bookmark(clean_db, user.id, "https://x/tiktok:asked",
                           platform="tiktok")
        bm.canonical_content_id = cc.id
        clean_db.commit()

        for _ in range(3):
            intelligence.get_or_create_summary(clean_db, bm, user_id=user.id)

        assert rec.calls == before, "answering re-acquired the source media"
