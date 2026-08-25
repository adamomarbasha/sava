"""Provider capability decoupling.

The property being protected: turning the reviewed extraction path down must not
turn the product down. Playback, analysis and metadata are three switches, and
the official embeds must keep working when the others are off.
"""
from __future__ import annotations

import importlib

import pytest

from api import providers


@pytest.fixture
def as_production(monkeypatch):
    import api.config
    monkeypatch.setattr(api.config, "IS_PRODUCTION", True)
    importlib.reload(providers)
    yield providers
    monkeypatch.setattr(api.config, "IS_PRODUCTION", False)
    importlib.reload(providers)


@pytest.fixture
def as_development(monkeypatch):
    import api.config
    monkeypatch.setattr(api.config, "IS_PRODUCTION", False)
    importlib.reload(providers)
    yield providers


class TestPlaybackAndAnalysisAreIndependent:
    def test_embeds_survive_analysis_being_off(self, as_production):
        """The product keeps playing even with no media analysis at all."""
        p = as_production
        for platform in ("youtube", "instagram"):
            caps = p.for_platform(platform)
            assert caps.playback is p.Playback.EMBED
            assert caps.analysis is not p.Analysis.MEDIA

    def test_understanding_survives_media_analysis_being_off(self, as_production):
        """Text-level analysis still yields summaries, key points, embeddings."""
        for platform in ("youtube", "tiktok", "instagram"):
            assert as_production.for_platform(platform).can_understand

    def test_turning_playback_off_does_not_disable_analysis(self, monkeypatch):
        monkeypatch.setenv("SAVA_TIKTOK_PLAYBACK", "none")
        monkeypatch.setenv("SAVA_TIKTOK_ANALYSIS", "media")
        importlib.reload(providers)
        caps = providers.for_platform("tiktok")
        assert caps.playback is providers.Playback.NONE
        assert caps.analysis is providers.Analysis.MEDIA

    def test_turning_analysis_off_does_not_disable_playback(self, monkeypatch):
        monkeypatch.setenv("SAVA_TIKTOK_PLAYBACK", "proxy")
        monkeypatch.setenv("SAVA_TIKTOK_ANALYSIS", "none")
        importlib.reload(providers)
        caps = providers.for_platform("tiktok")
        assert caps.playback is providers.Playback.PROXY
        assert caps.analysis is providers.Analysis.NONE

    def test_galleries_never_need_a_capability(self):
        """Images Sava already holds involve no platform access."""
        assert providers.playback_allowed("instagram", providers.Playback.GALLERY)
        assert providers.playback_allowed("anything", providers.Playback.GALLERY)


class TestProductionIsConservativeByDefault:
    def test_production_does_not_proxy_tiktok_by_default(self, as_production):
        assert not as_production.playback_allowed("tiktok",
                                                  as_production.Playback.PROXY)

    def test_production_does_not_download_media_by_default(self, as_production):
        for platform in ("youtube", "tiktok", "instagram"):
            assert not as_production.media_analysis_allowed(platform)

    def test_development_keeps_the_existing_behaviour(self, as_development):
        """Nothing about local work changes."""
        assert as_development.media_analysis_allowed("tiktok")
        assert as_development.playback_allowed("tiktok",
                                               as_development.Playback.PROXY)

    def test_a_deployment_can_opt_back_in(self, as_production, monkeypatch):
        """The answer from a platform should be a variable, not a refactor."""
        monkeypatch.setenv("SAVA_TIKTOK_PLAYBACK", "proxy")
        monkeypatch.setenv("SAVA_TIKTOK_ANALYSIS", "media")
        importlib.reload(providers)
        assert providers.playback_allowed("tiktok", providers.Playback.PROXY)
        assert providers.media_analysis_allowed("tiktok")


class TestRobustness:
    def test_an_unknown_platform_falls_back_to_the_conservative_row(self):
        caps = providers.for_platform("some-new-site")
        assert caps.playback is providers.Playback.NONE

    def test_a_missing_platform_name_does_not_crash(self):
        assert providers.for_platform(None) is not None

    def test_a_malformed_override_is_ignored_not_obeyed(self, monkeypatch):
        """A typo must not silently widen what a deployment may do."""
        monkeypatch.setenv("SAVA_TIKTOK_PLAYBACK", "prxy")
        importlib.reload(providers)
        assert providers.for_platform("tiktok").playback in (
            providers.Playback.PROXY, providers.Playback.NONE)

    def test_describe_reports_every_platform(self):
        matrix = providers.describe()
        for platform in ("youtube", "tiktok", "instagram"):
            assert platform in matrix
            assert set(matrix[platform]) == {"metadata", "playback", "analysis"}


class TestWiring:
    def test_the_vision_stage_asks_the_capability(self, monkeypatch):
        """Media analysis off must stop the download before any work happens."""
        from api.pipeline.ingest import PlatformStrategy
        strategy = PlatformStrategy(name="tiktok", try_native_captions=True,
                                    allow_asr=True, vision_mode="always",
                                    asr_max_seconds=900)
        monkeypatch.setattr(providers, "media_analysis_allowed", lambda p: False)
        assert strategy.wants_vision(visual_dependency=1.0, has_transcript=False,
                                     media_kind="video") is False

    def test_stored_imagery_is_never_gated(self, monkeypatch):
        """Carousels analyse frames Sava already holds — no platform access."""
        from api.pipeline.ingest import PlatformStrategy
        strategy = PlatformStrategy(name="instagram", try_native_captions=False,
                                    allow_asr=False, vision_mode="conditional",
                                    asr_max_seconds=900)
        monkeypatch.setattr(providers, "media_analysis_allowed", lambda p: False)
        assert strategy.wants_vision(visual_dependency=0.0, has_transcript=False,
                                     media_kind="carousel") is True

    def test_the_vision_stage_still_works_when_allowed(self, monkeypatch):
        from api.pipeline.ingest import PlatformStrategy
        strategy = PlatformStrategy(name="tiktok", try_native_captions=True,
                                    allow_asr=True, vision_mode="always",
                                    asr_max_seconds=900)
        monkeypatch.setattr(providers, "media_analysis_allowed", lambda p: True)
        assert strategy.wants_vision(visual_dependency=1.0, has_transcript=False,
                                     media_kind="video") is True
