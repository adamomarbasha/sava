"""Playback resolution for the short-form viewer.

Sava deliberately does not keep copies of videos — storing every save at full
resolution is the one decision that would make the unit economics impossible.
So "play this TikTok" cannot mean "serve the file we have"; it has to mean
"work out, right now, how this particular item can be played".

There are three honest answers, and this module's whole job is picking the
right one and saying so explicitly rather than handing the client a URL and
hoping:

  * **`video`** — TikTok. yt-dlp resolves a progressive MP4 on the CDN, but that
    URL is bound to the session that resolved it: fetched from anywhere else it
    returns 403, and the unlocking credential is the `ttwid` cookie yt-dlp
    picked up while extracting. The device therefore cannot fetch it directly
    no matter what headers it sends. Sava proxies it instead, replaying the
    resolver's cookie jar and passing `Range` through untouched so `AVPlayer`
    gets real seeking rather than a progressive download.

  * **`embed`** — YouTube. Extracting and re-serving YouTube's streams is
    against their terms regardless of whether it is technically possible, so
    Shorts play in the sanctioned IFrame player. That the client renders this
    in a web view instead of `AVPlayer` is an implementation detail of one page
    type, not a second viewer.

  * **`gallery`** — TikTok photo posts. There is no video to play; the content
    is an ordered set of images we already mirrored into object storage during
    ingestion. Presenting these as a video that fails to start is the specific
    bug this branch exists to prevent.

Anything else returns `unavailable` with a reason. A viewer that says "this
can't be played" is better than one that spins.

**Cost.** The `video` branch moves the whole file through Sava's egress — the
one place in the system that does. See `STREAM_MAX_BYTES` and the note on
`stream_upstream` before raising any limit here.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# How long a resolved CDN URL is assumed good for. TikTok's signed URLs last
# meaningfully longer than this; the short TTL is about not serving a stale
# handle after the platform rotates something, not about matching their expiry.
RESOLVE_TTL_SECONDS = 8 * 60

# How long a playback token is valid. Long enough to start and watch an item,
# short enough that a leaked URL is worthless by the time it travels.
TOKEN_TTL_SECONDS = 60 * 60

# A single short-form item that legitimately exceeds this is not a thing that
# exists; the ceiling is here so a resolver mistake cannot turn one request into
# unbounded egress.
STREAM_MAX_BYTES = 256 * 1024 * 1024

# Bytes per upstream read. Large enough not to thrash, small enough that an
# abandoned swipe stops costing almost immediately.
STREAM_CHUNK = 64 * 1024


# Cookie domains that may be replayed upstream. Suffix match against the
# cookie's own domain attribute, never against the request host — a cookie set
# by `bank.example` does not become TikTok's because we are talking to TikTok.
_COOKIE_DOMAINS = ("tiktok.com", "tiktokcdn.com", "tiktokcdn-us.com",
                   "tiktokcdn-eu.com", "tiktokv.com", "byteoversea.com",
                   "muscdn.com", "ibyteimg.com")


def _platform_cookies(jar) -> Dict[str, str]:
    """Narrow a cookie jar to the platform's own cookies."""
    kept: Dict[str, str] = {}
    for cookie in jar:
        domain = (getattr(cookie, "domain", "") or "").lower().lstrip(".")
        if any(domain == d or domain.endswith("." + d) for d in _COOKIE_DOMAINS):
            kept[cookie.name] = cookie.value
    return kept


@dataclass
class _Resolved:
    url: str
    headers: Dict[str, str]
    cookies: Dict[str, str]
    expires_at: float
    width: Optional[int] = None
    height: Optional[int] = None


class _ResolveCache:
    """Per-process cache of resolved stream handles.

    Deliberately in-process rather than in Redis or the database. A handle is
    only valid for the cookie jar that produced it, so sharing one between
    workers would hand a second process a URL its own session cannot fetch.
    Re-resolving per process is one cheap metadata call, already rate-limited by
    the platform budget, and it keeps this path free of new infrastructure.
    """

    def __init__(self) -> None:
        self._items: Dict[int, _Resolved] = {}
        self._lock = threading.Lock()

    def get(self, key: int) -> Optional[_Resolved]:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            if item.expires_at <= time.time():
                self._items.pop(key, None)
                return None
            return item

    def put(self, key: int, value: _Resolved) -> None:
        with self._lock:
            self._items[key] = value
            if len(self._items) > 512:
                now = time.time()
                for k in [k for k, v in self._items.items() if v.expires_at <= now]:
                    self._items.pop(k, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_cache = _ResolveCache()


def reset_cache() -> None:
    """Test seam."""
    _cache.clear()


# ─── Signed playback tokens ──────────────────────────────────────────────────
#
# `AVPlayer` and `WKWebView` issue their own requests and will not carry the
# app's bearer token, so the stream route cannot use the normal dependency. A
# short-lived HMAC bound to both the item and the user keeps it from being an
# open proxy: the token is useless for any other item, and expires on its own.

def _secret() -> bytes:
    from ..auth import SECRET_KEY
    return SECRET_KEY.encode("utf-8")


def sign_token(canonical_id: int, user_id: int, expires_at: int) -> str:
    payload = f"{canonical_id}:{user_id}:{expires_at}".encode("utf-8")
    digest = hmac.new(_secret(), payload, hashlib.sha256).hexdigest()[:32]
    return f"{user_id}.{expires_at}.{digest}"


def verify_token(token: str, canonical_id: int) -> Optional[int]:
    """Returns the user id the token was minted for, or None."""
    try:
        user_raw, expires_raw, digest = (token or "").split(".", 2)
        user_id, expires_at = int(user_raw), int(expires_raw)
    except (ValueError, AttributeError):
        return None
    if expires_at <= int(time.time()):
        return None
    expected = sign_token(canonical_id, user_id, expires_at)
    # Constant-time: the digest is the only secret-derived part of this string.
    if not hmac.compare_digest(expected, token):
        return None
    return user_id


# ─── Resolution ──────────────────────────────────────────────────────────────

def _resolve_tiktok_stream(url: str):
    """Ask the platform for a playable progressive URL, once.

    Returns an `AcquisitionResult`-shaped object so it can go through
    `guarded()` like every other platform call and be priced and circuit-broken
    with the rest.
    """
    from ..pipeline.acquire import AcquisitionResult
    from ..pipeline.acquire import _ydl_base_opts

    started = time.monotonic()
    try:
        import yt_dlp

        opts = _ydl_base_opts()
        opts.update({
            "skip_download": True,
            # Progressive MP4 only. A DASH/HLS split would need muxing, which
            # would mean actually downloading — the thing this avoids.
            "format": "best[ext=mp4][protocol^=http][height<=1080]/best[ext=mp4]/best",
        })
        ydl = yt_dlp.YoutubeDL(opts)
        info = ydl.extract_info(url, download=False)
        if not info or not info.get("url"):
            return AcquisitionResult(False, "playback", error="no playable format")

        # The cookie jar is not incidental — TikTok's CDN rejects the URL
        # without the `ttwid` cookie set during extraction, correct headers and
        # all — but it must be *narrowed* before it goes anywhere.
        #
        # `SAVA_YTDLP_COOKIES_FROM_BROWSER` loads the operator's whole browser
        # jar: hundreds of cookies for banks, email, and every ad network they
        # have ever loaded. Replaying that wholesale to a TikTok CDN host would
        # be handing a third party the operator's sessions. Only cookies whose
        # own domain is TikTok's are kept.
        cookies = _platform_cookies(ydl.cookiejar)
        return AcquisitionResult(
            True, "playback",
            wall_ms=int((time.monotonic() - started) * 1000),
            metadata={
                "url": info["url"],
                "headers": info.get("http_headers") or {},
                "cookies": cookies,
                "width": info.get("width"),
                "height": info.get("height"),
            },
        )
    except Exception as e:
        return AcquisitionResult(
            False, "playback", error=str(e)[:400],
            wall_ms=int((time.monotonic() - started) * 1000))


def resolve_stream(db, cc, *, user_id: Optional[int] = None) -> Optional[_Resolved]:
    """Cached, budget-guarded stream handle for a TikTok video."""
    cached = _cache.get(cc.id)
    if cached is not None:
        return cached

    from ..platform_budget import guarded

    result = guarded("playback", "resolve", _resolve_tiktok_stream,
                     cc.canonical_url, db=db, canonical_content_id=cc.id,
                     user_id=user_id)
    if not result.ok:
        logger.info("playback resolve failed for canonical %s: %s", cc.id, result.error)
        return None

    meta = result.metadata
    resolved = _Resolved(
        url=meta["url"], headers=meta.get("headers") or {},
        cookies=meta.get("cookies") or {},
        expires_at=time.time() + RESOLVE_TTL_SECONDS,
        width=meta.get("width"), height=meta.get("height"),
    )
    _cache.put(cc.id, resolved)
    return resolved


# ─── Warming ─────────────────────────────────────────────────────────────────
#
# The resolve is the expensive thing in this file — a live `yt-dlp` extraction,
# measured at 1.5–2.2s cold. It used to run inline on the first byte of the
# stream request, which put all of it on the critical path: the player asked for
# video and waited two seconds before receiving any.
#
# The client already prefetches descriptors three items ahead. Descriptors are
# cheap (4ms) so that alone bought very little; hooking the resolve to it is what
# makes the prefetch worth having. Asking for a descriptor now also warms the
# stream behind it, so by the time the player reaches that item the handle is in
# the cache and the first byte is a CDN round trip rather than an extraction.

# Four, to match the client's prefetch window (the current item plus three
# ahead) — with three, the last item in a window queued behind the others and
# was still cold when a fast swiper reached it.
_WARM_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sava-warm")

# Ids currently being warmed, so three descriptor prefetches in a row do not
# start three extractions of the same video.
_warming: set = set()
_warming_lock = threading.Lock()


def warm_stream(canonical_id: int, user_id: Optional[int] = None) -> None:
    """Resolve this item's stream in the background, if it isn't already.

    Fire and forget: nothing waits on the result, and a failure is logged
    exactly as an inline failure would be. The worst case is that the player
    arrives before the warm finishes and resolves inline — which is the old
    behaviour, not a regression.
    """
    if _cache.get(canonical_id) is not None:
        return

    with _warming_lock:
        if canonical_id in _warming:
            return
        _warming.add(canonical_id)

    def run() -> None:
        # Its own session: the request's session belongs to the request and is
        # very likely closed by the time this runs.
        from ..db import SessionLocal
        from ..models import CanonicalContent
        from ..platform_budget import PlatformUnavailable
        db = SessionLocal()
        try:
            cc = (db.query(CanonicalContent)
                  .filter(CanonicalContent.id == canonical_id).first())
            if cc is not None:
                resolve_stream(db, cc, user_id=user_id)
        except PlatformUnavailable as e:
            # The budget guard is doing its job — the platform is rate-limited
            # or the circuit is open. Expected, and speculative work is exactly
            # what should be dropped first. A traceback here would be noise.
            logger.info("stream warm skipped for canonical %s: %s", canonical_id, e)
        except Exception:
            logger.exception("stream warm failed for canonical %s", canonical_id)
        finally:
            db.close()
            with _warming_lock:
                _warming.discard(canonical_id)

    try:
        _WARM_POOL.submit(run)
    except RuntimeError:
        # Interpreter shutting down. Nothing to warm for.
        with _warming_lock:
            _warming.discard(canonical_id)


# ─── Descriptor ──────────────────────────────────────────────────────────────

@dataclass
class PlaybackDescriptor:
    kind: str                                  # video | embed | gallery | unavailable
    url: Optional[str] = None
    poster: Optional[str] = None
    aspect: Optional[float] = None             # width / height
    images: list = field(default_factory=list)
    reason: Optional[str] = None
    muted_start: bool = False
    duration_seconds: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "url": self.url,
            "poster": self.poster,
            "aspect": self.aspect,
            "images": self.images,
            "reason": self.reason,
            "muted_start": self.muted_start,
            "duration_seconds": self.duration_seconds,
        }


def _aspect(cc) -> Optional[float]:
    w, h = getattr(cc, "width", None), getattr(cc, "height", None)
    if w and h:
        return round(float(w) / float(h), 4)
    return None


def _gallery_images(db, cc) -> list:
    from ..models import ContentAsset
    from ..storage import get_storage

    assets = (db.query(ContentAsset)
              .filter(ContentAsset.canonical_content_id == cc.id)
              .order_by(ContentAsset.asset_index.asc())
              .all())
    images = []
    storage = get_storage()
    for asset in assets:
        # Prefer our durable copy: the source CDN URL for a TikTok slide expires
        # in days, and a carousel that half-loads is worse than one that does not.
        url = None
        if asset.storage_key:
            try:
                url = storage.url(asset.storage_key)
            except Exception:
                url = None
        url = url or asset.source_url
        if not url:
            continue
        images.append({
            "url": url,
            "width": asset.width,
            "height": asset.height,
            "index": asset.asset_index,
        })
    return images


def descriptor_for(db, cc, *, user_id: int, base_url: str = "") -> PlaybackDescriptor:
    """How this item plays. Never raises; an unplayable item says why."""
    poster = cc.thumbnail_url
    duration = float(cc.duration_seconds) if cc.duration_seconds else None
    platform = (cc.platform or "").lower()
    kind = (cc.media_kind or "").lower()

    if platform == "instagram":
        # Photos and carousels keep the native gallery: the images are already
        # mirrored, and swiping real thumbnails beats an iframe.
        #
        # Everything else goes to Instagram's own embed. The important part is
        # what is *not* here any more — a fallback that wrapped the cover image
        # in a one-item gallery. For a photo post that was right; for a Reel it
        # produced a still that could never play, which is precisely the "it
        # won't show the video" symptom. A cover image is not a degraded video,
        # it is the wrong medium.
        if kind != "video":
            images = _gallery_images(db, cc)
            if images:
                first = images[0]
                aspect = (round(first["width"] / first["height"], 4)
                          if first.get("width") and first.get("height") else _aspect(cc))
                return PlaybackDescriptor(kind="gallery", poster=poster, aspect=aspect,
                                          images=images)

        if cc.platform_content_id:
            expires = int(time.time()) + TOKEN_TTL_SECONDS
            token = sign_token(cc.id, user_id, expires)
            url = f"{base_url.rstrip('/')}/api/playback/{cc.id}/embed?t={token}"
            # 4:5 rather than 9:16: Instagram's embed adds a header and a caption
            # strip around the media, so the frame it needs is squarer than the
            # video inside it.
            return PlaybackDescriptor(kind="embed", url=url, poster=poster,
                                      aspect=_aspect(cc) or 0.8,
                                      duration_seconds=duration)

        return PlaybackDescriptor(
            kind="unavailable", poster=poster,
            reason="This post can't be identified.")

    if platform == "tiktok" and kind == "carousel":
        images = _gallery_images(db, cc)
        if not images:
            return PlaybackDescriptor(
                kind="unavailable", poster=poster,
                reason="This photo post hasn't finished processing yet.")
        first = images[0]
        aspect = (round(first["width"] / first["height"], 4)
                  if first.get("width") and first.get("height") else _aspect(cc))
        return PlaybackDescriptor(kind="gallery", poster=poster, aspect=aspect,
                                  images=images)

    if platform == "youtube":
        if not cc.platform_content_id:
            return PlaybackDescriptor(kind="unavailable", poster=poster,
                                      reason="This video can't be identified.")
        # A page served by us, not an iframe URL for the client to wrap.
        #
        # The obvious version — hand the app `youtube.com/embed/<id>` and let it
        # build a host page locally — fails: a `WKWebView` page loaded from a
        # string has no real origin, so YouTube's JS API refuses it and the
        # embed reports "video unavailable (152)". Serving the host page over
        # HTTP gives it a genuine origin to declare, which is what makes both
        # playback and programmatic play/pause work.
        expires = int(time.time()) + TOKEN_TTL_SECONDS
        token = sign_token(cc.id, user_id, expires)
        url = f"{base_url.rstrip('/')}/api/playback/{cc.id}/embed?t={token}"
        return PlaybackDescriptor(kind="embed", url=url, poster=poster,
                                  aspect=_aspect(cc) or (9 / 16),
                                  duration_seconds=duration)

    if platform == "tiktok":
        expires = int(time.time()) + TOKEN_TTL_SECONDS
        token = sign_token(cc.id, user_id, expires)
        url = f"{base_url.rstrip('/')}/api/playback/{cc.id}/stream?t={token}"
        # Start resolving now rather than when the player asks for bytes. The
        # client fetches descriptors several items ahead, so this is what turns
        # that lead time into an actually-warm stream.
        warm_stream(cc.id, user_id)
        return PlaybackDescriptor(kind="video", url=url, poster=poster,
                                  aspect=_aspect(cc) or (9 / 16),
                                  duration_seconds=duration)

    return PlaybackDescriptor(
        kind="unavailable", poster=poster,
        reason="Short-form playback supports TikTok and YouTube Shorts.")


# ─── The proxy ───────────────────────────────────────────────────────────────

def stream_upstream(db, cc, *, range_header: Optional[str], user_id: Optional[int]
                    ) -> Tuple[int, Dict[str, str], Any]:
    """Open the upstream media and return (status, headers, byte iterator).

    Every byte here is Sava's egress, which makes this the most expensive route
    in the product by a wide margin — one 40s TikTok is ~4 MB, and a user who
    swipes for ten minutes moves more data than their entire library of metadata
    ever will. It is deliberately the *only* proxied path: YouTube goes through
    the IFrame and carousels through object storage, so neither touches this.
    """
    import requests

    from ..net_guard import PLATFORM_IMAGE_HOSTS, UnsafeURL, validate

    resolved = resolve_stream(db, cc, user_id=user_id)
    if resolved is None:
        return 502, {}, None

    # The URL came from yt-dlp rather than from a user, so this is defence in
    # depth — but a resolver returning an unexpected host is exactly the case
    # where an unchecked server-side fetch becomes SSRF.
    try:
        validate(resolved.url, allowed_hosts=PLATFORM_IMAGE_HOSTS)
    except UnsafeURL as e:
        logger.warning("refusing to proxy %s: %s", cc.id, e)
        return 502, {}, None

    headers = dict(resolved.headers)
    if range_header:
        headers["Range"] = range_header
    headers.pop("Accept-Encoding", None)  # never re-encode a video

    session = requests.Session()
    upstream_host = urlparse(resolved.url).hostname or ""
    for name, value in resolved.cookies.items():
        # Pinned to the host actually being called. A domainless cookie in
        # `requests` is sent to *every* host the session touches, including
        # anywhere a redirect leads.
        session.cookies.set(name, value, domain=upstream_host)

    upstream = session.get(resolved.url, headers=headers, stream=True, timeout=20,
                           allow_redirects=True)
    if upstream.status_code >= 400:
        upstream.close()
        # A 403 here means the handle went stale mid-session. Drop it so the
        # next request re-resolves instead of failing identically forever.
        _cache.clear()
        return 502, {}, None

    passthrough = {}
    for header in ("Content-Type", "Content-Length", "Content-Range",
                   "Accept-Ranges", "ETag"):
        if header in upstream.headers:
            passthrough[header] = upstream.headers[header]
    passthrough.setdefault("Content-Type", "video/mp4")
    passthrough.setdefault("Accept-Ranges", "bytes")
    # The bytes are immutable for the life of the signed URL, and the client
    # re-requests on every swipe back. Letting it cache is free bandwidth.
    passthrough["Cache-Control"] = "private, max-age=600"

    def iterator():
        sent = 0
        try:
            for chunk in upstream.iter_content(STREAM_CHUNK):
                if not chunk:
                    continue
                sent += len(chunk)
                if sent > STREAM_MAX_BYTES:
                    logger.warning("stream for canonical %s exceeded cap", cc.id)
                    break
                yield chunk
        finally:
            upstream.close()
            session.close()

    return upstream.status_code, passthrough, iterator()


# ─── The embed host page ─────────────────────────────────────────────────────

EMBED_PAGE = """<!doctype html>
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1,
      maximum-scale=1, user-scalable=no">
<style>
  html,body{{margin:0;padding:0;background:#000;height:100%;overflow:hidden}}
  #frame{{position:absolute;inset:0;width:100%;height:100%;border:0}}
</style>
</head><body>
<iframe id="frame" allow="autoplay; encrypted-media" allowfullscreen
  src="https://www.youtube.com/embed/{video_id}?playsinline=1&rel=0&modestbranding=1&controls=0&iv_load_policy=3&fs=0&autoplay=0&enablejsapi=1&origin={origin}"></iframe>
<script>
  var f = document.getElementById('frame');
  function post(fn) {{
    f.contentWindow.postMessage(
      JSON.stringify({{event:'command', func:fn, args:[]}}), '*');
  }}
  function savaPlay(){{ post('playVideo'); }}
  function savaPause(){{ post('pauseVideo'); }}
  function savaMute(){{ post('mute'); }}
  function savaUnmute(){{ post('unMute'); }}
</script>
</body></html>"""


# Instagram's own embed, which is the only sanctioned way to show a Reel.
#
# Instagram serves media URLs to authenticated sessions only, so there is no
# stream to proxy the way TikTok's is — the previous code correctly refused to
# invent one, but then fell back to rendering the *cover image* as a one-item
# gallery, which is why a saved Reel appeared as a photo that would not play.
#
# `/embed/` is the endpoint Instagram publishes for exactly this purpose and
# every news site uses. It renders the real post — video, carousel or photo —
# plays inline, and requires no credentials. The captioned variant is used
# because the plain one crops tall Reels awkwardly.
INSTAGRAM_EMBED_PAGE = """<!doctype html>
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1,
      maximum-scale=1, user-scalable=no">
<style>
  html,body{{margin:0;padding:0;background:#000;height:100%;overflow:hidden}}
  #frame{{position:absolute;inset:0;width:100%;height:100%;border:0}}
</style>
</head><body>
<iframe id="frame" allow="autoplay; encrypted-media; fullscreen"
  allowtransparency="true" frameborder="0" scrolling="no"
  src="https://www.instagram.com/p/{code}/embed/captioned/"></iframe>
<script>
  // Instagram's embed exposes no JS control surface, so these exist only so the
  // app's player protocol has something to call. Playback is the user's tap
  // inside the frame.
  function savaPlay(){{}}
  function savaPause(){{}}
  function savaMute(){{}}
  function savaUnmute(){{}}
</script>
</body></html>"""


def instagram_embed_page(code: str, origin: str) -> str:
    """Host page for one Instagram post. `origin` is unused by Instagram but
    kept in the signature so both embed builders are called the same way."""
    return INSTAGRAM_EMBED_PAGE.format(code=code)


def embed_page(video_id: str, origin: str) -> str:
    """The host page for one video. Origin is declared, not guessed."""
    return EMBED_PAGE.format(video_id=video_id, origin=origin)
