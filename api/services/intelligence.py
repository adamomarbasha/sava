"""Summary, Ask This, and Ask Sava.

All three read persisted understanding and persisted chunks. None of them
touches the network, downloads media, or re-transcribes anything — that work
happened once during ingestion and is cached against canonical content, shared
across every user who saved it.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ..ai import telemetry
from ..ai.base import Mode, TaskType
from ..ai.router import get_router, resolve_task
from ..config import LAZY_SUMMARY_OVER_SECONDS
from ..models import (
    Bookmark, CanonicalContent, ContentTranscript, ContentUnderstanding,
)
from . import retrieval

logger = logging.getLogger(__name__)


def _parse(value: str, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def understanding_payload(cc: CanonicalContent, und: Optional[ContentUnderstanding]
                          ) -> Dict[str, Any]:
    if und is None:
        return {}
    return {
        "content_type": und.content_type or cc.content_type,
        "tl_dr": und.tl_dr,
        "key_points": _parse(und.key_points, []),
        "topics": _parse(und.topics, []),
        "entities": _parse(und.entities, {}),
        "typed_data": _parse(und.typed_data, {}),
        "chapters": _parse(und.chapters, []),
        "sources_used": _parse(und.sources_used, []),
        "schema_version": und.schema_version,
        "model": und.model,
        "created_at": und.created_at.isoformat() if und.created_at else None,
    }


# ─── AI Summary ──────────────────────────────────────────────────────────────

def get_or_create_summary(db, bookmark: Bookmark, *, user_id: int,
                          force: bool = False, mode: Mode = Mode.AUTO) -> Dict[str, Any]:
    """Return the cached structured understanding, generating it once if absent.

    Long-form content defers generation to this point on purpose: most long
    saves are never reopened, and generating for all of them at ingest is the
    single largest avoidable inference cost.
    """
    if not bookmark.canonical_content_id:
        return {"available": False, "reason": "not_linked",
                "message": "This save has not been processed yet."}

    cc = db.query(CanonicalContent).get(bookmark.canonical_content_id)
    if cc is None:
        return {"available": False, "reason": "not_linked"}

    und = (db.query(ContentUnderstanding)
           .filter(ContentUnderstanding.canonical_content_id == cc.id).first())
    if und is not None and not force:
        telemetry.record(db, operation="summary.read", user_id=user_id,
                         canonical_content_id=cc.id, bookmark_id=bookmark.id,
                         platform=cc.platform, cache_hit=True)
        return {"available": True, "cached": True,
                "processing_state": cc.processing_state, **understanding_payload(cc, und)}

    router = get_router()
    if not router.is_available():
        return {"available": False, "reason": "ai_unavailable",
                "message": "AI is not configured on this server."}

    tr = (db.query(ContentTranscript)
          .filter(ContentTranscript.canonical_content_id == cc.id).first())
    visual_text = _visual_text(db, cc.id)

    if not (tr or visual_text or cc.description or cc.title):
        return {"available": False, "reason": "no_content",
                "message": "Sava could not read anything from this item yet.",
                "processing_state": cc.processing_state}

    from ..pipeline import understanding as u_mod

    duration = float(cc.duration_seconds or 0)
    record, completion = u_mod.extract(
        router=router, content_type=cc.content_type or "other",
        title=cc.title, creator=cc.creator_name, caption=cc.description,
        description=None, transcript=(tr.text if tr else None),
        visual_text=visual_text, mode=mode,
        long_form=duration > LAZY_SUMMARY_OVER_SECONDS,
    )
    if completion is not None:
        telemetry.record_completion(db, completion, operation="summary.generate",
                                    user_id=user_id, canonical_content_id=cc.id,
                                    bookmark_id=bookmark.id, platform=cc.platform)
    if not record:
        return {"available": False, "reason": "generation_failed",
                "message": "Sava could not summarise this item."}

    from ..pipeline.ingest import _upsert_understanding, build_embeddings
    _upsert_understanding(db, cc.id, record)
    try:
        build_embeddings(db, cc.id, user_id=user_id, force=True, visual_text=visual_text)
    except Exception as e:
        logger.warning("re-embed after summary failed: %s", e)

    und = (db.query(ContentUnderstanding)
           .filter(ContentUnderstanding.canonical_content_id == cc.id).first())
    return {"available": True, "cached": False,
            "processing_state": cc.processing_state, **understanding_payload(cc, und)}


def _visual_text(db, canonical_id: int) -> str:
    from ..models import ContentFrame
    rows = (db.query(ContentFrame)
            .filter(ContentFrame.canonical_content_id == canonical_id)
            .order_by(ContentFrame.ts_ms).all())
    parts = []
    for r in rows:
        ts = f"{r.ts_ms//60000}:{(r.ts_ms//1000)%60:02d}"
        if r.ocr_text:
            parts.append(f"[{ts}] on-screen: {r.ocr_text}")
        if r.vision_caption:
            parts.append(f"[{ts}] {r.vision_caption}")
    return "\n".join(parts)


# ─── Ask This ────────────────────────────────────────────────────────────────

_ASK_THIS_SYSTEM = """You answer questions about ONE saved video or post, using
only the context provided.

Rules:
- Ground every claim in the supplied excerpts. If the answer is not there, say
  so plainly — "This save doesn't cover that" — and, if useful, say what it
  does cover. Never guess.
- Cite moments by their timestamp when one is available.
- On-screen text excerpts are labelled; treat them as reliable as speech.
- Be concise. Two or three sentences unless the question needs more."""


def ask_this(db, bookmark: Bookmark, question: str, *, user_id: int,
             mode: Mode = Mode.AUTO, history: Optional[List[Dict[str, str]]] = None
             ) -> Dict[str, Any]:
    """Grounded RAG over one save. Reads persisted chunks only."""
    if not bookmark.canonical_content_id:
        return {"ok": False, "reason": "not_processed",
                "message": "This save is still being processed."}

    cc = db.query(CanonicalContent).get(bookmark.canonical_content_id)
    router = get_router()
    if not router.is_available():
        return {"ok": False, "reason": "ai_unavailable",
                "message": "AI is not configured on this server."}

    chunks = retrieval.retrieve_chunks(db, cc.id, question, k=6)
    und = (db.query(ContentUnderstanding)
           .filter(ContentUnderstanding.canonical_content_id == cc.id).first())

    if not chunks and und is None:
        return {"ok": False, "reason": "no_content",
                "message": "Sava hasn't finished reading this save yet.",
                "processing_state": cc.processing_state}

    ctx: List[str] = [f"TITLE: {cc.title or ''}", f"CREATOR: {cc.creator_name or ''}"]
    if cc.content_type:
        ctx.append(f"CONTENT TYPE: {cc.content_type}")
    if und:
        payload = understanding_payload(cc, und)
        if payload.get("tl_dr"):
            ctx.append(f"SUMMARY: {payload['tl_dr']}")
        if payload.get("typed_data"):
            ctx.append(f"STRUCTURED DATA: {json.dumps(payload['typed_data'])[:2500]}")
        if payload.get("entities"):
            ctx.append(f"ENTITIES: {json.dumps(payload['entities'])[:1200]}")
    if bookmark.note:
        ctx.append(f"USER'S OWN NOTE: {bookmark.note}")
    if chunks:
        rendered = []
        for c in chunks:
            label = _timestamp(c.get("start_s"))
            tag = "on-screen" if c.get("modality") == "vision" else "transcript"
            rendered.append(f"[{label}] ({tag}) {c['text']}")
        ctx.append("RELEVANT EXCERPTS:\n" + "\n\n".join(rendered))

    task = resolve_task(TaskType.ASK_THIS_SIMPLE, question=question,
                        source_count=1, mode=mode)
    completion = router.complete(
        task, system=_ASK_THIS_SYSTEM,
        prompt="\n".join(ctx) + f"\n\nQUESTION: {question}",
        mode=mode, temperature=0.3, history=history, max_output_tokens=2048,
    )
    telemetry.record_completion(db, completion, operation=f"ask_this.{task.value}",
                                user_id=user_id, canonical_content_id=cc.id,
                                bookmark_id=bookmark.id, platform=cc.platform)

    return {
        "ok": True,
        "answer": completion.text,
        "mode": mode.value,
        "citations": [{
            "start_s": c.get("start_s"), "end_s": c.get("end_s"),
            "timestamp": _timestamp(c.get("start_s")),
            "source": c.get("modality"), "text": (c.get("text") or "")[:200],
        } for c in chunks[:4]],
        "grounded_in": len(chunks),
    }


# ─── Ask Sava ────────────────────────────────────────────────────────────────

_ASK_SAVA_SYSTEM = """You answer questions across ONE person's saved library.

You are given a numbered list of their actual saves with excerpts. Rules:
- Use only these saves. Never invent a save, a title, or a creator. If their
  library does not contain the answer, say so directly.
- Reference saves by their number, like [2], so the app can link them.
- When the question asks for a plan, comparison, or itinerary, organise the
  answer around the saves that support it.
- If only some of the question can be answered from the library, answer that
  part and say what is missing.
- Be specific and concrete. Prefer names, places, and figures from the saves
  over generic advice."""


def ask_sava(db, user_id: int, question: str, *, mode: Mode = Mode.AUTO,
             history: Optional[List[Dict[str, str]]] = None,
             max_saves: int = 10) -> Dict[str, Any]:
    """Library-wide RAG. Retrieval first, always."""
    router = get_router()
    if not router.is_available():
        return {"ok": False, "reason": "ai_unavailable",
                "message": "AI is not configured on this server."}

    retrieved = retrieval.retrieve_for_library_question(
        db, user_id, question, max_saves=max_saves)
    blocks = retrieved["context_blocks"]

    if not blocks:
        return {"ok": True, "answer": "I couldn't find anything in your library "
                                      "that relates to that yet.",
                "sources": [], "grounded_in": 0, "mode": mode.value}

    ctx: List[str] = []
    for i, b in enumerate(blocks, start=1):
        lines = [f"[{i}] {b['title'] or 'Untitled'} — {b['creator'] or 'unknown'} "
                 f"({b['platform']}{', ' + b['content_type'] if b['content_type'] else ''})"]
        if b.get("note"):
            lines.append(f"    user's note: {b['note']}")
        if b.get("tl_dr"):
            lines.append(f"    summary: {b['tl_dr']}")
        for ex in b.get("excerpts", [])[:2]:
            label = _timestamp(ex.get("start_s"))
            lines.append(f"    [{label}] {(ex.get('text') or '')[:350]}")
        ctx.append("\n".join(lines))

    task = resolve_task(TaskType.ASK_SAVA, question=question,
                        source_count=len(blocks), mode=mode)
    completion = router.complete(
        task, system=_ASK_SAVA_SYSTEM,
        prompt=f"THEIR SAVES:\n\n" + "\n\n".join(ctx) + f"\n\nQUESTION: {question}",
        mode=mode, temperature=0.35, history=history, max_output_tokens=3072,
    )
    telemetry.record_completion(db, completion, operation=f"ask_sava.{task.value}",
                                user_id=user_id)

    return {
        "ok": True,
        "answer": completion.text,
        "mode": mode.value,
        "sources": [s.to_dict() for s in retrieved["saves"]],
        "grounded_in": len(blocks),
    }


def _timestamp(seconds: Optional[int]) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


# ─── Resurfacing ─────────────────────────────────────────────────────────────

def worth_revisiting(db, user_id: int, *, limit: int = 6) -> List[Dict[str, Any]]:
    """Deterministic ranking. No model involved.

    Signals: age (old enough to have been forgotten, not so old it is stale),
    whether the save has ever been opened, and whether it carries a user note
    (a proxy for intent at save time).
    """
    from sqlalchemy import text as sql_text
    rows = db.execute(sql_text("""
        SELECT b.id, b.url, b.note, b.created_at,
               cc.id AS cid, cc.title, cc.creator_name, cc.platform,
               cc.thumbnail_url, cc.content_type, u.tl_dr
        FROM bookmarks b
        LEFT JOIN canonical_content cc ON cc.id = b.canonical_content_id
        LEFT JOIN content_understanding u ON u.canonical_content_id = cc.id
        WHERE b.user_id = :uid
        ORDER BY b.created_at DESC
        LIMIT 400
    """), {"uid": user_id}).mappings().all()

    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    scored = []
    for r in rows:
        created = r["created_at"]
        if isinstance(created, str):
            try:
                created = _dt.datetime.fromisoformat(created)
            except Exception:
                created = None
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=_dt.timezone.utc)
        age_days = (now - created).days

        # Peak around a month old: long enough to be forgotten, recent enough
        # to still matter.
        if age_days < 7:
            age_score = 0.1
        elif age_days <= 60:
            age_score = 1.0 - abs(age_days - 30) / 60.0
        else:
            age_score = max(0.15, 0.6 - (age_days - 60) / 500.0)

        score = age_score
        if r["note"]:
            score += 0.25
        if r["tl_dr"]:
            score += 0.15
        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [{
        "id": r["id"], "canonical_id": r["cid"], "title": r["title"],
        "author": r["creator_name"], "platform": r["platform"], "url": r["url"],
        "thumbnail_url": r["thumbnail_url"], "note": r["note"],
        "tl_dr": r["tl_dr"], "content_type": r["content_type"],
        "reason": "Saved a while ago and still relevant",
        "score": round(s, 3),
    } for s, r in scored[:limit]]
