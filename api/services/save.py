"""Fast, non-blocking save.

The previous path ran `add_bookmark()` — which calls yt-dlp — *inside* the HTTP
request handler. That is the single worst scaling property in the system: 5,000
concurrent saves meant 5,000 concurrent external extractions, each holding a
request thread open for seconds, with no rate control and no isolation. A
platform slowdown became an API outage.

This path does zero network I/O:

    SAVE
      ↓  resolve canonical identity from the URL (deterministic, free)
      ↓  cache hit?  ── YES → return the cached metadata immediately
      ↓              ── NO  → create the save, enqueue, return "queued"
      ↓  background: platform budget → acquisition → processing

The viral case is the point: when a thousand users save the same TikTok, the
first save queues one job and the other 999 are pure database reads that return
the already-processed title, thumbnail, and summary instantly.

Response shape is byte-compatible with the legacy path; only latency and the
`processing_state`/`canonical_id` additions differ.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from ..models import Bookmark, CanonicalContent, ProcessingState, YouTubeDetails

logger = logging.getLogger(__name__)


class DuplicateSave(ValueError):
    """The user already saved this URL."""


def _response(bookmark: Bookmark, cc: Optional[CanonicalContent],
              *, reused: bool) -> Dict[str, Any]:
    """Legacy-compatible payload, enriched from canonical content when present."""
    meta: Dict[str, Any] = {}
    if bookmark.youtube_details:
        yt = bookmark.youtube_details[0]
        meta = {
            "video_id": yt.video_id, "channel_id": yt.channel_id,
            "duration_seconds": yt.duration_seconds, "view_count": yt.view_count,
            "like_count": yt.like_count,
            "tags": json.loads(yt.tags) if yt.tags else [],
        }
    elif cc is not None and cc.platform == "youtube":
        meta = {"video_id": cc.platform_content_id,
                "duration_seconds": cc.duration_seconds}

    return {
        "id": bookmark.id,
        "platform": bookmark.platform,
        "url": bookmark.url,
        "title": bookmark.title,
        "author": bookmark.author,
        "thumbnail_url": bookmark.thumbnail_url,
        "note": bookmark.note,
        "published_at": bookmark.published_at.isoformat() if bookmark.published_at else None,
        "created_at": bookmark.created_at.isoformat() if bookmark.created_at else None,
        "meta": meta,
        # Additive fields — older clients ignore them.
        "processing_state": bookmark.processing_state or ProcessingState.QUEUED,
        "canonical_id": bookmark.canonical_content_id,
        "reused_canonical": reused,
    }


def create_save(db, *, url: str, user_id: int, note: Optional[str] = None,
                title: Optional[str] = None) -> Dict[str, Any]:
    """Create a user save immediately. Never performs network I/O.

    Raises `DuplicateSave` when this user already saved the URL.
    """
    from ..content.identity import detect_platform, resolve_identity
    from ..jobs import enqueue
    from ..pipeline.ingest import resolve_or_create_canonical

    url = (url or "").strip()
    if not url:
        raise ValueError("A URL is required")

    ident = resolve_identity(url)
    platform = ident.platform if ident else detect_platform(url)

    # Duplicate detection, per user. Checks both the literal URL and — via the
    # canonical key — any other URL shape for the same content, so saving the
    # same Reel twice through different links is still caught.
    existing = (db.query(Bookmark)
                .filter(Bookmark.user_id == user_id, Bookmark.url == url).first())
    if existing is None and ident is not None:
        cc_existing = (db.query(CanonicalContent)
                       .filter(CanonicalContent.content_key == ident.content_key)
                       .first())
        if cc_existing is not None:
            existing = (db.query(Bookmark)
                        .filter(Bookmark.user_id == user_id,
                                Bookmark.canonical_content_id == cc_existing.id)
                        .first())
    if existing is not None:
        raise DuplicateSave(
            "You already have this link bookmarked! "
            "Check your existing bookmarks to find it."
        )

    cc, created = resolve_or_create_canonical(db, url, platform)

    bookmark = Bookmark(
        user_id=user_id, url=url, platform=platform, raw="{}",
        note=(note or None), title=(title or None),
        canonical_content_id=(cc.id if cc else None),
        processing_state=(cc.processing_state if cc else ProcessingState.QUEUED),
    )
    db.add(bookmark)
    db.commit()
    db.refresh(bookmark)

    reused = False
    if cc is not None:
        # Cache hit: the content is already understood. Copy the public
        # metadata onto this user's save and return it fully populated.
        if cc.processing_state in (ProcessingState.READY, ProcessingState.PARTIAL):
            reused = True
            _apply_cached_metadata(db, bookmark, cc)
            from ..ai import telemetry
            telemetry.record(db, operation="save.cache_hit", user_id=user_id,
                             canonical_content_id=cc.id, bookmark_id=bookmark.id,
                             platform=cc.platform, cache_hit=True)
        else:
            if cc.title and not bookmark.title:
                _apply_cached_metadata(db, bookmark, cc)
            # One job per canonical item, regardless of how many users save it.
            enqueue(db, "content.process",
                    {"canonical_id": cc.id, "user_id": user_id},
                    idempotency_key=f"content.process:{cc.id}",
                    platform=cc.platform, priority=50)
            from ..ai import telemetry
            telemetry.record(db, operation="save.queued", user_id=user_id,
                             canonical_content_id=cc.id, bookmark_id=bookmark.id,
                             platform=cc.platform, cache_hit=not created)

    return _response(bookmark, cc, reused=reused)


def _apply_cached_metadata(db, bookmark: Bookmark, cc: CanonicalContent) -> None:
    """Copy public canonical metadata onto a user's save.

    Only ever copies *public* content fields. Notes, collections, and chat
    history are user-owned and never touched here.
    """
    bookmark.title = bookmark.title or cc.title
    bookmark.author = bookmark.author or cc.creator_name or cc.creator_handle
    bookmark.thumbnail_url = bookmark.thumbnail_url or cc.thumbnail_url
    bookmark.description = bookmark.description or cc.description
    bookmark.published_at = bookmark.published_at or cc.published_at
    bookmark.processing_state = cc.processing_state

    if (cc.platform == "youtube" and cc.platform_content_id
            and not bookmark.youtube_details):
        db.add(YouTubeDetails(
            bookmark_id=bookmark.id, video_id=cc.platform_content_id,
            duration_seconds=cc.duration_seconds, extra="{}",
        ))
    db.commit()
    db.refresh(bookmark)


def sync_bookmarks_for_canonical(db, canonical_id: int) -> int:
    """Push finished canonical metadata onto every user save pointing at it.

    Called when processing completes so users who saved before the content was
    understood get the title/thumbnail without re-fetching anything.
    """
    cc = db.query(CanonicalContent).get(canonical_id)
    if cc is None:
        return 0
    saves = (db.query(Bookmark)
             .filter(Bookmark.canonical_content_id == canonical_id).all())
    for bm in saves:
        try:
            _apply_cached_metadata(db, bm, cc)
        except Exception as e:
            logger.warning("could not sync bookmark %s: %s", bm.id, e)
    return len(saves)


def create_partial_capture(db, *, user_id: int, platform: str,
                           read: Dict[str, Any], screenshot: bytes) -> Dict[str, Any]:
    """Save a capture whose exact URL could not be recovered.

    Instagram and TikTok expose no public lookup from a handle+caption back to
    a post id, so a screenshot alone cannot yield the canonical URL. Everything
    stored here was actually read off the screen — creator, caption, on-screen
    text — and the screenshot itself becomes the thumbnail. Nothing is invented,
    and the record can be upgraded later if the real URL turns up.

    The URL points at the creator's profile (the most useful thing we can
    legitimately link to) with a content fingerprint appended so two different
    posts by the same creator stay distinct.
    """
    import hashlib
    import json as _json
    from pathlib import Path

    from ..config import API_DIR
    from ..models import CanonicalContent, ProcessingState

    creator = (read.get("creator") or "").strip().lstrip("@")
    caption = (read.get("caption") or "").strip()
    on_screen = (read.get("on_screen_text") or "").strip()
    title = (caption or on_screen or f"Capture from @{creator}" if creator else "Capture")
    title = title.replace("\n", " ")[:200]

    fingerprint = hashlib.sha256(
        f"{platform}|{creator}|{caption}|{on_screen}".encode("utf-8")
    ).hexdigest()[:12]

    host = {"instagram": "www.instagram.com", "tiktok": "www.tiktok.com"}.get(
        platform, "example.com")
    profile = f"https://{host}/@{creator}/" if platform == "tiktok" and creator \
        else (f"https://{host}/{creator}/" if creator else f"https://{host}/")
    url = f"{profile}#sava-{fingerprint}"

    existing = (db.query(Bookmark)
                .filter(Bookmark.user_id == user_id, Bookmark.url == url).first())
    if existing is not None:
        raise DuplicateSave("You already saved this one.")

    content_key = f"{platform}:partial:{fingerprint}"
    cc = (db.query(CanonicalContent)
          .filter(CanonicalContent.content_key == content_key).first())
    if cc is None:
        cc = CanonicalContent(
            content_key=content_key, platform=platform, canonical_url=url,
            media_kind="video", title=title, creator_handle=creator or None,
            description=(on_screen or None),
            # PARTIAL, never READY: this record is genuinely incomplete, and
            # marking it ready would hide that from the rest of the system.
            processing_state=ProcessingState.PARTIAL,
            processing_level=1,
            stage_status=_json.dumps({
                "metadata": {"status": "ok", "detail": "read from screenshot"},
                "transcript": {"status": "skipped", "detail": "no canonical URL"},
            }),
            last_error="exact post URL not recoverable from a screenshot",
        )
        db.add(cc)
        db.commit()
        db.refresh(cc)

    # Persist the screenshot as the thumbnail so the library looks right.
    try:
        thumbs = Path(API_DIR) / "static" / "thumbnails"
        thumbs.mkdir(parents=True, exist_ok=True)
        name = f"capture_{platform}_{fingerprint}.jpg"
        from .resolver import compress_for_vision
        jpeg, _ = compress_for_vision(screenshot)
        (thumbs / name).write_bytes(jpeg)
        cc.thumbnail_url = f"/static/thumbnails/{name}"
        db.commit()
    except Exception as e:
        logger.warning("could not store capture thumbnail: %s", e)

    bookmark = Bookmark(
        user_id=user_id, url=url, platform=platform, raw="{}",
        title=title, author=(creator or None),
        thumbnail_url=cc.thumbnail_url, description=(on_screen or None),
        canonical_content_id=cc.id,
        processing_state=ProcessingState.PARTIAL,
    )
    db.add(bookmark)
    db.commit()
    db.refresh(bookmark)

    from ..ai import telemetry
    telemetry.record(db, operation="save.partial_capture", user_id=user_id,
                     canonical_content_id=cc.id, bookmark_id=bookmark.id,
                     platform=platform)

    return _response(bookmark, cc, reused=False)
