"""Job handlers. Importing this module registers them with the queue."""
from __future__ import annotations

import logging
from typing import Any, Dict

from ..jobs import handler
from ..models import Bookmark, CanonicalContent, ProcessingState

logger = logging.getLogger(__name__)


@handler("content.process")
def process_content_job(payload: Dict[str, Any], db) -> None:
    """Run the ingestion ladder for one canonical content item.

    Idempotent: `process_content` returns early when the item is already at the
    current pipeline version, so a retry after a partial failure resumes rather
    than repeating the expensive stages.
    """
    from .ingest import process_content

    canonical_id = int(payload["canonical_id"])
    result = process_content(
        canonical_id, db,
        force=bool(payload.get("force")),
        user_id=payload.get("user_id"),
        deep=bool(payload.get("deep")),
    )
    _settle_units(db, canonical_id, payload.get("user_id"))
    _sync_bookmark_states(db, canonical_id)
    try:
        from ..services.save import sync_bookmarks_for_canonical
        sync_bookmarks_for_canonical(db, canonical_id)
    except Exception as e:
        logger.warning("metadata sync failed for canonical %s: %s", canonical_id, e)
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "processing failed")
    logger.info("processed canonical %s: %s", canonical_id, result.get("stages"))


@handler("content.comments")
def comments_job(payload: Dict[str, Any], db) -> None:
    """Fetch this content's comment sample, once, for everyone.

    A separate job on purpose. Comments are the least reliable and least
    important thing Sava reads: the provider is a scraper, the value is
    secondary, and nothing about the item's usefulness depends on it. Running it
    inline would let a comment-thread failure hold up or fail a save that is
    otherwise complete.
    """
    from ..services.comments import ensure_comments

    canonical_id = int(payload["canonical_id"])
    result = ensure_comments(db, canonical_id, force=bool(payload.get("force")),
                             user_id=payload.get("user_id"))
    logger.info("comments for canonical %s: %s", canonical_id, result)
    # A provider failure is recorded on the content and the job is done. It is
    # not retried into the ground for something optional.


@handler("content.backfill_metadata")
def backfill_metadata_job(payload: Dict[str, Any], db) -> None:
    """Fill in title/creator/thumbnail for a canonical row from a user's bookmark.

    Cheap path used when a bookmark already carries metadata the ingestor
    fetched, so we do not re-hit the network just to populate canonical fields.
    """
    cc = db.query(CanonicalContent).get(int(payload["canonical_id"]))
    if cc is None:
        return
    bm = db.query(Bookmark).get(int(payload["bookmark_id"])) if payload.get("bookmark_id") else None
    if bm is None:
        return
    cc.title = cc.title or bm.title
    cc.description = cc.description or bm.description
    cc.creator_handle = cc.creator_handle or bm.author
    cc.thumbnail_url = cc.thumbnail_url or bm.thumbnail_url
    cc.published_at = cc.published_at or bm.published_at
    db.commit()


@handler("collections.recluster")
def recluster_job(payload: Dict[str, Any], db) -> None:
    """Rebuild a user's automatic collections from their embeddings."""
    from ..services.collections import rebuild_auto_collections

    user_id = int(payload["user_id"])
    stats = rebuild_auto_collections(db, user_id)
    logger.info("reclustered collections for user %s: %s", user_id, stats)


@handler("collection.match")
def collection_match_job(payload: Dict[str, Any], db) -> None:
    """Populate a newly created manual collection with likely members."""
    from ..services.collections import suggest_for_collection

    suggest_for_collection(
        db, int(payload["collection_id"]),
        auto_add=bool(payload.get("auto_add")),
    )


def _settle_units(db, canonical_id: int, user_id) -> None:
    """Close the reservation now the real route is known.

    At save time nothing is known about how this item will be understood —
    `create_save` does no network I/O — so it reserved the cheap route. By here
    the pipeline has chosen and run a route, and `units_for_content` reads it off
    the row, so the account is charged for the work that actually happened.

    This is the "partial escalation debit": a save that stayed on captions costs
    its 1 reserved unit and settles to 1. One that had to download video and read
    frames settles up to 8. Nobody pays for frames that were never read.
    """
    if not user_id:
        return
    try:
        from .. import billing, plans
        cc = db.query(CanonicalContent).get(canonical_id)
        billing.settle(db, user_id=int(user_id), canonical_content_id=canonical_id,
                       actual_units=plans.units_for_content(cc))
    except Exception as e:
        logger.warning("unit settlement failed for canonical %s: %s", canonical_id, e)


def _sync_bookmark_states(db, canonical_id: int) -> None:
    """Mirror canonical processing state onto every user save that points at it.

    Lets the client show Saving/Processing/Ready without joining, and keeps the
    existing bookmark payload shape intact.
    """
    cc = db.query(CanonicalContent).get(canonical_id)
    if cc is None:
        return
    (db.query(Bookmark)
       .filter(Bookmark.canonical_content_id == canonical_id)
       .update({Bookmark.processing_state: cc.processing_state},
               synchronize_session=False))
    db.commit()


@handler("collection.cover")
def handle_collection_cover(payload, db):
    """Choose a Collection's cover.

    A background job on purpose. Selection makes external image-search requests
    and one model call, which is exactly the kind of work that must never
    happen while somebody is waiting for a screen to draw. Reading collections
    performs neither; this runs after a rebuild and writes the result.

    Idempotent: `select_cover` re-checks `needs_reselection` itself, so a
    duplicate or replayed job is a no-op rather than a second search.
    """
    from ..models import Collection
    from ..services import collection_covers as cover_svc

    collection_id = int(payload.get("collection_id") or 0)
    coll = db.query(Collection).filter(Collection.id == collection_id).first()
    if coll is None:
        return {"status": "gone"}
    return cover_svc.select_cover(db, coll, user_id=coll.user_id,
                                  force=bool(payload.get("force")))
