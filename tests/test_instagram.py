"""Instagram ingestion.

No test here touches Instagram. Providers are stubbed, so what is asserted is
Sava's own behaviour: what it accepts, what it refuses, what it stores, and —
most importantly — what it does when extraction fails, which on this platform is
a routine outcome rather than an exceptional one.

The properties worth protecting, in order of how much damage losing them does:

  * a screenshot can never become canonical Instagram identity,
  * a profile, feed or stale clipboard URL never becomes a library item,
  * every URL shape for one post is one canonical row,
  * extraction failure keeps the user's content instead of discarding it,
  * nothing is ever invented to fill a gap,
  * a thumbnail survives its source CDN URL expiring.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conftest import make_bookmark, make_user

from api.content.identity import (
    is_instagram_content_url, is_instagram_share_link, resolve_identity,
)
from api.models import CanonicalContent, ContentAsset
from api.services import instagram as ig


# ─── Canonicalization ────────────────────────────────────────────────────────

class TestInstagramCanonicalization:
    @pytest.mark.parametrize("url", [
        "https://www.instagram.com/p/DOEn9TGlLF9/",
        "https://instagram.com/p/DOEn9TGlLF9",
        "https://www.instagram.com/reel/DOEn9TGlLF9/",
        "https://www.instagram.com/reels/DOEn9TGlLF9/",
        "https://www.instagram.com/tv/DOEn9TGlLF9/",
        "https://m.instagram.com/p/DOEn9TGlLF9/",
        "https://www.instagram.com/zendaya/p/DOEn9TGlLF9/",
        "https://www.instagram.com/share/p/DOEn9TGlLF9/",
        "https://www.instagram.com/p/DOEn9TGlLF9/?igsh=MWx0eXo&img_index=1",
        "https://www.instagram.com/p/DOEn9TGlLF9/?utm_source=ig_web_copy_link",
    ])
    def test_every_shape_is_one_canonical_identity(self, url):
        ident = resolve_identity(url)
        assert ident is not None, url
        assert ident.content_key == "instagram:DOEn9TGlLF9"
        assert ident.platform == "instagram"
        assert ident.canonical_url == "https://instagram.com/p/DOEn9TGlLF9"

    def test_reel_and_post_urls_do_not_split(self):
        """The duplication that would double the extraction bill."""
        a = resolve_identity("https://www.instagram.com/reel/ABCDE12345/")
        b = resolve_identity("https://www.instagram.com/p/ABCDE12345/")
        assert a.content_key == b.content_key

    def test_tracking_parameters_are_removed(self):
        ident = resolve_identity(
            "https://www.instagram.com/p/DOEn9TGlLF9/?igshid=x&utm_campaign=y&fbclid=z")
        assert "igshid" not in ident.canonical_url
        assert "utm_campaign" not in ident.canonical_url
        assert "fbclid" not in ident.canonical_url

    def test_a_reel_is_known_to_be_video_from_the_url(self):
        assert resolve_identity(
            "https://www.instagram.com/reel/ABCDE12345/").media_kind == "video"

    def test_a_plain_post_is_not_assumed_to_be_a_carousel(self):
        """`/p/` is used for photos, videos and carousels alike.

        The old rule called every `/p/` a carousel, which mislabelled a plain
        photo from the moment it was saved and sent it down the wrong pipeline.
        """
        assert resolve_identity(
            "https://www.instagram.com/p/ABCDE12345/").media_kind == "unknown"


class TestNonContentUrlsAreRefused:
    @pytest.mark.parametrize("url", [
        "https://www.instagram.com/zendaya/",
        "https://instagram.com/some.user",
        "https://www.instagram.com/",
        "https://www.instagram.com/explore/",
        "https://www.instagram.com/explore/tags/food/",
        "https://www.instagram.com/accounts/login/",
        "https://www.instagram.com/accounts/emailsignup/",
        "https://www.instagram.com/direct/inbox/",
        "https://www.instagram.com/reels/",
        "https://www.instagram.com/stories/zendaya/3412/",
    ])
    def test_profiles_feeds_and_logins_never_become_content(self, url):
        """Returning None is what stops a library item being created at all.

        Falling through to the URL-hash fallback would manufacture a canonical
        row for a profile page, and once created it is indistinguishable from a
        real post.
        """
        assert resolve_identity(url) is None, url

    @pytest.mark.parametrize("url", [
        "https://www.instagram.com/p/DOEn9TGlLF9/",
        "https://www.instagram.com/reel/DOEn9TGlLF9/",
        "https://www.instagram.com/share/p/DOEn9TGlLF9/",
        "https://www.instagram.com/share/BAgTz9k1B/",
    ])
    def test_content_and_share_urls_are_accepted_for_capture(self, url):
        assert is_instagram_content_url(url)

    @pytest.mark.parametrize("url", [
        "https://www.instagram.com/zendaya/",
        "https://www.instagram.com/explore/",
        "https://news.example.com/article-from-an-hour-ago",
        "https://www.google.com/search?q=cats",
        "",
    ])
    def test_stale_clipboard_urls_are_rejected_for_capture(self, url):
        assert not is_instagram_content_url(url)

    def test_a_bare_share_link_is_not_treated_as_a_shortcode(self):
        """`/share/<token>` is an opaque redirect key, not a post id.

        Accepting it as a shortcode would key the canonical row on a token that
        resolves to a post already stored under its real id.
        """
        assert is_instagram_share_link("https://www.instagram.com/share/BAgTz9k1B/")
        ident = resolve_identity("https://www.instagram.com/share/BAgTz9k1B/")
        assert ident is not None
        assert ident.is_resolvable is False
        assert ident.platform_content_id is None


# ─── Open Graph parsing ──────────────────────────────────────────────────────

_OG_HTML = """
<html><head>
<meta property="og:title" content="Zendaya on Instagram: &quot;Just coming on here to say thanks&quot;" />
<meta property="og:description" content="6M likes, 20K comments - zendaya on September 1, 2025: &quot;Just coming on here to say thanks&quot;" />
<meta property="og:image" content="https://scontent.cdninstagram.com/v/t51/541396029_n.jpg?oh=abc" />
<meta property="og:type" content="article" />
</head><body></body></html>
"""


class TestOpenGraphParsing:
    def test_fields_are_read_and_attributed(self):
        tags = ig.parse_og_tags(_OG_HTML)
        meta = ig.metadata_from_og(tags, shortcode="X1", canonical_url="u").stamp("opengraph")

        assert meta.creator_name == "Zendaya"
        assert meta.creator_handle == "zendaya"
        assert meta.caption == "Just coming on here to say thanks"
        assert meta.published_at == datetime(2025, 9, 1, tzinfo=timezone.utc)
        assert meta.like_count == 6_000_000
        assert meta.comment_count == 20_000
        assert meta.thumbnail_url.startswith("https://scontent.cdninstagram.com/")
        # Provenance answers "how do we know this" for every stored value.
        assert meta.provenance["creator_handle"] == "opengraph"

    def test_nothing_is_invented_when_tags_are_sparse(self):
        """Missing metadata is acceptable. Placeholder metadata is not."""
        tags = ig.parse_og_tags(
            '<meta property="og:image" content="https://cdn.example/x.jpg">')
        meta = ig.metadata_from_og(tags, shortcode="X2", canonical_url="u")
        assert meta.thumbnail_url
        assert meta.creator_name is None
        assert meta.creator_handle is None
        assert meta.caption is None
        assert meta.published_at is None
        # Notably not "Instagram Post", which is what the old ingestor wrote.
        assert meta.media_kind is None

    def test_a_page_without_tags_yields_nothing(self):
        assert ig.metadata_from_og({}, shortcode="X3", canonical_url="u") is None

    @pytest.mark.parametrize("raw,expected", [
        ("6M", 6_000_000), ("20K", 20_000), ("1.5M", 1_500_000),
        ("1,234", 1234), ("7", 7), ("", None), (None, None), ("abc", None),
    ])
    def test_engagement_counts_parse_or_stay_absent(self, raw, expected):
        assert ig._parse_count(raw) == expected


# ─── Provider chain ──────────────────────────────────────────────────────────

class _StubProvider(ig.InstagramMetadataProvider):
    def __init__(self, name, result):
        self.name = name
        self._result = result
        self.calls = 0

    def extract(self, shortcode, canonical_url):
        self.calls += 1
        return self._result


class TestProviderChain:
    def test_a_terminal_failure_stops_the_chain(self, monkeypatch):
        """Asking a second provider about a deleted post spends a request to be
        told the same thing."""
        first = _StubProvider("first", ig.ProviderResult(
            False, "first", failure_reason=ig.FailureReason.NOT_FOUND))
        second = _StubProvider("second", ig.ProviderResult(
            True, "second", metadata=ig.InstagramMetadata("X", "u")))
        monkeypatch.setattr(ig, "get_providers", lambda: [first, second])

        result = ig.extract_metadata("X", "u")
        assert result.ok is False
        assert second.calls == 0

    def test_a_transient_failure_falls_through(self, monkeypatch):
        first = _StubProvider("first", ig.ProviderResult(
            False, "first", failure_reason=ig.FailureReason.NETWORK))
        second = _StubProvider("second", ig.ProviderResult(
            True, "second", metadata=ig.InstagramMetadata("X", "u")))
        monkeypatch.setattr(ig, "get_providers", lambda: [first, second])

        result = ig.extract_metadata("X", "u")
        assert result.ok and result.provider == "second"
        assert second.calls == 1

    def test_no_configured_provider_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setattr(ig, "get_providers", lambda: [])
        result = ig.extract_metadata("X", "u")
        assert result.ok is False
        assert result.failure_reason == ig.FailureReason.UNAVAILABLE

    def test_ytdlp_is_off_unless_deliberately_configured(self):
        """It needs an operator Instagram account, which does not scale."""
        assert ig.YtDlpProvider().available is False

    def test_opengraph_is_the_default_chain(self):
        assert [p.name for p in ig.get_providers()] == ["opengraph"]


# ─── Ingestion behaviour ─────────────────────────────────────────────────────

def _canonical(db, shortcode="ING12345AB", **kw):
    cc = CanonicalContent(
        content_key=f"instagram:{shortcode}", platform="instagram",
        platform_content_id=shortcode,
        canonical_url=f"https://instagram.com/p/{shortcode}",
        media_kind=kw.pop("media_kind", "unknown"), **kw)
    db.add(cc)
    db.commit()
    db.refresh(cc)
    return cc


class TestIngestionBehaviour:
    def test_provider_failure_keeps_the_content(self, db, monkeypatch):
        """The rule that matters most: extraction failing must never cost the
        user the thing they saved."""
        from api.pipeline.ingest import _ingest_instagram

        user = make_user(db, "ig-fail@test.dev")
        cc = _canonical(db, "IGFAIL0001")
        bm = make_bookmark(db, user.id, cc.canonical_url, platform="instagram")
        bm.canonical_content_id = cc.id
        db.commit()

        monkeypatch.setattr(
            ig, "extract_metadata",
            lambda *a, **k: ig.ProviderResult(
                False, "opengraph", error="gated",
                failure_reason=ig.FailureReason.LOGIN_REQUIRED))

        out = _ingest_instagram(db, cc)
        db.refresh(cc)

        assert out["status"].startswith("failed:")
        # Canonical identity survives, and so does the library reference.
        assert db.query(CanonicalContent).filter(
            CanonicalContent.id == cc.id).first() is not None
        assert cc.canonical_url.endswith("IGFAIL0001")
        # The reason is structured, so a retry policy can act on it.
        assert ig.FailureReason.LOGIN_REQUIRED in (cc.last_error or "")
        # And nothing was invented to paper over the gap.
        assert cc.title is None and cc.creator_name is None

    def test_metadata_is_stored_with_provenance(self, db, monkeypatch):
        from api.pipeline.ingest import _ingest_instagram
        import json

        cc = _canonical(db, "IGOK000001")
        meta = ig.InstagramMetadata(
            shortcode="IGOK000001", canonical_url=cc.canonical_url,
            creator_name="Zendaya", creator_handle="zendaya",
            caption="a caption", media_kind="image").stamp("opengraph")
        monkeypatch.setattr(ig, "extract_metadata",
                            lambda *a, **k: ig.ProviderResult(True, "opengraph", metadata=meta))

        _ingest_instagram(db, cc)
        db.refresh(cc)

        assert cc.creator_name == "Zendaya"
        assert cc.creator_handle == "zendaya"
        assert cc.media_kind == "image"
        sources = json.loads(cc.metadata_json)["field_sources"]
        assert sources["creator_name"] == "opengraph"

    def test_carousel_children_keep_their_order(self, db, monkeypatch):
        from api.pipeline.ingest import _ingest_instagram

        cc = _canonical(db, "IGCAR00001")
        meta = ig.InstagramMetadata(
            shortcode="IGCAR00001", canonical_url=cc.canonical_url,
            media_kind="carousel", carousel_count=3,
            children=[
                {"index": 0, "media_type": "image",
                 "source_url": "https://cdn.example/a.jpg", "width": 1080, "height": 1350},
                {"index": 1, "media_type": "image",
                 "source_url": "https://cdn.example/b.jpg"},
                {"index": 2, "media_type": "video",
                 "source_url": "https://cdn.example/c.jpg"},
            ]).stamp("ytdlp")
        monkeypatch.setattr(ig, "extract_metadata",
                            lambda *a, **k: ig.ProviderResult(True, "ytdlp", metadata=meta))
        # Mirroring is exercised separately; here it must not reorder anything.
        monkeypatch.setattr("api.services.thumbnails.mirror_to_storage",
                            lambda *a, **k: None)

        _ingest_instagram(db, cc, force=True)
        db.refresh(cc)

        assets = (db.query(ContentAsset)
                  .filter(ContentAsset.canonical_content_id == cc.id)
                  .order_by(ContentAsset.asset_index).all())
        assert [a.asset_index for a in assets] == [0, 1, 2]
        assert [a.source_url for a in assets] == [
            "https://cdn.example/a.jpg",
            "https://cdn.example/b.jpg",
            "https://cdn.example/c.jpg"]
        # Slide one is the cover — the image the creator chose.
        assert assets[0].kind == "cover"
        assert cc.media_kind == "carousel"

    def test_two_users_saving_one_reel_share_one_canonical(self, db):
        """The scale property: N users referencing a post is not N extractions."""
        from api.pipeline.ingest import resolve_or_create_canonical

        alice = make_user(db, "ig-alice@test.dev")
        bob = make_user(db, "ig-bob@test.dev")

        cc_a, created_a = resolve_or_create_canonical(
            db, "https://www.instagram.com/reel/SHARED1234/")
        cc_b, created_b = resolve_or_create_canonical(
            db, "https://www.instagram.com/p/SHARED1234/?igsh=abc")

        assert cc_a.id == cc_b.id
        assert created_a is True and created_b is False
        assert db.query(CanonicalContent).filter(
            CanonicalContent.content_key == "instagram:SHARED1234").count() == 1


class TestDurableThumbnails:
    def test_a_mirrored_thumbnail_outlives_its_source_url(self, db, monkeypatch):
        """Instagram signs its CDN URLs with a short expiry. Once mirrored, the
        library must not care that the original stopped resolving."""
        from api.pipeline.ingest import _mirror_cover

        cc = _canonical(db, "IGMIR00001",
                        thumbnail_url="https://scontent.cdninstagram.com/v/expiring.jpg?oh=x")
        monkeypatch.setattr(
            "api.services.thumbnails.mirror_to_storage",
            lambda *a, **k: ("thumbnails/durable.jpg", "/static/objects/thumbnails/durable.jpg"))

        assert _mirror_cover(db, cc) is True
        db.refresh(cc)
        assert cc.thumbnail_stored_key == "thumbnails/durable.jpg"
        assert "cdninstagram" not in cc.thumbnail_url

    def test_a_failed_mirror_leaves_the_record_usable(self, db, monkeypatch):
        from api.pipeline.ingest import _mirror_cover

        cc = _canonical(db, "IGMIR00002",
                        thumbnail_url="https://scontent.cdninstagram.com/v/gone.jpg")
        monkeypatch.setattr("api.services.thumbnails.mirror_to_storage",
                            lambda *a, **k: None)

        assert _mirror_cover(db, cc) is False
        db.refresh(cc)
        assert cc.thumbnail_url  # still points somewhere; nothing was destroyed


class TestScreenshotCannotClaimIdentity:
    def test_a_capture_never_collides_with_a_real_post(self, db):
        """A screenshot capture and a real post must be different rows even for
        the same creator — a screenshot cannot establish which post it shows."""
        from api.services.save import create_partial_capture

        user = make_user(db, "ig-shot@test.dev")
        real = _canonical(db, "REALPOST01")

        captured = create_partial_capture(
            db, user_id=user.id, platform="instagram",
            read={"creator": "zendaya", "caption": "some caption",
                  "on_screen_text": "text"},
            screenshot=b"\xff\xd8\xff\xe0" + b"0" * 256)

        cc = db.query(CanonicalContent).filter(
            CanonicalContent.id == captured["canonical_id"]).first()
        assert cc.id != real.id
        assert cc.content_key.startswith("instagram:partial:")
        assert cc.content_key != real.content_key
        # It does not claim to be a video, and it does not claim to be READY.
        assert cc.media_kind == "capture"
        assert cc.processing_state == "partial"

    def test_a_capture_is_not_short_form_playable(self):
        """It has no media to play and no post it can honestly link to."""
        from api.content.shortform import is_short_form
        assert is_short_form("instagram", media_kind="capture") is False
