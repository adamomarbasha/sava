"""Opening questions for Ask, drawn from the library that will answer them.

The previous suggestions were three fixed strings — "What did I add this week?",
"Find that video about…", "What restaurants do I have?" — shown to everyone on
every open. Two things are wrong with that beyond the repetition. They are not
about *this* library, so "What restaurants do I have?" appears for someone whose
saves are entirely Formula 1. And because a suggestion is a promise that an
answer exists, a generic one is a promise the library frequently cannot keep:
tapping it spends a model call to be told there is nothing.

So every suggestion here is generated from rows that are actually present. A
creator suggestion requires that creator to have saves; a topic suggestion
requires the topic to appear in `ContentUnderstanding.topics`; "this week"
requires saves this week. If the evidence is missing the candidate is never
built, which makes an empty result the honest answer for an empty library rather
than a list of dead ends.

Variety comes from generating many more candidates than are shown and sampling
per open. The sampling is stratified by `kind` first, so a run never returns four
questions about creators — the point of asking again is to be offered a
different *sort* of question, not a different name in the same sentence.
"""
from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from ..models import (
    Bookmark, CanonicalContent, Collection, CollectionItem, ContentUnderstanding,
)

# How many to return, and the pool we sample them from.
DEFAULT_LIMIT = 4

# A suggestion is only offered when its evidence clears these bars. They exist
# so a single stray save cannot generate a question about a "topic".
MIN_CREATOR_SAVES = 2
MIN_TOPIC_SAVES = 2
MIN_TYPE_SAVES = 2
MIN_UNWATCHED = 4

# SF Symbol names. Chosen per kind rather than per question so the icon reads as
# a category — the eye groups the list before it reads any of it.
_ICONS = {
    "recent": "clock",
    "creator": "person",
    "topic": "number",
    "type": "square.grid.2x2",
    "collection": "folder",
    "unwatched": "eye.slash",
    "synthesis": "sparkles",
    "detail": "text.alignleft",
    "practical": "checklist",
}


@dataclass(frozen=True)
class Suggestion:
    text: str
    kind: str
    icon: str

    def payload(self) -> Dict[str, str]:
        return asdict(self)


def _mk(text: str, kind: str) -> Suggestion:
    return Suggestion(text=text, kind=kind, icon=_ICONS.get(kind, "sparkles"))


def _loads(raw: Optional[str], fallback):
    if not raw:
        return fallback
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


# ── Phrasing ────────────────────────────────────────────────────────────────
#
# Several phrasings per kind, so asking twice about the same creator does not
# produce the same sentence. Kept in the register a person actually uses when
# talking about their own saves — "Catch me up on X", not "Provide a summary of
# content authored by X".

_CREATOR_FORMS = (
    "What have I saved from {name}?",
    "Catch me up on {name}",
    "What does {name} keep coming back to?",
)

_TOPIC_FORMS = (
    "What do I have on {topic}?",
    "Sum up everything I saved about {topic}",
    "What's the best thing I saved on {topic}?",
)

# Per content type, because the useful question about a recipe is not the useful
# question about a restaurant.
_TYPE_FORMS = {
    "recipe": ("What recipes have I saved?",
               "What could I cook this week from my saves?",
               "Which of my recipes is quickest?"),
    "restaurant": ("Which places do I want to try?",
                   "Where should I eat from my saves?"),
    "travel": ("Where am I planning to go?",
               "What did people recommend for my trip?"),
    "product": ("What products was I looking at?",
                "What did the reviews actually say?"),
    "shopping": ("What was I about to buy?",),
    "fashion": ("What outfits did I save?",),
    "beauty": ("What products did I save?",),
    "fitness": ("What workouts have I saved?",
                "Build a week from the workouts I saved"),
    "tutorial": ("What am I trying to learn?",
                 "What should I work through first?"),
    "educational": ("What have I been learning about?",),
    "coding": ("What techniques did I save?",
               "What should I actually try in code?"),
    "news": ("What was I following?",),
    "finance": ("What did I save about money?",),
}

_COLLECTION_FORMS = (
    "What's in {name}?",
    "Sum up {name} for me",
    "What should I start with in {name}?",
)

_UNWATCHED_FORMS = (
    "What have I saved but never watched?",
    "What's been sitting in my library unopened?",
    "Pick something I saved and forgot about",
)

_SYNTHESIS_FORMS = (
    "What keeps coming up across my saves?",
    "What have I been into lately?",
    "Find a pattern in what I save",
)


# ── Evidence gathering ──────────────────────────────────────────────────────

def _library_rows(db: Session, user_id: int, bookmark_ids: Optional[Sequence[int]] = None):
    """Bookmarks joined to what Sava understood about them.

    Outer-joined: a save whose understanding has not been produced yet still
    contributes its creator and platform, which is most of what the cheaper
    suggestions need.
    """
    q = (db.query(Bookmark, CanonicalContent, ContentUnderstanding)
         .outerjoin(CanonicalContent, CanonicalContent.id == Bookmark.canonical_content_id)
         .outerjoin(ContentUnderstanding,
                    ContentUnderstanding.canonical_content_id == CanonicalContent.id)
         .filter(Bookmark.user_id == user_id))
    if bookmark_ids is not None:
        if not bookmark_ids:
            return []
        q = q.filter(Bookmark.id.in_(list(bookmark_ids)))
    return q.order_by(Bookmark.created_at.desc()).limit(600).all()


def _candidates_from_rows(rows, rng: random.Random) -> List[Suggestion]:
    """Turn a slice of library into every question it can honestly support."""
    out: List[Suggestion] = []
    if not rows:
        return out

    creators: Counter = Counter()
    topics: Counter = Counter()
    types: Counter = Counter()
    unwatched = 0
    recent = 0
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    for bm, cc, und in rows:
        name = (cc.creator_name if cc else None) or bm.author
        if name and name.strip():
            creators[name.strip()] += 1

        content_type = (und.content_type if und else None) or (cc.content_type if cc else None)
        if content_type:
            types[content_type] += 1

        for topic in _loads(und.topics if und else None, [])[:6]:
            if isinstance(topic, str) and 2 < len(topic) < 40:
                topics[topic.strip().lower()] += 1

        if (bm.open_count or 0) == 0:
            unwatched += 1

        created = bm.created_at
        if created is not None:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created >= week_ago:
                recent += 1

    # Recency — only when there is something recent to talk about.
    if recent >= 2:
        out.append(_mk("What did I save this week?", "recent"))
    if recent >= 6:
        out.append(_mk("Sum up my week in saves", "recent"))

    for name, count in creators.most_common(6):
        if count >= MIN_CREATOR_SAVES:
            out.append(_mk(rng.choice(_CREATOR_FORMS).format(name=name), "creator"))

    for topic, count in topics.most_common(8):
        if count >= MIN_TOPIC_SAVES:
            out.append(_mk(rng.choice(_TOPIC_FORMS).format(topic=topic), "topic"))

    for content_type, count in types.most_common(6):
        if count >= MIN_TYPE_SAVES and content_type in _TYPE_FORMS:
            out.append(_mk(rng.choice(_TYPE_FORMS[content_type]), "type"))

    if unwatched >= MIN_UNWATCHED:
        out.append(_mk(rng.choice(_UNWATCHED_FORMS), "unwatched"))

    # Synthesis needs enough material for a pattern to mean anything.
    if len(rows) >= 8:
        out.append(_mk(rng.choice(_SYNTHESIS_FORMS), "synthesis"))

    return out


def _save_candidates(db: Session, bm: Bookmark, rng: random.Random) -> List[Suggestion]:
    """Questions about one save, from what Sava actually extracted from it.

    The typed data is the useful part: if a recipe parsed ingredients, "What are
    the ingredients?" is answerable, and if it did not, it is not — so the
    question is only offered when the field is populated.
    """
    cc = (db.query(CanonicalContent).get(bm.canonical_content_id)
          if bm.canonical_content_id else None)
    und = (db.query(ContentUnderstanding)
           .filter(ContentUnderstanding.canonical_content_id == cc.id).first()) if cc else None

    out: List[Suggestion] = []
    typed = _loads(und.typed_data if und else None, {})
    content_type = (und.content_type if und else None) or (cc.content_type if cc else None)

    if content_type == "recipe":
        if typed.get("ingredients"):
            out.append(_mk("What are the ingredients?", "detail"))
        if typed.get("steps"):
            out.append(_mk("Walk me through it", "practical"))
        out.append(_mk("Can I make this with what I have?", "practical"))
    elif content_type in ("restaurant", "travel"):
        if typed.get("location") or typed.get("address"):
            out.append(_mk("Where exactly is this?", "detail"))
        out.append(_mk("What did they recommend ordering?", "practical"))
    elif content_type in ("product", "shopping", "fashion", "beauty"):
        out.append(_mk("What's the verdict?", "synthesis"))
        if typed.get("price"):
            out.append(_mk("Is it worth the price?", "practical"))
    elif content_type == "fitness":
        out.append(_mk("What's the workout?", "detail"))
        out.append(_mk("What equipment do I need?", "practical"))
    elif content_type in ("tutorial", "educational", "coding"):
        out.append(_mk("Explain this more simply", "detail"))
        out.append(_mk("What should I try first?", "practical"))

    # Topics give a specific, non-generic follow-up for anything at all.
    for topic in _loads(und.topics if und else None, [])[:3]:
        if isinstance(topic, str) and 2 < len(topic) < 40:
            out.append(_mk(f"What does this say about {topic.strip().lower()}?", "topic"))

    if _loads(und.key_points if und else None, []):
        out.append(_mk(rng.choice(("What's the main point?",
                                   "What's the one thing to remember?")), "synthesis"))

    name = (cc.creator_name if cc else None) or bm.author
    if name and name.strip():
        out.append(_mk(f"What else do I have from {name.strip()}?", "creator"))

    # Always answerable from a transcript or a description, so it is the floor
    # rather than a headline.
    out.append(_mk(rng.choice(("Summarise this", "What are they talking about?")), "synthesis"))
    return out


# ── Selection ───────────────────────────────────────────────────────────────

def _pick(candidates: List[Suggestion], limit: int, rng: random.Random) -> List[Suggestion]:
    """Sample, stratified by kind, deduplicated by text.

    Round-robin across kinds rather than a flat sample: a flat sample over a pool
    that happens to hold five creator questions returns mostly creator questions,
    which is exactly the monotony this is meant to avoid.
    """
    by_kind: Dict[str, List[Suggestion]] = {}
    seen = set()
    for c in candidates:
        key = c.text.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        by_kind.setdefault(c.kind, []).append(c)

    for group in by_kind.values():
        rng.shuffle(group)

    kinds = list(by_kind)
    rng.shuffle(kinds)

    out: List[Suggestion] = []
    while kinds and len(out) < limit:
        for kind in list(kinds):
            if len(out) >= limit:
                break
            group = by_kind[kind]
            if group:
                out.append(group.pop())
            if not group:
                kinds.remove(kind)
    return out


def suggest(db: Session, *, user_id: int, scope: str = "library",
            collection_id: Optional[int] = None, bookmark_id: Optional[int] = None,
            limit: int = DEFAULT_LIMIT, seed: Optional[int] = None) -> Dict[str, Any]:
    """Opening questions for one Ask scope.

    `seed` makes a run reproducible; tests pass one. Left unset in production so
    that reopening Ask offers a different way in.
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    limit = max(1, min(int(limit or DEFAULT_LIMIT), 6))

    if scope == "save" and bookmark_id is not None:
        bm = (db.query(Bookmark)
              .filter(Bookmark.id == bookmark_id, Bookmark.user_id == user_id).first())
        candidates = _save_candidates(db, bm, rng) if bm else []

    elif scope == "collection" and collection_id is not None:
        coll = (db.query(Collection)
                .filter(Collection.id == collection_id,
                        Collection.user_id == user_id).first())
        if not coll:
            candidates = []
        else:
            ids = [r[0] for r in db.query(CollectionItem.bookmark_id)
                   .filter(CollectionItem.collection_id == coll.id).all()]
            candidates = _candidates_from_rows(_library_rows(db, user_id, ids), rng)
            # The collection itself is the most obvious thing to ask about, and
            # is the one question guaranteed to have evidence behind it.
            candidates.insert(
                0, _mk(rng.choice(_COLLECTION_FORMS).format(name=coll.name), "collection"))

    else:
        candidates = _candidates_from_rows(_library_rows(db, user_id), rng)
        # Named collections are a strong signal about what this person cares
        # about, and they are cheap to ask about.
        for coll in (db.query(Collection)
                     .filter(Collection.user_id == user_id)
                     .order_by(Collection.updated_at.desc()).limit(5).all()):
            candidates.append(
                _mk(rng.choice(_COLLECTION_FORMS).format(name=coll.name), "collection"))

    picked = _pick(candidates, limit, rng)
    return {"scope": scope,
            "suggestions": [s.payload() for s in picked],
            "generated": len(candidates)}
