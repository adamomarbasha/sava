"""The escalation decision: which pipeline does this item actually need?

── The bug this module exists to fix ───────────────────────────────────────

The ladder in `ingest.py` was already tiered, and on YouTube it worked: captions
are free, so a thirty-minute video cost almost nothing. On TikTok it collapsed
into the most expensive path on *every single save*, and the reason was ordering
rather than policy:

    1. metadata
    2. transcript  ← decides whether to download video, using
                     `visual_dependency` … which is still None here
    3. classify    ← computes `visual_dependency` (too late)
    4. vision

At step 2 `cc.visual_dependency` was `None`, so the code defaulted to 0.5 and
`has_transcript=False` (TikTok never tries captions), and `wants_vision()`
returns True whenever there is no transcript. So the full video was downloaded
before anything had formed an opinion about whether the video was needed.

The telemetry confirms it: `acquire.video` ran on 20 of 22 TikTok items, at
**7.39 MB average**, which at $3/GB is $0.0216 — about 83% of the cost of
understanding a TikTok.

The fix is not a bigger unit price. It is to decide *first*.

── The principle ───────────────────────────────────────────────────────────

**Use the cheapest route that produces good enough understanding.**

Escalation is one-directional and evidence-driven. Every route below is a
superset of the one above it, so escalating never throws away work already done
— the transcript acquired at AUDIO is still there when LIGHT_VISION runs.

Everything here is deterministic: no model call decides which model to call.
The one inference input is `visual_dependency`, which `classify` produces from
metadata alone for ~$0.0002 and which now runs *before* acquisition.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class Route(str, Enum):
    """What Sava will actually do to understand one item.

    Ordered cheapest to most expensive. The ordering is load-bearing:
    `Route.rank` drives "can we stop here?" and the metering weights.
    """

    #: Already understood — this user pays nothing and waits for nothing.
    CACHED = "cached"
    #: Identity, title, creator, thumbnail. No AI, no allowance consumed.
    METADATA = "metadata"
    #: Captions / subtitles / creator description → text understanding.
    #: No media is fetched at all. The target route for most saves.
    TEXT = "text"
    #: TEXT plus one vision call on the **already-mirrored cover image**.
    #: Costs no bandwidth — the thumbnail was fetched for the library grid
    #: regardless — and recovers the on-screen hook text that short-form
    #: content puts on its first frame.
    COVER = "cover"
    #: Audio-only download plus transcription. Roughly a quarter the bytes of
    #: the video, and enough whenever meaning is spoken rather than shown.
    AUDIO = "audio"
    #: Low-resolution video download plus sparse, deduplicated frames.
    LIGHT_VISION = "light_vision"
    #: A wider frame budget. Reserved for explicit user request and Pro
    #: enhanced analysis — never the default for a TikTok.
    DEEP_VISION = "deep_vision"

    @property
    def rank(self) -> int:
        return _ORDER.index(self)

    @property
    def needs_audio(self) -> bool:
        return self is Route.AUDIO

    @property
    def needs_video(self) -> bool:
        return self in (Route.LIGHT_VISION, Route.DEEP_VISION)

    @property
    def needs_transcript(self) -> bool:
        return self in (Route.AUDIO, Route.LIGHT_VISION, Route.DEEP_VISION)

    @property
    def reads_cover(self) -> bool:
        return self is Route.COVER


_ORDER = [Route.CACHED, Route.METADATA, Route.TEXT, Route.COVER,
          Route.AUDIO, Route.LIGHT_VISION, Route.DEEP_VISION]


# ─── Thresholds ──────────────────────────────────────────────────────────────
#
# Every one is env-tunable, because the right values depend on a real corpus and
# we have 129 items. They are deliberately *conservative*: the failure mode of a
# threshold that is too eager to escalate is a larger bill, and the failure mode
# of one too reluctant is a worse summary. Neither is silent — `route_reason` is
# recorded on every item, so the distribution can be audited and retuned from
# production data rather than from guesswork.

#: A creator caption at least this long is treated as real signal about the
#: content rather than as three hashtags.
MIN_CAPTION_CHARS = _int_env("SAVA_ROUTE_MIN_CAPTION_CHARS", 120)

#: A transcript at least this long is enough to understand the item from text.
MIN_TRANSCRIPT_CHARS = _int_env("SAVA_ROUTE_MIN_TRANSCRIPT_CHARS", 200)

#: `visual_dependency` at or above this means meaning genuinely lives on screen
#: (a recipe showing ingredients, an outfit, a before/after) and text alone will
#: miss it. Matches the pre-existing VISION_DEPENDENCY_THRESHOLD so behaviour on
#: YouTube is unchanged.
VISION_THRESHOLD = _float_env("SAVA_ROUTE_VISION_THRESHOLD", 0.6)

#: Above this, the cover image alone is unlikely to be enough and it is worth
#: paying for frames. Below it, the cover is tried first and usually ends it.
DEEP_VISUAL_THRESHOLD = _float_env("SAVA_ROUTE_DEEP_VISUAL_THRESHOLD", 0.8)

#: Content types where the visual channel *is* the content, regardless of what
#: the classifier scored. Kept short on purpose — a long list here is how every
#: item ends up escalating.
_VISUAL_CONTENT_TYPES = {"recipe", "fashion", "product", "diy", "workout",
                         "art", "makeup", "travel"}

#: Frames to extract per route. The light budget is genuinely sparse; the old
#: code used up to 8 for everything that reached vision.
LIGHT_FRAME_BUDGET = _int_env("SAVA_ROUTE_LIGHT_FRAMES", 4)
DEEP_FRAME_BUDGET = _int_env("SAVA_ROUTE_DEEP_FRAMES", 8)


@dataclass(frozen=True)
class RoutePlan:
    """The decision, plus why — the reason is persisted for auditing."""

    route: Route
    reason: str
    frame_budget: int = 0

    @property
    def needs_audio(self) -> bool:
        return self.route.needs_audio

    @property
    def needs_video(self) -> bool:
        return self.route.needs_video

    @property
    def reads_cover(self) -> bool:
        return self.route.reads_cover

    def escalated_to(self, route: Route, reason: str) -> "RoutePlan":
        """Move up the ladder, keeping the audit trail."""
        budget = (DEEP_FRAME_BUDGET if route is Route.DEEP_VISION
                  else LIGHT_FRAME_BUDGET if route is Route.LIGHT_VISION else 0)
        return RoutePlan(route=route, reason=f"{self.reason} -> {reason}",
                         frame_budget=budget)


@dataclass(frozen=True)
class Signals:
    """Everything known about an item *before* any media is fetched.

    Assembled from the metadata call plus classification, both of which are
    effectively free. Nothing here requires a download.
    """

    platform: str
    media_kind: str
    duration_seconds: float = 0.0
    caption_chars: int = 0
    transcript_chars: int = 0
    has_caption_track: bool = False
    has_cover: bool = False
    visual_dependency: Optional[float] = None
    content_type: Optional[str] = None
    #: The deployment is allowed to download this platform's media at all.
    media_allowed: bool = True
    #: ASR is configured and the item is within its ceiling.
    asr_available: bool = False
    #: The user explicitly asked for deep analysis.
    force_deep: bool = False
    #: Lazy escalation: somebody asked a question that needs the visual channel
    #: and this item has none cached. Forces at least the light frames route,
    #: without the cost of `force_deep`.
    force_vision: bool = False

    @property
    def visual(self) -> float:
        """`visual_dependency`, defaulting to neutral rather than to expensive.

        The old default of 0.5 combined with "escalate when no transcript" meant
        an unclassified item always escalated. Neutral now means *not* above
        `VISION_THRESHOLD`, so an unknown item takes the cheap route and can be
        escalated on evidence instead of on ignorance.
        """
        return self.visual_dependency if self.visual_dependency is not None else 0.4

    @property
    def visual_content_type(self) -> bool:
        return (self.content_type or "").lower() in _VISUAL_CONTENT_TYPES

    @property
    def has_usable_text(self) -> bool:
        return (self.transcript_chars >= MIN_TRANSCRIPT_CHARS
                or self.caption_chars >= MIN_CAPTION_CHARS)


def decide(signals: Signals) -> RoutePlan:
    """Pick the cheapest route that should produce good enough understanding.

    Called once, after metadata and classification, before anything is fetched.
    """
    # ── Explicit request always wins ────────────────────────────────────────
    if signals.force_deep:
        if not signals.media_allowed:
            return RoutePlan(Route.COVER, "deep requested but media analysis is off")
        return RoutePlan(Route.DEEP_VISION, "user requested deep analysis",
                         DEEP_FRAME_BUDGET)

    # ── Lazy escalation from a question ─────────────────────────────────────
    #
    # Somebody asked what colour the shirt is. Text routing was the right call
    # at save time and stays the right call for the other 95% of saves; this is
    # the one item, now, that turned out to need frames.
    #
    # Light, never deep: the question is "what is on screen", and four sparse
    # frames answer that. Deep stays an explicit, paid, Pro-only request.
    if signals.force_vision:
        if not signals.media_allowed:
            return RoutePlan(Route.COVER,
                             "visual answer needed but media analysis is off")
        if signals.media_kind in ("image", "carousel"):
            return RoutePlan(Route.COVER, "visual answer from stored imagery")
        return RoutePlan(Route.LIGHT_VISION, "visual answer needed for a question",
                         LIGHT_FRAME_BUDGET)

    # ── Content with no audio track ─────────────────────────────────────────
    # An image or a carousel has nothing to transcribe. Its stored slides are
    # read by the existing carousel path, which costs no platform request.
    if signals.media_kind in ("image", "carousel"):
        return RoutePlan(Route.COVER, "image content: read stored imagery")

    if signals.media_kind not in ("video", "audio"):
        return RoutePlan(Route.TEXT, "non-media item: text only")

    # ── Text is already sufficient ──────────────────────────────────────────
    # Captions or a substantial creator caption, and nothing suggesting the
    # meaning is visual. This is the route most saves should take.
    if signals.has_usable_text and signals.visual < VISION_THRESHOLD:
        if signals.has_cover:
            # The cover is already in object storage. Reading it costs one
            # small vision call and *zero* bandwidth, and on short-form it
            # routinely carries the hook text the caption omits. Free upside.
            return RoutePlan(Route.COVER, "text sufficient + free cover read")
        return RoutePlan(Route.TEXT, "captions/description sufficient")

    # ── Meaning is visual ───────────────────────────────────────────────────
    if signals.visual >= VISION_THRESHOLD or signals.visual_content_type:
        if not signals.media_allowed:
            # The deployment may not fetch this platform's media. Say so, and
            # take the best free route rather than failing.
            return RoutePlan(Route.COVER, "visual content but media analysis is off")

        # Very visual, or a type where the frames are the point.
        if signals.visual >= DEEP_VISUAL_THRESHOLD:
            return RoutePlan(Route.LIGHT_VISION, "high visual dependency",
                             LIGHT_FRAME_BUDGET)

        # Moderately visual. Try the cover first, whether or not we have text.
        #
        # The expected value is strongly one-sided: reading the cover costs
        # ~$0.0003 and no bandwidth, downloading the video costs ~$0.016. Even
        # if the cover only resolves a minority of these items, trying it first
        # is cheaper in aggregate — and `should_escalate_after_text` picks up
        # the ones it did not resolve, so nothing is understood *worse*, only
        # later and only when it turns out to be necessary.
        if signals.has_cover:
            return RoutePlan(
                Route.COVER,
                "visual hint: read the cover before paying for frames")

        return RoutePlan(Route.LIGHT_VISION, "visual, no text, no cover",
                         LIGHT_FRAME_BUDGET)

    # ── No usable text, meaning is probably spoken ──────────────────────────
    if signals.asr_available and signals.media_allowed:
        return RoutePlan(Route.AUDIO, "no text: transcribe audio")

    # ── Nothing else is possible ────────────────────────────────────────────
    if signals.has_cover:
        return RoutePlan(Route.COVER, "no text and no ASR: read the cover")
    return RoutePlan(Route.METADATA, "no signal available")


def should_escalate_after_text(signals: Signals, plan: RoutePlan, *,
                               transcript_chars: int,
                               cover_text_chars: int = 0) -> Optional[RoutePlan]:
    """Second-chance escalation, once the cheap route has actually run.

    The first decision is made on metadata. This one is made on *results*, and
    is the only place a cheap route is allowed to become an expensive one:

      * the transcript came back empty or trivially short, and
      * the cover did not supply usable on-screen text either, and
      * the item is visual enough that frames would plausibly help.

    Returns None to stop — which is the intended outcome for most items, and is
    what keeps the average cost near the cheap route rather than near the
    expensive one.
    """
    if plan.route.rank >= Route.LIGHT_VISION.rank:
        return None                       # already at or above frames
    if not signals.media_allowed:
        return None                       # not permitted to fetch media

    text_total = transcript_chars + cover_text_chars
    if text_total >= MIN_TRANSCRIPT_CHARS:
        return None                       # we understood it; stop

    if signals.caption_chars >= MIN_CAPTION_CHARS and signals.visual < VISION_THRESHOLD:
        return None                       # the caption carries it; stop

    if signals.visual >= VISION_THRESHOLD or signals.visual_content_type:
        return plan.escalated_to(Route.LIGHT_VISION,
                                 f"thin text ({text_total} chars) on visual content")

    if plan.route is Route.COVER and signals.asr_available:
        return plan.escalated_to(Route.AUDIO, f"thin text ({text_total} chars)")

    return None


def describe() -> list:
    """The route ladder, for docs and the ops endpoint."""
    return [{"route": r.value, "rank": r.rank,
             "fetches_audio": r.needs_audio, "fetches_video": r.needs_video}
            for r in _ORDER]
