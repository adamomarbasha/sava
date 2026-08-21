"""The short-form viewer's server half.

Nothing here touches TikTok or YouTube. Stream resolution is mocked, because
the point of these tests is the decisions Sava makes around playback, not the
platforms' behaviour:

  * what counts as short-form, and — just as importantly — what does not,
  * that a Short and a watch link remain one canonical item,
  * that each platform is routed to the playback it is actually allowed,
  * that a photo post is a gallery rather than a video that never starts,
  * that a playback token cannot be stretched to another item, another user,
    or another day,
  * that the operator's browser cookies do not leave the machine.

The last one is the one that would matter most if it broke, and is the least
likely to be noticed if it did.
"""
from __future__ import annotations

import time

import pytest

from conftest import make_bookmark, make_user

from api.content.identity import resolve_identity
from api.content.shortform import SHORT_MAX_SECONDS, is_short_form, is_shorts_url
from api.models import CanonicalContent, ContentAsset
from api.services import playback as P


# ─── Classification ──────────────────────────────────────────────────────────

class TestShortFormClassification:
    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://m.youtube.com/shorts/dQw4w9WgXcQ",
        "https://youtube.com/shorts/dQw4w9WgXcQ?feature=share",
    ])
    def test_shorts_urls_are_recognised(self, url):
        assert is_shorts_url(url)

    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://tiktok.com/@a/video/123456789",
        "",
        None,
    ])
    def test_non_shorts_urls_are_not(self, url):
        assert not is_shorts_url(url)

    def test_tiktok_video_is_short_form_regardless_of_length(self):
        """TikTok is a format, not a duration. A nine-minute one still swipes."""
        assert is_short_form("tiktok", media_kind="video", duration_seconds=540)

    def test_tiktok_carousel_is_short_form(self):
        assert is_short_form("tiktok", media_kind="carousel")

    def test_youtube_shorts_url_wins_over_everything(self):
        """The platform said so. Metadata does not get a vote."""
        assert is_short_form("youtube", media_kind="video", duration_seconds=900,
                             url_hint="https://youtube.com/shorts/abc")

    def test_youtube_vertical_and_brief_is_a_short(self):
        assert is_short_form("youtube", media_kind="video", duration_seconds=45,
                             width=1080, height=1920)

    def test_youtube_landscape_is_not_a_short_however_brief(self):
        assert not is_short_form("youtube", media_kind="video", duration_seconds=20,
                                 width=1920, height=1080)

    def test_youtube_long_vertical_is_not_a_short(self):
        assert not is_short_form("youtube", media_kind="video",
                                 duration_seconds=SHORT_MAX_SECONDS + 1,
                                 width=1080, height=1920)

    def test_unknown_geometry_does_not_guess(self):
        """Absence of evidence is not evidence.

        A brief YouTube video with no dimensions is far more likely to be an
        ordinary short video than a Short, and a long landscape video dropped
        into a vertical swipe feed is a much worse failure than one that has to
        be opened the ordinary way. The tie breaks toward not claiming it.
        """
        assert not is_short_form("youtube", media_kind="video", duration_seconds=30)

    @pytest.mark.parametrize("platform", ["twitter", "reddit", "linkedin", "other"])
    def test_unsupported_platforms_are_out_of_scope(self, platform):
        assert not is_short_form(platform, media_kind="video", duration_seconds=20,
                                 width=1080, height=1920)

    @pytest.mark.parametrize("kind", ["video", "carousel", "image"])
    def test_instagram_posts_use_the_same_viewer(self, kind):
        """Instagram was excluded when the viewer shipped and is included now.

        Reels are vertical video and carousels page horizontally, which is what
        the gallery already does for TikTok photo posts — so this is reuse of
        one viewer rather than a second Instagram-shaped one.
        """
        assert is_short_form("instagram", media_kind=kind)

    def test_an_instagram_screenshot_capture_is_not_playable(self):
        """A capture has no media and names no post, so it must never open in
        a viewer that implies it does."""
        assert not is_short_form("instagram", media_kind="capture")


class TestShortsDoNotDuplicateCanonicals:
    def test_shorts_and_watch_share_one_identity(self):
        shapes = [
            "https://www.youtube.com/shorts/utPvHoiaznA",
            "https://m.youtube.com/shorts/utPvHoiaznA?feature=share",
            "https://www.youtube.com/watch?v=utPvHoiaznA",
            "https://youtu.be/utPvHoiaznA",
        ]
        keys = {resolve_identity(u).content_key for u in shapes}
        assert keys == {"youtube:utPvHoiaznA"}

    def test_canonical_url_is_normalised_away_from_shorts(self):
        """Which is exactly why the `/shorts/` hint has to be captured early."""
        ident = resolve_identity("https://youtube.com/shorts/utPvHoiaznA")
        assert "/shorts/" not in ident.canonical_url
        assert ident.canonical_url == "https://youtube.com/watch?v=utPvHoiaznA"


# ─── Playback descriptors ────────────────────────────────────────────────────

def _canonical(db, **kw) -> CanonicalContent:
    cc = CanonicalContent(
        content_key=kw.pop("content_key"), platform=kw.pop("platform"),
        canonical_url=kw.pop("canonical_url", "https://example.com/x"),
        media_kind=kw.pop("media_kind", "video"), **kw)
    db.add(cc)
    db.commit()
    db.refresh(cc)
    return cc


class TestPlaybackDescriptor:
    def test_youtube_is_an_embed_never_a_proxied_stream(self, db):
        """Re-serving YouTube's streams is against their terms. This is the
        line, and it is enforced server-side so no client can cross it."""
        cc = _canonical(db, content_key="youtube:emb1", platform="youtube",
                        platform_content_id="emb1", duration_seconds=30)
        d = P.descriptor_for(db, cc, user_id=1, base_url="https://sava.test")
        assert d.kind == "embed"
        assert d.url.startswith("https://sava.test/api/playback/")
        assert "/stream" not in d.url

    def test_tiktok_is_a_proxied_stream(self, db):
        cc = _canonical(db, content_key="tiktok:str1", platform="tiktok",
                        platform_content_id="str1", duration_seconds=41)
        d = P.descriptor_for(db, cc, user_id=1, base_url="https://sava.test")
        assert d.kind == "video"
        assert d.url.startswith("https://sava.test/api/playback/")
        assert "/stream?t=" in d.url

    def test_tiktok_carousel_is_a_gallery_not_a_broken_video(self, db):
        """The specific bug this branch exists to prevent: a photo post routed
        down the video path is a page that buffers for ever."""
        cc = _canonical(db, content_key="tiktok:car1", platform="tiktok",
                        media_kind="carousel", platform_content_id="car1")
        for i in range(3):
            db.add(ContentAsset(canonical_content_id=cc.id, asset_index=i,
                                kind="cover" if i == 0 else "image",
                                source_url=f"https://cdn.test/{i}.jpg",
                                width=1080, height=1350))
        db.commit()

        d = P.descriptor_for(db, cc, user_id=1, base_url="https://sava.test")
        assert d.kind == "gallery"
        assert [i["index"] for i in d.images] == [0, 1, 2]
        assert d.url is None

    def test_carousel_without_slides_says_so(self, db):
        cc = _canonical(db, content_key="tiktok:car2", platform="tiktok",
                        media_kind="carousel", platform_content_id="car2")
        d = P.descriptor_for(db, cc, user_id=1, base_url="https://sava.test")
        assert d.kind == "unavailable"
        assert d.reason

    def test_instagram_video_is_an_embed_not_a_still(self, db):
        """The regression this replaces: a Reel rendered as its cover image.

        Instagram serves media only to authenticated sessions, so there is no
        stream to proxy — but falling back to a one-item gallery of the poster
        produced a photo that could never play. The sanctioned embed plays.
        """
        cc = _canonical(db, content_key="instagram:ig1", platform="instagram",
                        media_kind="video", platform_content_id="ig1")
        d = P.descriptor_for(db, cc, user_id=1, base_url="https://sava.test")
        assert d.kind == "embed"
        assert "/api/playback/" in (d.url or "")

    def test_instagram_video_never_degrades_to_a_gallery(self, db):
        """Even with a poster stored, a video must not become an image."""
        cc = _canonical(db, content_key="instagram:ig3", platform="instagram",
                        media_kind="video", platform_content_id="ig3",
                        thumbnail_url="https://cdn.example/cover.jpg")
        d = P.descriptor_for(db, cc, user_id=1, base_url="https://sava.test")
        assert d.kind == "embed"

    def test_instagram_without_an_id_refuses_clearly(self, db):
        cc = _canonical(db, content_key="instagram:u:deadbeef",
                        platform="instagram", platform_content_id=None)
        d = P.descriptor_for(db, cc, user_id=1, base_url="https://sava.test")
        assert d.kind == "unavailable"
        assert d.reason

    def test_unsupported_platform_refuses_clearly(self, db):
        cc = _canonical(db, content_key="linkedin:li1", platform="linkedin",
                        platform_content_id="li1")
        d = P.descriptor_for(db, cc, user_id=1, base_url="https://sava.test")
        assert d.kind == "unavailable"
        assert d.reason

    def test_youtube_without_an_id_does_not_build_a_broken_embed(self, db):
        cc = _canonical(db, content_key="youtube:u:deadbeef", platform="youtube",
                        platform_content_id=None)
        d = P.descriptor_for(db, cc, user_id=1, base_url="https://sava.test")
        assert d.kind == "unavailable"


# ─── Playback tokens ─────────────────────────────────────────────────────────

class TestPlaybackTokens:
    def test_a_valid_token_round_trips(self):
        token = P.sign_token(7, 42, int(time.time()) + 600)
        assert P.verify_token(token, 7) == 42

    def test_a_token_is_useless_for_another_item(self):
        """Otherwise one save's token streams the whole library."""
        token = P.sign_token(7, 42, int(time.time()) + 600)
        assert P.verify_token(token, 8) is None

    def test_an_expired_token_is_refused(self):
        token = P.sign_token(7, 42, int(time.time()) - 1)
        assert P.verify_token(token, 7) is None

    def test_a_forged_signature_is_refused(self):
        expires = int(time.time()) + 600
        assert P.verify_token(f"42.{expires}.{'0' * 32}", 7) is None

    def test_a_tampered_user_id_is_refused(self):
        """The user id is inside the signed payload, not merely alongside it."""
        token = P.sign_token(7, 42, int(time.time()) + 600)
        _, expires, digest = token.split(".")
        assert P.verify_token(f"99.{expires}.{digest}", 7) is None

    @pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "1.2", None])
    def test_malformed_tokens_are_refused_rather_than_raising(self, garbage):
        assert P.verify_token(garbage, 7) is None


# ─── Cookie handling ─────────────────────────────────────────────────────────

class _Cookie:
    def __init__(self, name, value, domain):
        self.name, self.value, self.domain = name, value, domain


class TestCookieScoping:
    """`SAVA_YTDLP_COOKIES_FROM_BROWSER` loads the operator's *entire* browser
    jar. Replaying that to a TikTok CDN would hand a third party their live
    sessions for everything else they use."""

    def test_only_platform_cookies_are_replayed(self):
        jar = [
            _Cookie("ttwid", "needed", ".tiktok.com"),
            _Cookie("sid", "also-needed", "v19-webapp-prime.us.tiktok.com"),
            _Cookie("SESSION", "bank-secret", ".bank.example"),
            _Cookie("auth", "mail-secret", ".mail.example"),
            _Cookie("sneaky", "x", "nottiktok.com"),
            _Cookie("cdn", "x", "v1.tiktokcdn-us.com"),
        ]
        kept = P._platform_cookies(jar)
        assert set(kept) == {"ttwid", "sid", "cdn"}

    def test_a_lookalike_domain_is_not_a_platform_domain(self):
        """Suffix matching has to be on a label boundary, or
        `eviltiktok.com` passes as `tiktok.com`."""
        kept = P._platform_cookies([_Cookie("x", "1", "eviltiktok.com")])
        assert kept == {}

    def test_an_empty_jar_is_not_an_error(self):
        assert P._platform_cookies([]) == {}


# ─── The stream route's ownership check ──────────────────────────────────────

class TestStreamOwnership:
    def test_a_token_holder_must_still_own_the_save(self, db):
        """A valid token is not enough. Deleting a save has to stop streaming
        it, and a token minted before deletion outlives the save by an hour."""
        from fastapi.testclient import TestClient

        from api.main import app

        user = make_user(db, "stream-owner@test.dev")
        cc = _canonical(db, content_key="tiktok:own1", platform="tiktok",
                        platform_content_id="own1")
        token = P.sign_token(cc.id, user.id, int(time.time()) + 600)

        client = TestClient(app)
        # No bookmark links this user to this content yet.
        assert client.get(f"/api/playback/{cc.id}/stream?t={token}").status_code == 404

        make_bookmark(db, user.id, "https://tiktok.com/@a/video/own1",
                      platform="tiktok", canonical_content_id=cc.id)
        # Now owned — it gets past the ownership gate and fails later, at the
        # network, which is the mocked-out part and not what this asserts.
        assert client.get(f"/api/playback/{cc.id}/stream?t={token}").status_code != 404

    def test_the_embed_route_refuses_a_bad_token(self, db):
        from fastapi.testclient import TestClient

        from api.main import app

        cc = _canonical(db, content_key="youtube:emb9", platform="youtube",
                        platform_content_id="emb9")
        client = TestClient(app)
        r = client.get(f"/api/playback/{cc.id}/embed?t=nonsense")
        assert r.status_code == 403

class TestStreamWarming:
    """The prefetch only pays off if it warms the expensive half.

    Measured before this existed: the descriptor returned in 4ms and the first
    byte of the stream took 1.5–2.2s, because the yt-dlp resolve ran inline on
    the stream request. Prefetching descriptors three items ahead therefore
    bought almost nothing.
    """

    def test_asking_for_a_tiktok_descriptor_warms_its_stream(self, db, monkeypatch):
        warmed = []
        monkeypatch.setattr(P, "warm_stream",
                            lambda cid, uid=None: warmed.append((cid, uid)))
        cc = _canonical(db, content_key="tiktok:warm1", platform="tiktok",
                        platform_content_id="warm1")
        P.descriptor_for(db, cc, user_id=7, base_url="https://sava.test")
        assert warmed == [(cc.id, 7)]

    def test_an_already_cached_stream_is_not_warmed_again(self, db):
        """Three prefetches of one item must not start three extractions."""
        cc = _canonical(db, content_key="tiktok:warm2", platform="tiktok",
                        platform_content_id="warm2")
        P._cache.put(cc.id, P._Resolved(url="https://cdn.example/v.mp4", headers={},
                                        cookies={}, expires_at=time.time() + 300))
        try:
            calls = []
            original = P.resolve_stream
            P.resolve_stream = lambda *a, **k: calls.append(1)
            try:
                P.warm_stream(cc.id, 7)
            finally:
                P.resolve_stream = original
            assert calls == [], "a cached handle should short-circuit the warm"
        finally:
            P.reset_cache()

    def test_embed_and_gallery_platforms_are_never_warmed(self, db, monkeypatch):
        """Only the proxied path has a stream to resolve."""
        warmed = []
        monkeypatch.setattr(P, "warm_stream",
                            lambda cid, uid=None: warmed.append(cid))
        for key, platform, pid in (("youtube:w1", "youtube", "w1"),
                                   ("instagram:w1", "instagram", "w1")):
            cc = _canonical(db, content_key=key, platform=platform,
                            media_kind="video", platform_content_id=pid)
            P.descriptor_for(db, cc, user_id=1, base_url="https://sava.test")
        assert warmed == []
