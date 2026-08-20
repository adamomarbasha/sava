"""Content identity and URL normalization.

The whole "process once, reuse everywhere" promise rests on this file. If two
users save the same TikTok through different URL shapes and we fail to resolve
them to one `content_key`, we pay for that video twice.

The rule: derive a *stable platform id* whenever we can, and only fall back to
hashing a normalized URL when we genuinely cannot.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse, urlunparse

# Params that never change which content is being referenced.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_name",
    "fbclid", "gclid", "igshid", "igsh", "ref", "ref_src", "ref_url", "source",
    "si", "feature", "app", "_r", "_t", "is_from_webapp", "sender_device",
    "sender_web_id", "web_id", "share_app_id", "share_link_id", "share_item_id",
    "tt_from", "u_code", "timestamp", "user_id", "social_sharing", "checksum",
    "share_source", "shareId", "spm", "lang", "pd", "enter_method", "enter_from",
}

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
             "youtu.be", "www.youtu.be", "youtube-nocookie.com"}
_TT_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com",
             "vt.tiktok.com", "www.vm.tiktok.com"}
_IG_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am"}

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_TT_NUMERIC = re.compile(r"^\d{6,25}$")
_IG_CODE = re.compile(r"^[A-Za-z0-9_-]{5,20}$")


@dataclass(frozen=True)
class ContentIdentity:
    platform: str
    platform_content_id: Optional[str]
    canonical_url: str
    content_key: str
    media_kind: str          # video | image | carousel | article | unknown
    is_resolvable: bool      # False when the id still needs a network round-trip


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().lstrip(".")
    except Exception:
        return ""


def detect_platform(url: str) -> str:
    h = _host(url)
    if not h:
        return "other"
    base = h[4:] if h.startswith("www.") else h
    if h in _YT_HOSTS or base in _YT_HOSTS or "youtube" in h or h == "youtu.be":
        return "youtube"
    if h in _TT_HOSTS or base in _TT_HOSTS or "tiktok" in h:
        return "tiktok"
    if h in _IG_HOSTS or base in _IG_HOSTS or "instagram" in h:
        return "instagram"
    for name in ("twitter", "x.com", "linkedin", "reddit", "pinterest",
                 "snapchat", "facebook"):
        if name in h:
            return {"x.com": "twitter"}.get(name, name)
    return "other"


def strip_tracking(url: str) -> str:
    """Remove tracking params and normalize scheme/host/trailing slash."""
    try:
        p = urlparse(url.strip())
    except Exception:
        return url.strip()
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") and len(host) > 2:
        host = host[2:]
    qs = parse_qs(p.query, keep_blank_values=False)
    kept = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
    query = "&".join(
        f"{k}={v[0]}" for k, v in sorted(kept.items()) if v
    )
    path = p.path.rstrip("/") or "/"
    return urlunparse(("https", host, path, "", query, ""))


# ─── Per-platform id extraction ──────────────────────────────────────────────

def youtube_video_id(url: str) -> Optional[str]:
    try:
        p = urlparse(url)
    except Exception:
        return None
    host = (p.hostname or "").lower()
    if "youtu.be" in host:
        cand = p.path.lstrip("/").split("/")[0]
        return cand if _YT_ID.match(cand) else None
    path = p.path or ""
    if path.startswith("/watch"):
        v = parse_qs(p.query).get("v", [None])[0]
        return v if v and _YT_ID.match(v) else None
    for prefix in ("/shorts/", "/embed/", "/v/", "/live/"):
        if path.startswith(prefix):
            cand = path[len(prefix):].split("/")[0].split("?")[0]
            return cand if _YT_ID.match(cand) else None
    return None


def tiktok_video_id(url: str) -> Optional[str]:
    """Numeric aweme id. Short links (vm./vt.) need a redirect to resolve."""
    try:
        p = urlparse(url)
    except Exception:
        return None
    m = re.search(r"/video/(\d{6,25})", p.path or "")
    if m:
        return m.group(1)
    m = re.search(r"/photo/(\d{6,25})", p.path or "")
    if m:
        return m.group(1)
    tail = (p.path or "").strip("/").split("/")[-1]
    return tail if _TT_NUMERIC.match(tail) else None


def _tiktok_media_kind(url: str) -> Optional[str]:
    """`/photo/<id>` is a carousel; `/video/<id>` is a video.

    The id space is shared, so identity is unaffected — the same post reached
    through either path is still one canonical row. Only the handling differs.
    """
    path = (urlparse(url).path or "").lower()
    if "/photo/" in path:
        return "carousel"
    if "/video/" in path:
        return "video"
    return None


def instagram_shortcode(url: str) -> Optional[str]:
    try:
        p = urlparse(url)
    except Exception:
        return None
    m = re.search(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", p.path or "")
    if m and _IG_CODE.match(m.group(1)):
        return m.group(1)
    return None


def _instagram_media_kind(url: str) -> str:
    path = (urlparse(url).path or "").lower()
    if "/reel" in path or "/tv/" in path:
        return "video"
    if "/p/" in path:
        return "carousel"   # may be a single image; ingestion narrows it
    return "unknown"


def _url_fallback_key(platform: str, normalized: str) -> str:
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"{platform}:u:{digest}"


def resolve_identity(url: str, platform_hint: Optional[str] = None) -> Optional[ContentIdentity]:
    """Map any URL variant onto a stable canonical identity."""
    if not url or not url.strip():
        return None
    raw = url.strip()
    platform = detect_platform(raw)
    if platform == "other" and platform_hint:
        hinted = (platform_hint or "").lower()
        if hinted and hinted != "other":
            platform = hinted

    normalized = strip_tracking(raw)

    if platform == "youtube":
        vid = youtube_video_id(raw) or youtube_video_id(normalized)
        if vid:
            return ContentIdentity(
                platform="youtube", platform_content_id=vid,
                canonical_url=f"https://youtube.com/watch?v={vid}",
                content_key=f"youtube:{vid}", media_kind="video", is_resolvable=True,
            )

    elif platform == "tiktok":
        vid = tiktok_video_id(raw) or tiktok_video_id(normalized)
        if vid:
            # A `/photo/` post is a swipeable image set, not a video that failed
            # to have a video. Saying so here is what lets the pipeline skip
            # audio acquisition entirely and read the slides instead.
            kind = _tiktok_media_kind(raw) or _tiktok_media_kind(normalized) or "video"
            path_segment = "photo" if kind == "carousel" else "video"
            return ContentIdentity(
                platform="tiktok", platform_content_id=vid,
                canonical_url=f"https://tiktok.com/@i/{path_segment}/{vid}",
                content_key=f"tiktok:{vid}", media_kind=kind, is_resolvable=True,
            )
        # vm.tiktok.com/XXXX — real id only known after following the redirect.
        return ContentIdentity(
            platform="tiktok", platform_content_id=None, canonical_url=normalized,
            content_key=_url_fallback_key("tiktok", normalized),
            media_kind="video", is_resolvable=False,
        )

    elif platform == "instagram":
        code = instagram_shortcode(raw) or instagram_shortcode(normalized)
        if code:
            kind = _instagram_media_kind(raw)
            return ContentIdentity(
                platform="instagram", platform_content_id=code,
                canonical_url=f"https://instagram.com/p/{code}",
                content_key=f"instagram:{code}", media_kind=kind, is_resolvable=True,
            )

    return ContentIdentity(
        platform=platform, platform_content_id=None, canonical_url=normalized,
        content_key=_url_fallback_key(platform, normalized),
        media_kind="unknown", is_resolvable=platform == "other",
    )


def upgrade_identity(existing_key: str, resolved_url: str) -> Optional[ContentIdentity]:
    """After a short link resolves, recompute identity from the real URL.

    Lets a `tiktok:u:<hash>` row be merged into the proper `tiktok:<id>` row so
    a short link and a full link do not stay split forever.
    """
    ident = resolve_identity(resolved_url)
    if ident and ident.is_resolvable and ident.content_key != existing_key:
        return ident
    return None
