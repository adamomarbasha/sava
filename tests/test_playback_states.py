"""Scroll playback: never a black rectangle with nothing behind it.

Reported from a physical device. `/api/bookmarks/{id}/playback` returned
`kind:"embed"` with `poster:null`, and the Scroll screen showed a full-screen
black area with no explanation and no way out.

── Two root causes, both here ──────────────────────────────────────────────

  1. **No poster.** `descriptor_for` read `cc.thumbnail_url` directly. Saves
     made before metadata-first ingestion carry NULL there, so the viewer had
     nothing to hold under the player while it loaded.

  2. **The host page painted the black.** `EMBED_PAGE` set `background:#000`
     and reported nothing: `didFinish` fires for the *page*, which loads fine,
     while the iframe inside it can stay empty forever. A failed embed was
     indistinguishable from a slow one, so the app showed neither a poster nor
     an error.

The client half of the fix is asserted in `test_ios_playback.py`.
"""
from __future__ import annotations

import pytest

from api.models import Bookmark, CanonicalContent, ProcessingState
from api.services import playback


def _cc(**kw):
    base = dict(content_key="youtube:x", platform="youtube",
                platform_content_id="tPEE9ZwTmy0",
                canonical_url="https://youtube.com/watch?v=tPEE9ZwTmy0",
                media_kind="video", stage_status="{}", metadata_json="{}",
                processing_state=ProcessingState.READY)
    base.update(kw)
    return CanonicalContent(**base)


# ─── The poster ──────────────────────────────────────────────────────────────

class TestPosterIsNeverNeedlesslyNull:

    def test_a_stored_thumbnail_wins(self):
        cc = _cc(thumbnail_url="https://cdn.example/stored.jpg")
        assert playback.poster_for(cc) == "https://cdn.example/stored.jpg"

    def test_a_youtube_row_with_no_thumbnail_still_gets_one(self):
        """The reported shape: `thumbnail_url` NULL on a YouTube save."""
        assert playback.poster_for(_cc(thumbnail_url=None)) == \
            "https://i.ytimg.com/vi/tPEE9ZwTmy0/hqdefault.jpg"

    def test_the_descriptor_carries_it(self, clean_db):
        cc = _cc(thumbnail_url=None)
        clean_db.add(cc); clean_db.commit(); clean_db.refresh(cc)
        d = playback.descriptor_for(clean_db, cc, user_id=1, base_url="https://x")
        assert d.kind == "embed"
        assert d.poster and d.poster.endswith("/tPEE9ZwTmy0/hqdefault.jpg")

    @pytest.mark.parametrize("platform,pid", [
        ("tiktok", "7100000000000000000"),
        ("instagram", "Cabc123"),
        ("other", "whatever"),
    ])
    def test_no_poster_is_invented_for_signed_cdns(self, platform, pid):
        """TikTok and Instagram CDN paths are signed and expire. A guessed URL
        that 404s renders as a broken image, which is worse than the designed
        no-image plate."""
        cc = _cc(platform=platform, platform_content_id=pid, thumbnail_url=None)
        assert playback.poster_for(cc) is None

    def test_the_listing_uses_the_same_fallback(self):
        """The grid, the Scroll poster and Ask's source list all read the
        listing, so the repair has to happen there too."""
        import pathlib
        source = (pathlib.Path(__file__).resolve().parent.parent
                  / "api" / "main.py").read_text()
        assert "playback_svc.poster_for(cc)" in source


# ─── The embed host page ─────────────────────────────────────────────────────

class TestEmbedPageReportsItsState:

    @staticmethod
    def _page():
        return playback.embed_page("tPEE9ZwTmy0", "https://sava-api.example")

    def test_it_is_transparent_so_the_poster_shows_through(self):
        """An opaque page hides the poster the app holds behind it, which is
        what made a slow embed look like a black hole."""
        assert "background:transparent" in self._page()
        assert "background:#000" not in self._page()

    def test_it_reports_ready_rather_than_leaving_the_app_guessing(self):
        page = self._page()
        assert "webkit.messageHandlers" in page
        assert "onReady" in page

    def test_it_reports_provider_errors(self):
        """Removed, private, age-gated, embedding-disabled. Each of these used
        to render as an unexplained black rectangle."""
        assert "onError" in self._page()

    def test_an_error_supersedes_a_prior_ready(self):
        """YouTube constructs the player and fires `onReady` *before* it
        discovers the video cannot play, then fires `onError`. A first-wins
        guard latched `ready`, the poster came down, and the user was shown
        YouTube's own unavailable screen colliding with Sava's overlay.

        Observed directly in the simulator:
            embed phase=ready         readyMs=2010
            embed phase=unavailable("The creator doesn't allow …")
        """
        page = self._page()
        assert "if (settled && !fatal) return;" in page
        assert "state === 'unavailable'" in page

    def test_a_watchdog_covers_a_provider_that_never_answers(self):
        """Neither callback fires if the API script itself failed to load."""
        page = self._page()
        assert "setTimeout" in page and "timeout" in page

    def test_it_still_exposes_the_player_control_surface(self):
        page = self._page()
        for fn in ("savaPlay", "savaPause", "savaMute", "savaUnmute"):
            assert f"function {fn}" in page, fn

    def test_the_origin_is_declared(self):
        """A page loaded from a string has no origin and YouTube answers such a
        request with "video unavailable" — which is why this is served over
        HTTP at all."""
        assert "origin: 'https://sava-api.example'" in self._page()


class TestInstagramEmbedPage:

    @staticmethod
    def _page():
        return playback.instagram_embed_page("Cabc123", "https://sava-api.example")

    def test_it_is_transparent_too(self):
        assert "background:transparent" in self._page()

    def test_it_reports_through_the_same_bridge(self):
        page = self._page()
        assert "webkit.messageHandlers" in page
        assert "addEventListener('load'" in page

    def test_it_has_a_watchdog(self):
        assert "timeout" in self._page()


# ─── Kinds ───────────────────────────────────────────────────────────────────

class TestDescriptorKinds:

    def test_youtube_embeds(self, clean_db):
        cc = _cc()
        clean_db.add(cc); clean_db.commit(); clean_db.refresh(cc)
        assert playback.descriptor_for(clean_db, cc, user_id=1,
                                       base_url="https://x").kind == "embed"

    def test_an_unidentifiable_youtube_item_says_so(self, clean_db):
        cc = _cc(platform_content_id=None, content_key="youtube:none")
        clean_db.add(cc); clean_db.commit(); clean_db.refresh(cc)
        d = playback.descriptor_for(clean_db, cc, user_id=1, base_url="https://x")
        assert d.kind == "unavailable"
        assert d.reason

    def test_every_unavailable_descriptor_carries_a_reason(self, clean_db):
        """A viewer that says "this can't be played" is better than one that
        spins — but only if it says *why*."""
        cc = _cc(platform="linkedin", platform_content_id=None,
                 content_key="linkedin:x")
        clean_db.add(cc); clean_db.commit(); clean_db.refresh(cc)
        d = playback.descriptor_for(clean_db, cc, user_id=1, base_url="https://x")
        assert d.kind == "unavailable"
        assert d.reason and d.reason.strip()
