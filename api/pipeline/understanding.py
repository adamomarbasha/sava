"""Content classification and structured understanding.

Two steps, deliberately separate:

  1. **Classify** from cheap signals (title, creator, caption, transcript head).
     Tiny input, ~30 output tokens. The result decides both the extraction
     schema and whether visual analysis is worth paying for.
  2. **Extract** using a schema chosen for that content type. A recipe yields
     ingredients and temperatures; a product review yields pros, cons, verdict.
     Forcing both through one generic "summary" schema loses the information
     that makes the save useful later.

Everything produced here is persisted once and reused by summary, search,
collections, Ask This, and Ask Sava.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CONTENT_TYPES = [
    "recipe", "restaurant", "travel", "product", "tutorial", "educational",
    "entertainment", "fitness", "fashion", "beauty", "news", "podcast",
    "coding", "finance", "shopping", "other",
]

# How much meaning typically lives on screen rather than in speech. Drives the
# conditional visual ladder: high values justify paying for frames.
VISUAL_DEPENDENCY_PRIOR = {
    "recipe": 0.85, "fashion": 0.9, "beauty": 0.85, "shopping": 0.85,
    "product": 0.7, "travel": 0.75, "restaurant": 0.75, "fitness": 0.7,
    "tutorial": 0.6, "coding": 0.5, "entertainment": 0.5, "news": 0.3,
    "educational": 0.3, "finance": 0.3, "podcast": 0.1, "other": 0.4,
}

TYPED_SCHEMAS: Dict[str, str] = {
    "recipe": (
        '"recipe":{"dish":str,"ingredients":[{"item":str,"quantity":str}],'
        '"steps":[str],"temperature":str,"cook_time":str,"servings":str,'
        '"equipment":[str],"tips":[str]}'
    ),
    "restaurant": (
        '"restaurant":{"name":str,"city":str,"neighborhood":str,"cuisine":str,'
        '"dishes":[str],"price_range":str,"reservation_notes":str,"verdict":str}'
    ),
    "travel": (
        '"travel":{"destination":str,"places":[{"name":str,"kind":str,"note":str}],'
        '"hotels":[str],"restaurants":[str],"activities":[str],"best_time":str,'
        '"budget_notes":str}'
    ),
    "product": (
        '"product":{"items":[{"name":str,"brand":str,"price":str}],"pros":[str],'
        '"cons":[str],"verdict":str,"alternatives":[str],"where_to_buy":str}'
    ),
    "shopping": (
        '"product":{"items":[{"name":str,"brand":str,"price":str}],"pros":[str],'
        '"cons":[str],"verdict":str,"alternatives":[str],"where_to_buy":str}'
    ),
    "fitness": (
        '"fitness":{"focus":str,"exercises":[{"name":str,"sets":str,"reps":str}],'
        '"equipment":[str],"duration":str,"level":str}'
    ),
    "beauty": (
        '"beauty":{"products":[{"name":str,"brand":str,"shade":str}],'
        '"concerns":[str],"routine_steps":[str],"skin_type":str}'
    ),
    "fashion": (
        '"fashion":{"items":[{"item":str,"brand":str,"price":str}],'
        '"style":str,"occasion":str,"where_to_buy":str}'
    ),
    "coding": (
        '"coding":{"languages":[str],"frameworks":[str],"concepts":[str],'
        '"commands":[str],"gotchas":[str]}'
    ),
}

_CLASSIFY_SYSTEM = f"""Classify a saved social-media post.
Return STRICT JSON only, no fences:
{{"content_type":one of {CONTENT_TYPES},
"confidence":0.0-1.0,
"visual_dependency":0.0-1.0,
"language":"ISO code"}}

`visual_dependency` = how much of the meaning is only visible on screen
(text overlays, products shown but not named, demonstrations, before/after)
rather than spoken. A talking-head explainer is low. A silent recipe with
on-screen ingredient text is high."""

_COMMON_SCHEMA = (
    '{"tl_dr":str,'
    '"key_points":[str],'
    '"topics":[str],'
    '"entities":{"people":[str],"brands":[str],"products":[str],"places":[str],'
    '"foods":[str],"ingredients":[str],"activities":[str],"prices":[str],'
    '"dates":[str],"urls":[str],"key_facts":[str],"recommendations":[str]},'
    '"chapters":[{"title":str,"start":int}]'
)


def _extract_system(content_type: str) -> str:
    typed = TYPED_SCHEMAS.get(content_type)
    schema = _COMMON_SCHEMA + (f',{typed}' if typed else "") + "}"
    return f"""You are building a structured record of a saved video/post so the
user can find and reason about it months later.

Return STRICT JSON matching this shape, no markdown fences:
{schema}

Rules:
- Use ONLY what the provided context states. Never invent facts, prices,
  names, or measurements.
- Omit an entity list entirely (or leave it empty) if nothing applies. Do not
  pad it with guesses.
- `people`: creator handles or publicly named figures only. Never attempt to
  identify individuals from visual description.
- `tl_dr`: 2-3 plain sentences a person would find useful on reopening.
- `key_points`: 3-7 concrete takeaways, not restatements of the title.
- `topics`: 3-8 lowercase tags for grouping (e.g. "pasta", "budget travel").
- `chapters`: only when the context contains real timestamps; else []."""


def classify(
    *,
    router,
    title: Optional[str],
    creator: Optional[str],
    caption: Optional[str],
    transcript_head: Optional[str],
    platform: str,
    duration_s: Optional[float],
) -> Tuple[Dict[str, Any], Any]:
    """Cheap first-pass classification. Returns (result, completion)."""
    from ..ai.base import TaskType

    context = "\n".join(filter(None, [
        f"PLATFORM: {platform}",
        f"DURATION: {int(duration_s)}s" if duration_s else None,
        f"TITLE: {title}" if title else None,
        f"CREATOR: {creator}" if creator else None,
        f"CAPTION: {(caption or '')[:900]}" if caption else None,
        f"TRANSCRIPT START: {(transcript_head or '')[:1200]}" if transcript_head else None,
    ]))
    if not context.strip():
        return {"content_type": "other", "confidence": 0.0,
                "visual_dependency": VISUAL_DEPENDENCY_PRIOR["other"]}, None

    completion = router.complete(
        TaskType.CLASSIFICATION, system=_CLASSIFY_SYSTEM, prompt=context,
        json_mode=True, temperature=0.0, max_output_tokens=1024,
    )
    try:
        data = json.loads(completion.text or "{}")
    except Exception:
        data = {}

    ctype = str(data.get("content_type") or "other").lower().strip()
    if ctype not in CONTENT_TYPES:
        ctype = "other"
    vd = data.get("visual_dependency")
    try:
        vd = float(vd)
    except (TypeError, ValueError):
        vd = None
    if vd is None:
        vd = VISUAL_DEPENDENCY_PRIOR.get(ctype, 0.4)

    return {
        "content_type": ctype,
        "confidence": float(data.get("confidence") or 0.5),
        "visual_dependency": max(0.0, min(1.0, vd)),
        "language": data.get("language") or "en",
    }, completion


def extract(
    *,
    router,
    content_type: str,
    title: Optional[str],
    creator: Optional[str],
    caption: Optional[str],
    description: Optional[str],
    transcript: Optional[str],
    visual_text: Optional[str],
    comments: Optional[List[str]] = None,
    mode=None,
    long_form: bool = False,
) -> Tuple[Dict[str, Any], Any]:
    """Produce the structured understanding record. Returns (record, completion)."""
    from ..ai.base import Mode, TaskType

    sources: List[str] = []
    ctx: List[str] = []
    if title:
        ctx.append(f"TITLE: {title}")
    if creator:
        ctx.append(f"CREATOR: {creator}")
    if caption:
        ctx.append(f"CAPTION: {caption[:2000]}")
        sources.append("caption")
    if description and description != caption:
        ctx.append(f"DESCRIPTION: {description[:3000]}")
        sources.append("description")
    if visual_text:
        ctx.append(f"ON-SCREEN TEXT AND VISUALS:\n{visual_text[:4000]}")
        sources.append("visual")
    if transcript:
        # Long-form gets a bigger budget; nothing is silently dropped because
        # the full transcript is already chunked and embedded separately.
        limit = 30000 if long_form else 14000
        ctx.append(f"TRANSCRIPT:\n{transcript[:limit]}")
        sources.append("transcript")
    if comments:
        joined = "\n".join(f"- {c}" for c in comments[:20])
        ctx.append(f"TOP COMMENTS:\n{joined[:1500]}")
        sources.append("comments")

    if not ctx:
        return {}, None

    task = TaskType.SUMMARY_LONG if long_form else TaskType.STRUCTURED_EXTRACTION
    completion = router.complete(
        task, system=_extract_system(content_type), prompt="\n\n".join(ctx),
        mode=mode or Mode.AUTO, json_mode=True, temperature=0.2,
        max_output_tokens=6144 if long_form else 4096,
    )

    try:
        data = json.loads(completion.text or "{}")
    except Exception as e:
        logger.warning("understanding JSON parse failed: %s", e)
        return {}, completion

    entities = data.get("entities") or {}
    if not isinstance(entities, dict):
        entities = {}
    typed: Dict[str, Any] = {}
    for key in ("recipe", "restaurant", "travel", "product", "fitness",
                "beauty", "fashion", "coding"):
        if isinstance(data.get(key), dict) and data[key]:
            typed[key] = data[key]

    return {
        "content_type": content_type,
        "tl_dr": (data.get("tl_dr") or "").strip(),
        "key_points": _str_list(data.get("key_points")),
        "topics": [t.lower() for t in _str_list(data.get("topics"))][:8],
        "entities": {k: _str_list(v) for k, v in entities.items() if v},
        "typed_data": typed,
        "chapters": data.get("chapters") if isinstance(data.get("chapters"), list) else [],
        "sources_used": sources,
    }, completion


def _str_list(value: Any, limit: int = 24) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for v in value:
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
        elif isinstance(v, dict):
            name = v.get("name") or v.get("item") or v.get("title")
            if name:
                out.append(str(name).strip())
    return out[:limit]


def entities_to_text(entities: Dict[str, Any], typed: Dict[str, Any]) -> str:
    """Flatten the structured layer into text so it contributes to embeddings."""
    parts: List[str] = []
    for key, values in (entities or {}).items():
        if isinstance(values, list) and values:
            parts.append(f"{key}: {', '.join(str(v) for v in values[:12])}")
    for key, block in (typed or {}).items():
        if not isinstance(block, dict):
            continue
        flat: List[str] = []
        for k, v in block.items():
            if isinstance(v, str) and v.strip():
                flat.append(f"{k} {v}")
            elif isinstance(v, list) and v:
                items = [str(i.get("item") or i.get("name") or i) if isinstance(i, dict) else str(i)
                         for i in v[:10]]
                flat.append(f"{k} {', '.join(items)}")
        if flat:
            parts.append(f"{key}: " + "; ".join(flat))
    return "\n".join(parts)
