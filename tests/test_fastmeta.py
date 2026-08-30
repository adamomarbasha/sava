"""Metadata-first saves, and the YouTube blank-card bug.

Reported from a physical iPhone against production. A newly saved YouTube
Short came back as

    platform: youtube      title: null       author: null
    thumbnail_url: null    processing_state: partial

and the UI showed a pink placeholder titled "youtube.com" forever, with the
detail screen saying "Sava could not read anything from this item yet."

Reproduced locally against the real endpoints:

    yt-dlp FAIL 2399ms  ERROR: [youtube] <id>: The page needs to be reloaded.
    oEmbed OK    263ms  title='…' author='…' thumbnail='…'

yt-dlp is losing to an anti-bot challenge. Nothing downstream had a fallback,
so a blocked extraction produced a permanently blank save. `api/pipeline/
fastmeta.py` adds two cheaper tiers in front of it.
"""
from __future__ import annotations

import json

import pytest

from api.models import CanonicalContent, ProcessingState
from api.pipeline import fastmeta, ingest


# ─── Tier 0: derived, costs no network ───────────────────────────────────────

class TestDerivedThumbnail:

    def test_a_youtube_id_yields_a_poster_with_no_network_call(self):
        assert fastmeta.derived_thumbnail("youtube", "tPEE9ZwTmy0") == \
            "https://i.ytimg.com/vi/tPEE9ZwTmy0/hqdefault.jpg"

    def test_it_uses_hqdefault_because_that_is_the_guaranteed_one(self):
        """`maxresdefault` 404s for most videos. A URL that fails to load is
        not a fallback — the client upgrades opportunistically instead."""
        url = fastmeta.derived_thumbnail("youtube", "tPEE9ZwTmy0")
        assert "hqdefault" in url and "maxresdefault" not in url

    @pytest.mark.parametrize("platform", ["tiktok", "instagram", "other", None])
    def test_no_other_platform_gets_a_guessed_thumbnail(self, platform):
        """TikTok and Instagram CDN paths are signed and expire. A guess would
        render as a broken image, which is worse than no image."""
        assert fastmeta.derived_thumbnail(platform, "abc123") is None

    @pytest.mark.parametrize("bad", ["", None, "short", "way-too-long-to-be-an-id",
                                     "has spaces", "elevenchar!"])
    def test_an_implausible_id_is_refused(self, bad):
        """Guards against putting a URL fragment or an empty string into an
        image URL the client will then try to load."""
        assert fastmeta.derived_thumbnail("youtube", bad) is None

    def test_the_host_is_on_the_image_allow_list(self):
        from api.net_guard import PLATFORM_IMAGE_HOSTS
        url = fastmeta.derived_thumbnail("youtube", "tPEE9ZwTmy0")
        assert any(host in url for host in PLATFORM_IMAGE_HOSTS)


# ─── Tier 1: one small public request ────────────────────────────────────────

class TestFastFetch:

    def test_oembed_supplies_title_creator_and_thumbnail(self, monkeypatch):
        monkeypatch.setattr(fastmeta, "_youtube_oembed", lambda url: {
            "title": "Shortest Video on Youtube",
            "creator_name": "Mylo the Cat",
            "thumbnail_url": "https://i.ytimg.com/vi/tPEE9ZwTmy0/hqdefault.jpg",
            "width": 480, "height": 360,
        })
        meta = fastmeta.fetch("youtube", "https://youtube.com/watch?v=tPEE9ZwTmy0",
                              content_id="tPEE9ZwTmy0")
        assert meta.source == "oembed"
        assert meta.title == "Shortest Video on Youtube"
        assert meta.creator_name == "Mylo the Cat"
        assert meta.useful

    def test_a_refused_oembed_still_yields_the_derived_poster(self, monkeypatch):
        """Private, age-gated, deleted, or rate-limited. A poster with no title
        still beats a placeholder with no poster."""
        monkeypatch.setattr(fastmeta, "_youtube_oembed", lambda url: None)
        meta = fastmeta.fetch("youtube", "https://youtube.com/watch?v=tPEE9ZwTmy0",
                              content_id="tPEE9ZwTmy0")
        assert meta.source == "derived"
        assert meta.thumbnail_url.endswith("/tPEE9ZwTmy0/hqdefault.jpg")
        assert meta.title is None

    def test_a_network_failure_is_never_fatal(self, monkeypatch):
        """A failure here means "continue to the full extraction", which is
        exactly what used to happen unconditionally."""
        def boom(url):
            raise OSError("connection reset")
        monkeypatch.setattr(fastmeta, "_youtube_oembed", boom)
        meta = fastmeta.fetch("youtube", "https://youtube.com/watch?v=zzzzzzzzzzz",
                              content_id="zzzzzzzzzzz")
        assert meta.useful is False or meta.source == "derived"

    def test_unsupported_platforms_return_nothing_without_calling_out(self, monkeypatch):
        called = []
        monkeypatch.setattr(fastmeta, "_youtube_oembed",
                            lambda url: called.append(url))
        for platform in ("tiktok", "instagram", "other"):
            assert fastmeta.fetch(platform, "https://example.com/x").useful is False
        assert called == [], "no cheap provider exists for these yet"


# ─── Applying it without destroying better data ──────────────────────────────

class TestApplyIsAdditiveOnly:

    @staticmethod
    def _cc(**kw):
        return CanonicalContent(content_key="youtube:x", platform="youtube",
                                canonical_url="https://youtube.com/watch?v=x",
                                media_kind="video", stage_status="{}",
                                metadata_json="{}", **kw)

    def test_it_fills_empty_fields(self):
        cc = self._cc()
        assert fastmeta.apply(cc, fastmeta.FastMeta(
            title="T", creator_name="C",
            thumbnail_url="https://i.ytimg.com/vi/x/hqdefault.jpg",
            source="oembed"))
        assert (cc.title, cc.creator_name) == ("T", "C")

    def test_it_never_overwrites_a_richer_title(self):
        """The full extraction is more authoritative than oEmbed, and a re-run
        must not trade its title for a shorter one."""
        cc = self._cc(title="The full, correct title", creator_name="Real Channel")
        fastmeta.apply(cc, fastmeta.FastMeta(title="Short", creator_name="Other",
                                             source="oembed"))
        assert cc.title == "The full, correct title"
        assert cc.creator_name == "Real Channel"

    def test_a_real_thumbnail_replaces_a_derived_guess(self):
        """The derived URL is a guess that happens to be right; the oEmbed URL
        is what YouTube says. That is the one upgrade allowed."""
        cc = self._cc(thumbnail_url="https://i.ytimg.com/vi/x/hqdefault.jpg")
        fastmeta.apply(cc, fastmeta.FastMeta(
            thumbnail_url="https://i.ytimg.com/vi/x/maxresdefault.jpg",
            source="oembed"))
        assert cc.thumbnail_url.endswith("maxresdefault.jpg")

    def test_a_derived_guess_never_replaces_a_real_thumbnail(self):
        cc = self._cc(thumbnail_url="https://i.ytimg.com/vi/x/sddefault.jpg")
        fastmeta.apply(cc, fastmeta.FastMeta(
            thumbnail_url="https://i.ytimg.com/vi/x/hqdefault.jpg",
            source="derived"))
        assert cc.thumbnail_url.endswith("sddefault.jpg")


# ─── The save path ───────────────────────────────────────────────────────────

class TestSavedCardIsUsefulImmediately:

    def test_a_new_youtube_save_has_a_poster_before_any_network_call(self, clean_db):
        """The reported bug, at its earliest observable point: the row is
        created by `resolve_or_create_canonical`, which does no network I/O."""
        cc, created = ingest.resolve_or_create_canonical(
            clean_db, "https://www.youtube.com/shorts/tPEE9ZwTmy0")
        assert created
        assert cc.thumbnail_url == \
            "https://i.ytimg.com/vi/tPEE9ZwTmy0/hqdefault.jpg"

    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/shorts/tPEE9ZwTmy0",
        "https://youtube.com/watch?v=tPEE9ZwTmy0",
        "https://youtu.be/tPEE9ZwTmy0",
        "https://www.youtube.com/watch?v=tPEE9ZwTmy0&feature=share&t=12",
        "https://m.youtube.com/watch?v=tPEE9ZwTmy0",
    ])
    def test_every_youtube_url_shape_resolves_to_the_same_poster(self, clean_db, url):
        """Shorts URLs, share links and tracking parameters all name one video.
        A poster that depended on the URL shape would be missing on exactly the
        links people actually paste."""
        cc, _ = ingest.resolve_or_create_canonical(clean_db, url)
        assert cc is not None, url
        assert cc.thumbnail_url == \
            "https://i.ytimg.com/vi/tPEE9ZwTmy0/hqdefault.jpg", url

    def test_a_tiktok_save_is_not_given_a_fake_poster(self, clean_db):
        cc, _ = ingest.resolve_or_create_canonical(
            clean_db, "https://www.tiktok.com/@user/video/7123456789012345678")
        assert cc is not None
        assert cc.thumbnail_url is None


# ─── The gate that nearly broke the full extraction ─────────────────────────

class TestStageADoesNotSuppressTheFullExtraction:
    """The subtle regression this fix could have introduced.

    Both heavy stages were gated on `not cc.title` — a fine proxy for "nothing
    fetched yet" right up until Stage A began filling the title in 200ms. After
    that the proxy inverted, and a successful cheap fetch would have skipped
    yt-dlp entirely, trading a blank card for a permanently shallow one: no
    duration, no geometry, no view counts, no caption track.
    """

    @staticmethod
    def _cc(**kw):
        return CanonicalContent(content_key="youtube:y", platform="youtube",
                                canonical_url="https://youtube.com/watch?v=y",
                                media_kind="video", stage_status="{}",
                                metadata_json="{}", **kw)

    def test_a_title_alone_does_not_mark_metadata_done(self):
        cc = self._cc(title="From oEmbed")
        assert ingest._stage_ok(cc, "metadata") is False

    def test_only_a_recorded_ok_marks_it_done(self):
        cc = self._cc()
        ingest._set_stage(cc, "metadata", "ok")
        assert ingest._stage_ok(cc, "metadata") is True

    def test_a_failed_stage_is_not_done(self):
        cc = self._cc(title="From oEmbed")
        ingest._set_stage(cc, "metadata", "failed", "blocked")
        assert ingest._stage_ok(cc, "metadata") is False

    def test_corrupt_stage_status_does_not_skip_the_work(self):
        cc = self._cc()
        cc.stage_status = "not json"
        assert ingest._stage_ok(cc, "metadata") is False

    def test_the_gates_in_the_pipeline_use_it(self):
        """Source-level, because the failure mode is silent: the pipeline would
        still return ok, just with nothing in it."""
        import pathlib
        source = (pathlib.Path(__file__).resolve().parent.parent
                  / "api" / "pipeline" / "ingest.py").read_text()
        assert source.count('_stage_ok(cc, "metadata")') >= 2
        assert "elif force or not cc.title:" not in source
        assert "strat.try_native_captions and (force or not cc.title)" not in source
