"""Screenshot-assisted content resolution.

The Action Button cannot hand Sava the URL of whatever app is on screen — iOS
gives a third-party App Intent no such API. What it *can* hand us is a
screenshot. This turns that screenshot into a canonical URL:

    screenshot
      -> vision model reads the on-screen text (title, creator, platform chrome)
      -> platform-specific lookup turns that text into a real video id
      -> canonical URL + confidence

Confidence matters more than coverage here. Saving the *wrong* video is worse
than saving nothing, so a weak match is reported as "not confident" and the
client falls back rather than guessing.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = float(os.getenv("SAVA_RESOLVE_MIN_CONFIDENCE", "0.55"))
SEARCH_RESULTS = int(os.getenv("SAVA_RESOLVE_SEARCH_RESULTS", "3"))

# A full-resolution iPhone screenshot is ~2.3 MB of PNG. Reading text off it
# needs nothing like that, and the upload dominates end-to-end latency, so it
# is downscaled to JPEG before the vision call.
VISION_MAX_EDGE = int(os.getenv("SAVA_RESOLVE_MAX_EDGE", "1280"))
VISION_JPEG_QUALITY = int(os.getenv("SAVA_RESOLVE_JPEG_QUALITY", "80"))


def compress_for_vision(image_bytes: bytes) -> tuple:
    """Downscale/re-encode a screenshot for the vision model.

    Returns (bytes, mime). Falls back to the original bytes if Pillow is
    unavailable or the image cannot be decoded.
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as im:
            im = im.convert("RGB")
            longest = max(im.size)
            if longest > VISION_MAX_EDGE:
                scale = VISION_MAX_EDGE / float(longest)
                im = im.resize((max(1, int(im.width * scale)),
                                max(1, int(im.height * scale))),
                               Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=VISION_JPEG_QUALITY, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except Exception as e:
        logger.warning("screenshot compression failed, sending original: %s", e)
        return image_bytes, "image/jpeg"

_VISION_SYSTEM = """You are looking at a screenshot taken on an iPhone while the
user was watching something. Identify what is on screen.

Return STRICT JSON, no markdown fences:
{"platform":"youtube|tiktok|instagram|other|unknown",
 "title":"the video title exactly as printed, empty string if none visible",
 "creator":"channel name or @handle exactly as printed, else empty",
 "caption":"visible caption/description text, else empty",
 "on_screen_text":"any other legible text that identifies this content",
 "confidence":0.0-1.0}

Rules:
- Transcribe text EXACTLY as shown. Do not translate, correct, or complete it.
- Judge the platform from the interface chrome (YouTube's red controls and
  title-above-description layout; TikTok's right-hand action rail and @handle;
  Instagram's Reels layout).
- If the screen shows a feed rather than one clear piece of content, or the
  text is unreadable, set confidence below 0.3.
- Never invent a title. An empty string is the correct answer when unsure."""


@dataclass
class ResolveResult:
    ok: bool
    url: Optional[str] = None
    platform: str = "unknown"
    confidence: float = 0.0
    reason: str = ""
    read: Dict[str, Any] = field(default_factory=dict)
    candidates: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok, "url": self.url, "platform": self.platform,
            "confidence": round(self.confidence, 3), "reason": self.reason,
            "read": self.read, "candidates": self.candidates[:5],
        }


def _similarity(a: str, b: str) -> float:
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def read_screenshot(image_bytes: bytes, *, db=None, user_id: Optional[int] = None
                    ) -> Dict[str, Any]:
    """Ask the vision model what is on screen. Returns the parsed reading."""
    import json

    from ..ai import telemetry
    from ..ai.base import TaskType
    from ..ai.router import get_router

    router = get_router()
    if not router.is_available():
        return {"error": "ai_unavailable"}

    payload, _mime = compress_for_vision(image_bytes)
    logger.info("resolver: screenshot %d KB -> %d KB for vision",
                len(image_bytes) // 1024, len(payload) // 1024)

    try:
        completion = router.complete(
            TaskType.VISION_ANALYSIS,
            system=_VISION_SYSTEM,
            prompt="Identify the content in this screenshot.",
            json_mode=True,
            images=[payload],
            temperature=0.0,
            max_output_tokens=1024,
        )
    except Exception as e:
        # Quota exhaustion, a retired model, or a provider outage must produce
        # an honest result — never a 500 that the client reports as
        # "Sava is having a moment".
        text = str(e)
        quota = "429" in text or "quota" in text.lower() or "rate limit" in text.lower()
        logger.warning("resolver vision call failed (%s): %s",
                       "quota" if quota else "error", text[:200])
        return {"error": "ai_quota_exceeded" if quota else "vision_unavailable",
                "detail": text[:200]}

    if db is not None:
        telemetry.record_completion(db, completion, operation="resolve.vision",
                                    user_id=user_id, frames_processed=1)
    try:
        data = json.loads(completion.text or "{}")
    except Exception as e:
        logger.warning("resolver vision JSON parse failed: %s", e)
        return {"error": "unreadable"}

    return {
        "platform": str(data.get("platform") or "unknown").lower(),
        "title": (data.get("title") or "").strip(),
        "creator": (data.get("creator") or "").strip(),
        "caption": (data.get("caption") or "").strip(),
        "on_screen_text": (data.get("on_screen_text") or "").strip(),
        "vision_confidence": float(data.get("confidence") or 0.0),
    }


def _search_youtube(title: str, creator: str) -> List[Dict[str, Any]]:
    """Find candidate YouTube videos by the title read off the screen."""
    if not title:
        return []
    query = f"{title} {creator}".strip()
    try:
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                "extract_flat": True, "noplaylist": True}
        proxy = os.getenv("SAVA_PROXY_URL")
        if proxy:
            opts["proxy"] = proxy
        cookies = os.getenv("SAVA_YTDLP_COOKIES_FROM_BROWSER")
        if cookies:
            opts["cookiesfrombrowser"] = (cookies,)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{SEARCH_RESULTS}:{query}", download=False)
    except Exception as e:
        logger.warning("youtube search failed: %s", e)
        return []

    out = []
    for entry in (info or {}).get("entries") or []:
        vid = entry.get("id")
        if not vid:
            continue
        out.append({
            "id": vid,
            "title": entry.get("title") or "",
            "creator": entry.get("channel") or entry.get("uploader") or "",
            "url": f"https://youtube.com/watch?v={vid}",
        })
    return out


def _score(candidate: Dict[str, Any], title: str, creator: str) -> float:
    """How well a search hit matches what we read off the screen."""
    title_score = _similarity(candidate.get("title", ""), title)
    score = title_score
    if creator:
        creator_score = _similarity(candidate.get("creator", ""), creator)
        # The title carries most of the signal; the creator confirms it.
        score = title_score * 0.75 + creator_score * 0.25
        if creator_score > 0.8:
            score = min(1.0, score + 0.08)
    return score


def resolve_screenshot(image_bytes: bytes, *, platform_hint: Optional[str] = None,
                       db=None, user_id: Optional[int] = None) -> ResolveResult:
    """Turn a screenshot into a canonical URL, or explain why it could not."""
    if not image_bytes:
        return ResolveResult(False, reason="empty_image")

    read = read_screenshot(image_bytes, db=db, user_id=user_id)
    if read.get("error"):
        return ResolveResult(False, reason=read["error"], read=read)
    if not read:
        return ResolveResult(False, reason="unreadable", read={})

    platform = (platform_hint or read.get("platform") or "unknown").lower()
    title = read.get("title", "")
    creator = read.get("creator", "")
    vision_confidence = float(read.get("vision_confidence") or 0.0)

    # A URL printed on screen (share sheet, browser bar) beats any search.
    blob = " ".join([read.get("on_screen_text", ""), read.get("caption", "")])
    printed = re.search(r"https?://[^\s\"'<>]+", blob)
    if printed:
        return ResolveResult(True, url=printed.group(0), platform=platform,
                             confidence=0.95, reason="url_printed_on_screen", read=read)

    if vision_confidence < 0.3:
        return ResolveResult(False, platform=platform, confidence=vision_confidence,
                             reason="screen_not_readable", read=read)

    if platform == "youtube":
        candidates = _search_youtube(title, creator)
        if not candidates:
            return ResolveResult(False, platform=platform, reason="no_search_results",
                                 read=read)
        scored = sorted(
            ({**c, "score": _score(c, title, creator)} for c in candidates),
            key=lambda c: c["score"], reverse=True,
        )
        best = scored[0]
        confidence = best["score"] * min(1.0, vision_confidence + 0.25)

        # An ambiguous top-2 is a red flag: saving the wrong video is worse
        # than saving nothing.
        if len(scored) > 1 and (best["score"] - scored[1]["score"]) < 0.06 \
                and best["score"] < 0.9:
            return ResolveResult(False, platform=platform, confidence=confidence,
                                 reason="ambiguous_match", read=read, candidates=scored)

        if confidence < MIN_CONFIDENCE:
            return ResolveResult(False, platform=platform, confidence=confidence,
                                 reason="low_confidence", read=read, candidates=scored)

        return ResolveResult(True, url=best["url"], platform="youtube",
                             confidence=confidence, reason="matched_by_title",
                             read=read, candidates=scored)

    # TikTok and Instagram expose no public search that maps a handle+caption
    # back to an exact post id (Instagram returns 401/403 to unauthenticated
    # profile queries), so an exact URL is not recoverable from a screenshot.
    #
    # Losing the save entirely is the worse outcome. When the screen was read
    # clearly we return a PARTIAL capture: everything actually observed —
    # creator, caption, on-screen text — with no invented video id. The caller
    # decides whether to save it.
    if platform in ("tiktok", "instagram"):
        if vision_confidence >= 0.5 and (creator or read.get("on_screen_text")
                                         or read.get("caption")):
            return ResolveResult(
                False, platform=platform, confidence=vision_confidence,
                reason="partial_capture", read=read)
        return ResolveResult(
            False, platform=platform, confidence=vision_confidence,
            reason="no_search_available_for_platform", read=read)

    return ResolveResult(False, platform=platform, confidence=vision_confidence,
                         reason="unsupported_platform", read=read)
