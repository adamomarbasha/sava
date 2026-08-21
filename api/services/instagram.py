"""Instagram metadata extraction, behind a provider interface.

Instagram is the one platform Sava supports where there is no sanctioned way to
ask "what is this post". So the design question is not which scraper to use — it
is how to keep the rest of the product from caring, and how to fail without
losing the user's content.

**What actually works, measured rather than assumed.** Two mechanisms were
tested against a real public post:

  * `yt-dlp` answers *"login required"* for Instagram without cookies. That
    makes it unusable as the default path: depending on it means depending on an
    operator account, which is the thing this architecture is explicitly not
    allowed to require.
  * Instagram serves complete Open Graph tags — creator, caption, publish date,
    engagement counts and a cover image — to crawler user agents, with no
    authentication and no account. This is the mechanism Instagram publishes so
    that links render as previews in other apps; it is the same data any chat
    app gets when you paste a Reel into it.

So Open Graph is the primary provider and yt-dlp is an optional richer one,
off unless someone deliberately configures credentials. Both sit behind the same
interface, and a licensed API can be added later as a third without the
ingestion pipeline changing.

**What Open Graph does not give**, and this is a real limit rather than a to-do:
it returns one cover image and no enumeration of a carousel's children, no media
dimensions, and no duration. The carousel data model, storage and gallery UI are
all built and work — they are populated by any provider that can enumerate
children, and left empty by one that cannot.

Every field records where it came from, so `provenance` answers "how do we know
this" for any value in the database.
"""
from __future__ import annotations

import html as html_module
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Instagram publishes Open Graph tags for link previews and serves them to
# crawler agents. Identifying honestly as a crawler is the point: this is the
# preview mechanism, used the way preview mechanisms are used.
CRAWLER_UA = ("facebookexternalhit/1.1 "
              "(+http://www.facebook.com/externalhit_uatext.php)")

REQUEST_TIMEOUT = 15.0
# Hard ceiling for a page that should have answered within the first
# few KB; reaching it means Instagram served something unexpected.
MAX_HTML_BYTES = 1024 * 1024


# ─── Structured failure reasons ──────────────────────────────────────────────
#
# A single "extraction failed" string is useless in production: it cannot tell
# a transient rate-limit apart from a deleted post, and those need opposite
# responses — retry one, stop retrying the other.

class FailureReason:
    LOGIN_REQUIRED = "login_required"      # gated; no amount of retrying helps
    NOT_FOUND = "not_found"                # deleted or private; terminal
    RATE_LIMITED = "rate_limited"          # back off and retry later
    BLOCKED = "blocked"                    # IP/agent refused; retry elsewhere
    NETWORK = "network"                    # transient; retry
    PARSE_FAILED = "parse_failed"          # served something unexpected
    UNAVAILABLE = "unavailable"            # provider not configured

    # Reasons where retrying the same way will produce the same answer.
    TERMINAL = {LOGIN_REQUIRED, NOT_FOUND, UNAVAILABLE}


@dataclass
class InstagramMetadata:
    """Everything legitimately obtainable about one post.

    Every field is optional. Missing metadata is a valid outcome; invented
    metadata is not, so nothing here is ever defaulted to a placeholder.
    """
    shortcode: str
    canonical_url: str
    media_kind: Optional[str] = None          # video | image | carousel
    creator_name: Optional[str] = None
    creator_handle: Optional[str] = None
    caption: Optional[str] = None
    published_at: Optional[datetime] = None
    thumbnail_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    carousel_count: Optional[int] = None
    children: List[Dict[str, Any]] = field(default_factory=list)
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    # field name -> provider that supplied it.
    provenance: Dict[str, str] = field(default_factory=dict)

    def stamp(self, provider: str) -> "InstagramMetadata":
        """Record which provider supplied each value that is actually present."""
        for name in ("media_kind", "creator_name", "creator_handle", "caption",
                     "published_at", "thumbnail_url", "width", "height",
                     "duration_seconds", "carousel_count", "like_count",
                     "comment_count"):
            if getattr(self, name) is not None:
                self.provenance.setdefault(name, provider)
        if self.children:
            self.provenance.setdefault("children", provider)
        return self


@dataclass
class ProviderResult:
    """Shaped like `AcquisitionResult` so it can go through `guarded()`."""
    ok: bool
    provider: str
    metadata: Optional[InstagramMetadata] = None
    error: Optional[str] = None
    failure_reason: Optional[str] = None
    wall_ms: int = 0
    bytes_moved: int = 0


class InstagramMetadataProvider(ABC):
    """One way of finding out about an Instagram post."""

    name: str = "abstract"

    @property
    def available(self) -> bool:
        """Whether this provider is configured well enough to try."""
        return True

    @abstractmethod
    def extract(self, shortcode: str, canonical_url: str) -> ProviderResult:
        ...


# ─── Open Graph ──────────────────────────────────────────────────────────────

_OG = re.compile(
    r'<meta[^>]+property=["\']og:(?P<key>[a-z:_]+)["\'][^>]+content=["\'](?P<val>[^"\']*)["\']',
    re.IGNORECASE)
_OG_REVERSED = re.compile(
    r'<meta[^>]+content=["\'](?P<val>[^"\']*)["\'][^>]+property=["\']og:(?P<key>[a-z:_]+)["\']',
    re.IGNORECASE)


def parse_og_tags(html: str) -> Dict[str, str]:
    """Open Graph tags, in whichever attribute order they were written."""
    tags: Dict[str, str] = {}
    for pattern in (_OG, _OG_REVERSED):
        for m in pattern.finditer(html or ""):
            tags.setdefault(m.group("key").lower(),
                            html_module.unescape(m.group("val")))
    return tags


# "Zendaya on Instagram: "Just coming on here to say...""
_TITLE = re.compile(r"^(?P<name>.+?)\s+on\s+Instagram(?:\s*:\s*(?P<caption>.*))?$",
                    re.DOTALL)
# "6M likes, 20K comments - zendaya on September 1, 2025: "caption""
_DESC = re.compile(
    r"^(?:(?P<likes>[\d.,KMB]+)\s+likes?)?(?:\s*,\s*(?P<comments>[\d.,KMB]+)\s+comments?)?"
    r"\s*-\s*(?P<handle>[A-Za-z0-9._]+)\s+on\s+(?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})",
    re.DOTALL)

_COUNT_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def _parse_count(raw: Optional[str]) -> Optional[int]:
    """"6M" -> 6000000. Approximate by nature — Instagram rounds these itself.

    Returned as engagement only; nothing downstream treats it as exact.
    """
    if not raw:
        return None
    text = raw.strip().replace(",", "")
    if not text:
        return None
    suffix = text[-1].lower()
    try:
        if suffix in _COUNT_SUFFIX:
            return int(float(text[:-1]) * _COUNT_SUFFIX[suffix])
        return int(float(text))
    except ValueError:
        return None


def _strip_quotes(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    cleaned = text.strip()
    for pair in (('"', '"'), ("“", "”"), ("'", "'")):
        if cleaned.startswith(pair[0]) and cleaned.endswith(pair[1]) and len(cleaned) > 1:
            cleaned = cleaned[1:-1]
            break
    cleaned = cleaned.strip()
    return cleaned or None


def metadata_from_og(tags: Dict[str, str], *, shortcode: str,
                     canonical_url: str) -> Optional[InstagramMetadata]:
    """Turn Open Graph tags into metadata, inventing nothing.

    Instagram's own phrasing is the parser's input: the title carries the
    creator's display name and the caption, the description carries the handle,
    the publish date and the engagement counts. When a piece does not match, it
    is left unset rather than guessed at.
    """
    title = tags.get("title") or ""
    description = tags.get("description") or ""
    image = tags.get("image") or None
    if not (title or description or image):
        return None

    meta = InstagramMetadata(shortcode=shortcode, canonical_url=canonical_url)

    m = _TITLE.match(title.strip())
    if m:
        meta.creator_name = (m.group("name") or "").strip() or None
        meta.caption = _strip_quotes(m.group("caption"))
    elif title.strip():
        # A title in a shape we do not recognise is still a caption; it is just
        # not one we can split a creator name out of.
        meta.caption = _strip_quotes(title)

    d = _DESC.match(description.strip())
    if d:
        meta.creator_handle = (d.group("handle") or "").strip() or None
        meta.like_count = _parse_count(d.group("likes"))
        meta.comment_count = _parse_count(d.group("comments"))
        try:
            meta.published_at = datetime.strptime(
                d.group("date"), "%B %d, %Y").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            meta.published_at = None

    if not meta.caption and description:
        tail = description.split(":", 1)
        if len(tail) == 2:
            meta.caption = _strip_quotes(tail[1])

    if image:
        meta.thumbnail_url = image

    # `og:video` is the only reliable signal of a video post available here.
    if tags.get("video") or tags.get("video:url") or tags.get("video:secure_url"):
        meta.media_kind = "video"

    return meta


class OpenGraphProvider(InstagramMetadataProvider):
    """Metadata from Instagram's own link-preview tags.

    Unauthenticated, no account, no cookie jar — which is what makes it the only
    one of these providers that can be the default at production scale.
    """

    name = "opengraph"

    def extract(self, shortcode: str, canonical_url: str) -> ProviderResult:
        from ..net_guard import UnsafeURL, safe_get

        started = time.monotonic()
        url = f"https://www.instagram.com/p/{shortcode}/"
        try:
            response, _final = safe_get(
                url, allowed_hosts=("instagram.com",),
                headers={"User-Agent": CRAWLER_UA,
                         "Accept": "text/html,application/xhtml+xml",
                         "Accept-Language": "en-US,en;q=0.9"},
                timeout=REQUEST_TIMEOUT, stream=True)
        except UnsafeURL as e:
            return ProviderResult(False, self.name, error=str(e),
                                  failure_reason=FailureReason.BLOCKED,
                                  wall_ms=self._ms(started))
        except Exception as e:
            return ProviderResult(False, self.name, error=str(e)[:300],
                                  failure_reason=FailureReason.NETWORK,
                                  wall_ms=self._ms(started))

        try:
            status = response.status_code
            if status == 404:
                return ProviderResult(False, self.name, error="post not found",
                                      failure_reason=FailureReason.NOT_FOUND,
                                      wall_ms=self._ms(started))
            if status == 429:
                return ProviderResult(False, self.name, error="rate limited",
                                      failure_reason=FailureReason.RATE_LIMITED,
                                      wall_ms=self._ms(started))
            if status in (401, 403):
                return ProviderResult(False, self.name, error=f"HTTP {status}",
                                      failure_reason=FailureReason.LOGIN_REQUIRED,
                                      wall_ms=self._ms(started))
            if status >= 400:
                return ProviderResult(False, self.name, error=f"HTTP {status}",
                                      failure_reason=FailureReason.NETWORK,
                                      wall_ms=self._ms(started))

            # Stop at `</head>`. The whole page is ~900KB and every Open Graph
            # tag is in the first few KB of it, so reading to the end would move
            # roughly a hundred times more data than the answer needs — per
            # extraction, at whatever rate the platform is being saved.
            chunks, size = [], 0
            body = ""
            for chunk in response.iter_content(32 * 1024):
                chunks.append(chunk)
                size += len(chunk)
                body = b"".join(chunks).decode("utf-8", "replace")
                if "</head>" in body.lower() or size >= MAX_HTML_BYTES:
                    break
        finally:
            response.close()

        tags = parse_og_tags(body)
        if not tags:
            reason = (FailureReason.LOGIN_REQUIRED
                      if "accounts/login" in body else FailureReason.PARSE_FAILED)
            return ProviderResult(False, self.name, error="no Open Graph tags",
                                  failure_reason=reason, wall_ms=self._ms(started),
                                  bytes_moved=size)

        meta = metadata_from_og(tags, shortcode=shortcode,
                                canonical_url=canonical_url)
        if meta is None:
            return ProviderResult(False, self.name, error="tags carried nothing usable",
                                  failure_reason=FailureReason.PARSE_FAILED,
                                  wall_ms=self._ms(started), bytes_moved=size)

        return ProviderResult(True, self.name, metadata=meta.stamp(self.name),
                              wall_ms=self._ms(started), bytes_moved=size)

    @staticmethod
    def _ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)


# ─── yt-dlp ──────────────────────────────────────────────────────────────────

class YtDlpProvider(InstagramMetadataProvider):
    """Richer, but requires credentials — so it is off unless configured.

    Measured: without cookies this answers "Requested content is not available,
    rate-limit reached or login required". Enabling it means running an operator
    Instagram account, which does not survive contact with 100k users and is
    exactly what the architecture is meant to avoid. It stays because it is the
    only provider available today that can enumerate a carousel's children, and
    because it is a useful reference implementation of the interface.
    """

    name = "ytdlp"

    @property
    def available(self) -> bool:
        from ..config import INSTAGRAM_YTDLP_ENABLED
        return INSTAGRAM_YTDLP_ENABLED

    def extract(self, shortcode: str, canonical_url: str) -> ProviderResult:
        started = time.monotonic()
        if not self.available:
            return ProviderResult(False, self.name, error="provider disabled",
                                  failure_reason=FailureReason.UNAVAILABLE)
        try:
            import yt_dlp

            from ..pipeline.acquire import _ydl_base_opts

            opts = _ydl_base_opts()
            opts.update({"skip_download": True, "noplaylist": False})
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.instagram.com/p/{shortcode}/", download=False)
            if not info:
                return ProviderResult(False, self.name, error="no info",
                                      failure_reason=FailureReason.PARSE_FAILED,
                                      wall_ms=self._ms(started))
            return ProviderResult(True, self.name,
                                  metadata=self._to_metadata(
                                      info, shortcode, canonical_url).stamp(self.name),
                                  wall_ms=self._ms(started))
        except Exception as e:
            text = str(e).lower()
            if "login" in text or "not available" in text:
                reason = FailureReason.LOGIN_REQUIRED
            elif "rate" in text:
                reason = FailureReason.RATE_LIMITED
            elif "not found" in text or "404" in text:
                reason = FailureReason.NOT_FOUND
            else:
                reason = FailureReason.NETWORK
            return ProviderResult(False, self.name, error=str(e)[:300],
                                  failure_reason=reason, wall_ms=self._ms(started))

    def _to_metadata(self, info: Dict[str, Any], shortcode: str,
                     canonical_url: str) -> InstagramMetadata:
        entries = info.get("entries") or []
        meta = InstagramMetadata(
            shortcode=shortcode, canonical_url=canonical_url,
            creator_name=info.get("uploader") or info.get("channel"),
            creator_handle=info.get("uploader_id"),
            caption=info.get("description") or info.get("title"),
            thumbnail_url=info.get("thumbnail"),
            width=info.get("width"), height=info.get("height"),
            duration_seconds=info.get("duration"),
            like_count=info.get("like_count"),
            comment_count=info.get("comment_count"),
        )
        if info.get("timestamp"):
            try:
                meta.published_at = datetime.fromtimestamp(
                    int(info["timestamp"]), tz=timezone.utc)
            except (ValueError, OSError, TypeError):
                meta.published_at = None

        if entries:
            meta.media_kind = "carousel"
            meta.carousel_count = len(entries)
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                meta.children.append({
                    "index": index,
                    "media_type": "video" if entry.get("duration") else "image",
                    "source_url": entry.get("thumbnail") or entry.get("url"),
                    "width": entry.get("width"),
                    "height": entry.get("height"),
                    "duration_seconds": entry.get("duration"),
                })
        elif info.get("duration"):
            meta.media_kind = "video"
        else:
            meta.media_kind = "image"
        return meta

    @staticmethod
    def _ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)


# ─── Orchestration ───────────────────────────────────────────────────────────

def get_providers() -> List[InstagramMetadataProvider]:
    """Providers in the order they should be tried."""
    from ..config import INSTAGRAM_PROVIDERS

    registry = {"opengraph": OpenGraphProvider, "ytdlp": YtDlpProvider}
    out: List[InstagramMetadataProvider] = []
    for name in INSTAGRAM_PROVIDERS:
        cls = registry.get(name.strip().lower())
        if cls is None:
            logger.warning("unknown Instagram provider configured: %s", name)
            continue
        out.append(cls())
    return out


def extract_metadata(shortcode: str, canonical_url: str, *, db=None,
                     canonical_content_id: Optional[int] = None,
                     user_id: Optional[int] = None) -> ProviderResult:
    """Try each configured provider until one answers.

    A terminal failure from the first provider (the post is deleted, or gated)
    stops the chain: asking a second provider the same question about a post
    that does not exist just spends another request to be told the same thing.
    """
    from ..ai import telemetry

    last: Optional[ProviderResult] = None
    for provider in get_providers():
        if not provider.available:
            continue
        try:
            result = provider.extract(shortcode, canonical_url)
        except Exception as e:
            logger.exception("Instagram provider %s raised", provider.name)
            result = ProviderResult(False, provider.name, error=str(e)[:300],
                                    failure_reason=FailureReason.NETWORK)

        if db is not None:
            telemetry.record(
                db, operation=f"instagram.provider.{provider.name}",
                platform="instagram", canonical_content_id=canonical_content_id,
                user_id=user_id, wall_ms=result.wall_ms,
                proxy_bytes=result.bytes_moved, success=result.ok,
                error=None if result.ok else f"[{result.failure_reason}] {result.error}"[:400],
            )

        if result.ok:
            return result
        last = result
        if result.failure_reason in FailureReason.TERMINAL:
            break

    return last or ProviderResult(
        False, "none", error="no Instagram provider is configured",
        failure_reason=FailureReason.UNAVAILABLE)
