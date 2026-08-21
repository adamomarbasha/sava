"""Outbound URL safety.

Sava fetches URLs that arrive from users: thumbnails to mirror, short links to
resolve. Both are server-side fetches of an attacker-influenced address, which
is the exact shape of SSRF — point the parameter at `http://169.254.169.254/`
or `http://localhost:8000/api/...` and the server reads it on the caller's
behalf, from inside the trust boundary.

Four checks, all of which have to pass:

  1. **Scheme.** http/https only. `file://`, `gopher://`, `ftp://` are not
     images and are not short links.
  2. **Host allowlist.** For platform fetches, the hostname must belong to a
     known platform CDN. An open image proxy is a bandwidth donation and an
     anonymiser for whoever finds it.
  3. **Address.** Every resolved IP must be public. Loopback, link-local, and
     RFC1918 are refused — this is what stops cloud metadata endpoints and
     internal services.
  4. **Redirects.** Followed manually, one hop at a time, re-validating at each
     step, with a hard limit. A permitted host that 302s to `127.0.0.1` defeats
     any check performed only on the original URL.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5

# Suffix match against the resolved hostname.
PLATFORM_IMAGE_HOSTS: Tuple[str, ...] = (
    # YouTube
    "ytimg.com", "ggpht.com", "youtube.com", "googleusercontent.com",
    # TikTok
    "tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com", "tiktokv.com",
    "tiktok.com", "ibyteimg.com", "byteoversea.com", "muscdn.com",
    # Instagram / Meta
    "cdninstagram.com", "fbcdn.net", "instagram.com",
    # Other platforms Sava stores links from
    "licdn.com", "twimg.com", "redd.it", "redditmedia.com", "pinimg.com",
)

# Hosts that serve rights-cleared editorial imagery for Collection covers.
#
# Deliberately separate from `PLATFORM_IMAGE_HOSTS` rather than merged into it.
# These two lists authorise different things for different reasons — one is
# "social CDNs whose thumbnails we mirror", the other is "licensed image
# sources we may publish a cover from" — and collapsing them would silently
# widen both. Every host here publishes machine-readable licence metadata.
COVER_IMAGE_HOSTS: Tuple[str, ...] = (
    "wikimedia.org", "wikipedia.org", "wikimedia.commons",
    "openverse.org", "openverse.engineering",
    "flickr.com", "staticflickr.com",
    "stocksnap.io", "rawpixel.com", "nappy.co",
    "smithsonianmag.com", "si.edu",
)

SHORT_LINK_HOSTS: Tuple[str, ...] = (
    "tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "youtu.be", "youtube.com",
)


class UnsafeURL(ValueError):
    """The URL is not one this server is willing to fetch."""


def _host_allowed(host: str, allowed: Iterable[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == a or host.endswith("." + a) for a in allowed)


def _resolve(host: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise UnsafeURL(f"cannot resolve host: {e}") from e
    return sorted({info[4][0] for info in infos})


def _addresses_are_public(addresses: Iterable[str]) -> None:
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise UnsafeURL(f"unparseable address: {raw}")
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise UnsafeURL(f"address is not public: {raw}")


def validate(url: str, *, allowed_hosts: Optional[Iterable[str]] = None,
             resolve: bool = True) -> str:
    """Raise `UnsafeURL` unless this is safe to fetch. Returns the URL.

    `resolve=False` skips the DNS lookup and checks scheme and host only. Used
    by tests so that host-allowlist behaviour can be asserted without depending
    on a working resolver, and by callers that have already pinned the address.
    Production paths leave it on — the address check is what stops a permitted
    hostname pointing at 127.0.0.1.
    """
    if not url or len(url) > 4096:
        raise UnsafeURL("missing or oversized URL")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURL(f"scheme not allowed: {parsed.scheme or '(none)'}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeURL("no host")

    if allowed_hosts is not None and not _host_allowed(host, allowed_hosts):
        raise UnsafeURL(f"host not allowed: {host}")

    # A literal IP skips DNS but never the address check.
    try:
        ipaddress.ip_address(host)
        _addresses_are_public([host])
        return url
    except ValueError:
        pass

    if resolve:
        _addresses_are_public(_resolve(host))
    return url


def safe_get(url: str, *, allowed_hosts: Optional[Iterable[str]] = None,
             headers: Optional[dict] = None, timeout: float = 12.0,
             stream: bool = False, max_redirects: int = MAX_REDIRECTS):
    """A GET that re-validates on every redirect hop.

    `requests`' own redirect following is not usable here: it validates nothing,
    so an allowed host that redirects inward would sail straight through.
    """
    import requests

    current = validate(url, allowed_hosts=allowed_hosts)
    session = requests.Session()

    for _ in range(max_redirects + 1):
        response = session.get(current, headers=headers or {}, timeout=timeout,
                               stream=stream, allow_redirects=False)
        if response.status_code not in (301, 302, 303, 307, 308):
            return response, current

        location = response.headers.get("Location")
        if not location:
            return response, current
        current = validate(requests.compat.urljoin(current, location),
                           allowed_hosts=allowed_hosts)
        response.close()

    raise UnsafeURL("too many redirects")


def resolve_short_link(url: str, *, timeout: float = 10.0) -> str:
    """Follow a platform short link to its real URL, safely.

    Used for `vm.tiktok.com/XXXX`, whose real video id is only knowable after the
    redirect. Restricted to platform hosts so it cannot be turned into a general
    redirect-following oracle.
    """
    import requests

    current = validate(url, allowed_hosts=SHORT_LINK_HOSTS)
    session = requests.Session()

    for _ in range(MAX_REDIRECTS):
        response = session.head(current, timeout=timeout, allow_redirects=False)
        if response.status_code not in (301, 302, 303, 307, 308):
            return current
        location = response.headers.get("Location")
        if not location:
            return current
        current = validate(requests.compat.urljoin(current, location),
                           allowed_hosts=SHORT_LINK_HOSTS)
    return current
