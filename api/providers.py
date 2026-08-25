"""Per-provider capabilities, as four independent switches.

Sava's relationship with each platform is not one decision, it is four, and they
had become tangled: whether we can read a title, whether we can play the video,
whether we can analyse the media, and what we may derive from it. Because they
were tangled, the only way to make production safer was to break the product —
turning off extraction also turned off Scroll.

The four are now separate:

    Saved URL
      → metadata     can we identify it: title, creator, thumbnail, duration
      → playback     how it plays: an official embed, a proxied stream, or not
      → analysis     what we may read: the media itself, or only its text
      → understanding what we derive: summaries, key points, embeddings

**Playback and analysis are independent.** A YouTube Short plays through the
official IFrame embed and always has; that is unaffected by whether we are
allowed to download its audio for a transcript. An Instagram Reel plays through
Instagram's own `/embed/` endpoint with no credentials and no proxying at all.
Turning analysis down does not remove Scroll, and turning playback down does not
remove understanding.

**Why this exists now.** The server-side extraction path — spoofed user agent,
optional residential proxy, browser cookie replay — is under external review
with TikTok, YouTube and Meta. Nothing here changes that code or expands it. What
this adds is the ability to *turn each capability down independently, per
platform, by configuration*, so that when an answer arrives it is an environment
variable rather than a refactor.

Defaults are chosen so production is conservative and development is unchanged:
production does not depend on the extraction path, development keeps it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from .config import IS_PRODUCTION


class Playback(str, Enum):
    """How an item is played."""

    EMBED = "embed"        # the platform's own player, sanctioned, no credentials
    PROXY = "proxy"        # media streamed through Sava — the reviewed path
    GALLERY = "gallery"    # still images we already mirrored
    NONE = "none"


class Analysis(str, Enum):
    """What Sava may read in order to understand an item."""

    MEDIA = "media"        # audio and frames from the media itself
    TEXT = "text"          # captions, description, title, comments — no media
    NONE = "none"


@dataclass(frozen=True)
class Capabilities:
    metadata: bool
    playback: Playback
    analysis: Analysis

    @property
    def can_understand(self) -> bool:
        """Understanding needs *something* to read, but not necessarily media."""
        return self.analysis is not Analysis.NONE


def _env(platform: str, capability: str, default: str) -> str:
    """`SAVA_TIKTOK_PLAYBACK=embed` overrides one switch for one platform."""
    return os.getenv(f"SAVA_{platform.upper()}_{capability.upper()}", default).strip().lower()


# The conservative production posture and the permissive development one.
#
# The only difference is the two capabilities that route through server-side
# extraction: TikTok proxied playback, and media-level analysis. Metadata,
# official embeds, galleries and text-level analysis are identical in both,
# which is the point — production keeps the product.
_DEFAULTS: Dict[str, Dict[str, Capabilities]] = {
    "development": {
        "youtube":   Capabilities(True, Playback.EMBED, Analysis.MEDIA),
        "tiktok":    Capabilities(True, Playback.PROXY, Analysis.MEDIA),
        "instagram": Capabilities(True, Playback.EMBED, Analysis.TEXT),
        "other":     Capabilities(True, Playback.NONE,  Analysis.TEXT),
    },
    "production": {
        # Official IFrame player; captions come from the platform, not the media.
        "youtube":   Capabilities(True, Playback.EMBED, Analysis.TEXT),
        # Proxied playback stays available but is opt-in per deployment, because
        # it is the path under review. `SAVA_TIKTOK_PLAYBACK=proxy` restores it.
        "tiktok":    Capabilities(True, Playback.NONE,  Analysis.TEXT),
        # Instagram's own /embed/ needs nothing from us and is unaffected.
        "instagram": Capabilities(True, Playback.EMBED, Analysis.TEXT),
        "other":     Capabilities(True, Playback.NONE,  Analysis.TEXT),
    },
}


def _profile() -> Dict[str, Capabilities]:
    return _DEFAULTS["production" if IS_PRODUCTION else "development"]


def for_platform(platform: Optional[str]) -> Capabilities:
    """Capabilities for one platform, after environment overrides."""
    name = (platform or "other").lower()
    profile = _profile()
    base = profile.get(name, profile["other"])

    try:
        playback = Playback(_env(name, "playback", base.playback.value))
    except ValueError:
        playback = base.playback
    try:
        analysis = Analysis(_env(name, "analysis", base.analysis.value))
    except ValueError:
        analysis = base.analysis

    metadata = _env(name, "metadata", "1" if base.metadata else "0") not in ("0", "false", "no")
    return Capabilities(metadata=metadata, playback=playback, analysis=analysis)


def playback_allowed(platform: Optional[str], kind: Playback) -> bool:
    """May this platform play this way?

    `GALLERY` is always allowed: those are images Sava already holds, so serving
    them involves no platform access at all.
    """
    if kind is Playback.GALLERY:
        return True
    return for_platform(platform).playback is kind


def media_analysis_allowed(platform: Optional[str]) -> bool:
    """May Sava download this platform's media to analyse it?

    The single question the frozen extraction path should ask before doing any
    work. Text-level understanding — captions, description, comments — does not
    go through here and is unaffected.
    """
    return for_platform(platform).analysis is Analysis.MEDIA


def describe() -> Dict[str, Dict[str, str]]:
    """The active matrix, for `/api/ops/*` and for debugging a deployment."""
    return {
        platform: {
            "metadata": str(caps.metadata),
            "playback": caps.playback.value,
            "analysis": caps.analysis.value,
        }
        for platform, caps in ((p, for_platform(p)) for p in _profile())
    }
