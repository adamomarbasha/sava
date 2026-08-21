"""Worth revisiting.

A library that only ever grows is a pile. The value of keeping things is being
able to come back to them, and almost nothing gets come back to on its own —
so this brings a few older saves forward.

The line this deliberately does not cross: it reports **facts about content**,
never scores about behaviour. "Saved in March, never opened" is a fact about a
save. "You've saved 12 things this week" is a statistic about a person, and
turns a library into a fitness tracker. Every reason this module produces is of
the first kind, and there is no streak, no count of the user's activity, and
nothing to keep up.

Ranking uses signals that already exist:

  * **age** — something saved yesterday is not being forgotten yet,
  * **never opened** — the strongest signal that something was lost rather than
    used, and the case the feature exists for,
  * **collection affinity** — belonging to a collection opened recently means
    the subject is live for this person right now,
  * **question affinity** — matching what they have recently been asking about.

All four are free. No model is called.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text as sql_text

logger = logging.getLogger(__name__)

# Below this a save is still fresh in mind; surfacing it is just repetition.
MIN_AGE_DAYS = 7
# How far back "recently active" reaches for collections and questions.
RECENT_WINDOW_DAYS = 30
DEFAULT_LIMIT = 8

_STOPWORDS = {
    "what", "which", "when", "where", "who", "why", "how", "the", "a", "an",
    "and", "or", "but", "for", "with", "about", "from", "into", "that", "this",
    "these", "those", "did", "does", "do", "was", "were", "is", "are", "be",
    "been", "have", "has", "had", "i", "me", "my", "mine", "you", "your", "it",
    "its", "of", "in", "on", "at", "to", "any", "all", "some", "said", "say",
    "says", "tell", "show", "find", "saved", "save", "video", "videos", "here",
    # Words that describe the act of asking rather than the thing asked about.
    # Without these, "summarise anything related to food" contributes four
    # terms and three of them match half the library.
    "related", "relate", "like", "likes", "can", "could", "would", "should",
    "they", "them", "their", "stuff", "things", "anything", "everything",
    "something", "then", "than", "also", "just", "only", "more", "most",
    "summarise", "summarize", "summary", "explain", "describe", "list",
    "common", "similar", "between", "across", "over", "under", "much", "many",
    "make", "made", "get", "got", "give", "want", "need", "know", "think",
    "talking", "talk", "said", "mention", "mentions", "mentioned", "please",
}

# A term present in more than this share of the library says nothing about any
# particular save. Matching on it produces a reason that is true of everything,
# which reads to the user as the feature making things up.
MAX_TERM_PREVALENCE = 0.15
# Terms shorter than this collide too easily under substring matching.
MIN_TERM_LENGTH = 4

# Saying *why* something resurfaced is a claim, and a claim the reader can
# check. "You asked about gaming" on a news clip is technically a word match
# and obviously wrong to a human, which makes the whole section look careless.
# So a term earns a small ranking nudge at MAX_TERM_PREVALENCE, but only earns
# the right to be *named* if it is genuinely rare in this library and long
# enough to be a subject rather than a connective.
NAMEABLE_TERM_PREVALENCE = 0.04
NAMEABLE_TERM_LENGTH = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value) -> Optional[datetime]:
    """SQLite hands back naive datetimes; comparing them to aware ones raises."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _recent_question_terms(db, user_id: int, *, limit: int = 40) -> Counter:
    """Words the user has recently been asking about.

    Keyword overlap rather than embeddings, on purpose: this runs on every load
    of the collections screen, the signal is a nudge rather than a ranking, and
    an embedding round trip per question would be real latency for a section
    that sits below the fold.
    """
    since = _now() - timedelta(days=RECENT_WINDOW_DAYS)
    terms: Counter = Counter()
    try:
        rows = db.execute(sql_text("""
            SELECT m.content FROM chat_messages m
            JOIN chat_threads t ON t.id = m.thread_id
            WHERE t.user_id = :uid AND m.role = 'user' AND m.created_at >= :since
            ORDER BY m.created_at DESC LIMIT :lim
        """), {"uid": user_id, "since": since, "lim": limit}).fetchall()
    except Exception as e:
        logger.debug("question terms unavailable: %s", e)
        return terms

    for (content,) in rows:
        for word in re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", (content or "").lower()):
            if word not in _STOPWORDS:
                terms[word] += 1
    return terms


def _recently_viewed_collection_ids(db, user_id: int) -> List[int]:
    since = _now() - timedelta(days=RECENT_WINDOW_DAYS)
    try:
        rows = db.execute(sql_text("""
            SELECT collection_id FROM collection_views
            WHERE user_id = :uid AND viewed_at >= :since
            ORDER BY viewed_at DESC LIMIT 8
        """), {"uid": user_id, "since": since}).fetchall()
    except Exception:
        return []
    return [int(r[0]) for r in rows]


def _distinctive_terms(terms: Counter, blobs: List[str]) -> Dict[str, float]:
    """Asked-about words that single something out, with how rare each is."""
    if not terms or not blobs:
        return {}
    total = len(blobs)
    keep: Dict[str, float] = {}
    for term in terms:
        if len(term) < MIN_TERM_LENGTH:
            continue
        pattern = re.compile(rf"\b{re.escape(term)}", re.IGNORECASE)
        present = sum(1 for blob in blobs if pattern.search(blob))
        prevalence = present / total
        if 0 < prevalence <= MAX_TERM_PREVALENCE:
            keep[term] = prevalence
    return keep


def _appears_as_proper_noun(term: str, raw: str) -> bool:
    """True when the term occurs capitalised, and not merely sentence-initial."""
    for match in re.finditer(rf"\b{re.escape(term)}\w*", raw, re.IGNORECASE):
        word = match.group(0)
        if not word[:1].isupper():
            continue
        before = raw[:match.start()].rstrip()
        # A word at the very start, or straight after a full stop, is
        # capitalised by grammar rather than by being a name.
        if before and before[-1] not in ".!?":
            return True
    return False


def _matching_terms(blob: str, distinctive: Dict[str, float]) -> set:
    """Word-prefix matches, not substrings — "can" must not match "cannot"."""
    return {t for t in distinctive
            if re.search(rf"\b{re.escape(t)}", blob, re.IGNORECASE)}


def worth_revisiting(db, user_id: int, *, limit: int = DEFAULT_LIMIT
                     ) -> List[Dict[str, Any]]:
    """A short list of older saves worth another look, each with a reason."""
    cutoff = _now() - timedelta(days=MIN_AGE_DAYS)

    rows = db.execute(sql_text("""
        SELECT b.id AS bid, b.created_at, b.last_opened_at, b.open_count,
               cc.title, cc.creator_name, cc.platform, cc.description
        FROM bookmarks b
        LEFT JOIN canonical_content cc ON cc.id = b.canonical_content_id
        WHERE b.user_id = :uid AND b.created_at <= :cutoff
        ORDER BY b.created_at DESC
        LIMIT 400
    """), {"uid": user_id, "cutoff": cutoff}).mappings().all()
    if not rows:
        return []

    viewed_collections = _recently_viewed_collection_ids(db, user_id)
    collection_members: Dict[int, str] = {}
    if viewed_collections:
        placeholders = ",".join(str(i) for i in viewed_collections)
        for r in db.execute(sql_text(f"""
            SELECT ci.bookmark_id AS bid, c.name AS name
            FROM collection_items ci
            JOIN collections c ON c.id = ci.collection_id
            WHERE ci.collection_id IN ({placeholders})
        """)).mappings().all():
            collection_members.setdefault(int(r["bid"]), r["name"])

    # Blobs are built once and reused for both the prevalence pass and scoring.
    raw_blobs = {
        int(r["bid"]): (f"{r['title'] or ''} {r['creator_name'] or ''} "
                        f"{r['description'] or ''}")
        for r in rows
    }
    blobs = {bid: text.lower() for bid, text in raw_blobs.items()}
    distinctive = _distinctive_terms(_recent_question_terms(db, user_id),
                                     list(blobs.values()))
    now = _now()
    scored: List[tuple] = []

    for r in rows:
        created = _aware(r["created_at"])
        if created is None:
            continue
        age_days = max(0.0, (now - created).total_seconds() / 86400.0)

        # Grows with age but flattens, so a two-year-old save does not
        # permanently outrank everything from last month. A small uncapped term
        # rides along purely to break ties — without it every save older than
        # three months scored identically and the order was whatever the
        # database returned.
        score = min(age_days / 90.0, 1.5) + min(age_days / 3650.0, 0.4)
        reasons: List[str] = []

        name = collection_members.get(int(r["bid"]))
        if name:
            score += 0.8
            reasons.append(f"From {name}")

        never_opened = r["last_opened_at"] is None
        if never_opened:
            score += 1.0

        matched = _matching_terms(blobs[int(r["bid"])], distinctive)
        if matched:
            # Two independent hits, not one. A single common-ish word overlap
            # produced reasons like "You asked about game" on a baseball clip —
            # technically true, and obviously nonsense to the person reading it.
            score += min(len(matched) * 0.3, 0.9)
            # Naming it is a separate, higher bar than counting it.
            # And it has to be a *subject*. Rarity alone let through "there",
            # "recent" and "saving" — rare in this library, but not things
            # anyone asks about. Requiring the word to appear capitalised in
            # the item itself is what separates a proper noun from a common
            # one without maintaining an endless stoplist.
            nameable = [t for t in matched
                        if distinctive[t] <= NAMEABLE_TERM_PREVALENCE
                        and len(t) >= NAMEABLE_TERM_LENGTH
                        and _appears_as_proper_noun(t, raw_blobs[int(r["bid"])])]
            if nameable:
                best = sorted(nameable, key=lambda t: (distinctive[t], -len(t)))[0]
                reasons.append(f"You asked about {best.title()}")

        if never_opened:
            reasons.append("Never opened")

        # Ordered by how much the reason actually tells the reader: what it
        # belongs to, then what they were asking, then that it was never
        # opened. Age is always available, so it goes last and acts as the
        # fallback when nothing else applies.
        reasons.append(_age_phrase(age_days))
        scored.append((score, int(r["bid"]), reasons))

    scored.sort(key=lambda t: (-t[0], -t[1]))

    # One per collection subject at most, so the section is a set of different
    # things rather than five items from whichever collection was opened last.
    seen_subjects: set = set()
    out: List[Dict[str, Any]] = []
    for score, bid, reasons in scored:
        subject = collection_members.get(bid)
        if subject and subject in seen_subjects:
            continue
        if subject:
            seen_subjects.add(subject)
        out.append({"bookmark_id": bid, "reason": " · ".join(reasons[:2]),
                    "score": round(score, 3)})
        if len(out) >= limit:
            break
    return out


def _age_phrase(days: float) -> str:
    if days < 30:
        return f"Saved {int(days)} days ago"
    if days < 365:
        months = max(1, int(days / 30))
        return f"Saved {months} month{'s' if months > 1 else ''} ago"
    years = max(1, int(days / 365))
    return f"Saved {years} year{'s' if years > 1 else ''} ago"


def record_open(db, user_id: int, bookmark_id: int) -> None:
    """Note that a save was actually opened."""
    from ..models import Bookmark

    bm = (db.query(Bookmark)
          .filter(Bookmark.id == bookmark_id, Bookmark.user_id == user_id).first())
    if bm is None:
        return
    bm.last_opened_at = _now()
    bm.open_count = (bm.open_count or 0) + 1
    try:
        db.commit()
    except Exception:
        db.rollback()


def record_collection_view(db, user_id: int, collection_id: int) -> None:
    from ..models import CollectionView

    row = (db.query(CollectionView)
           .filter(CollectionView.user_id == user_id,
                   CollectionView.collection_id == collection_id).first())
    if row is None:
        db.add(CollectionView(user_id=user_id, collection_id=collection_id,
                              viewed_at=_now(), view_count=1))
    else:
        row.viewed_at = _now()
        row.view_count = (row.view_count or 0) + 1
    try:
        db.commit()
    except Exception:
        db.rollback()
