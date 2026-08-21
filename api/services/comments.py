"""Community comments as canonical, cached enrichment.

Two problems with how comments worked before, both fatal at scale:

  1. **They were keyed on the user's save.** `comments.bookmark_id` means ten
     thousand people saving one video is ten thousand fetches of the same public
     comment thread and ten thousand copies of the same rows. Every other
     expensive thing in this pipeline is keyed on canonical content; comments
     were the exception.
  2. **They had no clock.** Nothing recorded when a thread was last read, so
     there was no way to answer "is this stale?" other than fetching it again.

Both are fixed here: comments belong to `canonical_content`, they carry a
`fetched_at`, and a TTL decides whether a re-read is warranted. A second user
saving the same video does no network work at all.

Comments are also deliberately *subordinate*. They are fetched by their own job,
at low priority, behind the platform budget, and their failure is recorded on the
content without touching its processing state. An item is READY when Sava
understands it — not when the internet has finished arguing underneath it.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..ai import telemetry
from ..config import (
    COMMENTS_ENABLED, COMMENTS_MAX_PER_ITEM, COMMENTS_MIN_LIKES,
    COMMENTS_TIKTOK_ENABLED, COMMENTS_TTL_DAYS, COMMENTS_YOUTUBE_ENABLED,
    COMMENT_VERSION,
)
from ..models import CanonicalContent, ContentComment
from ..platform_budget import PlatformUnavailable, get_manager

logger = logging.getLogger(__name__)


@dataclass
class FetchedComment:
    text: str
    author: Optional[str] = None
    platform_comment_id: Optional[str] = None
    like_count: int = 0
    reply_count: int = 0
    is_creator: bool = False
    published_at: Optional[datetime] = None


@dataclass
class CommentFetch:
    ok: bool
    comments: List[FetchedComment] = field(default_factory=list)
    source: str = "top"
    error: Optional[str] = None
    wall_ms: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)


class CommentsProvider(ABC):
    """Reads a bounded, ranked sample of a public comment thread."""

    platform: str = "other"

    @abstractmethod
    def fetch(self, content: CanonicalContent, *, limit: int) -> CommentFetch: ...

    @property
    def available(self) -> bool:
        return True


class YouTubeCommentsProvider(CommentsProvider):
    """Wraps the existing `youtube_comment_downloader` service.

    Preserved rather than rewritten — it works, it needs no key, and it already
    sorts by popularity, which is exactly the bounded sample wanted here.
    """

    platform = "youtube"

    @property
    def available(self) -> bool:
        return COMMENTS_YOUTUBE_ENABLED

    def fetch(self, content: CanonicalContent, *, limit: int) -> CommentFetch:
        import time

        started = time.monotonic()
        try:
            from ..comment_service import youtube_comment_service

            result = youtube_comment_service.get_comments(
                content.canonical_url, limit=limit, sort_by="popular")
            wall = int((time.monotonic() - started) * 1000)

            if not result.get("success"):
                return CommentFetch(False, error=result.get("error") or "fetch failed",
                                    wall_ms=wall)

            comments = []
            for raw in result.get("comments") or []:
                text = (raw.get("text") or "").strip()
                if not text:
                    continue
                blob = raw.get("raw") or {}
                comments.append(FetchedComment(
                    text=text,
                    author=raw.get("author"),
                    platform_comment_id=str(blob.get("cid") or "") or None,
                    like_count=int(raw.get("like_count") or 0),
                    reply_count=int(blob.get("replies") or 0),
                    is_creator=bool(blob.get("channel") and blob.get("heart")),
                ))
            return CommentFetch(True, comments=comments, source="top", wall_ms=wall)
        except Exception as e:
            return CommentFetch(False, error=str(e)[:300],
                                wall_ms=int((time.monotonic() - started) * 1000))


class TikTokCommentsProvider(CommentsProvider):
    """Wraps the existing TikTok comment scraper.

    Off by default. That path needs a logged-in session cookie which this
    deployment does not have, and an unconfigured scraper that fails on every
    call is worse than one that is honestly switched off — it burns the circuit
    breaker and fills the error budget for a feature nobody enabled.
    """

    platform = "tiktok"

    @property
    def available(self) -> bool:
        if not COMMENTS_TIKTOK_ENABLED:
            return False
        try:
            from ..tiktok_comment_service import tiktok_service
            cookies = getattr(tiktok_service, "cookies", "") or ""
            return bool(cookies) and "PASTE_YOUR_FULL_COOKIE_STRING_HERE" not in cookies
        except Exception:
            return False

    def fetch(self, content: CanonicalContent, *, limit: int) -> CommentFetch:
        import time

        started = time.monotonic()
        if not content.platform_content_id:
            return CommentFetch(False, error="no TikTok post id")
        try:
            from ..tiktok_comment_service import tiktok_service

            result = tiktok_service.fetch_comments(content.platform_content_id,
                                                   max_comments=limit)
            wall = int((time.monotonic() - started) * 1000)
            if not result.get("success"):
                return CommentFetch(False, error=result.get("message") or "fetch failed",
                                    wall_ms=wall)

            comments = []
            for raw in result.get("comments") or []:
                text = (raw.get("text") or "").strip()
                if not text:
                    continue
                comments.append(FetchedComment(
                    text=text,
                    author=(raw.get("user") or {}).get("unique_id")
                    if isinstance(raw.get("user"), dict) else raw.get("author"),
                    platform_comment_id=str(raw.get("cid") or "") or None,
                    like_count=int(raw.get("digg_count") or raw.get("like_count") or 0),
                    reply_count=int(raw.get("reply_comment_total") or 0),
                ))
            return CommentFetch(True, comments=comments, source="top", wall_ms=wall)
        except Exception as e:
            return CommentFetch(False, error=str(e)[:300],
                                wall_ms=int((time.monotonic() - started) * 1000))


class InstagramCommentsProvider(CommentsProvider):
    """Instagram comments, which in practice means: not without an account.

    The audit found no unauthenticated path. Open Graph carries a comment
    *count* and no comment text; the endpoints that return threads all require
    a logged-in session, which is the operator-account dependency this
    architecture exists to avoid.

    So the provider is real, registered, and reports itself unavailable. That is
    deliberately not the same as omitting it: `ensure_comments` already treats an
    unavailable provider as "skip, state = disabled", so the intelligence
    pipeline runs to completion on caption, OCR and carousel imagery without
    ever waiting on comments. Registering an honest "no" also means the slot is
    there the day a licensed provider can fill it, with no other code changing.
    """

    platform = "instagram"

    @property
    def available(self) -> bool:
        from ..config import COMMENTS_INSTAGRAM_ENABLED
        return COMMENTS_INSTAGRAM_ENABLED

    def fetch(self, content: CanonicalContent, *, limit: int) -> CommentFetch:
        return CommentFetch(
            False,
            error="Instagram exposes no unauthenticated comment source")


_PROVIDERS: Dict[str, CommentsProvider] = {
    "youtube": YouTubeCommentsProvider(),
    "tiktok": TikTokCommentsProvider(),
    "instagram": InstagramCommentsProvider(),
}


def provider_for(platform: str) -> Optional[CommentsProvider]:
    return _PROVIDERS.get((platform or "").lower())


# ─── Cache policy ────────────────────────────────────────────────────────────

def is_stale(content: CanonicalContent) -> bool:
    """Whether this thread is worth reading again.

    Comments on old content barely move, so the default TTL is long. The point
    of the TTL is not freshness for its own sake — it is to make sure the answer
    to "should I fetch?" is a cheap timestamp comparison rather than a request.
    """
    if content.comments_state == "disabled":
        return False
    if content.comment_version != COMMENT_VERSION:
        return True
    if not content.comments_fetched_at:
        return True
    age = datetime.now(timezone.utc) - _aware(content.comments_fetched_at)
    return age > timedelta(days=COMMENTS_TTL_DAYS)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ensure_comments(db, canonical_id: int, *, force: bool = False,
                    user_id: Optional[int] = None) -> Dict[str, Any]:
    """Fetch this content's comments once, or reuse what is already stored.

    Safe to call for every save: the common path is a timestamp check and a
    return. Never raises into the caller's flow — the worst outcome is a
    recorded failure and an item that has everything except comments.
    """
    content = db.query(CanonicalContent).get(canonical_id)
    if content is None:
        return {"ok": False, "reason": "not_found"}

    if not COMMENTS_ENABLED:
        return {"ok": True, "reason": "disabled", "cached": True}

    provider = provider_for(content.platform)
    if provider is None or not provider.available:
        content.comments_state = "disabled"
        db.commit()
        return {"ok": True, "reason": "no_provider", "cached": True}

    existing = (db.query(ContentComment)
                .filter(ContentComment.canonical_content_id == canonical_id).count())

    if not force and existing and not is_stale(content):
        telemetry.record(db, operation="comments.cache_hit", canonical_content_id=canonical_id,
                         user_id=user_id, platform=content.platform, cache_hit=True)
        return {"ok": True, "reason": "cached", "cached": True, "count": existing}

    # Behind the platform budget like every other outbound call, so a comment
    # storm cannot consume the request allowance that metadata needs.
    manager = get_manager()
    available, wait_s, reason = manager.availability(content.platform)
    if not available:
        raise PlatformUnavailable(content.platform, f"comments: {reason}", wait_s)

    with manager.acquire(content.platform, "comments") as slot:
        result = provider.fetch(content, limit=COMMENTS_MAX_PER_ITEM)
        if result.ok:
            slot.ok()
        else:
            slot.failed(result.error or "comments fetch failed")

    telemetry.record(
        db, operation="comments.fetch", canonical_content_id=canonical_id,
        user_id=user_id, platform=content.platform, wall_ms=result.wall_ms,
        success=result.ok, error=result.error,
    )

    if not result.ok:
        content.comments_state = "failed"
        db.commit()
        return {"ok": False, "reason": result.error, "count": existing}

    stored = _store(db, content, result)
    content.comments_state = "ok"
    content.comment_version = COMMENT_VERSION
    content.comments_fetched_at = datetime.now(timezone.utc)
    content.comment_count = stored
    db.commit()

    # Comments become their own retrieval modality so Ask can tell the audience
    # apart from the creator.
    try:
        _index_comments(db, canonical_id, user_id=user_id)
    except Exception as e:
        logger.warning("comment indexing failed for %s: %s", canonical_id, e)

    return {"ok": True, "reason": "fetched", "count": stored}


def _store(db, content: CanonicalContent, result: CommentFetch) -> int:
    """Replace the stored sample. Ranked, bounded, deduplicated."""
    kept = [c for c in result.comments
            if c.like_count >= COMMENTS_MIN_LIKES and c.text.strip()]
    kept = kept[:COMMENTS_MAX_PER_ITEM]

    (db.query(ContentComment)
       .filter(ContentComment.canonical_content_id == content.id)
       .delete(synchronize_session=False))

    seen: set = set()
    written = 0
    for rank, comment in enumerate(kept):
        key = comment.platform_comment_id or f"t:{hash(comment.text) & 0xFFFFFFFF}"
        if key in seen:
            continue
        seen.add(key)
        db.add(ContentComment(
            canonical_content_id=content.id,
            platform_comment_id=key[:64],
            author=(comment.author or "")[:255] or None,
            text=comment.text[:4000],
            like_count=comment.like_count,
            reply_count=comment.reply_count,
            is_creator=comment.is_creator,
            rank=rank,
            source=result.source,
            published_at=comment.published_at,
        ))
        written += 1
    db.commit()
    return written


def _index_comments(db, canonical_id: int, *, user_id: Optional[int] = None) -> int:
    """Make the sample retrievable, tagged as `comment`.

    A separate modality is the whole point. Without it a popular comment reads
    to the model exactly like something the creator said, and Ask starts
    attributing audience opinion to the video.
    """
    from ..ai.router import get_router
    from ..models import ContentChunk
    from ..pipeline.chunking import Chunk
    from ..vectors import to_storage

    rows = (db.query(ContentComment)
            .filter(ContentComment.canonical_content_id == canonical_id)
            .order_by(ContentComment.rank).all())
    if not rows:
        return 0

    (db.query(ContentChunk)
       .filter(ContentChunk.canonical_content_id == canonical_id,
               ContentChunk.modality == "comment")
       .delete(synchronize_session=False))

    # Comments are short; batching several into one chunk keeps the embedding
    # count sane and gives the model enough context to read consensus.
    batches: List[str] = []
    buf: List[str] = []
    for row in rows:
        buf.append(f"- {row.text}" + (f" ({row.like_count} likes)" if row.like_count else ""))
        if len(buf) >= 8:
            batches.append("Viewer comments:\n" + "\n".join(buf))
            buf = []
    if buf:
        batches.append("Viewer comments:\n" + "\n".join(buf))

    base_index = (db.query(ContentChunk)
                  .filter(ContentChunk.canonical_content_id == canonical_id)
                  .count())

    router = get_router()
    vectors = []
    if router.is_available():
        try:
            res = router.embed(batches, task_type="retrieval_document")
            vectors = res.vectors or []
            telemetry.record_embedding(db, res, operation="comments.embed",
                                       canonical_content_id=canonical_id, user_id=user_id)
        except Exception as e:
            logger.warning("comment embedding failed: %s", e)

    for offset, text in enumerate(batches):
        db.add(ContentChunk(
            canonical_content_id=canonical_id,
            chunk_index=base_index + offset,
            modality="comment",
            text=text,
            embedding=to_storage(vectors[offset]) if offset < len(vectors) else None,
        ))
    db.commit()
    return len(batches)


def comments_for(db, canonical_id: int, *, limit: int = 20) -> List[ContentComment]:
    """The stored sample, most prominent first."""
    return (db.query(ContentComment)
            .filter(ContentComment.canonical_content_id == canonical_id)
            .order_by(ContentComment.rank)
            .limit(limit).all())
