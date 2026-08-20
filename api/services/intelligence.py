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
from ..ai.base import Mode, ProviderError, TaskType
from ..ai.router import get_router, resolve_task
from ..config import LAZY_SUMMARY_OVER_SECONDS
from ..models import (
    Bookmark, CanonicalContent, ContentTranscript, ContentUnderstanding,
)
from . import retrieval

logger = logging.getLogger(__name__)


def _busy_message(error: Exception) -> str:
    """Turn a provider failure into something worth reading.

    A rate limit is not a crash, and it is not "Sava is having a moment" either
    — it is a specific, temporary, recoverable condition. Letting `ProviderError`
    escape gave the client a 500 for it, which the app reports as a server fault
    and which tells the user nothing about waiting a minute and trying again.
    """
    text = str(error).lower()
    if "429" in text or "quota" in text or "rate limit" in text:
        return "Sava is at its limit for the moment. Try again in a minute."
    return "Sava couldn't finish that. Try again shortly."


def _clean_text(value: Optional[str]) -> str:
    """Strip stray control characters out of generated text.

    A model very occasionally emits a raw C0 control character mid-sentence. It
    survives into the JSON body and then fails strict decoders — including
    Swift's — so a good answer surfaces in the app as "something went wrong".
    Newlines and tabs are meaningful here; nothing else in that range is.
    """
    if not value:
        return ""
    return "".join(ch for ch in value if ch >= " " or ch in "\n\t")


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
    try:
        record, completion = u_mod.extract(
            router=router, content_type=cc.content_type or "other",
            title=cc.title, creator=cc.creator_name, caption=cc.description,
            description=None, transcript=(tr.text if tr else None),
            visual_text=visual_text, mode=mode,
            long_form=duration > LAZY_SUMMARY_OVER_SECONDS,
        )
    except ProviderError as e:
        logger.warning("summary provider failure: %s", e)
        return {"available": False, "reason": "provider_error",
                "message": _busy_message(e),
                "processing_state": cc.processing_state}
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

_ASK_THIS_SYSTEM = """You are Sava. You are talking with someone about a piece
of media from their library. You have already watched or read it — everything
below is simply what you know about it.

The media is your starting context. It is NOT the limit of what you may say.

How to behave:
- Talk like a knowledgeable friend who happens to have seen this. Normal
  conversational English. Vary your sentence length. No preamble.
- If they ask something the media does not cover — how a game works, whether
  something is common, what an ingredient does, how it compares to something
  else — just answer it from what you know, the way any capable assistant would.
  Explain, reason, compare, brainstorm, follow up.
- Never describe your own plumbing. Do not write "this save", "the provided
  context", "based on the material", "the content states", "I can only answer
  from this video", or any variant. The person did not ask about your sources.
- Be accurate about the media itself. Only assert that something happens in it
  when the context supports that. When you are drawing on general knowledge
  instead, let it read that way naturally — "games like this usually…" — rather
  than presenting it as something the media said.
- Point at a moment with an inline timestamp, like [0:10], only when it genuinely
  helps. Never append a list of timestamps.
- Match the length to the question. One sentence is a perfectly good answer.
- Do not restate the question. Do not add a closing summary paragraph. Do not
  add headings unless the answer really is a list of separate things."""


def ask_this(db, bookmark: Bookmark, question: str, *, user_id: int,
             mode: Mode = Mode.AUTO, history: Optional[List[Dict[str, str]]] = None
             ) -> Dict[str, Any]:
    """Conversation about one item, with the item as context.

    Deliberately *not* a strict RAG gate. An item Sava has not finished reading
    used to make this endpoint refuse outright, which meant a perfectly ordinary
    question — "is this a common game?" — got "this save is still being
    processed" instead of an answer. The retrieved passages are context that
    makes the assistant better informed about this particular item; their absence
    is a reason to lean on general knowledge, not a reason to stop talking.
    """
    router = get_router()
    if not router.is_available():
        return {"ok": False, "reason": "ai_unavailable",
                "message": "AI is not configured on this server."}

    cc = (db.query(CanonicalContent).get(bookmark.canonical_content_id)
          if bookmark.canonical_content_id else None)

    chunks: List[Dict[str, Any]] = []
    und = None
    if cc is not None:
        chunks = retrieval.retrieve_chunks(db, cc.id, question, k=6)
        und = (db.query(ContentUnderstanding)
               .filter(ContentUnderstanding.canonical_content_id == cc.id).first())

    title = (cc.title if cc else None) or bookmark.title
    creator = (cc.creator_name if cc else None) or bookmark.author

    ctx: List[str] = []
    if title:
        ctx.append(f"TITLE: {title}")
    if creator:
        ctx.append(f"CREATOR: {creator}")
    ctx.append(f"PLATFORM: {bookmark.platform}")
    if cc is not None and cc.content_type:
        ctx.append(f"CONTENT TYPE: {cc.content_type}")

    if und is not None:
        payload = understanding_payload(cc, und)
        if payload.get("tl_dr"):
            ctx.append(f"WHAT IT IS: {payload['tl_dr']}")
        if payload.get("key_points"):
            ctx.append("NOTABLE: " + " | ".join(payload["key_points"][:6]))
        if payload.get("typed_data"):
            ctx.append(f"EXTRACTED DETAIL: {json.dumps(payload['typed_data'])[:2500]}")
        if payload.get("entities"):
            ctx.append(f"MENTIONED: {json.dumps(payload['entities'])[:1200]}")

    if chunks:
        rendered = []
        for c in chunks:
            label = _timestamp(c.get("start_s"))
            tag = "on-screen" if c.get("modality") == "vision" else "spoken"
            rendered.append(f"[{label}] ({tag}) {c['text']}")
        ctx.append("FROM THE MEDIA ITSELF:\n" + "\n\n".join(rendered))
    elif und is None:
        # Say so plainly *to the model*, so it knows to answer from general
        # knowledge and to avoid asserting specifics about this item.
        ctx.append(
            "NOTE: you have not been able to watch this one in detail yet, so you "
            "know little beyond the title and creator above. Answer helpfully from "
            "general knowledge, and do not claim specifics about what happens in it."
        )

    task = resolve_task(TaskType.ASK_THIS_SIMPLE, question=question,
                        source_count=1, mode=mode)
    try:
        completion = router.complete(
            task, system=_ASK_THIS_SYSTEM,
            prompt="\n".join(ctx) + f"\n\nQUESTION: {question}",
            mode=mode, temperature=0.5, history=history, max_output_tokens=2048,
        )
    except ProviderError as e:
        logger.warning("ask_this provider failure: %s", e)
        return {"ok": False, "reason": "provider_error", "message": _busy_message(e)}
    telemetry.record_completion(db, completion, operation=f"ask_this.{task.value}",
                                user_id=user_id,
                                canonical_content_id=cc.id if cc else None,
                                bookmark_id=bookmark.id, platform=bookmark.platform)

    return {
        "ok": True,
        "answer": _clean_text(completion.text),
        "mode": mode.value,
        "citations": [{
            "start_s": c.get("start_s"), "end_s": c.get("end_s"),
            "timestamp": _timestamp(c.get("start_s")),
            "source": c.get("modality"), "text": (c.get("text") or "")[:200],
        } for c in chunks[:4]],
        "grounded_in": len(chunks),
    }


# ─── Ask Sava ────────────────────────────────────────────────────────────────

_ASK_SAVA_SYSTEM = """You are Sava. You are talking with someone about their own
library — the things they have collected from across the internet. Below is a
numbered list of the items relevant to what they just asked, with excerpts.

How to behave:
- Talk like a knowledgeable friend who knows their library well. Conversational
  English, varied rhythm, no preamble, no throat-clearing.
- Reference an item by its number, like [2], when you talk about it, so the app
  can show it. Only use numbers that appear in the list.
- Never invent an item, a title or a creator, and never claim something is in
  their library when it is not.
- Their library is your starting context, not your limit. Once you have covered
  what they actually have, you can reason, compare, recommend, and answer from
  general knowledge like any capable assistant. Just keep the two clearly
  distinguishable in tone.
- Follow-ups are normal conversation. "Which one looks best for a date?" refers
  to what you were both just discussing — answer it directly instead of starting
  over or asking them to rephrase.
- Let the question decide the shape of the answer. Sometimes one line. Sometimes
  a paragraph. Sometimes a short comparison. Use bullets only when the answer
  really is a list. Do not reach for the same structure every time.
- Never write "based on your saved library", "here are the key takeaways", "in
  your collection I found", or any other stock opener. Just answer.
- Do not restate the question. Do not add a closing summary paragraph."""


def ask_sava(db, user_id: int, question: str, *, mode: Mode = Mode.AUTO,
             history: Optional[List[Dict[str, str]]] = None,
             max_saves: int = 10,
             collection_id: Optional[int] = None,
             carry_over_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """A conversation about someone's library, grounded in retrieval.

    With `collection_id`, retrieval is confined to that collection's members, so
    "Ask this collection" genuinely answers from the collection rather than
    silently widening to everything they have.

    `carry_over_ids` are the items the previous turn talked about. A follow-up
    like "which one looks best for a date?" contains almost no retrievable words,
    so searching on it alone returns nothing and the assistant answers "I
    couldn't find anything" about a conversation it was mid-way through. Keeping
    the previous turn's items in scope is what makes follow-ups behave like
    conversation instead of like a fresh search box.
    """
    router = get_router()
    if not router.is_available():
        return {"ok": False, "reason": "ai_unavailable",
                "message": "AI is not configured on this server."}

    restrict_to = None
    scope_label = "your library"
    if collection_id is not None:
        from sqlalchemy import text as _sql
        rows = db.execute(_sql("""
            SELECT DISTINCT b.canonical_content_id FROM collection_items ci
            JOIN bookmarks b ON b.id = ci.bookmark_id
            WHERE ci.collection_id = :c AND b.user_id = :u
              AND b.canonical_content_id IS NOT NULL
        """), {"c": collection_id, "u": user_id}).all()
        restrict_to = {r[0] for r in rows}
        scope_label = "this collection"
        if not restrict_to:
            return {"ok": True, "sources": [], "grounded_in": 0, "mode": mode.value,
                    "answer": "Nothing in this collection has been processed yet, "
                              "so there is nothing for me to read."}

    # A short follow-up carries its meaning in the conversation, not in its own
    # words, so the retrieval query borrows the previous question's vocabulary.
    query = question
    if history:
        previous = [m["content"] for m in history if m.get("role") == "user"]
        if previous and len(question.split()) <= 12:
            query = f"{previous[-1]} {question}"

    retrieved = retrieval.retrieve_for_library_question(
        db, user_id, query, max_saves=max_saves, restrict_to=restrict_to)
    blocks = retrieved["context_blocks"]
    saves = list(retrieved["saves"])

    if carry_over_ids:
        seen = {b["canonical_id"] for b in blocks}
        extra = [cid for cid in carry_over_ids if cid not in seen]
        if extra:
            carried = retrieval._load_saves(db, user_id, extra)
            for cid in extra:
                save = carried.get(cid)
                if save is None:
                    continue
                saves.append(save)
                blocks.append({
                    "bookmark_id": save.bookmark_id, "canonical_id": save.canonical_id,
                    "title": save.title, "creator": save.creator,
                    "platform": save.platform, "content_type": save.content_type,
                    "note": save.note, "tl_dr": save.tl_dr, "topics": save.topics,
                    "excerpts": [],
                })

    if not blocks:
        # Still worth answering: they may be asking something general, and
        # refusing outright is the RAG-bot behaviour this is meant to avoid.
        try:
            completion = router.complete(
                resolve_task(TaskType.ASK_SAVA, question=question,
                             source_count=0, mode=mode),
                system=_ASK_SAVA_SYSTEM,
            prompt=(f"Nothing in {scope_label} matches this question, so answer it "
                    f"from general knowledge and say briefly that you did not find "
                    f"anything of theirs on it.\n\nQUESTION: {question}"),
                mode=mode, temperature=0.5, history=history, max_output_tokens=1024,
            )
        except ProviderError as e:
            logger.warning("ask_sava provider failure: %s", e)
            return {"ok": False, "reason": "provider_error", "message": _busy_message(e)}
        telemetry.record_completion(db, completion, operation="ask_sava.no_match",
                                    user_id=user_id)
        return {"ok": True, "answer": _clean_text(completion.text), "sources": [],
                "grounded_in": 0, "mode": mode.value}

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
    try:
        completion = router.complete(
            task, system=_ASK_SAVA_SYSTEM,
            prompt="FROM THEIR LIBRARY:\n\n" + "\n\n".join(ctx)
                   + f"\n\nQUESTION: {question}",
            mode=mode, temperature=0.5, history=history, max_output_tokens=3072,
        )
    except ProviderError as e:
        logger.warning("ask_sava provider failure: %s", e)
        return {"ok": False, "reason": "provider_error", "message": _busy_message(e)}
    telemetry.record_completion(db, completion, operation=f"ask_sava.{task.value}",
                                user_id=user_id)

    return {
        "ok": True,
        "answer": _clean_text(completion.text),
        "mode": mode.value,
        "sources": [s.to_dict() for s in saves],
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
