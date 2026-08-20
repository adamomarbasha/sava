"""Durable thumbnail storage.

A save's cover image is the single most important thing the library renders, and
until now it was the least durable thing Sava stored. TikTok and Instagram serve
*signed* CDN URLs — `p16-common-sign.tiktokcdn-us.com/...?x-expires=...`,
`instagram.f<pop>.fna.fbcdn.net/...` — whose signature expires and whose hostname
is tied to a point of presence that is later retired. Weeks after a save, the URL
still exists in the database and still looks fine, but returns 403, or the host
stops resolving entirely. The user experiences that as "my old saves lost their
pictures".

The invariant this module restores:

    once Sava has successfully seen a thumbnail, it keeps a copy.

So thumbnails are *mirrored*: fetched once, written under `static/thumbnails/`,
and the database row rewritten to the local path. Local paths never expire and
never need the proxy. Nothing is invented — if the remote image is already gone,
the row is left exactly as it was and the client shows its designed fallback.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
from pathlib import Path
from typing import Optional, Tuple

import requests

from ..config import API_DIR

logger = logging.getLogger(__name__)

THUMBNAIL_DIR = API_DIR / "static" / "thumbnails"
PUBLIC_PREFIX = "/static/thumbnails"

# Hosts that hand out signed, expiring, or referer-gated images. These are the
# ones worth spending a request to mirror.
EPHEMERAL_HOSTS = (
    "tiktokcdn", "tiktok.com", "fbcdn.net", "cdninstagram.com",
    "licdn.com", "twimg.com", "redd.it", "pinimg.com",
)

_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif", "image/heic": ".heic",
}

# Some CDNs only serve images to a request that looks like a browser on the
# platform's own site. Sending nothing at all is what makes an otherwise-live
# Instagram thumbnail 403.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

_REFERERS = {
    "instagram": "https://www.instagram.com/",
    "tiktok": "https://www.tiktok.com/",
    "facebook": "https://www.facebook.com/",
    "linkedin": "https://www.linkedin.com/",
    "twitter": "https://twitter.com/",
    "pinterest": "https://www.pinterest.com/",
}

MAX_BYTES = 8 * 1024 * 1024


def is_local(url: Optional[str]) -> bool:
    """True when the URL is already a path this server owns."""
    return bool(url) and url.startswith(PUBLIC_PREFIX)


def is_ephemeral(url: Optional[str]) -> bool:
    """True when the URL is the kind that quietly stops working."""
    if not url or is_local(url):
        return False
    lowered = url.lower()
    return any(host in lowered for host in EPHEMERAL_HOSTS)


def local_path_for(public_url: str) -> Optional[Path]:
    """Map a `/static/thumbnails/x.jpg` URL back to a file on disk."""
    if not is_local(public_url):
        return None
    name = Path(public_url).name
    if not name or "/" in name or ".." in name:
        return None
    return THUMBNAIL_DIR / name


def _stored_name(source_url: str, platform: Optional[str], suffix: str) -> str:
    digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:16]
    return f"{(platform or 'web').lower()}_{digest}{suffix}"


def cached_path_for(source_url: str, *, platform: Optional[str] = None) -> Optional[Path]:
    """The on-disk mirror of a remote URL, if one has already been stored."""
    for suffix in (".jpg", ".webp", ".png", ".gif", ".heic"):
        candidate = THUMBNAIL_DIR / _stored_name(source_url, platform, suffix)
        if candidate.is_file():
            return candidate
    return None


def fetch(url: str, *, platform: Optional[str] = None, timeout: float = 12.0
          ) -> Tuple[Optional[bytes], Optional[str]]:
    """Download an image. Returns (bytes, content_type) or (None, None).

    Never raises: a dead thumbnail is an expected, ordinary outcome.
    """
    headers = dict(_HEADERS)
    referer = _REFERERS.get((platform or "").lower())
    if referer:
        headers["Referer"] = referer

    try:
        # Server-side fetch of a user-supplied address: allowlisted host,
        # public IP only, redirects re-validated at every hop.
        from ..net_guard import PLATFORM_IMAGE_HOSTS, UnsafeURL, safe_get

        try:
            response, _final = safe_get(url, allowed_hosts=PLATFORM_IMAGE_HOSTS,
                                        headers=headers, timeout=timeout, stream=True)
        except UnsafeURL as e:
            logger.warning("refused image fetch: %s (%s)", e, url[:96])
            return None, None
        response.raise_for_status()
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
        if content_type and not content_type.startswith("image/"):
            return None, None
        body = b""
        for chunk in response.iter_content(chunk_size=64 * 1024):
            body += chunk
            if len(body) > MAX_BYTES:
                return None, None
        if len(body) < 512:          # an error page or a tracking pixel
            return None, None
        return body, (content_type or "image/jpeg")
    except Exception as e:
        logger.info("thumbnail fetch failed for %s: %s", url[:96], e)
        return None, None


def store(data: bytes, *, source_url: str, platform: Optional[str],
          content_type: Optional[str]) -> str:
    """Write image bytes into static storage and return the public path.

    The filename is derived from the *source* URL, so mirroring the same image
    twice overwrites rather than accumulating copies.
    """
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    suffix = (_EXTENSIONS.get((content_type or "").lower())
              or mimetypes.guess_extension(content_type or "") or ".jpg")
    name = _stored_name(source_url, platform, suffix)
    (THUMBNAIL_DIR / name).write_bytes(data)
    return f"{PUBLIC_PREFIX}/{name}"


def mirror_to_storage(url: Optional[str], *, namespace: str = "thumbnails",
                      platform: Optional[str] = None) -> Optional[tuple]:
    """Fetch an image and put it in durable object storage.

    Returns `(storage_key, public_url)` or None.

    This is the version the pipeline uses, and it runs the moment metadata
    yields an image rather than on first view. TikTok cover URLs are signed and
    expire in days; waiting for a user to open the item means the picture is
    often already gone by the time anyone looks. Mirroring at extraction is what
    makes "once Sava has seen a thumbnail, it keeps it" actually true.
    """
    from ..storage import derive_key, get_storage

    if not url:
        return None

    storage = get_storage()
    # Cheap idempotency: the key is a hash of the source, so a second save of
    # the same content re-uses the stored object without a fetch.
    probe = derive_key(namespace, url, content_type="image/jpeg")
    if storage.exists(probe):
        return probe, storage.url(probe)

    data, content_type = fetch(url, platform=platform)
    if not data:
        return None

    key = derive_key(namespace, url, content_type=content_type)
    try:
        storage.put(key, data, content_type=content_type or "image/jpeg")
    except Exception as e:
        logger.warning("could not mirror image to storage: %s", e)
        return None
    return key, storage.url(key)


def mirror(url: Optional[str], *, platform: Optional[str] = None) -> Optional[str]:
    """Mirror a remote thumbnail locally. Returns the new public path, or None.

    None means "keep whatever you had" — either the URL is already local, or the
    remote image could not be fetched. Callers must not overwrite a working URL
    with nothing.
    """
    if not url or is_local(url):
        return None
    data, content_type = fetch(url, platform=platform)
    if not data:
        return None
    try:
        return store(data, source_url=url, platform=platform, content_type=content_type)
    except Exception as e:
        logger.warning("could not store mirrored thumbnail: %s", e)
        return None
