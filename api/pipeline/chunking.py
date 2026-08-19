"""Transcript and text chunking.

Requirement: never silently truncate. The previous implementation sent
`text[:20000]` characters to an embedding model with a 2,048-token limit, so
roughly 60% of every long video was dropped before it was ever indexed — a
30-minute talk was searchable only for its first few minutes.

Here, long content is split into overlapping windows that each fit the model,
and every window is embedded. Nothing is discarded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..config import CHUNK_OVERLAP_TOKENS, CHUNK_TARGET_TOKENS

# gemini-embedding-001 accepts 2,048 tokens. Stay well inside it.
MAX_EMBED_TOKENS = 1800

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def estimate_tokens(text: str) -> int:
    """Cheap token estimate. ~4 chars/token for English, floor of word count."""
    if not text:
        return 0
    return max(len(text) // 4, len(text.split()) * 3 // 4, 1)


@dataclass
class Chunk:
    text: str
    start_s: Optional[int] = None
    end_s: Optional[int] = None
    modality: str = "transcript"
    token_count: int = 0

    def __post_init__(self):
        if not self.token_count:
            self.token_count = estimate_tokens(self.text)


def chunk_transcript(
    segments: Sequence[Dict[str, Any]],
    *,
    target_tokens: int = CHUNK_TARGET_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
) -> List[Chunk]:
    """Group timed transcript segments into overlapping, time-anchored chunks.

    Overlap matters for retrieval: an answer that straddles a boundary would
    otherwise be split across two chunks and score poorly in both.
    """
    if not segments:
        return []

    norm: List[Dict[str, Any]] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start", 0) or 0)
        dur = float(seg.get("duration", 0) or 0)
        norm.append({"text": text, "start": start, "end": start + dur,
                     "tokens": estimate_tokens(text)})
    if not norm:
        return []

    chunks: List[Chunk] = []
    i = 0
    while i < len(norm):
        buf: List[Dict[str, Any]] = []
        total = 0
        j = i
        while j < len(norm) and total < target_tokens:
            buf.append(norm[j])
            total += norm[j]["tokens"]
            j += 1

        # A single oversized segment still has to fit the embedding window.
        if len(buf) == 1 and total > MAX_EMBED_TOKENS:
            for piece in _split_long_text(buf[0]["text"], MAX_EMBED_TOKENS):
                chunks.append(Chunk(piece, int(buf[0]["start"]), int(buf[0]["end"])))
            i = j
            continue

        chunks.append(Chunk(
            " ".join(b["text"] for b in buf),
            int(buf[0]["start"]), int(buf[-1]["end"]),
        ))

        if j >= len(norm):
            break
        # Step back far enough to create the overlap.
        back, acc = 0, 0
        while back < len(buf) - 1 and acc < overlap_tokens:
            acc += buf[-1 - back]["tokens"]
            back += 1
        i = max(i + 1, j - back)

    return chunks


def chunk_text(
    text: str,
    *,
    modality: str = "caption",
    target_tokens: int = CHUNK_TARGET_TOKENS,
) -> List[Chunk]:
    """Chunk untimed text (captions, descriptions, OCR) on sentence boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    if estimate_tokens(text) <= target_tokens:
        return [Chunk(text, modality=modality)]

    out: List[Chunk] = []
    buf: List[str] = []
    total = 0
    for sentence in _SENTENCE_END.split(text):
        st = estimate_tokens(sentence)
        if st > MAX_EMBED_TOKENS:
            if buf:
                out.append(Chunk(" ".join(buf), modality=modality))
                buf, total = [], 0
            out.extend(Chunk(p, modality=modality)
                       for p in _split_long_text(sentence, MAX_EMBED_TOKENS))
            continue
        if total + st > target_tokens and buf:
            out.append(Chunk(" ".join(buf), modality=modality))
            buf, total = [], 0
        buf.append(sentence)
        total += st
    if buf:
        out.append(Chunk(" ".join(buf), modality=modality))
    return out


def _split_long_text(text: str, max_tokens: int) -> List[str]:
    """Hard-split on word boundaries. Last resort; never drops content."""
    words = text.split()
    if not words:
        return []
    per = max(1, max_tokens * 4 // 5)   # words per piece, conservative
    return [" ".join(words[i:i + per]) for i in range(0, len(words), per)]


def build_document_text(
    *,
    title: Optional[str] = None,
    creator: Optional[str] = None,
    description: Optional[str] = None,
    note: Optional[str] = None,
    topics: Optional[Iterable[str]] = None,
    tl_dr: Optional[str] = None,
    key_points: Optional[Iterable[str]] = None,
    ocr_text: Optional[str] = None,
    transcript_head: Optional[str] = None,
) -> str:
    """The document-level text that gets one embedding for library search.

    Deliberately favours *distilled* signal (summary, topics, entities) over raw
    transcript: the doc vector is for finding the right save, and chunk vectors
    handle finding the right moment inside it.
    """
    parts: List[str] = []
    if title:
        parts.append(title)
    if creator:
        parts.append(f"by {creator}")
    if tl_dr:
        parts.append(tl_dr)
    if key_points:
        parts.append(" ".join(str(k) for k in key_points))
    if topics:
        parts.append(" ".join(str(t) for t in topics))
    if note:
        parts.append(f"user note: {note}")
    if description:
        parts.append(description[:1200])
    if ocr_text:
        parts.append(f"on-screen text: {ocr_text[:800]}")
    if transcript_head:
        parts.append(transcript_head[:1500])
    doc = "\n".join(p for p in parts if p and p.strip())
    # Trim to the embedding window on a word boundary rather than mid-token.
    if estimate_tokens(doc) > MAX_EMBED_TOKENS:
        words = doc.split()
        doc = " ".join(words[: MAX_EMBED_TOKENS * 4 // 5])
    return doc.strip()
