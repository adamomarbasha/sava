"""Lazy visual escalation: asking a question that needs the picture.

The behaviour these pin down was demonstrated against the live model before it
existed. Asked "what text is shown on screen?" about a transcript-only save,
Sava replied:

    "…right at the very end [26:00], Sean Evans drops some promotional text on
     screen for the Hot Ones and Shake Shack collaboration…"

Nothing had ever looked at that video. It inferred on-screen text from spoken
words and cited a timestamp for it. Three of four visual questions produced
confident visual claims from no visual evidence.

So the two properties under test are: **never claim to have seen what was never
looked at**, and **go and look once, then reuse it forever**.
"""
from __future__ import annotations

import pytest

from api import billing, entitlements, plans
from api.models import (
    Bookmark, CanonicalContent, ContentAsset, ContentFrame, ContentTranscript,
    Job, ProcessingState, UnitReservation,
)
from api.services import visual_ask

from conftest import make_user


# ─── The detector ────────────────────────────────────────────────────────────

class TestVisualQuestionDetection:
    """Precision matters more than recall.

    A false positive downloads a video for a question the transcript could have
    answered. A false negative just yields the honest "I haven't looked yet",
    which is still a correct answer.
    """

    @pytest.mark.parametrize("question", [
        "What color shirt is he wearing?",
        "What ingredients are shown on screen?",
        "What happens visually at the end?",
        "What brand/logo appears in the video?",
        "What text is shown on screen?",
        "How many people are in the clip?",
        "what does he look like",
        "is there anything in the background",
        "what colour is the car",
        "can you see the price",
        "what's the outfit",
        "any tattoos visible",
    ])
    def test_visual_questions_are_detected(self, question):
        assert visual_ask.needs_visual(question) is True

    @pytest.mark.parametrize("question", [
        "summarize this",
        "what did he say about pasta",
        "what are the key points",
        "how long is this video",
        "who made this",
        "what is this about",
        "tl;dr",
        "when was it posted",
        "show me all my recipes",
        "what did they mention about the sauce",
        "give me the main points",
        "what does the transcript say",
        "is this worth watching",
        "what recipe is this",
    ])
    def test_text_questions_do_not_escalate(self, question):
        assert visual_ask.needs_visual(question) is False

    def test_empty_input_is_not_visual(self):
        assert visual_ask.needs_visual("") is False
        assert visual_ask.needs_visual(None) is False


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _content(db, key, *, platform="tiktok", media_kind="video", route="text",
             state=ProcessingState.READY):
    cc = CanonicalContent(
        content_key=key, platform=platform, canonical_url=f"https://x/{key}",
        media_kind=media_kind, duration_seconds=30, title="A video",
        description="something", processing_state=state, processing_level=4,
        route=route, stage_status="{}", metadata_json="{}")
    db.add(cc)
    db.commit()
    db.refresh(cc)
    return cc


def _frames(db, cc, *, ts_ms=5000, n=1):
    for i in range(n):
        db.add(ContentFrame(canonical_content_id=cc.id, ts_ms=ts_ms + i * 1000,
                            ocr_text="3 INGREDIENT PASTA",
                            vision_caption="a bowl on a counter"))
    db.commit()


@pytest.fixture
def user(clean_db):
    return make_user(clean_db, "visual-ask@example.com")


# ─── What counts as already having looked ────────────────────────────────────

class TestVisualCache:
    def test_no_frames_means_not_looked_at(self, clean_db, user):
        cc = _content(clean_db, "tiktok:v1")
        assert visual_ask.has_visual_intelligence(clean_db, cc.id) is False
        assert visual_ask.has_frame_intelligence(clean_db, cc.id) is False

    def test_frames_count_as_looked_at(self, clean_db, user):
        cc = _content(clean_db, "tiktok:v2")
        _frames(clean_db, cc, n=3)
        assert visual_ask.has_visual_intelligence(clean_db, cc.id) is True
        assert visual_ask.has_frame_intelligence(clean_db, cc.id) is True

    def test_a_cover_read_counts_as_visual_but_not_as_frames(self, clean_db, user):
        """The cover answers "what is the hook", not "what happens at the end".

        So a cover-only item still has visual intelligence worth putting in the
        prompt, and is still worth escalating for a question about the video.
        """
        cc = _content(clean_db, "tiktok:v3", route="cover")
        _frames(clean_db, cc, ts_ms=0)          # ts=0 is the cover by convention
        assert visual_ask.has_visual_intelligence(clean_db, cc.id) is True
        assert visual_ask.has_frame_intelligence(clean_db, cc.id) is False

    def test_carousel_slides_count(self, clean_db, user):
        cc = _content(clean_db, "tiktok:v4", media_kind="carousel")
        clean_db.add(ContentAsset(canonical_content_id=cc.id, asset_index=0,
                                  kind="cover", ocr_text="STEP 1"))
        clean_db.commit()
        assert visual_ask.has_visual_intelligence(clean_db, cc.id) is True

    def test_empty_frame_rows_do_not_count(self, clean_db, user):
        """A frame row with nothing read off it is not visual intelligence."""
        cc = _content(clean_db, "tiktok:v5")
        clean_db.add(ContentFrame(canonical_content_id=cc.id, ts_ms=1000))
        clean_db.commit()
        assert visual_ask.has_visual_intelligence(clean_db, cc.id) is False


# ─── Escalation ──────────────────────────────────────────────────────────────

class TestEscalation:
    def test_a_text_question_never_escalates(self, clean_db, user):
        cc = _content(clean_db, "tiktok:e1")
        ctx = visual_ask.prepare(clean_db, cc, user_id=user.id,
                                 question="what did he say about pasta")
        assert ctx.required is False and ctx.escalated is False
        assert clean_db.query(Job).count() == 0
        assert billing.current_period(clean_db, user.id).units_used == 0

    def test_first_visual_question_queues_one_job(self, clean_db, user):
        cc = _content(clean_db, "tiktok:e2")
        ctx = visual_ask.prepare(clean_db, cc, user_id=user.id,
                                 question="what colour is his shirt")
        assert ctx.required and ctx.escalated and not ctx.available
        jobs = clean_db.query(Job).filter(
            Job.idempotency_key == f"content.vision:{cc.id}").all()
        assert len(jobs) == 1
        assert jobs[0].state == "queued"

    def test_the_job_asks_for_vision_and_nothing_else(self, clean_db, user):
        import json
        cc = _content(clean_db, "tiktok:e3")
        visual_ask.prepare(clean_db, cc, user_id=user.id,
                           question="what text is on screen")
        job = clean_db.query(Job).filter(
            Job.idempotency_key == f"content.vision:{cc.id}").first()
        payload = json.loads(job.payload)
        assert payload["want_vision"] is True
        assert not payload.get("deep"), "must never escalate straight to deep"
        assert not payload.get("force"), "must not re-run metadata/transcript"

    def test_three_visual_questions_cause_one_acquisition(self, clean_db, user):
        """The property that makes repeat questions cheap."""
        cc = _content(clean_db, "tiktok:e4")
        for q in ("what colour is the shirt", "what logo appears",
                  "how many people are in the clip"):
            visual_ask.prepare(clean_db, cc, user_id=user.id, question=q)
        assert clean_db.query(Job).filter(
            Job.idempotency_key == f"content.vision:{cc.id}").count() == 1
        # And charged once, not three times.
        assert clean_db.query(UnitReservation).filter(
            UnitReservation.canonical_content_id == cc.id,
            UnitReservation.reason == "vision_escalation").count() == 1

    def test_concurrent_asks_produce_one_job(self, clean_db, user):
        """Two questions racing must not download the video twice."""
        cc = _content(clean_db, "tiktok:e5")
        results = [visual_ask.prepare(clean_db, cc, user_id=user.id,
                                      question="what colour is the shirt")
                   for _ in range(5)]
        assert clean_db.query(Job).filter(
            Job.idempotency_key == f"content.vision:{cc.id}").count() == 1
        assert sum(1 for r in results if r.blocked == "in_flight") == 4

    def test_a_cached_item_reuses_and_charges_nothing(self, clean_db, user):
        """The second visual Ask, and every one after it."""
        cc = _content(clean_db, "tiktok:e6", route="light_vision")
        _frames(clean_db, cc, n=4)
        ctx = visual_ask.prepare(clean_db, cc, user_id=user.id,
                                 question="what colour is the shirt")
        assert ctx.required and ctx.available
        assert ctx.escalated is False and ctx.blocked == "cached"
        assert clean_db.query(Job).count() == 0
        assert billing.current_period(clean_db, user.id).units_used == 0

    def test_a_cover_only_item_still_escalates_for_a_video_question(
            self, clean_db, user):
        cc = _content(clean_db, "tiktok:e7", route="cover")
        _frames(clean_db, cc, ts_ms=0)
        ctx = visual_ask.prepare(clean_db, cc, user_id=user.id,
                                 question="what happens visually at the end")
        assert ctx.available is True      # the cover is real context
        assert ctx.escalated is True      # but the end of the video is not in it


class TestEscalationMetering:
    def test_only_the_difference_is_charged(self, clean_db, user):
        """A text-routed item already paid 1 unit; frames cost 8, so 7 is due.

        Charging the full 8 would bill twice for the transcript nobody
        re-fetched.
        """
        cc = _content(clean_db, "tiktok:m1", route="text")
        ctx = visual_ask.prepare(clean_db, cc, user_id=user.id,
                                 question="what colour is the shirt")
        expected = (plans.units_for_route("light_vision")
                    - plans.units_for_route("text"))
        assert ctx.units_charged == expected == 7
        assert billing.current_period(clean_db, user.id).units_used == 7

    def test_an_audio_routed_item_pays_less(self, clean_db, user):
        cc = _content(clean_db, "tiktok:m2", route="audio")
        ctx = visual_ask.prepare(clean_db, cc, user_id=user.id,
                                 question="what colour is the shirt")
        assert ctx.units_charged == 8 - 3 == 5

    def test_escalation_never_costs_zero(self, clean_db, user):
        """Even if the recorded route already priced above frames."""
        cc = _content(clean_db, "tiktok:m3", route="deep_vision")
        ctx = visual_ask.prepare(clean_db, cc, user_id=user.id,
                                 question="what colour is the shirt")
        assert ctx.units_charged >= 1


# ─── Exhausted allowance ─────────────────────────────────────────────────────

class TestQuotaExhaustion:
    def _exhaust(self, db, user):
        e = entitlements.for_user(db, user.id)
        billing.reserve_units(db, user.id, units=e.limits.processing_units,
                              entitlement=e, canonical_content_id=999999)

    def test_no_job_is_queued_and_nothing_is_deleted(self, clean_db, user):
        cc = _content(clean_db, "tiktok:q1")
        bm = Bookmark(user_id=user.id, url="https://x/q1", platform="tiktok",
                      raw="{}", canonical_content_id=cc.id)
        clean_db.add(bm)
        clean_db.commit()
        self._exhaust(clean_db, user)

        ctx = visual_ask.prepare(clean_db, cc, user_id=user.id,
                                 question="what colour is the shirt")
        assert ctx.blocked == "quota" and ctx.escalated is False
        assert clean_db.query(Job).count() == 0
        # The save is untouched.
        assert clean_db.query(Bookmark).get(bm.id) is not None
        assert bm.url == "https://x/q1"

    def test_the_model_is_still_told_not_to_invent(self, clean_db, user):
        """Exhaustion must not become a licence to hallucinate."""
        cc = _content(clean_db, "tiktok:q2")
        self._exhaust(clean_db, user)
        ctx = visual_ask.prepare(clean_db, cc, user_id=user.id,
                                 question="what colour is the shirt")
        note = visual_ask.context_note(ctx)
        assert note is not None
        assert "have NOT seen" in note
        assert "allowance" in note

    def test_free_is_offered_an_upgrade_and_pro_is_not(self, clean_db, user):
        from api.services import subscription as sub_svc
        cc = _content(clean_db, "tiktok:q3")
        self._exhaust(clean_db, user)
        assert visual_ask.prepare(clean_db, cc, user_id=user.id,
                                  question="what colour is the shirt"
                                  ).upgrade_available is True

        from test_subscription import _transaction
        sub_svc.apply_transaction(clean_db, user.id, _transaction(original="VA-1"))
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id, units=e.limits.processing_units,
                              entitlement=e, canonical_content_id=999998)
        cc2 = _content(clean_db, "tiktok:q4")
        assert visual_ask.prepare(clean_db, cc2, user_id=user.id,
                                  question="what colour is the shirt"
                                  ).upgrade_available is False


# ─── Capability gates ────────────────────────────────────────────────────────

class TestGatesRespected:
    def test_a_disabled_provider_is_never_escalated(self, clean_db, user, monkeypatch):
        """The kill switch outranks a user's question."""
        from api import providers
        monkeypatch.setattr(providers, "media_analysis_allowed", lambda p: False)
        cc = _content(clean_db, "tiktok:g1")
        ctx = visual_ask.prepare(clean_db, cc, user_id=user.id,
                                 question="what colour is the shirt")
        assert ctx.blocked == "not_allowed" and ctx.escalated is False
        assert clean_db.query(Job).count() == 0
        assert billing.current_period(clean_db, user.id).units_used == 0

    def test_an_image_has_no_video_to_fetch(self, clean_db, user):
        cc = _content(clean_db, "instagram:g2", platform="instagram",
                      media_kind="image", route="cover")
        ctx = visual_ask.prepare(clean_db, cc, user_id=user.id,
                                 question="what colour is the shirt")
        assert ctx.blocked == "no_video" and ctx.escalated is False
        assert clean_db.query(Job).count() == 0

    def test_escalation_can_be_disabled_outright(self, clean_db, user):
        cc = _content(clean_db, "tiktok:g3")
        ctx = visual_ask.prepare(clean_db, cc, user_id=user.id,
                                 question="what colour is the shirt",
                                 allow_escalation=False)
        assert ctx.escalated is False and clean_db.query(Job).count() == 0


# ─── The anti-hallucination note ─────────────────────────────────────────────

class TestBlindNote:
    def test_no_note_when_the_question_is_not_visual(self, clean_db, user):
        ctx = visual_ask.VisualContext(required=False, available=False)
        assert visual_ask.context_note(ctx) is None

    def test_no_note_when_visual_intelligence_exists(self, clean_db, user):
        ctx = visual_ask.VisualContext(required=True, available=True)
        assert visual_ask.context_note(ctx) is None

    def test_the_note_forbids_exactly_what_the_model_actually_did(self):
        """It invented on-screen text and cited [26:00] for it."""
        ctx = visual_ask.VisualContext(required=True, available=False)
        note = visual_ask.context_note(ctx)
        for forbidden in ("on-screen text", "wearing", "colours", "logos",
                          "counts of people", "timestamp"):
            assert forbidden in note, forbidden
        assert "have NOT seen" in note

    def test_a_queued_escalation_is_mentioned(self):
        ctx = visual_ask.VisualContext(required=True, available=False,
                                       escalated=True)
        assert "watching the video now" in visual_ask.context_note(ctx)


# ─── Reuse across users ──────────────────────────────────────────────────────

class TestCanonicalReuse:
    def test_a_second_user_reuses_the_first_users_frames(self, clean_db, user):
        """Derived visual intelligence hangs off canonical content, so the
        second person to ask pays nothing — the same rule as the save path."""
        cc = _content(clean_db, "tiktok:r1", route="light_vision")
        _frames(clean_db, cc, n=4)
        other = make_user(clean_db, "second-asker@example.com")

        ctx = visual_ask.prepare(clean_db, cc, user_id=other.id,
                                 question="what colour is the shirt")
        assert ctx.available and not ctx.escalated
        assert billing.current_period(clean_db, other.id).units_used == 0

    def test_a_second_user_joins_an_in_flight_job(self, clean_db, user):
        cc = _content(clean_db, "tiktok:r2")
        visual_ask.prepare(clean_db, cc, user_id=user.id,
                           question="what colour is the shirt")
        other = make_user(clean_db, "joiner@example.com")
        ctx = visual_ask.prepare(clean_db, cc, user_id=other.id,
                                 question="what logo is shown")
        assert ctx.blocked == "in_flight"
        assert billing.current_period(clean_db, other.id).units_used == 0
        assert clean_db.query(Job).filter(
            Job.idempotency_key == f"content.vision:{cc.id}").count() == 1
