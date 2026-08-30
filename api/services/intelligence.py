"""Summary, Ask This, and Ask Sava.

All three read persisted understanding and persisted chunks. None of them
touches the network, downloads media, or re-transcribes anything — that work
happened once during ingestion and is cached against canonical content, shared
across every user who saved it.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..ai import telemetry
from ..ai.base import Completion, Mode, ProviderError, TaskType
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


@dataclass
class AskThisPlan:
    """One item's Ask, prepared. See `AskPlan` for why this is extracted."""
    system: str = ""
    prompt: str = ""
    task: Optional[TaskType] = None
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    visual: Any = None
    canonical_id: Optional[int] = None
    operation: str = "ask_this"
    early: Optional[Dict[str, Any]] = None


def _prepare_ask_this(db, bookmark: Bookmark, question: str, *, user_id: int,
                      mode: Mode, history) -> AskThisPlan:
    """Context assembly for one item. No model call, no writes.

    Reuses what Sava already knows — the stored summary, typed data, entities,
    transcript chunks and any cached visual reading — and only escalates to
    looking at the video when `visual_ask.prepare` says the question actually
    needs it. A transcript question never triggers frame work.
    """
    router = get_router()
    if not router.is_available():
        return AskThisPlan(early={
            "ok": False, "reason": "ai_unavailable",
            "message": "AI is not configured on this server."})

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

    # ── Does this question need the picture? ────────────────────────────────
    #
    # Decided before the model runs, deterministically and for free. Three
    # outcomes: the item has visual intelligence and it goes into the context;
    # it does not and a frames job is queued; or it cannot be looked at and we
    # say so. In every case the model is told whether it can see, because the
    # measured failure was it inventing on-screen text — with a timestamp —
    # from spoken words.
    from . import visual_ask
    visual = visual_ask.prepare(db, cc, user_id=user_id, question=question)

    if visual.required and visual.available:
        # Make sure the cached visual reading is actually in the prompt rather
        # than relying on it winning a top-k similarity race against transcript
        # chunks. It is the whole reason the question can be answered.
        seen = {c["text"] for c in chunks}
        cached_visual = _visual_text(db, cc.id) if cc is not None else ""
        if cached_visual and cached_visual not in seen:
            ctx.append("WHAT SAVA SAW ON SCREEN:\n" + cached_visual[:4000])

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

    note = visual_ask.context_note(visual)
    if note:
        ctx.append(note)

    task = resolve_task(TaskType.ASK_THIS_SIMPLE, question=question,
                        source_count=1, mode=mode)
    return AskThisPlan(
        system=_ASK_THIS_SYSTEM,
        prompt="\n".join(ctx) + f"\n\nQUESTION: {question}",
        task=task, chunks=chunks, visual=visual,
        canonical_id=cc.id if cc else None,
        operation=f"ask_this.{task.value}")


def _ask_this_payload(plan: AskThisPlan, answer: str, mode: Mode,
                      timings_ms: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """The response shape, defined once so both paths return the same thing."""
    out = {
        "ok": True,
        "answer": answer,
        "mode": mode.value,
        "citations": [{
            "start_s": c.get("start_s"), "end_s": c.get("end_s"),
            "timestamp": _timestamp(c.get("start_s")),
            "source": c.get("modality"), "text": (c.get("text") or "")[:200],
        } for c in plan.chunks[:4]],
        "grounded_in": len(plan.chunks),
        # Additive: lets the client say "Sava is watching this now" or offer an
        # upgrade. Older clients ignore the extra keys.
        **plan.visual.public(),
    }
    if timings_ms:
        out["timings_ms"] = timings_ms
    return out


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
    plan = _prepare_ask_this(db, bookmark, question, user_id=user_id,
                             mode=mode, history=history)
    if plan.early is not None:
        return plan.early

    router = get_router()
    try:
        completion = router.complete(
            plan.task, system=plan.system, prompt=plan.prompt,
            mode=mode, temperature=0.5, history=history, max_output_tokens=2048)
    except ProviderError as e:
        logger.warning("ask_this provider failure: %s", e)
        return {"ok": False, "reason": "provider_error", "message": _busy_message(e)}

    telemetry.record_completion(db, completion, operation=plan.operation,
                                user_id=user_id,
                                canonical_content_id=plan.canonical_id,
                                bookmark_id=bookmark.id, platform=bookmark.platform)
    return _ask_this_payload(plan, _clean_text(completion.text), mode)


def ask_this_stream(db, bookmark: Bookmark, question: str, *, user_id: int,
                    mode: Mode = Mode.AUTO,
                    history: Optional[List[Dict[str, str]]] = None):
    """One item's Ask, streamed. Same events as `ask_sava_stream`.

    Emits a `status` event before the tokens when the question needed the video
    and Sava has gone to look at it, so the chat can say "Looking through the
    video…" instead of freezing. The answer that follows is still written from
    what is known *now* — a queued frames job does not licence guessing at what
    the frames will show.
    """
    timings = AskTimings()
    plan = _prepare_ask_this(db, bookmark, question, user_id=user_id,
                             mode=mode, history=history)
    timings.mark("context")

    if plan.early is not None:
        yield {"event": "error", "reason": plan.early.get("reason", "unavailable"),
               "message": plan.early.get("message", "Sava could not answer that.")}
        return

    visual = plan.visual
    if getattr(visual, "queued", False):
        yield {"event": "status", "state": "visual_queued",
               "message": "Looking through the video…"}
    elif getattr(visual, "required", False) and getattr(visual, "available", False):
        yield {"event": "status", "state": "visual_cached",
               "message": "Reading what Sava saw in the video…"}

    router = get_router()
    pieces: List[str] = []
    first_token_ms: Optional[int] = None
    started = time.monotonic()
    final = None

    try:
        for chunk in router.complete_stream(
                plan.task, system=plan.system, prompt=plan.prompt,
                mode=mode, temperature=0.5, history=history,
                max_output_tokens=2048):
            if chunk.done:
                final = chunk
                break
            if not chunk.text:
                continue
            if first_token_ms is None:
                first_token_ms = int((time.monotonic() - started) * 1000)
                timings.mark("first_token")
            pieces.append(chunk.text)
            yield {"event": "token", "text": chunk.text}
    except ProviderError as e:
        logger.warning("ask_this_stream provider failure: %s", e)
        yield {"event": "error", "reason": "provider_error",
               "message": _busy_message(e)}
        return
    except Exception as e:                                   # pragma: no cover
        logger.exception("ask_this_stream failed: %s", e)
        yield {"event": "error", "reason": "internal",
               "message": "Sava couldn't finish that answer."}
        return

    timings.mark("model")
    answer = _clean_text("".join(pieces))

    if final is not None:
        completion = Completion(
            text=answer, provider="gemini",
            model=getattr(final, "_model", "") or "",
            input_tokens=final.input_tokens, output_tokens=final.output_tokens,
            wall_ms=timings.total_ms)
        setattr(completion, "_usd", getattr(final, "_usd", 0.0))
        telemetry.record_completion(db, completion, operation=plan.operation,
                                    user_id=user_id,
                                    canonical_content_id=plan.canonical_id,
                                    bookmark_id=bookmark.id,
                                    platform=bookmark.platform)

    payload = timings.as_dict()
    if first_token_ms is not None:
        payload["first_token"] = first_token_ms
    logger.info("ask_this_stream user=%s bookmark=%s timings=%s",
                user_id, bookmark.id, payload)

    done = _ask_this_payload(plan, answer, mode, timings_ms=payload)
    yield {"event": "done", **done}


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


class AskTimings:
    """Wall-clock per phase of one Ask, so latency can be argued about.

    Ask was reported as "extremely slow and frequently times out" with no way
    to say *which* part was slow — retrieval, the embedding call, the model, or
    the database. Guessing at that is how a timeout gets raised instead of a
    bottleneck getting fixed, which is explicitly not the goal.

    The numbers ride back on the response under `timings_ms` and are logged at
    INFO, so a slow Ask in production leaves a breakdown behind rather than a
    single duration.
    """

    __slots__ = ("_started", "_marks", "_last")

    def __init__(self) -> None:
        now = time.monotonic()
        self._started = now
        self._last = now
        self._marks: Dict[str, int] = {}

    def mark(self, phase: str) -> None:
        now = time.monotonic()
        self._marks[phase] = int((now - self._last) * 1000)
        self._last = now

    @property
    def total_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    def as_dict(self) -> Dict[str, int]:
        return {**self._marks, "total": self.total_ms}


@dataclass
class AskPlan:
    """Everything an Ask needs decided before the model is called.

    Extracted so the streaming and non-streaming paths cannot disagree about
    what was retrieved, what the prompt said, or which task was resolved. They
    used to be one function; adding a second copy for streaming would have meant
    two prompt builders drifting apart, which is how "Ask" and "Ask, streamed"
    end up answering differently.
    """
    system: str = ""
    prompt: str = ""
    task: Optional[TaskType] = None
    saves: List[Any] = field(default_factory=list)
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    max_output_tokens: int = 3072
    operation: str = "ask_sava"
    #: A finished response requiring no model call at all.
    early: Optional[Dict[str, Any]] = None


def _prepare_ask(db, user_id: int, question: str, *, mode: Mode,
                 history: Optional[List[Dict[str, str]]],
                 max_saves: int, collection_id: Optional[int],
                 carry_over_ids: Optional[List[int]],
                 timings: "AskTimings") -> AskPlan:
    """Retrieval and prompt construction. No model call, no writes."""
    router = get_router()
    if not router.is_available():
        return AskPlan(early={"ok": False, "reason": "ai_unavailable",
                              "message": "AI is not configured on this server."})

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
            return AskPlan(early={
                "ok": True, "sources": [], "grounded_in": 0, "mode": mode.value,
                "answer": "Nothing in this collection has been processed yet, "
                          "so there is nothing for me to read."})

    # A short follow-up carries its meaning in the conversation, not in its own
    # words, so the retrieval query borrows the previous question's vocabulary.
    query = question
    if history:
        previous = [m["content"] for m in history if m.get("role") == "user"]
        if previous and len(question.split()) <= 12:
            query = f"{previous[-1]} {question}"

    timings.mark("setup")
    retrieved = retrieval.retrieve_for_library_question(
        db, user_id, query, max_saves=max_saves, restrict_to=restrict_to)
    timings.mark("retrieval")
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
        timings.mark("context")
        return AskPlan(
            system=_ASK_SAVA_SYSTEM,
            prompt=(f"Nothing in {scope_label} matches this question, so answer it "
                    f"from general knowledge and say briefly that you did not find "
                    f"anything of theirs on it.\n\nQUESTION: {question}"),
            task=resolve_task(TaskType.ASK_SAVA, question=question,
                              source_count=0, mode=mode),
            saves=[], blocks=[], max_output_tokens=1024,
            operation="ask_sava.no_match")

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
    timings.mark("context")
    return AskPlan(
        system=_ASK_SAVA_SYSTEM,
        prompt="FROM THEIR LIBRARY:\n\n" + "\n\n".join(ctx)
               + f"\n\nQUESTION: {question}",
        task=task, saves=saves, blocks=blocks,
        max_output_tokens=3072, operation=f"ask_sava.{task.value}")


def ask_sava(db, user_id: int, question: str, *, mode: Mode = Mode.AUTO,
             history: Optional[List[Dict[str, str]]] = None,
             max_saves: int = 10,
             collection_id: Optional[int] = None,
             carry_over_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """A conversation about someone's library, grounded in retrieval.

    The non-streaming path, kept because plenty of callers want one object:
    tests, tools, and any client that has not adopted the stream. It is now a
    thin wrapper over the same plan the streaming path uses.
    """
    timings = AskTimings()
    plan = _prepare_ask(db, user_id, question, mode=mode, history=history,
                        max_saves=max_saves, collection_id=collection_id,
                        carry_over_ids=carry_over_ids, timings=timings)
    if plan.early is not None:
        return plan.early

    router = get_router()
    try:
        completion = router.complete(
            plan.task, system=plan.system, prompt=plan.prompt,
            mode=mode, temperature=0.5, history=history,
            max_output_tokens=plan.max_output_tokens)
    except ProviderError as e:
        logger.warning("ask_sava provider failure: %s", e)
        return {"ok": False, "reason": "provider_error", "message": _busy_message(e)}

    telemetry.record_completion(db, completion, operation=plan.operation,
                                user_id=user_id)
    timings.mark("model")
    logger.info("ask_sava user=%s grounded=%s timings=%s",
                user_id, len(plan.blocks), timings.as_dict())

    return {
        "ok": True,
        "answer": _clean_text(completion.text),
        "mode": mode.value,
        "sources": [s.to_dict() for s in plan.saves],
        "grounded_in": len(plan.blocks),
        "timings_ms": timings.as_dict(),
    }


def ask_sava_stream(db, user_id: int, question: str, *, mode: Mode = Mode.AUTO,
                    history: Optional[List[Dict[str, str]]] = None,
                    max_saves: int = 10,
                    collection_id: Optional[int] = None,
                    carry_over_ids: Optional[List[int]] = None):
    """The same answer, yielded as it is generated.

    Emits plain dicts; the route turns them into SSE frames. Event shapes:

        {"event": "sources", "sources": [...], "grounded_in": n}
        {"event": "token",   "text": "…"}                    # a *delta*
        {"event": "done",    "answer": "…", "timings_ms": {...}}
        {"event": "error",   "reason": "…", "message": "…"}

    Sources are emitted *before* the first token, which is the point of the
    whole exercise: retrieval finishes in tens of milliseconds and the model
    takes seconds, so the client can render what it is reading from while the
    answer is still being written.
    """
    timings = AskTimings()
    plan = _prepare_ask(db, user_id, question, mode=mode, history=history,
                        max_saves=max_saves, collection_id=collection_id,
                        carry_over_ids=carry_over_ids, timings=timings)

    if plan.early is not None:
        early = plan.early
        if early.get("ok"):
            yield {"event": "sources", "sources": early.get("sources", []),
                   "grounded_in": early.get("grounded_in", 0)}
            yield {"event": "token", "text": early.get("answer", "")}
            yield {"event": "done", "answer": early.get("answer", ""),
                   "timings_ms": timings.as_dict(), "grounded_in": 0,
                   "mode": mode.value, "sources": early.get("sources", [])}
        else:
            yield {"event": "error", "reason": early.get("reason", "unavailable"),
                   "message": early.get("message", "Sava could not answer that.")}
        return

    sources = [s.to_dict() for s in plan.saves]
    yield {"event": "sources", "sources": sources,
           "grounded_in": len(plan.blocks)}

    router = get_router()
    pieces: List[str] = []
    first_token_ms: Optional[int] = None
    started = time.monotonic()
    final = None

    try:
        for chunk in router.complete_stream(
                plan.task, system=plan.system, prompt=plan.prompt,
                mode=mode, temperature=0.5, history=history,
                max_output_tokens=plan.max_output_tokens):
            if chunk.done:
                final = chunk
                break
            if not chunk.text:
                continue
            if first_token_ms is None:
                first_token_ms = int((time.monotonic() - started) * 1000)
                timings.mark("first_token")
            pieces.append(chunk.text)
            yield {"event": "token", "text": chunk.text}
    except ProviderError as e:
        logger.warning("ask_sava_stream provider failure: %s", e)
        yield {"event": "error", "reason": "provider_error",
               "message": _busy_message(e)}
        return
    except Exception as e:                                   # pragma: no cover
        logger.exception("ask_sava_stream failed: %s", e)
        yield {"event": "error", "reason": "internal",
               "message": "Sava couldn't finish that answer."}
        return

    timings.mark("model")
    answer = _clean_text("".join(pieces))

    if final is not None:
        # Same accounting as the non-streaming path — a streamed answer costs
        # exactly what an unstreamed one costs, and must be recorded.
        completion = Completion(
            text=answer, provider="gemini",
            model=getattr(final, "_model", "") or "",
            input_tokens=final.input_tokens, output_tokens=final.output_tokens,
            wall_ms=timings.total_ms)
        setattr(completion, "_usd", getattr(final, "_usd", 0.0))
        telemetry.record_completion(db, completion, operation=plan.operation,
                                    user_id=user_id)

    payload = timings.as_dict()
    if first_token_ms is not None:
        payload["first_token"] = first_token_ms
    logger.info("ask_sava_stream user=%s grounded=%s timings=%s",
                user_id, len(plan.blocks), payload)

    yield {"event": "done", "answer": answer, "mode": mode.value,
           "sources": sources, "grounded_in": len(plan.blocks),
           "timings_ms": payload}


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
