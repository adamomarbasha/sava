"""Lazy visual escalation: paying for frames when a *question* needs them.

── What this fixes ─────────────────────────────────────────────────────────

Routing means most videos are understood from captions and description alone,
which is right: it is 78% cheaper and it is enough for almost every question
somebody actually asks. But it leaves a hole, and the hole was demonstrated
rather than theorised. Asked four visual questions about a transcript-only save,
the live model produced:

    Q: What text is shown on screen?
    A: "…right at the very end [26:00], Sean Evans drops some promotional text
        on screen for the Hot Ones and Shake Shack collaboration…"

Nothing had ever looked at that video. The model inferred on-screen text from
spoken words and cited a timestamp for it. Three of four visual questions
produced confident visual claims with no visual evidence.

Two things were missing and both are here:

  1. **Honesty.** When a question needs the picture and Sava has never seen it,
     the model has to be told so. It cannot infer the absence of a modality from
     context that simply does not mention it.
  2. **Escalation.** The right answer to "what colour is his shirt" is not a
     permanent refusal — it is to go and look, once, and cache it.

── Why the detector is deterministic ───────────────────────────────────────

A model call to decide whether to make a model call is self-defeating, and this
one runs on every Ask. The same reasoning as `router.classify_question`, which
this deliberately mirrors.

Precision matters more than recall here. A false positive spends ~$0.015 and
downloads a video for a question the transcript could have answered; a false
negative just means the honest "I haven't looked at the picture" answer, which
is still a correct answer. So the patterns are narrow and the negative list is
long.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ─── Detecting a question that needs the picture ─────────────────────────────

#: Things that can only be known by looking. Each alternative is anchored to
#: vocabulary that a transcript genuinely cannot supply.
_VISUAL = re.compile(
    r"""
    \b(?:
        # appearance and clothing
        wear(?:ing|s)? | dressed | outfit | shirt | jacket | hoodie | dress\b
      | hair(?:style|cut)? | tattoos? | makeup | piercing
        # colour
      | colou?rs? | colou?red
        # things rendered on screen
      | on[-\s]?screen | on\s+the\s+screen | caption\s+text | subtitle[sd]?
      | overlay | text\s+(?:on|in)\s+(?:the\s+)?(?:screen|video|frame|image)
      | what\s+(?:does|do)\s+(?:it|the\s+\w+)\s+say\s+on
        # brands and marks that appear rather than are mentioned
      | logos? | brand(?:ing)?\s+(?:appear|show|visible)
      | (?:appear|shown?|visible|displayed)\s+(?:on|in)\s+(?:the\s+)?
        (?:screen|video|frame|background|shot)
        # composition
      | background | foreground | on\s+the\s+(?:left|right) | camera\s+angle
      | thumbnail | scenery | setting\s+look
        # counting or identifying things seen
      | how\s+many\s+(?:people|persons|men|women|items|things|objects|ingredients)
      | what\s+(?:does|do)\s+(?:he|she|they|it)\s+look\s+like
      | what\s+(?:is|are)\s+(?:shown|displayed|visible|pictured)
      | what\s+(?:ingredients?|products?|items?|objects?)\s+
        (?:are\s+)?(?:shown|visible|displayed|on\s+screen)
        # explicit references to seeing
      | can\s+you\s+see | do\s+you\s+see | what\s+do\s+you\s+see
      | visually | in\s+the\s+(?:image|picture|frame|footage|video\s+itself)
      | happens?\s+visually
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Questions that merely *sound* visual. "Show me recipes" is a library search;
#: "what does he show" is usually answered by narration. These veto a match, so
#: the expensive path is not taken for something the transcript already covers.
_NOT_VISUAL = re.compile(
    r"""
    \b(?:
        summar(?:y|ise|ize) | tl;?dr | main\s+points? | key\s+points?
      | what(?:'s| is)\s+(?:it|this)\s+about | what\s+did\s+(?:he|she|they)\s+say
      | transcript | quote | mention(?:ed|s)? | recipe\s+(?:link|url)
      | how\s+long | duration | when\s+was\s+it\s+(?:posted|published)
      | who\s+(?:made|posted|created)\s+(?:it|this)
      | show\s+me\s+(?:all|my|other|more)      # library search phrasing
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def needs_visual(question: str) -> bool:
    """True when answering honestly requires having looked at the picture.

    Deterministic and free. Runs on every Ask.
    """
    q = (question or "").strip()
    if not q:
        return False
    if _NOT_VISUAL.search(q):
        return False
    return bool(_VISUAL.search(q))


# ─── What visual intelligence already exists ─────────────────────────────────

def has_visual_intelligence(db, canonical_id: Optional[int]) -> bool:
    """Has anything ever actually looked at this item's imagery?

    `ContentFrame` is the cache. It holds both sampled video frames and the
    ts=0 cover reading, so an item routed through `cover` already counts as
    having been looked at — its hook text is cached and reusable, and that is
    frequently the whole answer.

    Carousels and images store their reading on `ContentAsset` instead, because
    the slides *are* the content rather than samples of it.
    """
    if not canonical_id:
        return False
    from ..models import ContentAsset, ContentFrame

    frames = (db.query(ContentFrame)
              .filter(ContentFrame.canonical_content_id == canonical_id)
              .filter((ContentFrame.ocr_text.isnot(None))
                      | (ContentFrame.vision_caption.isnot(None)))
              .count())
    if frames:
        return True
    return bool(db.query(ContentAsset)
                .filter(ContentAsset.canonical_content_id == canonical_id)
                .filter((ContentAsset.ocr_text.isnot(None))
                        | (ContentAsset.vision_caption.isnot(None)))
                .count())


def has_frame_intelligence(db, canonical_id: Optional[int]) -> bool:
    """Has anything looked at frames *from the video*, beyond the cover?

    The cover answers "what is the hook" but not "what happens at the end", so a
    cover-only item can still be worth escalating. `ts_ms > 0` is the test: the
    cover is stored at ts=0 by construction.
    """
    if not canonical_id:
        return False
    from ..models import ContentFrame
    return bool(db.query(ContentFrame)
                .filter(ContentFrame.canonical_content_id == canonical_id,
                        ContentFrame.ts_ms > 0).count())


# ─── The escalation decision ─────────────────────────────────────────────────

@dataclass(frozen=True)
class VisualContext:
    """What Ask should do about the visual channel for this question."""

    #: The question needs the picture.
    required: bool
    #: Sava has visual intelligence for this item and it is in the context.
    available: bool
    #: A job was just queued to go and look.
    escalated: bool = False
    #: Why nothing was queued: "quota", "not_allowed", "in_flight", "cached", …
    blocked: Optional[str] = None
    #: Units charged for the escalation, if any.
    units_charged: int = 0
    #: Offer an upgrade — the allowance ran out and Pro would raise it.
    upgrade_available: bool = False

    @property
    def should_warn(self) -> bool:
        """Tell the model it is blind, so it does not invent."""
        return self.required and not self.available

    def public(self) -> Dict[str, Any]:
        """Additive fields on the Ask response. Older clients ignore them."""
        out: Dict[str, Any] = {"visual_required": self.required,
                               "visual_available": self.available}
        if self.escalated:
            out["visual_processing"] = True
        if self.blocked:
            out["visual_blocked"] = self.blocked
        if self.upgrade_available:
            out["upgrade_available"] = True
        return out


#: The context note added when a visual question meets an item Sava has not
#: looked at. This is the anti-hallucination guard, and it is deliberately
#: blunt: the measured failure was a model inventing on-screen text *and a
#: timestamp for it* from spoken words, which a gentle hint does not prevent.
BLIND_NOTE = (
    "IMPORTANT: you have NOT seen this item's imagery. No frames, no on-screen "
    "text, and no visual description exist for it — everything above came from "
    "audio, captions or metadata. The question asks about something visual. Do "
    "NOT describe what appears on screen, what anyone is wearing, colours, "
    "logos, backgrounds, counts of people, or on-screen text, and do not infer "
    "them from what was said. Say plainly that you have not looked at the video "
    "itself yet. Do not cite a timestamp for anything visual."
)

QUEUED_NOTE = (
    " Sava is watching the video now — say that it will only take a moment and "
    "they can ask again."
)

EXHAUSTED_NOTE = (
    " Their monthly video-understanding allowance is used up, so Sava cannot "
    "watch it right now. Mention that briefly and without alarm."
)


def prepare(db, cc, *, user_id: int, question: str,
            allow_escalation: bool = True) -> VisualContext:
    """Decide what to do about the visual channel for one Ask.

    Called by `ask_this` before the model runs. Never blocks on acquisition:
    the job is queued and the current question is answered honestly from what
    exists. A 7 MB download plus ffmpeg plus a vision call inside an HTTP
    request would hold a worker for tens of seconds and violates the same rule
    that keeps `create_save` free of network I/O.
    """
    required = needs_visual(question)
    if not required:
        return VisualContext(required=False, available=False)

    canonical_id = getattr(cc, "id", None)
    available = has_visual_intelligence(db, canonical_id)

    # Already looked at the frames — nothing to do, and nothing to charge. This
    # is the path every question after the first takes.
    if has_frame_intelligence(db, canonical_id):
        return VisualContext(required=True, available=True, blocked="cached")

    if cc is None or not allow_escalation:
        return VisualContext(required=True, available=available,
                             blocked="no_content" if cc is None else "disabled")

    # Images and carousels have no video to fetch; their slides are already read.
    if getattr(cc, "media_kind", None) in ("image", "carousel"):
        return VisualContext(required=True, available=available, blocked="no_video")

    from .. import providers
    if not providers.media_analysis_allowed(getattr(cc, "platform", None)):
        # The deployment may not fetch this platform's media. Answer honestly
        # from the cover reading if there is one; never quietly download anyway.
        return VisualContext(required=True, available=available,
                             blocked="not_allowed")

    return _escalate(db, cc, user_id=user_id, available=available)


def _escalate(db, cc, *, user_id: int, available: bool) -> VisualContext:
    """Charge the difference and queue the frames job."""
    from .. import billing, entitlements, plans
    from ..jobs import enqueue
    from ..models import Job

    # One job per canonical item, on its own key. Reusing the ingest key would
    # hit `enqueue`'s "already done, return it" branch and silently do nothing.
    # The unique constraint on `idempotency_key` is what makes two concurrent
    # Asks produce one job rather than two downloads.
    key = f"content.vision:{cc.id}"
    existing = (db.query(Job)
                .filter(Job.idempotency_key == key,
                        Job.state.in_(("queued", "running"))).first())
    if existing is not None:
        return VisualContext(required=True, available=available,
                             escalated=True, blocked="in_flight")

    entitlement = entitlements.for_user(db, user_id)

    # Charge only the *difference*. The item already settled at its save-time
    # route — 1 unit for text or cover — so escalating to frames costs the gap,
    # not the full 8. Paying twice for the transcript nobody re-fetched would
    # be double-charging for work already done.
    already = plans.units_for_route(getattr(cc, "route", None) or "text")
    target = plans.units_for_route("light_vision")
    delta = max(1, target - already)

    reservation = billing.reserve_units(
        db, user_id, units=delta, entitlement=entitlement,
        canonical_content_id=cc.id, reason="vision_escalation",
        # What the save-time route already paid toward this item. Settlement
        # needs it, or it compares the 8-unit frames route against this 7-unit
        # top-up and bills a ninth unit for work nobody did.
        baseline_units=already)

    if not reservation.granted:
        from ..ai import telemetry
        telemetry.record(db, operation="paywall.quota_reached_processing",
                         user_id=user_id, canonical_content_id=cc.id,
                         platform=getattr(cc, "platform", None), success=False)
        return VisualContext(required=True, available=available, blocked="quota",
                             upgrade_available=not entitlement.is_pro)

    enqueue(db, "content.process",
            {"canonical_id": cc.id, "user_id": user_id, "want_vision": True},
            idempotency_key=key, platform=getattr(cc, "platform", None),
            # Ahead of ordinary ingest: somebody is waiting for this answer.
            priority=max(1, entitlement.limits.job_priority - 10),
            user_id=user_id)

    logger.info("visual escalation queued for canonical %s (user %s, %s units)",
                cc.id, user_id, delta)
    return VisualContext(required=True, available=available, escalated=True,
                         units_charged=delta)


def context_note(ctx: VisualContext) -> Optional[str]:
    """The note appended to the Ask prompt, if any."""
    if not ctx.should_warn:
        return None
    note = BLIND_NOTE
    if ctx.escalated:
        note += QUEUED_NOTE
    elif ctx.blocked == "quota":
        note += EXHAUSTED_NOTE
    return note
