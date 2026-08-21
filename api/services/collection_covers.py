"""Choosing what a Collection looks like.

A Collection called "Kai Cenat" whose cover is a reaction thumbnail with Kai
four pixels tall in the corner has failed at the one job a cover has: making
someone understand, instantly, what is inside. Picking the highest-scoring
member thumbnail does that surprisingly often, because the strongest *item* is
rarely the most representative *image*.

So the cover is chosen editorially rather than arithmetically:

    identity  ->  discovery  ->  cheap filtering  ->  AI ranking
              ->  rights check  ->  mirror  ->  stored, stable cover

Four rules the design is built around:

  * **AI selects, never generates.** Fabricated artwork for a library of real
    media would be a lie about what is in it. The model's only job is to answer
    "which of these actual images best represents this?".
  * **Bounded cost.** Discovery is capped, filtering is deterministic and free,
    and only a short list reaches a multimodal model. One selection, not five
    hundred.
  * **Reading is free.** Opening Collections performs zero searches and zero
    inference. Selection is a write-time background operation, and its result
    is stored.
  * **The user outranks the system.** A manually chosen cover is authoritative
    and is never replaced by a later automatic pass.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# ─── Bounds ──────────────────────────────────────────────────────────────────
# Discovery is wide and cheap; inference is narrow and paid. These are the only
# two numbers that decide the cost of a cover.
MAX_RAW_CANDIDATES = 40        # across all providers, before filtering
MAX_AI_CANDIDATES = 10         # what a multimodal model ever sees
MIN_EDGE_PX = 320              # anything smaller looks soft at cover size
MAX_ASPECT = 2.4               # panoramas and banners crop badly
MIN_ASPECT = 0.4
# Below this the ranker is telling us nothing here represents the collection,
# and the collection's own media is the better answer.
LOW_CONFIDENCE_FLOOR = 0.5

# Licences Sava is willing to publish inside someone's library. Anything not on
# this list is refused rather than assumed — appearing in a search result says
# nothing about whether an image may be reused.
ACCEPTABLE_LICENSES = {
    "cc0", "pdm", "publicdomain", "public domain", "by", "by-sa",
    "cc by", "cc by-sa", "cc0 1.0", "no restrictions",
}

USER_AGENT = "Sava/1.0 (personal media library; +https://sava.app)"


# ─── Identity ────────────────────────────────────────────────────────────────

@dataclass
class CollectionIdentity:
    """What a Collection is *about*, normalised.

    Derived from the grouping signature rather than from a model: the signature
    already records how the collection was discovered, and "creator:penguinz0"
    is a person by construction. Free, deterministic, and right far more often
    than asking a model to categorise a bare string would be.
    """
    display_name: str
    entity_type: str        # person | brand | place | organization | topic | visual_topic
    canonical_subject: str
    search_intent: str      # the query to search for
    visual_intent: str      # what a good image should show

    def as_dict(self) -> Dict[str, Any]:
        return {
            "display_name": self.display_name, "entity_type": self.entity_type,
            "canonical_subject": self.canonical_subject,
            "search_intent": self.search_intent, "visual_intent": self.visual_intent,
        }


_PLACE_WORDS = ("restaurants", "restaurant", "cafes", "bars", "hotels", "travel")
_FOOD_WORDS = ("recipes", "recipe", "food", "cooking", "meals", "dessert")
_FITNESS_WORDS = ("workouts", "workout", "training", "gym")
_STYLE_WORDS = ("outfits", "style", "fashion", "streetwear", "fits")


def identify(name: str, signature: Optional[str]) -> CollectionIdentity:
    """Normalise a Collection into something searchable and art-directable."""
    display = (name or "").strip()
    lowered = display.lower()
    sig = (signature or "")

    if sig.startswith("creator:"):
        return CollectionIdentity(
            display, "person", display,
            search_intent=display,
            visual_intent=f"a clear, recognisable photograph of {display}")

    if sig.startswith("typed:restaurant") or any(w in lowered for w in _PLACE_WORDS):
        subject = re.sub(r"\b(restaurants?|cafes?|bars?|hotels?)\b", "", lowered).strip() or display
        return CollectionIdentity(
            display, "place", subject.title(),
            search_intent=f"{subject} city food",
            visual_intent=f"an appetising, recognisable image of {display}")

    if sig.startswith("typed:recipe") or any(w in lowered for w in _FOOD_WORDS):
        return CollectionIdentity(
            display, "topic", display,
            search_intent=display,
            visual_intent=f"an appetising, well-lit photograph representing {display}")

    if any(w in lowered for w in _FITNESS_WORDS):
        return CollectionIdentity(display, "topic", display, display,
                                  f"a clean photograph representing {display}")

    if any(w in lowered for w in _STYLE_WORDS):
        return CollectionIdentity(display, "visual_topic", display, display,
                                  f"a stylish, uncluttered image representing {display}")

    if sig.startswith("entity:") or sig.startswith("tag:"):
        # A named thing — a show, a brand, a place. Treated as a subject to be
        # pictured rather than a category to be illustrated.
        return CollectionIdentity(
            display, "brand", display, display,
            visual_intent=f"a clear, recognisable image of {display}")

    return CollectionIdentity(display, "topic", display, display,
                              f"an image that immediately conveys {display}")


# ─── Candidates ──────────────────────────────────────────────────────────────

@dataclass
class ImageCandidate:
    candidate_id: str
    image_url: str
    source_page: Optional[str] = None
    source_domain: Optional[str] = None
    title: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    license: Optional[str] = None
    attribution: Optional[str] = None
    provider: str = "unknown"
    # Set for images that already live in the user's own library.
    bookmark_id: Optional[int] = None

    @property
    def aspect(self) -> Optional[float]:
        if self.width and self.height and self.height > 0:
            return self.width / self.height
        return None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id, "image_url": self.image_url,
            "source_page": self.source_page, "source_domain": self.source_domain,
            "title": self.title, "width": self.width, "height": self.height,
            "license": self.license, "attribution": self.attribution,
            "provider": self.provider, "bookmark_id": self.bookmark_id,
        }


class CollectionImageSearchProvider(ABC):
    """One compliant source of candidate imagery."""

    name: str = "abstract"

    @property
    def available(self) -> bool:
        return True

    @abstractmethod
    def search(self, identity: CollectionIdentity, *, limit: int) -> List[ImageCandidate]:
        ...


def _fetch_json(url: str, *, timeout: float = 12.0) -> Optional[Dict[str, Any]]:
    import requests

    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout)
        if response.status_code != 200:
            return None
        return response.json()
    except Exception as e:
        logger.info("image search request failed: %s", e)
        return None


class OpenverseProvider(CollectionImageSearchProvider):
    """Openverse — openly licensed imagery with machine-readable rights.

    Chosen precisely because rights are part of the response rather than
    something to be guessed at afterwards, and because the API filters to
    commercially usable, modifiable licences at the query itself.
    """

    name = "openverse"

    def search(self, identity: CollectionIdentity, *, limit: int) -> List[ImageCandidate]:
        query = urlencode({
            "q": identity.search_intent, "page_size": min(limit, 20),
            "license_type": "commercial,modification", "mature": "false",
        })
        payload = _fetch_json(f"https://api.openverse.org/v1/images/?{query}")
        if not payload:
            return []

        out: List[ImageCandidate] = []
        for row in (payload.get("results") or [])[:limit]:
            url = row.get("url")
            if not url:
                continue
            out.append(ImageCandidate(
                candidate_id=f"openverse:{row.get('id')}",
                image_url=url, source_page=row.get("foreign_landing_url"),
                source_domain=row.get("source"), title=row.get("title"),
                width=row.get("width"), height=row.get("height"),
                license=(row.get("license") or "").lower(),
                attribution=row.get("attribution"), provider=self.name))
        return out


class WikimediaProvider(CollectionImageSearchProvider):
    """Wikimedia Commons — public domain and CC, strong on people and places."""

    name = "wikimedia"

    def search(self, identity: CollectionIdentity, *, limit: int) -> List[ImageCandidate]:
        query = urlencode({
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": identity.search_intent, "gsrlimit": min(limit, 20),
            "gsrnamespace": 6, "prop": "imageinfo",
            "iiprop": "url|size|extmetadata", "iiurlwidth": 1200,
        })
        payload = _fetch_json(f"https://commons.wikimedia.org/w/api.php?{query}")
        if not payload:
            return []

        pages = ((payload.get("query") or {}).get("pages") or {})
        out: List[ImageCandidate] = []
        for page in list(pages.values())[:limit]:
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("thumburl") or info.get("url")
            if not url:
                continue
            extra = info.get("extmetadata") or {}
            out.append(ImageCandidate(
                candidate_id=f"wikimedia:{page.get('pageid')}",
                image_url=url, source_page=info.get("descriptionurl"),
                source_domain="commons.wikimedia.org",
                title=(page.get("title") or "").replace("File:", ""),
                width=info.get("thumbwidth") or info.get("width"),
                height=info.get("thumbheight") or info.get("height"),
                license=(extra.get("LicenseShortName", {}).get("value") or "").lower(),
                attribution=(extra.get("Artist", {}).get("value") or None),
                provider=self.name))
        return out


class InternalMediaProvider(CollectionImageSearchProvider):
    """The Collection's own media.

    Always available, always rights-clean — it is the user's own library — and
    the fallback whenever external discovery cannot establish usable rights.
    """

    name = "internal"

    def __init__(self, db, collection_id: int):
        self._db = db
        self._collection_id = collection_id

    def search(self, identity: CollectionIdentity, *, limit: int) -> List[ImageCandidate]:
        from sqlalchemy import text as sql_text

        rows = self._db.execute(sql_text("""
            SELECT b.id AS bid, cc.width AS w, cc.height AS h, cc.title AS title,
                   COALESCE(cc.thumbnail_url, b.thumbnail_url) AS thumb,
                   cc.thumbnail_stored_key AS stored
            FROM collection_items ci
            JOIN bookmarks b ON b.id = ci.bookmark_id
            LEFT JOIN canonical_content cc ON cc.id = b.canonical_content_id
            WHERE ci.collection_id = :c
              AND COALESCE(cc.thumbnail_url, b.thumbnail_url) IS NOT NULL
            ORDER BY (cc.thumbnail_stored_key IS NOT NULL) DESC, ci.created_at ASC
            LIMIT :lim
        """), {"c": self._collection_id, "lim": limit}).mappings().all()

        return [
            ImageCandidate(
                candidate_id=f"internal:{r['bid']}", image_url=r["thumb"],
                source_domain="sava", title=r["title"],
                width=r["w"], height=r["h"],
                license="internal", provider=self.name, bookmark_id=int(r["bid"]))
            for r in rows if r["thumb"]
        ]


def get_providers(db, collection_id: int) -> List[CollectionImageSearchProvider]:
    from ..config import COLLECTION_COVER_PROVIDERS

    registry = {"openverse": OpenverseProvider, "wikimedia": WikimediaProvider}
    out: List[CollectionImageSearchProvider] = []
    for name in COLLECTION_COVER_PROVIDERS:
        cls = registry.get(name.strip().lower())
        if cls is not None:
            out.append(cls())
    # Internal media is always last and always present: it is the fallback that
    # guarantees a Collection can always be given a cover.
    out.append(InternalMediaProvider(db, collection_id))
    return out


# ─── Rights ──────────────────────────────────────────────────────────────────

def license_is_acceptable(raw: Optional[str]) -> bool:
    """Whether this licence permits use as a cover.

    Unknown means no. An image is not reusable because it turned up in a search
    result, and treating absent metadata as permission is how a media product
    ends up distributing someone else's photograph.
    """
    if not raw:
        return False
    text = raw.strip().lower()
    if text == "internal":
        return True
    text = text.replace("cc-", "").replace("_", " ").strip()
    # "by-3.0" / "cc by 3.0" / "cc0 1.0" -> compare on the licence stem.
    stem = re.split(r"[\s\-]?\d", text)[0].strip(" -")
    return stem in ACCEPTABLE_LICENSES or text in ACCEPTABLE_LICENSES


# ─── Cheap filtering ─────────────────────────────────────────────────────────

def filter_candidates(candidates: Sequence[ImageCandidate], *,
                      limit: int = MAX_AI_CANDIDATES) -> List[ImageCandidate]:
    """Deterministic, free, and does most of the work.

    Everything here is a rule a model should never be paid to apply: rights,
    resolution, silly aspect ratios, duplicates. What survives is a short list
    where every option is already usable, so the model is only asked the one
    question it is actually good at.
    """
    seen_urls: set = set()
    kept: List[ImageCandidate] = []

    for candidate in candidates:
        if not candidate.image_url or candidate.image_url in seen_urls:
            continue
        if not license_is_acceptable(candidate.license):
            continue
        if candidate.width and candidate.height:
            if min(candidate.width, candidate.height) < MIN_EDGE_PX:
                continue
            aspect = candidate.aspect or 1.0
            if aspect > MAX_ASPECT or aspect < MIN_ASPECT:
                continue
        seen_urls.add(candidate.image_url)
        kept.append(candidate)

    # External imagery first: an internal thumbnail is the safety net, not the
    # preferred answer, and it is what the old cover system already produced.
    kept.sort(key=lambda c: (c.provider == "internal",
                             -((c.width or 0) * (c.height or 0))))
    return kept[:limit]


# ─── Selection ───────────────────────────────────────────────────────────────

@dataclass
class CoverSelection:
    images: List[ImageCandidate] = field(default_factory=list)
    confidence: float = 0.0
    reason: Optional[str] = None
    is_mosaic: bool = False


_SELECT_SYSTEM = """You are the picture editor for someone's personal media
collection. You choose a cover from a numbered list of candidate images.

Answer with STRICT JSON:
{"pick": [<index>, ...], "confidence": <0-1>, "reason": "<short>"}

Pick ONE index when a single image makes someone immediately understand what the
collection is about. Pick 2-4 only when no single image can, and then choose
images that COMPLEMENT rather than repeat each other.

Judge each candidate by asking, in order:

1. Is this actually the subject? A photo of a different person, a generic stock
   scene, a logo, a book cover, a map, or a screenshot of text is NOT the
   subject even when the title mentions it.
2. Is the subject the main thing in the frame, large and unobstructed?
3. Is it clean — no watermark, no caption bar, no collage, no heavy text?
4. Would it survive being cropped to a wide rectangle?
5. Is it good quality rather than small, dark, or blurry?

Titles are the strongest evidence you have. "Kai Cenat 2023" is the subject;
"Rayasianboy 2025" is a different person; "Cai Kenat" is a misspelling of
something unrelated. Prefer the candidate whose title names the subject plainly.

Be willing to reject everything. Set confidence below 0.5 when nothing here
genuinely represents the collection — a weak cover is worse than none, because
the fallback is the user's own media, which is always on-topic."""


def rank_with_ai(db, identity: CollectionIdentity,
                 candidates: Sequence[ImageCandidate], *,
                 user_id: Optional[int] = None) -> CoverSelection:
    """Ask a model which of a short list best represents the collection.

    Text-only on purpose. The candidates arrive with titles, source and
    dimensions, which is enough to reject "Kai Cenat reaction meme" in favour of
    "Kai Cenat 2023" — and it costs a fraction of a multimodal call over ten
    images. If this ever proves too blunt, the same bounded short list is
    exactly what a vision model would need, so the upgrade is local to here.
    """
    from ..ai import telemetry
    from ..ai.base import TaskType
    from ..ai.router import get_router

    if not candidates:
        return CoverSelection(reason="no candidates")

    router = get_router()
    if router is None or not router.is_available():
        # No model: take the best-filtered candidate. Deterministic and free.
        return CoverSelection(images=[candidates[0]], confidence=0.3,
                              reason="model unavailable; used ranked first")

    listing = "\n".join(
        f"{i}. {c.title or 'untitled'} — {c.width or '?'}x{c.height or '?'} "
        f"from {c.source_domain or c.provider}"
        for i, c in enumerate(candidates))
    prompt = (f"Collection: {identity.display_name}\n"
              f"Represents: {identity.entity_type} — {identity.canonical_subject}\n"
              f"A good cover shows: {identity.visual_intent}\n\n"
              f"Candidates:\n{listing}")

    try:
        completion = router.complete(
            TaskType.COLLECTION_NAMING, system=_SELECT_SYSTEM, prompt=prompt,
            json_mode=True, temperature=0.2, max_output_tokens=400)
        telemetry.record_completion(db, completion, operation="collection.cover.rank",
                                    user_id=user_id)
        data = json.loads(completion.text or "{}")
        picks = [int(i) for i in (data.get("pick") or [])
                 if isinstance(i, (int, float)) and 0 <= int(i) < len(candidates)]
        if not picks:
            return CoverSelection(images=[candidates[0]], confidence=0.3,
                                  reason="model returned no usable pick")
        chosen = [candidates[i] for i in picks[:4]]
        return CoverSelection(
            images=chosen,
            confidence=float(data.get("confidence") or 0.5),
            reason=(data.get("reason") or None),
            is_mosaic=len(chosen) > 1)
    except Exception as e:
        logger.warning("cover ranking failed: %s", e)
        return CoverSelection(images=[candidates[0]], confidence=0.3,
                              reason=f"ranking failed: {e}"[:120])


# ─── Signature ───────────────────────────────────────────────────────────────

def cover_signature(db, coll) -> str:
    """What the cover was chosen for.

    Deliberately coarse. It changes when the collection's *identity* changes or
    when its composition changes substantially — not when one item is added,
    not when ordering changes, and never merely because a screen was opened.
    Bucketing the member count is what stops a 12-item collection re-running
    discovery on its way to 13.
    """
    from sqlalchemy import text as sql_text

    count = db.execute(sql_text(
        "SELECT COUNT(*) FROM collection_items WHERE collection_id = :c"
    ), {"c": coll.id}).scalar() or 0
    bucket = 0 if count == 0 else max(1, int(count ** 0.5))
    raw = f"{(coll.name or '').strip().lower()}|{coll.signature or ''}|{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def needs_reselection(db, coll) -> bool:
    """Whether an automatic cover should be chosen again.

    The default answer is no. A manual cover is never reselected, a healthy
    automatic cover is left alone, and only a genuine change — different
    identity, substantially different composition, a cover that has gone
    missing, or one we were never confident in — reopens the question.
    """
    if coll.cover_source and coll.cover_source != "automatic":
        return False
    if not coll.cover_storage_key and not coll.cover_mosaic:
        return True
    if (coll.cover_confidence or 0) < 0.35:
        return True
    return coll.cover_signature != cover_signature(db, coll)


# ─── The pipeline ────────────────────────────────────────────────────────────

def discover_candidates(db, coll, *, identity: Optional[CollectionIdentity] = None,
                        raw_limit: int = MAX_RAW_CANDIDATES) -> List[ImageCandidate]:
    """Gather, then cut down to the short list a model may see."""
    identity = identity or identify(coll.name, coll.signature)
    raw: List[ImageCandidate] = []
    per_provider = max(5, raw_limit // 3)

    for provider in get_providers(db, coll.id):
        if not provider.available:
            continue
        try:
            raw.extend(provider.search(identity, limit=per_provider))
        except Exception as e:
            logger.warning("cover provider %s failed: %s", provider.name, e)
        if len(raw) >= raw_limit:
            break

    return filter_candidates(raw[:raw_limit])


def select_cover(db, coll, *, user_id: Optional[int] = None,
                 force: bool = False) -> Dict[str, Any]:
    """Choose, validate, mirror and store a Collection's cover.

    A write-time operation. Nothing on the read path calls this.
    """
    if not force and not needs_reselection(db, coll):
        return {"status": "unchanged", "reason": "current cover still valid"}
    if coll.cover_source and coll.cover_source not in ("automatic",) and not force:
        return {"status": "skipped", "reason": "cover is user-owned"}

    identity = identify(coll.name, coll.signature)
    candidates = discover_candidates(db, coll, identity=identity)
    if not candidates:
        return {"status": "no_candidates", "identity": identity.as_dict()}

    selection = rank_with_ai(db, identity, candidates, user_id=user_id)
    if not selection.images:
        return {"status": "no_selection", "identity": identity.as_dict()}

    # A weak external pick is worse than the collection's own media.
    #
    # External search finds *something* for almost any query, and for a subject
    # with no public imagery that something is whatever was least dissimilar —
    # which is how a collection ends up fronted by a dark, unreadable photo of
    # nobody in particular. When the ranker is not confident, an image the user
    # actually has is more representative than a stranger's, so the fallback is
    # taken rather than the low-confidence result published.
    if selection.confidence < LOW_CONFIDENCE_FLOOR:
        internal = [c for c in candidates if c.provider == "internal"]
        if not internal:
            internal = InternalMediaProvider(db, coll.id).search(identity, limit=4)
        if internal:
            logger.info("cover for %s: falling back to internal media (confidence %.2f)",
                        coll.id, selection.confidence)
            selection = CoverSelection(
                images=internal[:1], confidence=selection.confidence,
                reason="external candidates were weak; used the collection's own media")

    stored = _mirror_selection(db, coll, selection)
    if not stored:
        return {"status": "mirror_failed", "identity": identity.as_dict()}

    coll.cover_source = "automatic"
    coll.cover_signature = cover_signature(db, coll)
    coll.cover_confidence = selection.confidence
    coll.cover_updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok", "identity": identity.as_dict(),
            "mosaic": selection.is_mosaic, "images": len(selection.images),
            "confidence": selection.confidence, "reason": selection.reason}


def _mirror_selection(db, coll, selection: CoverSelection) -> bool:
    """Copy the chosen imagery into Sava's own storage, with provenance.

    Hotlinking would mean a cover that disappears when someone reorganises a
    wiki page. Provenance travels with it because an image is only usable while
    we can still say where it came from and under what licence.
    """
    from ..net_guard import COVER_IMAGE_HOSTS, PLATFORM_IMAGE_HOSTS
    from . import thumbnails as thumb_svc

    keys: List[str] = []
    urls: List[str] = []
    provenance: List[Dict[str, Any]] = []

    for candidate in selection.images[:4]:
        mirrored = None
        # Internal media is already on a platform CDN Sava mirrors from;
        # external candidates come from the licensed-source list instead.
        hosts = (None if candidate.provider == "internal"
                 else COVER_IMAGE_HOSTS + PLATFORM_IMAGE_HOSTS)
        try:
            mirrored = thumb_svc.mirror_to_storage(
                candidate.image_url, namespace="covers", platform=None,
                allowed_hosts=hosts, user_agent=USER_AGENT)
        except Exception as e:
            logger.warning("cover mirror failed (%s): %s", candidate.candidate_id, e)
        if not mirrored:
            continue
        key, public = mirrored
        keys.append(key)
        urls.append(public)
        provenance.append({
            "candidate_id": candidate.candidate_id,
            "source_page": candidate.source_page,
            "source_domain": candidate.source_domain,
            "license": candidate.license,
            "attribution": candidate.attribution,
            "provider": candidate.provider,
        })

    if not keys:
        return False

    coll.cover_storage_key = keys[0]
    coll.cover_url = urls[0]
    coll.cover_mosaic = json.dumps(urls) if len(urls) > 1 else None
    coll.cover_provenance = json.dumps(provenance)[:8000]
    db.commit()
    return True


# ─── Manual control ──────────────────────────────────────────────────────────

def set_manual_cover(db, coll, *, image_url: Optional[str] = None,
                     image_bytes: Optional[bytes] = None,
                     source: str = "suggested",
                     provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Install a cover the user chose. Authoritative from that moment on."""
    from . import thumbnails as thumb_svc
    from ..storage import derive_key, get_storage

    if image_bytes:
        storage = get_storage()
        key = derive_key("covers", f"upload:{coll.id}:{time.time()}",
                         content_type="image/jpeg")
        public = storage.put(key, image_bytes, content_type="image/jpeg")
    elif image_url:
        from ..net_guard import COVER_IMAGE_HOSTS, PLATFORM_IMAGE_HOSTS
        mirrored = thumb_svc.mirror_to_storage(
            image_url, namespace="covers", platform=None,
            allowed_hosts=COVER_IMAGE_HOSTS + PLATFORM_IMAGE_HOSTS,
            user_agent=USER_AGENT)
        if not mirrored:
            return {"status": "mirror_failed"}
        key, public = mirrored
    else:
        return {"status": "nothing_supplied"}

    coll.cover_storage_key = key
    coll.cover_url = public
    coll.cover_mosaic = None
    coll.cover_source = source
    coll.cover_confidence = 1.0          # the user is certain by definition
    coll.cover_signature = cover_signature(db, coll)
    coll.cover_provenance = json.dumps(provenance or {})[:8000]
    coll.cover_updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok", "cover_url": public, "cover_source": source}


def reset_to_automatic(db, coll, *, user_id: Optional[int] = None) -> Dict[str, Any]:
    """Hand cover choice back to Sava."""
    coll.cover_source = "automatic"
    coll.cover_signature = None          # forces the next pass to reselect
    coll.cover_confidence = None
    db.commit()
    return select_cover(db, coll, user_id=user_id, force=True)


def suggestions(db, coll, *, limit: int = MAX_AI_CANDIDATES) -> Dict[str, Any]:
    """Alternative covers for the picker. Search runs here, never on a read."""
    identity = identify(coll.name, coll.signature)
    candidates = discover_candidates(db, coll, identity=identity)
    external = [c.as_dict() for c in candidates if c.provider != "internal"][:limit]
    internal = [c.as_dict() for c in candidates if c.provider == "internal"][:limit]
    if not internal:
        internal = [c.as_dict() for c in
                    InternalMediaProvider(db, coll.id).search(identity, limit=limit)]
    return {"identity": identity.as_dict(), "suggested": external,
            "from_collection": internal}
