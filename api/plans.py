"""Plan definitions: the one place product limits are written down.

Everything commercial about Sava is in this file. Routes, the save service, the
worker and the iOS client all read from here; none of them contain a number.
Changing what Free includes is a change to `_FREE`, not a search through a dozen
call sites.

Two things are deliberately kept apart:

  * **Plan limits** (this module) — what a subscription buys. Monthly, resets on
    a billing period, and the ceiling a user is allowed to reach and be upsold
    from. Hitting one is a *product* event.
  * **Abuse limits** (`api/quota.py`) — what stops a runaway loop. Rolling
    24-hour, far above any real usage, and identical for everyone. Hitting one
    is an *operational* event and means something is wrong.

Collapsing them would be a mistake in both directions: a paying customer would
be told to upgrade when they were actually being rate-limited, and a script
hammering the API would be answered with a paywall.

── Processing units ────────────────────────────────────────────────────────

Sava does not meter saves, because saving is not what costs money. It meters the
expensive *understanding* of what was saved — and it prices that by **the route
the pipeline actually took**, not by how long the video was.

That distinction is the whole design. Measured spend showed a 3-minute TikTok
costing $0.0310 and a 30-minute YouTube video costing $0.0097: three times
cheaper for ten times the length. Duration is not the cost driver. What Sava had
to *do* is — read free captions, download audio, or download video and read
frames — and `api/pipeline/route.py` now chooses the cheapest route that is good
enough, then reports which one ran.

One unit is calibrated to one ordinary short video on the cheap route. That is
what lets the product say "understand N videos a month" and mean it, instead of
making people do arithmetic in a credit meter.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ─── Plan identity ───────────────────────────────────────────────────────────

FREE = "free"
PRO = "pro"

#: Every plan name the backend will accept. A subscription row carrying anything
#: else is treated as Free — an unknown plan must never grant more than nothing.
PLAN_NAMES = (FREE, PRO)


@dataclass(frozen=True)
class PlanLimits:
    """What one plan includes for one billing month.

    `saved_items` and `collections` are absent on purpose rather than set to a
    large number. Sava does not limit them, and a nullable ceiling invites
    somebody to fill it in later; there is nothing to fill in.
    """

    name: str
    #: Monthly understanding allowance, in units. One unit ~= one ordinary short
    #: video on the cheap route, which is what the client renders as a video
    #: count rather than as a credit balance.
    processing_units: int
    #: Ask messages, across `/api/ask` and per-item Ask alike.
    ask_messages: int
    #: How many of this user's expensive jobs may run at once.
    concurrent_jobs: int
    #: Queue priority. Lower number = claimed sooner (matches `Job.priority`).
    job_priority: int
    #: Deep/enhanced analysis — the more expensive understanding pass.
    enhanced_analysis: bool

    @property
    def is_pro(self) -> bool:
        return self.name == PRO

    @property
    def display_name(self) -> str:
        return "Sava Pro" if self.is_pro else "Free"

    @property
    def approx_videos(self) -> int:
        """Roughly how many ordinary videos this allowance understands.

        Derived from `TYPICAL_UNITS_PER_VIDEO`, not written down separately, so
        the marketing number and the meter cannot drift apart. It exists so no
        surface has to say "1,200 processing units" at a person — the product
        talks about videos, and the units stay internal.

        Approximate on purpose, and the direction of the approximation is
        generous: a YouTube-heavy library goes roughly twice as far, a
        TikTok-only one somewhat less.
        """
        return int(round(self.processing_units / TYPICAL_UNITS_PER_VIDEO / 10) * 10)


# ─── The launch limits ───────────────────────────────────────────────────────
#
# Every value is env-overridable so pricing experiments do not need a code
# change, let alone a client release. The defaults are the launch numbers.

# ── Why these numbers ──────────────────────────────────────────────────────
#
# They are what the optimised pipeline can afford, computed rather than chosen.
#
# Blended measured cost per video after routing: YouTube $0.0026, Instagram
# $0.0052, TikTok $0.0068 — against $0.0097 / $0.0145 / $0.0310 before. A mixed
# short-form user averages ~2.6 units and ~$0.0053 per video.
#
# Pro is $9.99, or $8.49 net of Apple's 15%. At 1,200 units:
#
#   typical  (250 videos + 200 asks)   $1.52   18% of net revenue
#   full     (462 videos + 800 asks)   $3.22   38% of net revenue
#   absolute worst (every unit on the frames route, every Ask used)
#                                      $3.78   45% of net revenue
#
# The last line is the one that matters. Under the old duration-based model the
# worst case was 126% of net revenue — a subscriber could lose money without
# doing anything abusive. Route-based units make the expensive path consume the
# allowance eight times faster, so the worst case is *bounded* and still
# profitable. No cap, no throttle, no fair-use clause: the arithmetic simply
# cannot go negative.
#
# Free at 300 units costs at most ~$0.75/month and typically ~$0.30, which is a
# sane acquisition cost for ~115 understood videos a month.
#
# Dedup is deliberately modelled at zero. Cross-user cache hits cost nothing and
# the ratio only improves as the library grows, so real margins should beat
# these. `/api/ops/routes` reports the true distribution once there is traffic.

_FREE = PlanLimits(
    name=FREE,
    processing_units=_int_env("SAVA_FREE_PROCESSING_UNITS", 300),
    ask_messages=_int_env("SAVA_FREE_ASK_MESSAGES", 150),
    concurrent_jobs=_int_env("SAVA_FREE_CONCURRENT_JOBS", 1),
    # 50 is what `services.save` already enqueues at, so Free keeps exactly
    # today's behaviour and Pro moves ahead of it rather than Free falling behind.
    job_priority=_int_env("SAVA_FREE_JOB_PRIORITY", 50),
    enhanced_analysis=False,
)

_PRO = PlanLimits(
    name=PRO,
    processing_units=_int_env("SAVA_PRO_PROCESSING_UNITS", 1200),
    ask_messages=_int_env("SAVA_PRO_ASK_MESSAGES", 1500),
    concurrent_jobs=_int_env("SAVA_PRO_CONCURRENT_JOBS", 3),
    job_priority=_int_env("SAVA_PRO_JOB_PRIORITY", 20),
    enhanced_analysis=True,
)

_PLANS: Dict[str, PlanLimits] = {FREE: _FREE, PRO: _PRO}


def limits_for(plan: Optional[str]) -> PlanLimits:
    """The limits for a plan name. Unknown or missing names resolve to Free.

    Failing closed matters here: a corrupt subscription row, a plan name from a
    future release, or a typo in configuration must all mean "no entitlement",
    never "unlimited".
    """
    return _PLANS.get((plan or "").strip().lower(), _FREE)


def all_plans() -> Dict[str, PlanLimits]:
    return dict(_PLANS)


# ─── Processing weights ──────────────────────────────────────────────────────
#
# Weighted by the **route actually taken**, not by how long the video is.
#
# Duration was the wrong axis and the measurements said so plainly: a 3-minute
# TikTok cost $0.0310 while a 30-minute YouTube video cost $0.0097 — three times
# cheaper for ten times the length. Cost tracks *what Sava had to do* (fetch
# captions? download audio? download video and read frames?), and duration only
# correlates with that by accident. On Sava's core platform it correlated
# backwards.
#
# One unit is calibrated to one ordinary short video on the cheap route —
# roughly $0.002 of measured spend. That calibration is what lets the product
# say "understand N videos a month" honestly instead of exposing a credit meter:
# for the overwhelming majority of saves, 1 unit really is 1 video.

#: The measured dollar cost each route is calibrated against. Not used for
#: billing — kept here so the weights can be re-derived when the route
#: distribution is measured in production rather than estimated.
ROUTE_USD = {
    "cached": 0.0,
    "metadata": 0.0001,
    "text": 0.0017,
    "cover": 0.0020,
    "audio": 0.0066,
    "light_vision": 0.0157,
    "deep_vision": 0.0240,
}

#: Units the average short-form video consumes, across a realistic platform
#: mix (40% TikTok / 40% Reels / 20% YouTube) and the estimated route
#: distribution for each. Used only to turn an allowance into a video count for
#: the UI. Re-derive from `/api/ops/routes` once there is real traffic.
TYPICAL_UNITS_PER_VIDEO = float(os.getenv("SAVA_TYPICAL_UNITS_PER_VIDEO", "2.6"))

#: USD that one unit represents.
USD_PER_UNIT = float(os.getenv("SAVA_USD_PER_UNIT", "0.002"))

#: route -> units. Derived from `ROUTE_USD`, rounded to whole units, then
#: pinned here so a pricing change is explicit rather than emergent.
ROUTE_UNITS: Dict[str, int] = {
    "cached": _int_env("SAVA_UNITS_CACHED", 0),
    "metadata": _int_env("SAVA_UNITS_METADATA", 0),
    "text": _int_env("SAVA_UNITS_TEXT", 1),
    "cover": _int_env("SAVA_UNITS_COVER", 1),
    "audio": _int_env("SAVA_UNITS_AUDIO", 3),
    "light_vision": _int_env("SAVA_UNITS_LIGHT_VISION", 8),
    "deep_vision": _int_env("SAVA_UNITS_DEEP_VISION", 12),
}

#: What a save is charged before its route is known.
#
# `create_save` performs no network I/O, so at save time nothing is known about
# whether this item has captions or needs frames. It is charged the cheap route
# and `settle()` collects the difference once the worker has actually decided.
#
# Reserving the cheap amount is the deliberate choice: reserving the expensive
# amount would tell a user with 5 units left that a video they can comfortably
# afford is unaffordable, purely because of our own ignorance at that moment.
UNITS_ON_SAVE = _int_env("SAVA_UNITS_ON_SAVE", 1)

#: Non-video content — an article, an image, a screenshot.
UNITS_LIGHT = _int_env("SAVA_UNIT_WEIGHT_LIGHT", 1)


def units_for_route(route: Optional[str]) -> int:
    """Units for a completed route. Unknown routes cost the save-time estimate."""
    if route is None:
        return max(0, UNITS_ON_SAVE)
    return max(0, ROUTE_UNITS.get(str(route).strip().lower(), UNITS_ON_SAVE))


def units_for(media_kind: Optional[str],
              duration_seconds: Optional[float] = None) -> int:
    """The save-time estimate, before the route is known.

    Deliberately flat. The previous duration ladder (2/4/8/15/25 units) is gone:
    it charged a 40-minute YouTube video 15 units for work that costs $0.0097,
    and a 30-second TikTok 2 units for work that cost $0.0310. Every item now
    reserves the cheap route and settles to what it actually used.
    """
    kind = (media_kind or "").strip().lower()
    if kind in ("video", "audio"):
        return max(0, UNITS_ON_SAVE)
    return max(0, UNITS_LIGHT)


def units_for_content(content) -> int:
    """`units_for` reading straight off a `CanonicalContent` row.

    Once the row carries a `route`, that wins — it is what actually happened.
    """
    if content is None:
        return max(0, UNITS_LIGHT)
    route = getattr(content, "route", None)
    if route:
        return units_for_route(route)
    return units_for(getattr(content, "media_kind", None),
                     getattr(content, "duration_seconds", None))


def describe_weights() -> list:
    """The weight table, for the pricing endpoint and documentation.

    Generated from the constants so the published table cannot drift from the
    one actually charged.
    """
    labels = {
        "cached": "already understood by Sava (any platform)",
        "metadata": "link saved, no AI understanding",
        "text": "captions or description were enough",
        "cover": "captions/description plus the cover image",
        "audio": "spoken audio had to be transcribed",
        "light_vision": "video frames had to be read",
        "deep_vision": "deep visual analysis (Pro, on request)",
    }
    return [{"route": r, "media": labels[r], "units": ROUTE_UNITS[r],
             "approx_usd": ROUTE_USD[r]}
            for r in ("cached", "metadata", "text", "cover", "audio",
                      "light_vision", "deep_vision")]
