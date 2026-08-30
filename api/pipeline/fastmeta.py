"""Stage A — the cheapest metadata that makes a save look like something.

── Why this exists ────────────────────────────────────────────────────────

A save used to become useful only after `acquire.fetch_metadata`, which is
yt-dlp. On YouTube that is now unreliable: production logs showed saves landing
with `title=null, author=null, thumbnail_url=null, processing_state=partial`,
and reproducing it locally gives

    yt-dlp FAIL 2399ms  ERROR: [youtube] <id>: The page needs to be reloaded.
    oEmbed OK    263ms  title='…' author='…' thumbnail='…'

yt-dlp is fighting an anti-bot challenge; YouTube's own oEmbed endpoint answers
in a quarter of a second. Because nothing downstream had a fallback, a blocked
extraction meant the user stared at a pink placeholder titled "youtube.com"
forever, and the item read as broken rather than as processing.

So metadata is now acquired in three escalating tiers, cheapest first:

    0. DERIVED   zero network. A YouTube video id yields its thumbnail URL by
                 construction, so a card has a poster the instant it is saved.
    1. FAST      one small public request, no auth, no key, no proxy. Title,
                 creator, and the real thumbnail in ~250ms.
    2. FULL      yt-dlp, as before, for duration, geometry, captions, counts.

Tier 2 remains the source of truth and overwrites nothing it can improve. The
point of tiers 0 and 1 is that the *user* never waits for tier 2, and that tier
2 failing is no longer the difference between a usable save and a blank one.

── What this deliberately does not do ─────────────────────────────────────

No scraping, no HTML parsing, no cookie replay, no residential proxy, no
API keys, no signed-in requests. oEmbed is a documented public endpoint whose
entire purpose is "give me the title and thumbnail for this public URL", and
the thumbnail pattern is the one YouTube itself serves to every embed on the
web. Nothing here widens Sava's policy surface — it narrows it, because the
common case stops going through an extractor at all.

Nothing is invented. If a tier cannot answer, it returns nothing and the caller
keeps whatever honest state it already had.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

#: Small. This runs on the worker in front of everything else, and a slow
#: answer here delays the whole pipeline — the fallback for "too slow" is the
#: full extraction that was going to run anyway.
TIMEOUT_SECONDS = 6.0

_UA = "Sava/1.0 (+https://sava.app)"


@dataclass(frozen=True)
class FastMeta:
    """Whatever the cheap path managed to learn. Any field may be None."""
    title: Optional[str] = None
    creator_name: Optional[str] = None
    thumbnail_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    source: str = "none"
    wall_ms: int = 0

    @property
    def useful(self) -> bool:
        return bool(self.title or self.creator_name or self.thumbnail_url)


# ─── Tier 0: derived, no network at all ──────────────────────────────────────

def derived_thumbnail(platform: Optional[str], content_id: Optional[str]) -> Optional[str]:
    """A poster URL computed from the content id, costing nothing.

    Only YouTube. Its thumbnail URLs are a documented, stable pattern that every
    embed on the web already relies on, and `i.ytimg.com` is on the image
    allow-list in `net_guard`. TikTok and Instagram have no such pattern — their
    CDN paths are signed and expire — so guessing one would produce a broken
    image, which is worse than no image.

    `hqdefault` rather than `maxresdefault`: only `hqdefault` is guaranteed to
    exist for every video, and the client already upgrades opportunistically
    (`MediaImage.swift`). A URL that 404s is not a fallback.
    """
    if (platform or "").lower() != "youtube":
        return None
    vid = (content_id or "").strip()
    if not vid or not _is_plausible_youtube_id(vid):
        return None
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"


def _is_plausible_youtube_id(vid: str) -> bool:
    """11 characters of the YouTube id alphabet.

    Guards against putting a URL fragment, an empty string, or somebody else's
    identifier into an image URL that the client will then try to load.
    """
    if len(vid) != 11:
        return False
    return all(c.isalnum() or c in "-_" for c in vid)


# ─── Tier 1: one small public request ────────────────────────────────────────

def fetch(platform: Optional[str], url: str,
          *, content_id: Optional[str] = None) -> FastMeta:
    """Cheap metadata for a URL, or an empty result.

    Never raises. A failure here is not an error condition — it means the
    pipeline continues to the full extraction exactly as it did before.
    """
    started = time.monotonic()
    platform = (platform or "").lower()
    try:
        if platform == "youtube":
            meta = _youtube_oembed(url)
            if meta is None:
                # oEmbed refused (private, age-gated, deleted, or rate-limited).
                # The derived thumbnail still stands on its own: a poster with
                # no title beats a placeholder with no poster.
                thumb = derived_thumbnail(platform, content_id)
                if thumb:
                    return FastMeta(thumbnail_url=thumb, source="derived",
                                    wall_ms=_ms(started))
                return FastMeta(source="none", wall_ms=_ms(started))
            return FastMeta(**meta, source="oembed", wall_ms=_ms(started))
    except Exception as e:                                   # never fatal
        logger.info("fastmeta %s failed for %s: %s", platform, url, str(e)[:200])
    return FastMeta(source="none", wall_ms=_ms(started))


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _youtube_oembed(url: str) -> Optional[dict]:
    """`https://www.youtube.com/oembed` — public, keyless, documented.

    Returns title, `author_name`, and a thumbnail. Works for Shorts and for
    watch URLs alike, which is why the canonicalised `watch?v=` form is what
    gets sent: Shorts URLs occasionally 404 here while their watch equivalent
    resolves, and `resolve_identity` has already produced the watch form.
    """
    query = urllib.parse.urlencode({"url": url, "format": "json"})
    request = urllib.request.Request(
        f"https://www.youtube.com/oembed?{query}",
        headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            return None
        payload = json.loads(response.read().decode("utf-8", "replace"))

    title = (payload.get("title") or "").strip() or None
    author = (payload.get("author_name") or "").strip() or None
    thumb = (payload.get("thumbnail_url") or "").strip() or None

    if not (title or author or thumb):
        return None
    return {
        "title": title,
        "creator_name": author,
        "thumbnail_url": thumb,
        "width": _int(payload.get("thumbnail_width")),
        "height": _int(payload.get("thumbnail_height")),
    }


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ─── Applying it ─────────────────────────────────────────────────────────────

def apply(cc, meta: FastMeta) -> bool:
    """Fill only the empty fields on a canonical row. Returns whether it changed.

    Never overwrites: the full extraction is more authoritative than oEmbed, and
    on a re-run this must not undo a better title with a shorter one. The
    thumbnail is the one exception, and only in one direction — a real oEmbed
    thumbnail replaces a *derived* one, because the derived URL is a guess that
    happens to be right and the oEmbed URL is what YouTube says.
    """
    changed = False
    if meta.title and not cc.title:
        cc.title = meta.title[:500]
        changed = True
    if meta.creator_name and not cc.creator_name:
        cc.creator_name = meta.creator_name[:200]
        changed = True
    if meta.thumbnail_url:
        current = cc.thumbnail_url or ""
        if not current or (meta.source == "oembed" and "/hqdefault.jpg" in current):
            if meta.thumbnail_url != current:
                cc.thumbnail_url = meta.thumbnail_url
                changed = True
    return changed
