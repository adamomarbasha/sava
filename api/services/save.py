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
