"""Discovering what a library is actually *about*.

The job here is to look at one person's saves and find the groupings they would
recognise as theirs — "Kai Cenat", "Attack on Titan", "Air Fryer Recipes" — and
to not produce "Entertainment", "Other", or "Videos".

The previous approach clustered embeddings and asked a model to name each
cluster. It produced "Late Night Scroll", "Creative Inspo" and "Cinematic
Chaos": evocative, and useless. Two things were wrong with it. Embeddings
existed for only a third of the library, so two thirds of the evidence was
invisible; and a cluster centroid has no name, so the model was being asked to
invent one, and invention is exactly where vagueness comes from.

This module inverts that. A collection is not a cluster that needs naming — it
is **a name that gathers items**. Start from things that already have names in
the data (a creator, a hashtag, an entity, a cuisine), and the name problem
disappears: `#attackontitan` is called Attack on Titan because that is what it
is, not because a model was asked to be creative about it.

Ordered by coverage and cost, cheapest first:

  1. **Creator** — present on ~93% of saves, free, and the most recognisable
     grouping there is. Someone with five penguinz0 saves has a penguinz0
     collection.
  2. **Hashtags** — present on every caption, free, and the closest thing to
     the user's own vocabulary. Requires real work to be useful: `#fyp` is
     noise, and `#aot`/`#attackontitan`/`#erenjaeger` are one collection.
  3. **Entities and topics** — precise where they exist, but only on items that
     have been through understanding.
  4. **Typed data** — a cuisine, a city, a muscle group. Specific by nature.
  5. **Embedding clusters** — last, and only over items the named signals
     missed, because this is the tier that cannot name itself.

Every tier emits the same `Candidate`, so merging, quality gates and ranking
are written once and the tiers stay independent.

No tier calls a model. Naming a cluster is the only thing that ever does, and
it is optional — with it unavailable, the first four tiers still work.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# A grouping smaller than this is a coincidence, not an interest.
MIN_MEMBERS = 3
# Beyond this a collections screen stops being a way to find things.
MAX_COLLECTIONS = 12
# Two candidates sharing this much of their membership are the same collection
# wearing two names.
MERGE_OVERLAP = 0.6


# ─── What is never a collection ──────────────────────────────────────────────
#
# Two different kinds of junk. Platform furniture (`#fyp`, "TikTok") describes
# where something was found, not what it is. Generic buckets ("entertainment",
# "other") are the exact failure this feature is meant to avoid — a collection
# called Entertainment tells its owner nothing they did not already know.

_PLATFORM_NOISE = {
    "fyp", "fypage", "foryou", "foryoupage", "foryourpage", "viral", "viralvideo",
    "viralvideos", "trending", "trend", "shorts", "short", "youtubeshorts",
    "reels", "reel", "explore", "explorepage", "tiktok", "tiktokviral", "youtube",
    "instagram", "twitter", "snapchat", "facebook", "twitch", "capcut", "duet",
    "stitch", "greenscreen", "followme", "follow", "like", "share", "subscribe",
    "comment", "new", "video", "videos", "clip", "clips", "edit", "edits",
    "editing", "funny", "lol", "lmao", "omg", "wow", "fun", "cool", "best",
    "top", "must", "watch", "goviral", "blowthisup", "xyzbca", "usa", "uk",
}

# Names some ingestors write when the platform gave them nothing. They are
# placeholders, not people, and "Instagram User" is not a collection.
_PLACEHOLDER_CREATORS = {
    "instagram user", "instagram", "tiktok user", "tiktok", "youtube",
    "unknown", "unknown creator", "anonymous", "user", "admin", "guest",
    "facebook user", "twitter user", "reddit user", "n/a", "none", "null",
}

# Genre words. Broader than a collection should be — this is the "Entertainment"
# failure wearing a different coat. "Gaming" tells its owner nothing; "Kai
# Cenat" tells them exactly what is inside. Applied to the topic tier, which is
# where an understanding pass emits genre-level labels.
_BROAD_TOPICS = {
    "gaming", "games", "game", "comedy", "humor", "humour", "music", "sports",
    "sport", "food", "cooking", "travel", "fashion", "beauty", "technology",
    "tech", "science", "art", "dance", "fitness", "health", "business",
    "finance", "education", "learning", "animation", "anime", "movies", "film",
    "tv", "television", "streaming", "podcast", "vlog", "vlogs", "reaction",
    "reactions", "review", "reviews", "tutorial", "tutorials", "diy", "culture",
    "politics", "history", "nature", "animals", "cars", "photography",
}

_GENERIC_LABELS = {
    "entertainment", "other", "others", "general", "misc", "miscellaneous",
    "videos", "video", "content", "collection", "collections", "saved", "saves",
    "stuff", "things", "random", "various", "assorted", "media", "clips",
    "uncategorized", "unsorted", "inspiration", "inspo", "vibes", "aesthetic",
    "life", "lifestyle", "daily", "day", "today", "people", "person", "thing",
    "social media", "internet", "online", "news", "update", "updates",
}

# Entity buckets worth grouping on. `key_facts` and `urls` are not names of
# anything; `dates` are not interests.
_ENTITY_KINDS = ("people", "brands", "places", "products", "organizations",
                 "teams", "works", "games", "artists")


@dataclass
class Candidate:
    """One proposed collection, before quality gates and merging."""
    signature: str           # stable identity across rebuilds
    label: str               # what the user will see
    members: Set[int]        # bookmark ids
    source: str              # creator | tag | entity | topic | typed | cluster
    strength: float = 0.0    # tie-break only; not shown

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def label_quality(self) -> Tuple[int, int]:
        """How much the label reads like a name rather than a slug.

        Decides which name survives a merge. `#aot` and `#attackontitan` cover
        the same saves, so one of them has to go — and it should be the
        acronym, because "Attack on Titan" is what the collection is called.
        Multi-word wins first, then length, so an expanded phrase always beats
        the compressed tag it came from.
        """
        words = len(self.label.split())
        return (min(words, 4), len(self.label))


@dataclass
class LibraryItem:
    """One save, flattened into every signal it can contribute."""
    bookmark_id: int
    canonical_id: Optional[int]
    platform: str
    creator: Optional[str]
    title: str
    caption: str
    content_type: Optional[str]
    topics: List[str] = field(default_factory=list)
    entities: Dict[str, List[str]] = field(default_factory=dict)
    typed: Dict[str, Any] = field(default_factory=dict)
    hashtags: List[str] = field(default_factory=list)


# ─── Text helpers ────────────────────────────────────────────────────────────

_HASHTAG = re.compile(r"#([A-Za-z][A-Za-z0-9_]{2,29})")
_LETTERS = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    """Letters and digits only. `Attack on Titan` and `#attackontitan` agree."""
    return _LETTERS.sub("", (text or "").lower())


def _norm_key(text: str) -> str:
    """A comparison key that tolerates spacing and punctuation."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def extract_hashtags(text: str) -> List[str]:
    return [m.group(1).lower() for m in _HASHTAG.finditer(text or "")]


class PhraseIndex:
    """Turns `attackontitan` back into `Attack on Titan`, for free.

    Hashtags arrive with their spacing and capitalisation destroyed, and a
    collection called "Attackontitan" looks broken. The usual fix is to ask a
    model to expand it; the cheaper and more accurate one is to notice that the
    properly written phrase is almost always sitting in the same library — in a
    video title, a caption, or a creator's name — because that is where the
    hashtag came from.

    So: index every 1–4 word phrase the user's own library contains, keyed by
    its letters-only form, and look the slug up. `Attack on Titan` is recovered
    from the user's own data, with their own capitalisation, and a slug that
    genuinely has no expansion falls back to a title-cased guess.
    """

    def __init__(self, items: Sequence[LibraryItem]):
        self._by_slug: Dict[str, Counter] = defaultdict(Counter)
        for item in items:
            for source in (item.title, item.caption, item.creator or ""):
                self._index(source)

    def _index(self, text: str) -> None:
        # Hashtag runs are stripped first: indexing "#attackontitan" as a phrase
        # would just map the slug back to itself.
        cleaned = _HASHTAG.sub(" ", text or "")
        words = re.findall(r"[A-Za-z][A-Za-z0-9'&-]*", cleaned)
        for n in (1, 2, 3, 4):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i:i + n])
                key = _slug(phrase)
                if 3 <= len(key) <= 40:
                    self._by_slug[key][phrase] += 1

    def expand(self, slug: str) -> Optional[str]:
        """The most common real spelling of this slug, if the library has one."""
        options = self._by_slug.get(slug)
        if not options:
            return None
        # Prefer the most frequent spelling; break ties toward the one with
        # more capitals, which is usually the proper noun.
        best = max(options.items(),
                   key=lambda kv: (kv[1], sum(1 for c in kv[0] if c.isupper())))
        return best[0]


def display_label(raw: str, phrases: Optional[PhraseIndex] = None) -> str:
    """The human form of a signal's name."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    # Already written like a name — a creator, an entity — so leave it alone.
    if " " in raw or any(c.isupper() for c in raw[1:]):
        return raw[:60]
    if phrases:
        expanded = phrases.expand(_slug(raw))
        if expanded:
            return expanded[:60]
    return raw.replace("_", " ").title()[:60]


def is_junk_label(label: str) -> bool:
    key = _norm_key(label)
    if not key or len(key) < 2:
        return True
    if key in _GENERIC_LABELS or _slug(key) in _PLATFORM_NOISE:
        return True
    # A label that is only digits, or a bare number, names nothing.
    return key.isdigit()


# ─── Loading the library ─────────────────────────────────────────────────────

def load_items(db, user_id: int) -> List[LibraryItem]:
    """Every save, with every signal already attached.

    One query. The signals live across four tables and grouping needs all of
    them for every item, so fetching them per-item would be a few hundred
    round trips to build one screen.
    """
    from sqlalchemy import text as sql_text

    rows = db.execute(sql_text("""
        SELECT b.id AS bid, cc.id AS cid, cc.platform, cc.creator_name,
               cc.creator_handle, cc.title, cc.description, cc.content_type,
               u.topics, u.entities, u.typed_data
        FROM bookmarks b
        JOIN canonical_content cc ON cc.id = b.canonical_content_id
        LEFT JOIN content_understanding u ON u.canonical_content_id = cc.id
        WHERE b.user_id = :uid
    """), {"uid": user_id}).mappings().all()

    def _loads(raw, fallback):
        if not raw:
            return fallback
        try:
            value = json.loads(raw)
            return value if isinstance(value, type(fallback)) else fallback
        except Exception:
            return fallback

    items: List[LibraryItem] = []
    for r in rows:
        title = r["title"] or ""
        caption = r["description"] or ""
        entities_raw = _loads(r["entities"], {})
        entities = {
            k: [v for v in vals if isinstance(v, str)]
            for k, vals in entities_raw.items()
            if isinstance(vals, list)
        }
        items.append(LibraryItem(
            bookmark_id=int(r["bid"]), canonical_id=r["cid"],
            platform=(r["platform"] or "other"),
            creator=(r["creator_name"] or r["creator_handle"] or None),
            title=title, caption=caption,
            content_type=r["content_type"],
            topics=[t for t in _loads(r["topics"], []) if isinstance(t, str)],
            entities=entities,
            typed=_loads(r["typed_data"], {}),
            hashtags=extract_hashtags(f"{title} {caption}"),
        ))
    return items


# ─── Tier 1: creators ────────────────────────────────────────────────────────

def creator_candidates(items: Sequence[LibraryItem]) -> List[Candidate]:
    """The highest-coverage, highest-recognition signal there is.

    Someone who saved five penguinz0 videos has a penguinz0 collection, and no
    amount of clustering will name it better than the creator already has.
    """
    by_creator: Dict[str, Set[int]] = defaultdict(set)
    display: Dict[str, str] = {}
    for item in items:
        if not item.creator:
            continue
        name = item.creator.strip()
        # A numeric handle is a database key that leaked into a name field, and
        # a placeholder is not a person.
        if (not name or name.isdigit() or is_junk_label(name)
                or _norm_key(name) in _PLACEHOLDER_CREATORS):
            continue
        key = _norm_key(name)
        by_creator[key].add(item.bookmark_id)
        display.setdefault(key, name)

    out = []
    for key, members in by_creator.items():
        if len(members) < MIN_MEMBERS:
            continue
        out.append(Candidate(signature=f"creator:{_slug(key)}",
                             label=display[key][:60], members=members,
                             source="creator", strength=1.0 + len(members) / 100))
    return out


# ─── Tier 2: hashtags ────────────────────────────────────────────────────────

def hashtag_candidates(items: Sequence[LibraryItem],
                       phrases: PhraseIndex) -> List[Candidate]:
    """The user's own vocabulary, once the noise is taken out.

    Hashtags are the only rich signal present on every save, but they are raw:
    `#fyp` appears on a third of TikToks and means nothing, and one subject
    arrives spelled four ways. Both problems are handled here rather than
    downstream, because a hashtag that survives this function is already a
    plausible collection name.
    """
    by_tag: Dict[str, Set[int]] = defaultdict(set)
    for item in items:
        for tag in set(item.hashtags):
            if tag in _PLATFORM_NOISE or is_junk_label(tag):
                continue
            by_tag[tag].add(item.bookmark_id)

    # Fold variants into their base before size is judged: `#aotedit` and
    # `#attackontitanedit` are Attack on Titan, and counted separately none of
    # them might clear the threshold.
    folded: Dict[str, Set[int]] = defaultdict(set)
    tags_by_size = sorted(by_tag, key=lambda t: (-len(by_tag[t]), len(t)))
    for tag in tags_by_size:
        base = _fold_tag(tag, folded.keys() or by_tag.keys())
        folded[base] |= by_tag[tag]

    out = []
    for tag, members in folded.items():
        if len(members) < MIN_MEMBERS:
            continue
        label = display_label(tag, phrases)
        if is_junk_label(label):
            continue
        out.append(Candidate(signature=f"tag:{_slug(tag)}", label=label,
                             members=members, source="tag",
                             strength=0.8 + len(members) / 100))
    return out


_TAG_SUFFIXES = ("edit", "edits", "edito", "editz", "clip", "clips", "fan",
                 "fans", "fandom", "core", "tok", "reel", "reels", "meme",
                 "memes", "song", "songs", "music", "video", "videos")


def _fold_tag(tag: str, known: Iterable[str]) -> str:
    """Map a hashtag onto its base subject.

    Two moves, both conservative. Strip a decorative suffix when what remains
    is still a real tag in this library (`aotedit` -> `aot`, but `podcast` is
    left alone because `pod` is not a tag here). Then, if the result is a
    prefix or suffix of a longer known tag, defer to the longer one, so
    `#aot` and `#attackontitan` do not become two collections.
    """
    known = set(known)
    base = tag
    for suffix in _TAG_SUFFIXES:
        if base.endswith(suffix) and len(base) - len(suffix) >= 3:
            trimmed = base[: -len(suffix)]
            if trimmed in known:
                base = trimmed
                break
    for other in known:
        if other != base and len(other) > len(base) >= 3 and other.startswith(base):
            return other
    return base


# ─── Tier 3: entities and topics ─────────────────────────────────────────────

def entity_candidates(items: Sequence[LibraryItem],
                      phrases: PhraseIndex) -> List[Candidate]:
    """Named things the understanding pass already pulled out."""
    by_entity: Dict[str, Set[int]] = defaultdict(set)
    display: Dict[str, str] = {}
    for item in items:
        for kind in _ENTITY_KINDS:
            for value in item.entities.get(kind, []):
                name = (value or "").strip()
                if not name or len(name) > 48 or is_junk_label(name):
                    continue
                key = _norm_key(name)
                by_entity[key].add(item.bookmark_id)
                display.setdefault(key, name)

    out = []
    for key, members in by_entity.items():
        if len(members) < MIN_MEMBERS:
            continue
        out.append(Candidate(signature=f"entity:{_slug(key)}",
                             label=display_label(display[key], phrases),
                             members=members, source="entity",
                             strength=0.7 + len(members) / 100))
    return out


def topic_candidates(items: Sequence[LibraryItem],
                     phrases: PhraseIndex) -> List[Candidate]:
    """Topics are broader than entities, so they are held to the same bar and
    ranked below them — "gaming" is a worse collection than "Kai Cenat"."""
    by_topic: Dict[str, Set[int]] = defaultdict(set)
    display: Dict[str, str] = {}
    for item in items:
        for topic in item.topics:
            name = (topic or "").strip()
            if (not name or len(name) > 40 or is_junk_label(name)
                    or _norm_key(name) in _BROAD_TOPICS):
                continue
            key = _norm_key(name)
            by_topic[key].add(item.bookmark_id)
            display.setdefault(key, name)

    out = []
    for key, members in by_topic.items():
        if len(members) < MIN_MEMBERS:
            continue
        out.append(Candidate(signature=f"topic:{_slug(key)}",
                             label=display_label(display[key], phrases).title()[:60],
                             members=members, source="topic",
                             strength=0.5 + len(members) / 100))
    return out


# ─── Tier 4: typed data ──────────────────────────────────────────────────────

# Which typed fields name a collection well. A recipe's cuisine does; its
# cooking time does not. The label template is what keeps these specific:
# "Japanese Recipes" rather than "Recipes".
_TYPED_FIELDS: Dict[str, Sequence[Tuple[str, str]]] = {
    "recipe": (("cuisine", "{} Recipes"), ("method", "{} Recipes"),
               ("meal_type", "{} Recipes"), ("diet", "{} Recipes")),
    "restaurant": (("city", "{} Restaurants"), ("cuisine", "{} Restaurants"),
                   ("neighborhood", "{} Restaurants")),
    "travel": (("city", "{}"), ("country", "{}"), ("region", "{}")),
    "fitness": (("muscle_group", "{} Workouts"), ("equipment", "{} Workouts"),
                ("goal", "{} Workouts")),
    "product": (("brand", "{}"), ("category", "{}")),
    "fashion": (("brand", "{}"), ("style", "{} Style")),
    "beauty": (("brand", "{}"), ("category", "{}")),
    "coding": (("language", "{}"), ("framework", "{}")),
}


def typed_candidates(items: Sequence[LibraryItem]) -> List[Candidate]:
    """Groupings from typed understanding — a cuisine, a city, a muscle group.

    These are the "Air Fryer Recipes" and "New York Restaurants" cases. The
    value carries the specificity and the template carries the noun, so the
    result is never the bare category on its own.
    """
    buckets: Dict[Tuple[str, str], Set[int]] = defaultdict(set)
    labels: Dict[Tuple[str, str], str] = {}

    for item in items:
        ctype = (item.content_type or "").lower()
        spec = _TYPED_FIELDS.get(ctype)
        if not spec or not isinstance(item.typed, dict):
            continue
        # typed_data is sometimes nested under its own type key.
        payload = item.typed.get(ctype) if isinstance(item.typed.get(ctype), dict) \
            else item.typed
        for field_name, template in spec:
            value = payload.get(field_name)
            values = value if isinstance(value, list) else [value]
            for v in values:
                if not isinstance(v, str):
                    continue
                v = v.strip()
                if not v or len(v) > 32 or is_junk_label(v):
                    continue
                key = (ctype, _norm_key(v))
                buckets[key].add(item.bookmark_id)
                labels.setdefault(key, template.format(v.title() if v.islower() else v))

    out = []
    for key, members in buckets.items():
        if len(members) < MIN_MEMBERS:
            continue
        ctype, value = key
        out.append(Candidate(signature=f"typed:{ctype}:{_slug(value)}",
                             label=labels[key][:60], members=members,
                             source="typed", strength=0.9 + len(members) / 100))
    return out


# ─── Merging, gating, ranking ────────────────────────────────────────────────

def _overlap(a: Set[int], b: Set[int]) -> float:
    """Containment, not Jaccard.

    Jaccard would keep a small precise collection alongside the large loose one
    that swallows it, because their sizes differ. What matters for "do not
    create overlapping collections" is whether one is mostly inside the other.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def merge_candidates(candidates: List[Candidate]) -> List[Candidate]:
    """Collapse candidates that are the same collection under two names.

    The survivor is the strongest source, which is deliberate: given a creator
    candidate and a hashtag candidate covering the same items, "Kai Cenat Live"
    beats "kaicenatstream" because the creator field is a real name and the
    hashtag is a slug.
    """
    ordered = sorted(candidates, key=lambda c: (-c.strength, -c.size, c.label))
    kept: List[Candidate] = []
    for cand in ordered:
        merged_into = None
        for existing in kept:
            if _overlap(cand.members, existing.members) >= MERGE_OVERLAP:
                merged_into = existing
                break
        if merged_into is None:
            kept.append(cand)
            continue
        merged_into.members |= cand.members
        # The winner keeps the better *name*, even though it lost on strength —
        # otherwise a creator field of "Kai Cenat Live" or an acronym like
        # "Aot" becomes the permanent title of a collection whose other name
        # was the readable one.
        if cand.label_quality > merged_into.label_quality:
            merged_into.label = cand.label
    return kept


def apply_feedback(candidates: List[Candidate], *, rejected: Set[str],
                   removed: Dict[str, Set[int]]) -> List[Candidate]:
    """Honour the user's corrections before anything is materialised.

    A rejected signature never comes back, and an item removed from a grouping
    is not re-added to it — even though the signal that put it there is still
    true. Re-deriving the same grouping and quietly undoing the user's edit is
    what makes automatic organisation feel like it is fighting you.
    """
    out = []
    for cand in candidates:
        if cand.signature in rejected:
            continue
        excluded = removed.get(cand.signature)
        if excluded:
            cand.members = cand.members - excluded
        if cand.size >= MIN_MEMBERS:
            out.append(cand)
    return out


def rank(candidates: List[Candidate], *, limit: int = MAX_COLLECTIONS
         ) -> List[Candidate]:
    """Pick the final set: specific sources first, bigger before smaller."""
    source_rank = {"creator": 0, "typed": 1, "entity": 2, "tag": 3,
                   "topic": 4, "cluster": 5}
    ordered = sorted(
        candidates,
        key=lambda c: (source_rank.get(c.source, 9), -c.size, c.label.lower()))
    return ordered[:limit]


def discover(db, user_id: int, *, limit: int = MAX_COLLECTIONS,
             rejected: Optional[Set[str]] = None,
             removed: Optional[Dict[str, Set[int]]] = None,
             items: Optional[Sequence[LibraryItem]] = None,
             ) -> Tuple[List[Candidate], List[LibraryItem]]:
    """The whole pipeline: signals in, collections out. No model involved."""
    library = list(items) if items is not None else load_items(db, user_id)
    if not library:
        return [], []

    phrases = PhraseIndex(library)
    candidates = (
        creator_candidates(library)
        + typed_candidates(library)
        + entity_candidates(library, phrases)
        + hashtag_candidates(library, phrases)
        + topic_candidates(library, phrases)
    )
    candidates = [c for c in candidates if not is_junk_label(c.label)]
    candidates = merge_candidates(candidates)
    candidates = apply_feedback(candidates, rejected=rejected or set(),
                                removed=removed or {})
    return rank(candidates, limit=limit), library
