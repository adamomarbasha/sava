"""The cheap-first routing engine.

The property under test throughout: **Sava must not download a video unless it
has a reason to.** The old pipeline downloaded on every TikTok because it asked
"is vision likely?" before anything had classified the item, defaulted the
answer to yes, and never revisited it. These tests pin the corrected ordering
and the escalation rules so that regression cannot come back silently.
"""
from __future__ import annotations

import pytest

from api import plans
from api.pipeline import route
from api.pipeline.route import Route, Signals


def _video(**kw) -> Signals:
    """A short-form video with sane defaults, overridden per test."""
    base = dict(platform="tiktok", media_kind="video", duration_seconds=30.0,
                caption_chars=0, transcript_chars=0, has_caption_track=False,
                has_cover=True, visual_dependency=None, content_type=None,
                media_allowed=True, asr_available=True, force_deep=False)
    base.update(kw)
    return Signals(**base)


# ─── The cheap paths ─────────────────────────────────────────────────────────

class TestCheapFirst:
    def test_captions_are_enough(self):
        """A YouTube video with a transcript never touches the media."""
        plan = route.decide(_video(platform="youtube", transcript_chars=4000,
                                   visual_dependency=0.2, has_cover=False))
        assert plan.route is Route.TEXT
        assert not plan.needs_video and not plan.needs_audio

    def test_a_rich_creator_caption_is_enough(self):
        """TikTok has no captions but often has a real description."""
        plan = route.decide(_video(caption_chars=300, visual_dependency=0.3))
        assert plan.route is Route.COVER          # cover is free, so take it
        assert not plan.needs_video and not plan.needs_audio

    def test_the_cover_read_costs_no_bandwidth(self):
        plan = route.decide(_video(caption_chars=300, visual_dependency=0.3))
        assert plan.reads_cover
        assert not plan.needs_video and not plan.needs_audio

    def test_no_text_falls_back_to_audio_not_video(self):
        """The single most valuable rule: transcribe, don't download the video."""
        plan = route.decide(_video(caption_chars=10, visual_dependency=0.3))
        assert plan.route is Route.AUDIO
        assert plan.needs_audio and not plan.needs_video

    def test_a_thin_caption_is_not_treated_as_signal(self):
        """Three hashtags is not a description."""
        plan = route.decide(_video(caption_chars=25, visual_dependency=0.2))
        assert plan.route is Route.AUDIO

    def test_images_never_download_anything(self):
        for kind in ("image", "carousel"):
            plan = route.decide(_video(media_kind=kind))
            assert not plan.needs_video and not plan.needs_audio

    def test_unknown_visual_dependency_takes_the_cheap_path(self):
        """The old default of 0.5 + 'no transcript' escalated on ignorance.

        An unclassified item must not be assumed expensive.
        """
        plan = route.decide(_video(caption_chars=300, visual_dependency=None))
        assert not plan.needs_video


# ─── Escalation happens, but only on evidence ────────────────────────────────

class TestEscalation:
    def test_high_visual_dependency_reaches_frames(self):
        plan = route.decide(_video(caption_chars=300, transcript_chars=900,
                                   visual_dependency=0.9))
        assert plan.route is Route.LIGHT_VISION
        assert plan.frame_budget == route.LIGHT_FRAME_BUDGET

    def test_a_visual_content_type_with_text_tries_the_cover_first(self):
        """A recipe is visual, but its cover often *is* the ingredient list."""
        plan = route.decide(_video(caption_chars=300, transcript_chars=900,
                                   visual_dependency=0.65, content_type="recipe"))
        assert plan.route is Route.COVER
        assert not plan.needs_video

    def test_thin_results_on_visual_content_escalate(self):
        """Cover first, frames only when the cover did not resolve it."""
        signals = _video(caption_chars=30, visual_dependency=0.7,
                         content_type="recipe")
        plan = route.decide(signals)
        assert plan.route is Route.COVER          # cheap attempt first
        escalated = route.should_escalate_after_text(
            signals, plan, transcript_chars=12, cover_text_chars=0)
        assert escalated is not None
        assert escalated.route is Route.LIGHT_VISION

    def test_no_cover_and_visual_goes_straight_to_frames(self):
        """Nothing free to try, so do not waste a round trip pretending."""
        plan = route.decide(_video(caption_chars=0, visual_dependency=0.7,
                                   has_cover=False))
        assert plan.route is Route.LIGHT_VISION

    def test_a_good_transcript_stops_escalation(self):
        """The rule that keeps the average near the cheap route."""
        signals = _video(caption_chars=30, visual_dependency=0.7,
                         content_type="recipe")
        plan = route.decide(signals)
        assert route.should_escalate_after_text(
            signals, plan, transcript_chars=2000, cover_text_chars=0) is None

    def test_cover_text_alone_can_stop_escalation(self):
        """The cover carried the hook, so the video is not needed."""
        signals = _video(caption_chars=30, visual_dependency=0.7)
        plan = route.decide(signals)
        assert route.should_escalate_after_text(
            signals, plan, transcript_chars=0, cover_text_chars=400) is None

    def test_escalation_never_exceeds_light_without_a_request(self):
        signals = _video(caption_chars=0, visual_dependency=0.99)
        plan = route.decide(signals)
        assert plan.route is Route.LIGHT_VISION   # >= DEEP_VISUAL_THRESHOLD
        assert route.should_escalate_after_text(
            signals, plan, transcript_chars=0) is None   # already at frames

    def test_deep_is_only_ever_explicit(self):
        """No signal combination reaches DEEP_VISION on its own."""
        for vd in (0.0, 0.5, 0.9, 1.0):
            for ct in (None, "recipe", "fashion", "product"):
                for caption in (0, 500):
                    plan = route.decide(_video(visual_dependency=vd,
                                               content_type=ct,
                                               caption_chars=caption))
                    assert plan.route is not Route.DEEP_VISION

    def test_explicit_request_reaches_deep(self):
        plan = route.decide(_video(force_deep=True))
        assert plan.route is Route.DEEP_VISION
        assert plan.frame_budget == route.DEEP_FRAME_BUDGET


# ─── Capability gates are respected ──────────────────────────────────────────

class TestCapabilityGates:
    def test_media_analysis_off_never_downloads(self):
        """The provider kill switch outranks every routing preference."""
        for vd in (0.5, 0.9, 1.0):
            plan = route.decide(_video(visual_dependency=vd, media_allowed=False))
            assert not plan.needs_video and not plan.needs_audio

    def test_deep_request_respects_the_kill_switch(self):
        plan = route.decide(_video(force_deep=True, media_allowed=False))
        assert not plan.needs_video

    def test_no_asr_configured_never_downloads_audio(self):
        """Fetching audio a server cannot transcribe is pure waste."""
        plan = route.decide(_video(caption_chars=0, asr_available=False,
                                   visual_dependency=0.2))
        assert not plan.needs_audio
        assert plan.route is Route.COVER

    def test_escalation_respects_the_kill_switch(self):
        signals = _video(caption_chars=0, visual_dependency=0.9,
                         media_allowed=False)
        plan = route.decide(signals)
        assert route.should_escalate_after_text(
            signals, plan, transcript_chars=0) is None


# ─── Per-platform expectations ───────────────────────────────────────────────

class TestPlatforms:
    def test_youtube_long_form_stays_on_text(self):
        """A 40-minute talking head must never pull frames by default."""
        plan = route.decide(_video(platform="youtube", duration_seconds=2400,
                                   transcript_chars=40000, has_caption_track=True,
                                   visual_dependency=0.3))
        assert plan.route in (Route.TEXT, Route.COVER)
        assert not plan.needs_video

    def test_youtube_short_with_captions_is_cheap(self):
        plan = route.decide(_video(platform="youtube", duration_seconds=45,
                                   transcript_chars=600, has_caption_track=True,
                                   visual_dependency=0.35))
        assert not plan.needs_video

    def test_a_typical_tiktok_does_not_download_video(self):
        """The headline regression test.

        Before routing, this exact item downloaded 7.39 MB of video. Its
        description and cover are enough.
        """
        plan = route.decide(_video(platform="tiktok", caption_chars=180,
                                   visual_dependency=0.45))
        assert not plan.needs_video
        assert plans.units_for_route(plan.route.value) == 1

    def test_a_reel_behaves_like_a_tiktok(self):
        plan = route.decide(_video(platform="instagram", caption_chars=180,
                                   visual_dependency=0.45))
        assert not plan.needs_video


# ─── Metering follows the route ──────────────────────────────────────────────

class TestRouteMetering:
    def test_weights_are_ordered_by_cost(self):
        order = ["cached", "metadata", "text", "cover", "audio",
                 "light_vision", "deep_vision"]
        units = [plans.units_for_route(r) for r in order]
        assert units == sorted(units), units
        usd = [plans.ROUTE_USD[r] for r in order]
        assert usd == sorted(usd), usd

    def test_cached_and_metadata_are_free(self):
        assert plans.units_for_route("cached") == 0
        assert plans.units_for_route("metadata") == 0

    def test_duration_no_longer_changes_the_price(self):
        """A 40-minute YouTube video used to cost 15 units and $0.0097."""
        assert plans.units_for("video", 30) == plans.units_for("video", 3600)

    def test_save_reserves_the_cheap_route(self):
        assert plans.units_for("video", None) == plans.UNITS_ON_SAVE == 1

    def test_an_unknown_route_costs_the_estimate_not_zero(self):
        """Failing closed: a route we cannot price must not be free."""
        assert plans.units_for_route("something_new") == plans.UNITS_ON_SAVE

    def test_content_row_prices_by_its_recorded_route(self):
        class Row:
            route = "light_vision"
            media_kind = "video"
            duration_seconds = 30
        assert plans.units_for_content(Row()) == 8

    def test_a_row_without_a_route_falls_back_to_the_estimate(self):
        class Row:
            route = None
            media_kind = "video"
            duration_seconds = 3600
        assert plans.units_for_content(Row()) == plans.UNITS_ON_SAVE

    def test_published_weights_match_what_is_charged(self):
        for entry in plans.describe_weights():
            assert entry["units"] == plans.units_for_route(entry["route"])


# ─── The economic claim, asserted ────────────────────────────────────────────

class TestCostReduction:
    """These encode the numbers the pricing model depends on.

    If someone changes a weight without changing the model, this fails.
    """

    def test_the_cheap_route_is_an_order_of_magnitude_below_video(self):
        cheap = plans.ROUTE_USD["cover"]
        expensive = plans.ROUTE_USD["light_vision"]
        assert expensive / cheap >= 7

    def test_a_text_routed_tiktok_beats_the_old_measured_cost(self):
        """Measured cost of a TikTok before this work: $0.0310."""
        assert plans.ROUTE_USD["cover"] <= 0.0310 / 10

    def test_one_unit_is_calibrated_to_one_ordinary_video(self):
        assert plans.units_for_route("cover") == 1
        assert abs(plans.ROUTE_USD["cover"] - plans.USD_PER_UNIT) < 0.001
