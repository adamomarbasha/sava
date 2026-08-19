"""Intelligence endpoints.

Mounted alongside the existing routes. Nothing here replaces or changes an
existing endpoint's behaviour — the iOS capture flow, `POST /bookmarks`,
`GET /api/bookmarks`, transcripts and comments all keep their current contract.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .ai import telemetry
from .ai.base import Mode
from .ai.router import describe_modes, get_router
from .auth import get_current_user
from .db import get_db
from .jobs import enqueue, queue_stats
from .models import (
    Bookmark, CanonicalContent, ChatMessage, ChatThread, Collection,
    CollectionItem, ContentTranscript, ContentUnderstanding,
)
from .services import collections as coll_svc
from .services import intelligence, retrieval

logger = logging.getLogger(__name__)
router = APIRouter(tags=["intelligence"])


def _mode(value: Optional[str]) -> Mode:
    try:
        return Mode((value or "auto").lower())
    except ValueError:
        return Mode.AUTO


def _owned_bookmark(db: Session, bookmark_id: int, user_id: int) -> Bookmark:
    bm = (db.query(Bookmark)
          .filter(Bookmark.id == bookmark_id, Bookmark.user_id == user_id).first())
    if bm is None:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    return bm


# ─── Model picker (provider-neutral) ─────────────────────────────────────────

@router.get("/api/ai/modes")
def ai_modes():
    """Copy for the Ask Sava picker. Never exposes a vendor or model name."""
    return {"modes": describe_modes(), "available": get_router().is_available()}


# ─── Processing status ───────────────────────────────────────────────────────

@router.get("/api/bookmarks/{bookmark_id}/status")
def processing_status(bookmark_id: int,
                      current_user: dict = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    bm = _owned_bookmark(db, bookmark_id, current_user["id"])
    if not bm.canonical_content_id:
        return {"bookmark_id": bm.id, "state": bm.processing_state or "queued",
                "level": 0, "stages": {}, "linked": False}
    cc = db.query(CanonicalContent).get(bm.canonical_content_id)
    import json as _json
    return {
        "bookmark_id": bm.id, "canonical_id": cc.id, "linked": True,
        "state": cc.processing_state, "level": cc.processing_level,
        "content_type": cc.content_type,
        "stages": _json.loads(cc.stage_status or "{}"),
        "error": cc.last_error,
        "has_transcript": db.query(ContentTranscript).filter(
            ContentTranscript.canonical_content_id == cc.id).count() > 0,
        "has_understanding": db.query(ContentUnderstanding).filter(
            ContentUnderstanding.canonical_content_id == cc.id).first() is not None,
    }


@router.post("/api/bookmarks/{bookmark_id}/reprocess")
def reprocess(bookmark_id: int, force: bool = Query(False),
              current_user: dict = Depends(get_current_user),
              db: Session = Depends(get_db)):
    bm = _owned_bookmark(db, bookmark_id, current_user["id"])
    if not bm.canonical_content_id:
        from .pipeline.ingest import resolve_or_create_canonical
        cc, _ = resolve_or_create_canonical(db, bm.url, bm.platform)
        if cc is None:
            raise HTTPException(status_code=422, detail="Cannot resolve this URL")
        bm.canonical_content_id = cc.id
        db.commit()
    job = enqueue(db, "content.process",
                  {"canonical_id": bm.canonical_content_id,
                   "user_id": current_user["id"], "force": force},
                  idempotency_key=f"content.process:{bm.canonical_content_id}:{int(force)}",
                  force=force, priority=50)
    return {"queued": True, "job_id": job.id if job else None,
            "canonical_id": bm.canonical_content_id}


# ─── Search (no generative model) ────────────────────────────────────────────

@router.get("/api/search")
def search(q: Optional[str] = Query(None),
           platform: Optional[str] = Query(None),
           content_type: Optional[str] = Query(None),
           limit: int = Query(30, ge=1, le=100),
           current_user: dict = Depends(get_current_user),
           db: Session = Depends(get_db)):
    """Fast hybrid retrieval: vectors + keyword + structured filters."""
    with telemetry.timed() as t:
        results = retrieval.search_library(
            db, current_user["id"], q or "", limit=limit,
            platform=platform, content_type=content_type,
        )
    telemetry.record(db, operation="search", user_id=current_user["id"], wall_ms=t.ms)
    return {"query": q, "count": len(results),
            "results": [r.to_dict() for r in results],
            "semantic": get_router().is_available(), "took_ms": t.ms}


# ─── AI Summary ──────────────────────────────────────────────────────────────

@router.get("/api/bookmarks/{bookmark_id}/summary")
def summary(bookmark_id: int, refresh: bool = Query(False),
            mode: Optional[str] = Query(None),
            current_user: dict = Depends(get_current_user),
            db: Session = Depends(get_db)):
    bm = _owned_bookmark(db, bookmark_id, current_user["id"])
    return intelligence.get_or_create_summary(
        db, bm, user_id=current_user["id"], force=refresh, mode=_mode(mode))


# ─── Ask This ────────────────────────────────────────────────────────────────

class AskIn(BaseModel):
    question: str
    mode: Optional[str] = "auto"
    thread_id: Optional[int] = None


@router.post("/api/bookmarks/{bookmark_id}/ask")
def ask_this(bookmark_id: int, body: AskIn,
             current_user: dict = Depends(get_current_user),
             db: Session = Depends(get_db)):
    bm = _owned_bookmark(db, bookmark_id, current_user["id"])
    if not (body.question or "").strip():
        raise HTTPException(status_code=422, detail="A question is required")

    thread, history = _thread_and_history(
        db, current_user["id"], body.thread_id, scope="save",
        bookmark_id=bm.id, title=body.question[:60])

    result = intelligence.ask_this(db, bm, body.question,
                                   user_id=current_user["id"],
                                   mode=_mode(body.mode), history=history)
    if not result.get("ok"):
        return {**result, "thread_id": thread.id}

    _persist_turn(db, thread.id, body.question, result, _mode(body.mode))
    return {**result, "thread_id": thread.id}


# ─── Ask Sava ────────────────────────────────────────────────────────────────

class AskSavaIn(BaseModel):
    question: str
    mode: Optional[str] = "auto"
    thread_id: Optional[int] = None


@router.post("/api/ask")
def ask_sava(body: AskSavaIn,
             current_user: dict = Depends(get_current_user),
             db: Session = Depends(get_db)):
    if not (body.question or "").strip():
        raise HTTPException(status_code=422, detail="A question is required")

    thread, history = _thread_and_history(
        db, current_user["id"], body.thread_id, scope="library",
        bookmark_id=None, title=body.question[:60])

    result = intelligence.ask_sava(db, current_user["id"], body.question,
                                   mode=_mode(body.mode), history=history)
    if not result.get("ok"):
        return {**result, "thread_id": thread.id}

    _persist_turn(db, thread.id, body.question, result, _mode(body.mode))
    return {**result, "thread_id": thread.id}


def _thread_and_history(db, user_id: int, thread_id: Optional[int], *, scope: str,
                        bookmark_id: Optional[int], title: str):
    if thread_id:
        thread = (db.query(ChatThread)
                  .filter(ChatThread.id == thread_id, ChatThread.user_id == user_id)
                  .first())
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")
    else:
        thread = ChatThread(user_id=user_id, bookmark_id=bookmark_id,
                            scope=scope, title=title)
        db.add(thread)
        db.commit()
        db.refresh(thread)
    history = [{"role": m.role, "content": m.content}
               for m in db.query(ChatMessage)
               .filter(ChatMessage.thread_id == thread.id)
               .order_by(ChatMessage.created_at).all()]
    return thread, history


def _persist_turn(db, thread_id: int, question: str, result: Dict[str, Any], mode: Mode):
    import json as _json
    db.add(ChatMessage(thread_id=thread_id, role="user", content=question,
                       mode=mode.value))
    db.add(ChatMessage(
        thread_id=thread_id, role="assistant", content=result.get("answer", ""),
        citations=_json.dumps(result.get("citations") or result.get("sources") or [],
                              default=str),
        mode=mode.value))
    db.commit()


@router.get("/api/threads")
def list_threads(scope: Optional[str] = Query(None),
                 bookmark_id: Optional[int] = Query(None),
                 current_user: dict = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    q = db.query(ChatThread).filter(ChatThread.user_id == current_user["id"])
    if scope:
        q = q.filter(ChatThread.scope == scope)
    if bookmark_id:
        q = q.filter(ChatThread.bookmark_id == bookmark_id)
    return {"threads": [
        {"id": t.id, "title": t.title, "scope": t.scope,
         "bookmark_id": t.bookmark_id,
         "created_at": t.created_at.isoformat() if t.created_at else None}
        for t in q.order_by(ChatThread.created_at.desc()).limit(50).all()]}


@router.get("/api/threads/{thread_id}/messages")
def thread_messages(thread_id: int,
                    current_user: dict = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    import json as _json
    thread = (db.query(ChatThread)
              .filter(ChatThread.id == thread_id,
                      ChatThread.user_id == current_user["id"]).first())
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    msgs = (db.query(ChatMessage).filter(ChatMessage.thread_id == thread_id)
            .order_by(ChatMessage.created_at).all())
    return {"thread_id": thread_id, "messages": [
        {"role": m.role, "content": m.content, "mode": m.mode,
         "citations": _json.loads(m.citations or "[]"),
         "created_at": m.created_at.isoformat() if m.created_at else None}
        for m in msgs]}


# ─── Related & resurfacing (no model) ────────────────────────────────────────

@router.get("/api/bookmarks/{bookmark_id}/related")
def related(bookmark_id: int, limit: int = Query(8, ge=1, le=30),
            current_user: dict = Depends(get_current_user),
            db: Session = Depends(get_db)):
    bm = _owned_bookmark(db, bookmark_id, current_user["id"])
    if not bm.canonical_content_id:
        return {"results": [], "reason": "not_processed"}
    items = retrieval.related_saves(db, current_user["id"], bm.canonical_content_id,
                                    limit=limit)
    return {"count": len(items), "results": [i.to_dict() for i in items]}


@router.get("/api/resurface")
def resurface(limit: int = Query(6, ge=1, le=30),
              current_user: dict = Depends(get_current_user),
              db: Session = Depends(get_db)):
    return {"results": intelligence.worth_revisiting(db, current_user["id"], limit=limit)}


# ─── Collections ─────────────────────────────────────────────────────────────

class CollectionIn(BaseModel):
    name: str
    description: Optional[str] = None
    auto_populate: bool = True


@router.get("/api/collections")
def get_collections(current_user: dict = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    return {"collections": coll_svc.list_collections(db, current_user["id"])}


@router.post("/api/collections")
def post_collection(body: CollectionIn,
                    current_user: dict = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    try:
        coll = coll_svc.create_collection(db, current_user["id"], body.name,
                                          description=body.description)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    suggestions = coll_svc.suggest_for_collection(db, coll.id, auto_add=False)
    return {"id": coll.id, "name": coll.name, "kind": coll.kind,
            "suggestions": suggestions}


@router.get("/api/collections/{collection_id}")
def get_collection(collection_id: int,
                   current_user: dict = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    coll = (db.query(Collection)
            .filter(Collection.id == collection_id,
                    Collection.user_id == current_user["id"]).first())
    if coll is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    rows = (db.query(Bookmark, CanonicalContent, CollectionItem)
            .join(CollectionItem, CollectionItem.bookmark_id == Bookmark.id)
            .outerjoin(CanonicalContent, CanonicalContent.id == Bookmark.canonical_content_id)
            .filter(CollectionItem.collection_id == collection_id).all())
    return {
        "id": coll.id, "name": coll.name, "kind": coll.kind,
        "description": coll.description,
        "items": [{
            "id": bm.id, "title": (cc.title if cc else None) or bm.title,
            "author": (cc.creator_name if cc else None) or bm.author,
            "platform": (cc.platform if cc else None) or bm.platform,
            "url": bm.url, "note": bm.note,
            "thumbnail_url": (cc.thumbnail_url if cc else None) or bm.thumbnail_url,
            "added_by": ci.added_by, "score": ci.score,
        } for bm, cc, ci in rows],
    }


class ItemsIn(BaseModel):
    bookmark_ids: List[int]


@router.post("/api/collections/{collection_id}/items")
def post_items(collection_id: int, body: ItemsIn,
               current_user: dict = Depends(get_current_user),
               db: Session = Depends(get_db)):
    coll = (db.query(Collection)
            .filter(Collection.id == collection_id,
                    Collection.user_id == current_user["id"]).first())
    if coll is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    n = coll_svc.add_items(db, collection_id, body.bookmark_ids)
    return {"added": n}


@router.get("/api/collections/{collection_id}/suggestions")
def collection_suggestions(collection_id: int, limit: int = Query(25, ge=1, le=100),
                           current_user: dict = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    coll = (db.query(Collection)
            .filter(Collection.id == collection_id,
                    Collection.user_id == current_user["id"]).first())
    if coll is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return {"suggestions": coll_svc.suggest_for_collection(db, collection_id, limit=limit)}


@router.post("/api/collections/rebuild")
def rebuild_collections(background: bool = Query(True),
                        current_user: dict = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Rebuild automatic collections from this user's own save patterns."""
    if background:
        job = enqueue(db, "collections.recluster", {"user_id": current_user["id"]},
                      idempotency_key=f"collections.recluster:{current_user['id']}",
                      force=True, priority=200)
        return {"queued": True, "job_id": job.id if job else None}
    return coll_svc.rebuild_auto_collections(db, current_user["id"])


# ─── Ops / cost telemetry ────────────────────────────────────────────────────

@router.get("/api/ops/usage")
def usage(days: int = Query(30, ge=1, le=365), mine: bool = Query(True),
          current_user: dict = Depends(get_current_user),
          db: Session = Depends(get_db)):
    return telemetry.summarize(db, user_id=current_user["id"] if mine else None,
                               days=days)


@router.get("/api/ops/queue")
def queue(current_user: dict = Depends(get_current_user),
          db: Session = Depends(get_db)):
    return telemetry.queue_health(db)


@router.get("/api/ops/platforms")
def platforms(days: int = Query(1, ge=1, le=30),
              current_user: dict = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """Per-platform request health, throttle state, and circuit status."""
    return telemetry.platform_health(db, days=days)


@router.get("/api/ops/economics")
def economics(days: int = Query(30, ge=1, le=365),
              current_user: dict = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """User saves vs unique content processed — the ratio that decides margin."""
    return telemetry.dedup_economics(db, days=days)


@router.get("/api/ops/latency")
def latency(days: int = Query(7, ge=1, le=90),
            current_user: dict = Depends(get_current_user),
            db: Session = Depends(get_db)):
    return telemetry.processing_latency(db, days=days)


@router.post("/api/ops/upgrade-pipeline")
def upgrade_pipeline(limit: int = Query(50, ge=1, le=500),
                     target_version: Optional[int] = Query(None),
                     dry_run: bool = Query(True),
                     current_user: dict = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Controlled, batched reprocessing of content stuck on an older pipeline.

    Deliberately opt-in and rate-limited rather than an automatic sweep: a
    global reprocess of the whole library would be the single most expensive
    action Sava can take. Call repeatedly with a small `limit` to drain
    gradually while watching cost.
    """
    from .content.upgrade import plan_upgrade, queue_upgrade

    plan = plan_upgrade(db, limit=limit, target_version=target_version)
    if dry_run:
        return {"dry_run": True, **plan}
    return {"dry_run": False, **queue_upgrade(db, plan, user_id=current_user["id"])}
