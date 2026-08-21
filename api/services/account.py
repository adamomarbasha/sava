"""Account deletion and export.

Apple's Guideline 5.1.1(v) requires any app offering account creation to offer
account deletion from inside the app. Neither existed here.

The interesting part is not the deletion, it is the *boundary*. Sava
canonicalises content: two people who save the same TikTok share one
`canonical_content` row and one expensive `content_understanding`,
`content_transcript` and set of `content_embeddings` derived from it. That is a
real cost saving and it is also a trap — a naive "delete everything this user
touched" would reach through the shared row and destroy another user's library.

So deletion is defined in two layers:

  * **Private.** Rows carrying this user's `user_id`, plus everything hanging off
    them. Always deleted.
  * **Shared.** `canonical_content` and its derived children. Deleted only when
    the last bookmark referencing it goes away — reference counted, not
    presumed.

Cascades are declared in the schema, but this deletes explicitly and in
dependency order anyway. SQLite does not enforce `ON DELETE` unless
`PRAGMA foreign_keys=ON`, and Postgres always does; relying on the database would
mean deletion behaved differently in development and production. That difference
is precisely the class of bug that already cost this codebase a silently
discarded write.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from ..models import (
    Bookmark, CanonicalContent, Caption, ChatMessage, ChatThread, Collection,
    CollectionFeedback, CollectionItem, CollectionView, Comment,
    ContentAsset, ContentChunk, ContentComment, ContentEmbedding, ContentFrame,
    ContentTranscript, ContentUnderstanding, UsageEvent, User, YouTubeDetails,
)

logger = logging.getLogger(__name__)


@dataclass
class DeletionReport:
    """What was removed, for the audit trail and for the tests."""
    user_id: int
    deleted: Dict[str, int] = field(default_factory=dict)
    canonical_deleted: int = 0
    canonical_retained: int = 0

    def note(self, table: str, count: int) -> None:
        if count:
            self.deleted[table] = self.deleted.get(table, 0) + count


def _delete_orphaned_canonical(db: Session, canonical_ids: List[int],
                               report: DeletionReport) -> None:
    """Delete shared content only where nothing references it any more.

    The reference count is over `bookmarks`, because a bookmark *is* the
    statement "a user has this saved". Once the last one is gone the canonical
    row is unreachable by anybody and keeping it would be retaining a stranger's
    content with no user attached — which is the thing a deletion request is
    asking us not to do.
    """
    for canonical_id in canonical_ids:
        remaining = (db.query(Bookmark.id)
                     .filter(Bookmark.canonical_content_id == canonical_id)
                     .first())
        if remaining is not None:
            report.canonical_retained += 1
            continue

        for model in (ContentUnderstanding, ContentTranscript, ContentEmbedding,
                      ContentChunk, ContentFrame, ContentAsset, ContentComment):
            n = (db.query(model)
                 .filter(model.canonical_content_id == canonical_id)
                 .delete(synchronize_session=False))
            report.note(model.__tablename__, n)

        n = (db.query(CanonicalContent)
             .filter(CanonicalContent.id == canonical_id)
             .delete(synchronize_session=False))
        report.canonical_deleted += n


def delete_account(db: Session, user_id: int) -> DeletionReport:
    """Erase a user and everything private to them. Returns what was removed."""
    report = DeletionReport(user_id=user_id)

    bookmark_ids = [row[0] for row in
                    db.query(Bookmark.id).filter(Bookmark.user_id == user_id).all()]
    canonical_ids = sorted({
        row[0] for row in
        db.query(Bookmark.canonical_content_id)
        .filter(Bookmark.user_id == user_id,
                Bookmark.canonical_content_id.isnot(None)).all()
        if row[0] is not None})

    # ── Conversations ────────────────────────────────────────────────────────
    thread_ids = [row[0] for row in
                  db.query(ChatThread.id).filter(ChatThread.user_id == user_id).all()]
    if thread_ids:
        report.note("chat_messages", db.query(ChatMessage)
                    .filter(ChatMessage.thread_id.in_(thread_ids))
                    .delete(synchronize_session=False))
    report.note("chat_threads", db.query(ChatThread)
                .filter(ChatThread.user_id == user_id)
                .delete(synchronize_session=False))

    # ── Collections ──────────────────────────────────────────────────────────
    collection_ids = [row[0] for row in
                      db.query(Collection.id)
                      .filter(Collection.user_id == user_id).all()]
    if collection_ids:
        report.note("collection_items", db.query(CollectionItem)
                    .filter(CollectionItem.collection_id.in_(collection_ids))
                    .delete(synchronize_session=False))
    report.note("collection_views", db.query(CollectionView)
                .filter(CollectionView.user_id == user_id)
                .delete(synchronize_session=False))
    report.note("collection_feedback", db.query(CollectionFeedback)
                .filter(CollectionFeedback.user_id == user_id)
                .delete(synchronize_session=False))
    report.note("collections", db.query(Collection)
                .filter(Collection.user_id == user_id)
                .delete(synchronize_session=False))

    # ── Per-bookmark children ────────────────────────────────────────────────
    if bookmark_ids:
        for model in (Caption, Comment, YouTubeDetails):
            report.note(model.__tablename__, db.query(model)
                        .filter(model.bookmark_id.in_(bookmark_ids))
                        .delete(synchronize_session=False))
        # A collection belonging to *another* user could reference this
        # bookmark; clear those links before the row goes.
        report.note("collection_items", db.query(CollectionItem)
                    .filter(CollectionItem.bookmark_id.in_(bookmark_ids))
                    .delete(synchronize_session=False))

    report.note("bookmarks", db.query(Bookmark)
                .filter(Bookmark.user_id == user_id)
                .delete(synchronize_session=False))

    # Flushed before reference counting, so the count sees the deletions.
    db.flush()

    # ── Shared content, only where now unreferenced ──────────────────────────
    _delete_orphaned_canonical(db, canonical_ids, report)

    # ── Usage attribution ────────────────────────────────────────────────────
    # `usage_events` has no foreign key, so nothing would have removed these.
    # They are billing telemetry, but they are also a per-user record of when
    # somebody used the product, which is exactly what a deletion request covers.
    report.note("usage_events", db.query(UsageEvent)
                .filter(UsageEvent.user_id == user_id)
                .delete(synchronize_session=False))

    report.note("users", db.query(User).filter(User.id == user_id)
                .delete(synchronize_session=False))

    db.commit()
    logger.info("account %s deleted: %s (canonical deleted=%s retained=%s)",
                user_id, report.deleted, report.canonical_deleted,
                report.canonical_retained)
    return report


# ─── Export ──────────────────────────────────────────────────────────────────

def _iso(value) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def export_account(db: Session, user_id: int) -> Dict[str, Any]:
    """Everything Sava holds about this user, as plain JSON.

    Their own content and their own derived understanding — not the internals of
    the shared canonical layer, and never a password hash.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return {}

    bookmarks = db.query(Bookmark).filter(Bookmark.user_id == user_id).all()
    collections = db.query(Collection).filter(Collection.user_id == user_id).all()
    threads = db.query(ChatThread).filter(ChatThread.user_id == user_id).all()
    thread_ids = [t.id for t in threads]
    messages = (db.query(ChatMessage).filter(ChatMessage.thread_id.in_(thread_ids)).all()
                if thread_ids else [])

    return {
        "exported_at": _iso(__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc)),
        "account": {
            "id": user.id,
            "email": user.email,
            "created_at": _iso(user.created_at),
        },
        "saves": [{
            "id": b.id,
            "url": b.url,
            "title": b.title,
            "author": b.author,
            "platform": b.platform,
            "note": b.note,
            "created_at": _iso(b.created_at),
            "last_opened_at": _iso(b.last_opened_at),
            "open_count": b.open_count,
        } for b in bookmarks],
        "collections": [{
            "id": c.id,
            "name": c.name,
            "description": getattr(c, "description", None),
            "created_at": _iso(c.created_at),
            "bookmark_ids": [row[0] for row in
                             db.query(CollectionItem.bookmark_id)
                             .filter(CollectionItem.collection_id == c.id).all()],
        } for c in collections],
        "conversations": [{
            "id": t.id,
            "scope": getattr(t, "scope", None),
            "created_at": _iso(t.created_at),
            "messages": [{
                "role": m.role,
                "content": m.content,
                "created_at": _iso(m.created_at),
            } for m in messages if m.thread_id == t.id],
        } for t in threads],
    }
