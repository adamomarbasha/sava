"""Collections — manual and automatic.

Manual collections are the user's. They are created, renamed, filled and
emptied by hand, and nothing in this module ever rewrites one.

Automatic collections are derived, and derivation happens in `grouping.py`;
this module is what turns a set of discovered groupings into durable rows and
keeps them stable while the library changes underneath them. Three properties
matter, and each is a decision rather than a detail:

  * **Stability.** A rebuild reconciles by *signature*, not by deleting every
    automatic collection and creating new ones. "Kai Cenat" keeps its id, its
    cover and its place on the screen when the library grows, instead of
    flickering into a different collection with the same name.
  * **Deference.** Corrections recorded in `collection_feedback` are applied
    before anything is written, so a rebuild cannot undo a removal or resurrect
    a deleted collection.
  * **Restraint.** No model is called to decide what the collections are. The
    only optional model call names an embedding cluster, and a cluster whose
    name comes back generic is dropped rather than shown.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import text as sql_text

from ..ai import telemetry
from ..ai.base import Mode, TaskType
from ..models import (
    Bookmark, CanonicalContent, Collection, CollectionItem, ContentUnderstanding,
)
from ..vectors import from_storage, knn, normalize, to_storage

logger = logging.getLogger(__name__)

MIN_CLUSTER_SIZE = 3
MIN_SAVES_FOR_AUTO = 8
MAX_AUTO_COLLECTIONS = 8
MATCH_THRESHOLD = 0.62


# ─── Manual collections ──────────────────────────────────────────────────────

def create_collection(db, user_id: int, name: str, *,
                      description: Optional[str] = None,
                      auto_populate: bool = False) -> Collection:
    """Create immediately, then find likely members.

    Returns as soon as the row exists so the UI can navigate; matching is done
    by `suggest_for_collection`, which the caller may run inline or enqueue.
    """
    name = (name or "").strip()[:120]
    if not name:
        raise ValueError("Collection name is required")

    existing = (db.query(Collection)
                .filter(Collection.user_id == user_id, Collection.name == name).first())
    if existing:
        return existing

    coll = Collection(user_id=user_id, name=name, kind="manual",
                      description=description)
    db.add(coll)
    db.commit()
    db.refresh(coll)

    # Embed the collection's meaning so matching is semantic, not substring.
    try:
        from ..ai.router import get_router
        router = get_router()
        if router.is_available():
            seed = f"{name}. {description}" if description else name
            res = router.embed([seed], task_type="retrieval_query")
            if res.vectors:
                coll.embedding = to_storage(res.vectors[0])
                db.commit()
            telemetry.record_embedding(db, res, operation="collection.embed",
                                       user_id=user_id)
    except Exception as e:
        logger.warning("collection embedding failed: %s", e)

    # A brand-new collection has no members, so there is no member thumbnail to
    # fall back to and the card would show a bare name plate — which is exactly
    # what "I made a collection and it just says Music" was. Its *name* is
    # enough to find a cover for, so selection is queued the moment it exists.
    _queue_cover_selection(db, [coll.id])

    return coll


def suggest_for_collection(db, collection_id: int, *, limit: int = 25,
                           auto_add: bool = False) -> List[Dict[str, Any]]:
    """Find saves that plausibly belong. Vector similarity + name/entity match."""
    coll = db.query(Collection).get(collection_id)
    if coll is None:
        return []

    scores: Dict[int, float] = {}

    if coll.embedding is not None:
        vec = from_storage(coll.embedding)
        for cid, sim in knn(
            db, table="content_embeddings", vector_column="embedding",
            id_column="canonical_content_id", query_vec=vec, k=limit * 3,
            where_sql=("canonical_content_id IN (SELECT canonical_content_id FROM bookmarks "
                       "WHERE user_id = :uid AND canonical_content_id IS NOT NULL)"),
            params={"uid": coll.user_id},
        ):
            scores[cid] = sim

    # A literal name match ("Kai Cenat") is strong evidence a vector may miss.
    tokens = [t for t in re.findall(r"[A-Za-z0-9']{3,}", coll.name.lower())]
    if tokens:
        params = {"uid": coll.user_id}
        clauses = []
        for i, tok in enumerate(tokens):
            params[f"t{i}"] = f"%{tok}%"
            clauses.append(
                f"(LOWER(COALESCE(cc.title,'')) LIKE :t{i} OR "
                f" LOWER(COALESCE(cc.creator_name,'')) LIKE :t{i} OR "
                f" LOWER(COALESCE(cc.creator_handle,'')) LIKE :t{i} OR "
                f" LOWER(COALESCE(b.note,'')) LIKE :t{i} OR "
                f" LOWER(COALESCE(u.topics,'')) LIKE :t{i} OR "
                f" LOWER(COALESCE(u.entities,'')) LIKE :t{i})"
            )
        rows = db.execute(sql_text(f"""
            SELECT DISTINCT cc.id AS cid FROM bookmarks b
            JOIN canonical_content cc ON cc.id = b.canonical_content_id
            LEFT JOIN content_understanding u ON u.canonical_content_id = cc.id
            WHERE b.user_id = :uid AND ({' OR '.join(clauses)})
        """), params).mappings().all()
        for r in rows:
            scores[int(r["cid"])] = max(scores.get(int(r["cid"]), 0.0), 0.80)

    if not scores:
        return []

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    ranked = [(cid, s) for cid, s in ranked if s >= MATCH_THRESHOLD][:limit]
    if not ranked:
        return []

    existing = {r[0] for r in db.execute(sql_text(
        "SELECT bookmark_id FROM collection_items WHERE collection_id = :c"
    ), {"c": collection_id}).fetchall()}

    bm_rows = (db.query(Bookmark, CanonicalContent)
               .join(CanonicalContent, CanonicalContent.id == Bookmark.canonical_content_id)
               .filter(Bookmark.user_id == coll.user_id,
                       Bookmark.canonical_content_id.in_([c for c, _ in ranked]))
               .all())
    by_cid = {cc.id: (bm, cc) for bm, cc in bm_rows}

    out: List[Dict[str, Any]] = []
    for cid, score in ranked:
        pair = by_cid.get(cid)
        if not pair:
            continue
        bm, cc = pair
        if bm.id in existing:
            continue
        out.append({
            "bookmark_id": bm.id, "canonical_id": cc.id,
            "title": cc.title or bm.title, "author": cc.creator_name or bm.author,
            "platform": cc.platform, "thumbnail_url": cc.thumbnail_url or bm.thumbnail_url,
            "score": round(score, 4),
        })
        if auto_add:
            db.add(CollectionItem(collection_id=collection_id, bookmark_id=bm.id,
                                  added_by="auto", score=score))

    if auto_add and out:
        db.commit()
        _set_cover(db, coll)
    return out


def add_items(db, collection_id: int, bookmark_ids: List[int], *, added_by: str = "user") -> int:
    coll = db.query(Collection).get(collection_id)
    if coll is None:
        return 0
    existing = {r[0] for r in db.execute(sql_text(
        "SELECT bookmark_id FROM collection_items WHERE collection_id = :c"
    ), {"c": collection_id}).fetchall()}
    added = 0
    for bid in bookmark_ids:
        if bid in existing:
            continue
        owns = db.query(Bookmark).filter(Bookmark.id == bid,
                                         Bookmark.user_id == coll.user_id).first()
        if owns is None:
            continue
        db.add(CollectionItem(collection_id=collection_id, bookmark_id=bid,
                              added_by=added_by))
        added += 1
    if added:
        db.commit()
        _set_cover(db, coll)
    return added


def _set_cover(db, coll: Collection) -> None:
    """Use real member imagery. Never generate an image for a cover."""
    if coll.cover_bookmark_id:
        return
    row = db.execute(sql_text("""
        SELECT b.id FROM collection_items ci
        JOIN bookmarks b ON b.id = ci.bookmark_id
        LEFT JOIN canonical_content cc ON cc.id = b.canonical_content_id
        WHERE ci.collection_id = :c
          AND (cc.thumbnail_url IS NOT NULL OR b.thumbnail_url IS NOT NULL)
        ORDER BY ci.score DESC NULLS LAST, ci.created_at ASC
        LIMIT 1
    """) if _supports_nulls_last(db) else sql_text("""
        SELECT b.id FROM collection_items ci
        JOIN bookmarks b ON b.id = ci.bookmark_id
        LEFT JOIN canonical_content cc ON cc.id = b.canonical_content_id
        WHERE ci.collection_id = :c
          AND (cc.thumbnail_url IS NOT NULL OR b.thumbnail_url IS NOT NULL)
        ORDER BY ci.score DESC, ci.created_at ASC
        LIMIT 1
    """), {"c": coll.id}).first()
    if row:
        coll.cover_bookmark_id = int(row[0])
        db.commit()


def refresh_cover(db, coll: Collection) -> None:
    """Re-pick the cover after membership changed.

    `_set_cover` deliberately does nothing when a cover is already set, so that
    a stable collection keeps a stable face. That is wrong in exactly one case:
    the cover image was the item just removed, and leaving it would show a
    collection fronted by something no longer in it.
    """
    if coll.cover_bookmark_id is not None:
        still_present = db.execute(sql_text(
            "SELECT 1 FROM collection_items WHERE collection_id = :c AND bookmark_id = :b"
        ), {"c": coll.id, "b": coll.cover_bookmark_id}).first()
        if still_present:
            return
        coll.cover_bookmark_id = None
        db.commit()
    _set_cover(db, coll)


def _supports_nulls_last(db) -> bool:
    return db.bind.dialect.name == "postgresql"


# ─── Automatic collections ───────────────────────────────────────────────────

def _kmeans(X: np.ndarray, k: int, *, iters: int = 40, seed: int = 13
            ) -> Tuple[np.ndarray, np.ndarray]:
    """k-means++ on unit vectors (cosine == dot). No sklearn dependency."""
    rng = np.random.default_rng(seed)
    n = X.shape[0]
    centers = [X[rng.integers(n)]]
    for _ in range(1, k):
        d = 1.0 - np.max(X @ np.array(centers).T, axis=1)
        d = np.clip(d, 0, None)
        total = d.sum()
        idx = int(rng.integers(n)) if total <= 0 else int(rng.choice(n, p=d / total))
        centers.append(X[idx])
    C = np.array(centers, dtype=np.float32)

    labels = np.zeros(n, dtype=np.int32)
    for _ in range(iters):
        labels_new = np.argmax(X @ C.T, axis=1).astype(np.int32)
        if np.array_equal(labels_new, labels):
            break
        labels = labels_new
        for j in range(k):
            members = X[labels == j]
            if len(members):
                v = members.mean(axis=0)
                nv = np.linalg.norm(v)
                if nv > 0:
                    C[j] = v / nv
    return labels, C


def _name_cluster(router, db, user_id: int, items: List[Dict[str, Any]]
                  ) -> Tuple[str, Optional[str]]:
    """Name a discovered cluster with a cheap model. Falls back to topic voting."""
    topic_counts = Counter()
    for it in items:
        for t in it.get("topics", []):
            topic_counts[t] += 1
    creators = Counter(it["creator"] for it in items if it.get("creator"))

    fallback = None
    if creators and creators.most_common(1)[0][1] >= max(3, len(items) * 0.6):
        fallback = creators.most_common(1)[0][0]
    elif topic_counts:
        fallback = topic_counts.most_common(1)[0][0].title()

    if router is None or not router.is_available():
        return (fallback or "Saved"), None

    listing = "\n".join(
        f"- {it['title'] or 'Untitled'} ({it.get('creator') or 'unknown'})"
        + (f" [{', '.join(it.get('topics', [])[:4])}]" if it.get("topics") else "")
        for it in items[:12]
    )
    system = """Name a collection of someone's saved videos.

Return STRICT JSON: {"name": str, "description": str}

The name must be 1-3 words, specific to what these items actually are, and read
like something the person would have named it themselves — a creator, a topic,
a place, an activity. Do not use generic words like "Videos", "Content",
"Collection", "Miscellaneous", or "Saved". Description: one short sentence."""
    try:
        completion = router.complete(
            TaskType.COLLECTION_NAMING, system=system,
            prompt=f"Saved items:\n{listing}", json_mode=True,
            temperature=0.3, max_output_tokens=512,
        )
        telemetry.record_completion(db, completion, operation="collection.name",
                                    user_id=user_id)
        data = json.loads(completion.text or "{}")
        name = (data.get("name") or "").strip()[:120]
        desc = (data.get("description") or "").strip()[:300] or None
        banned = {"videos", "content", "collection", "saved", "miscellaneous", "other"}
        if name and name.lower() not in banned:
            return name, desc
    except Exception as e:
        logger.warning("cluster naming failed: %s", e)
    return (fallback or "Saved"), None


def _feedback(db, user_id: int):
    """What this user has already corrected, as (rejected, removed)."""
    from ..models import CollectionFeedback

    rejected: set = set()
    removed: Dict[str, set] = {}
    for row in db.query(CollectionFeedback).filter(
            CollectionFeedback.user_id == user_id).all():
        if row.action == "reject_collection":
            rejected.add(row.signature)
        elif row.action == "remove_item" and row.bookmark_id is not None:
            removed.setdefault(row.signature, set()).add(row.bookmark_id)
    return rejected, removed


def record_feedback(db, user_id: int, signature: str, action: str,
                    bookmark_id: Optional[int] = None) -> None:
    """Remember a correction so the next rebuild does not undo it."""
    from ..models import CollectionFeedback

    if not signature:
        return
    exists = (db.query(CollectionFeedback)
              .filter(CollectionFeedback.user_id == user_id,
                      CollectionFeedback.signature == signature,
                      CollectionFeedback.action == action,
                      CollectionFeedback.bookmark_id == bookmark_id)
              .first())
    if exists:
        return
    db.add(CollectionFeedback(user_id=user_id, signature=signature,
                              action=action, bookmark_id=bookmark_id))
    try:
        db.commit()
    except Exception as e:
        # Logged, not silently discarded.
        #
        # This was a bare `except: rollback()`, which is invisible on SQLite and
        # actively misleading on Postgres. `bookmark_id` carries a foreign key
        # that SQLite does not enforce by default and Postgres always does — so a
        # stale id made the insert fail, the failure was swallowed, and the user's
        # correction was silently forgotten. The next rebuild then undid the very
        # change they had just made, with nothing anywhere to explain why.
        db.rollback()
        logger.warning("could not record collection feedback "
                       "(user=%s action=%s bookmark=%s): %s",
                       user_id, action, bookmark_id, type(e).__name__)


def _cluster_candidates(db, user_id: int, covered: set, library, *,
                        limit: int):
    """Embedding clusters, for saves the named signals could not reach.

    Last resort, and deliberately hard to satisfy. This is the tier that
    produced "Late Night Scroll" and "Cinematic Chaos", because a centroid has
    no name and a model asked to name one will reach for atmosphere. So it runs
    only over leftovers, demands real cohesion, and — critically — throws the
    cluster away if the name that comes back is generic, rather than showing a
    vague collection because the clustering succeeded.
    """
    from ..ai.router import get_router
    from ..vectors import from_storage, normalize

    leftovers = [i for i in library if i.bookmark_id not in covered]
    if len(leftovers) < MIN_CLUSTER_SIZE * 2:
        return []

    ids = [i.canonical_id for i in leftovers if i.canonical_id]
    if len(ids) < MIN_CLUSTER_SIZE * 2:
        return []
    placeholders = ",".join(str(int(i)) for i in ids)
    rows = db.execute(sql_text(
        f"SELECT canonical_content_id AS cid, embedding FROM content_embeddings "
        f"WHERE canonical_content_id IN ({placeholders}) AND embedding IS NOT NULL"
    )).mappings().all()
    if len(rows) < MIN_CLUSTER_SIZE * 2:
        return []

    by_canonical = {i.canonical_id: i for i in leftovers}
    vectors, members = [], []
    for r in rows:
        vec = normalize(from_storage(r["embedding"]))
        item = by_canonical.get(r["cid"])
        if vec is None or item is None:
            continue
        vectors.append(vec)
        members.append(item)
    if len(vectors) < MIN_CLUSTER_SIZE * 2:
        return []

    X = np.vstack(vectors).astype(np.float32)
    k = max(2, min(limit, len(X) // MIN_CLUSTER_SIZE))
    labels, centers = _kmeans(X, k)

    router = get_router()
    if router is None or not router.is_available():
        # Without a namer this tier can only produce unnamed clusters, and an
        # unnamed collection is worse than a missing one.
        return []

    from .grouping import Candidate, is_junk_label

    out = []
    for j in range(k):
        idx = [i for i, lab in enumerate(labels) if lab == j]
        if len(idx) < MIN_CLUSTER_SIZE:
            continue
        cohesion = float(np.mean(X[idx] @ centers[j]))
        if cohesion < 0.62:
            continue
        group = [members[i] for i in idx]
        payload = [{"title": m.title, "creator": m.creator, "topics": m.topics}
                   for m in group]
        name, desc = _name_cluster(router, db, user_id, payload)
        if not name or is_junk_label(name):
            continue
        out.append(Candidate(
            signature=f"cluster:{_slug_signature(name)}", label=name,
            members={m.bookmark_id for m in group}, source="cluster",
            strength=0.3 + cohesion))
    return out


def _slug_signature(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())[:60]


def rebuild_auto_collections(db, user_id: int, *,
                             max_collections: int = MAX_AUTO_COLLECTIONS,
                             use_clusters: bool = False) -> Dict[str, Any]:
    """Discover this user's groupings and reconcile them into rows.

    Reconciliation rather than replacement. The previous implementation deleted
    every automatic collection and rebuilt it, which meant ids churned, covers
    were re-picked, and anything the user had done was silently reverted. Here
    a signature that already exists is updated in place, one that has gone is
    removed, and one that is new is created.

    `use_clusters` is off by default, and that default is the finding rather
    than a configuration preference. Enabling it on this library produced "Late
    Night Scroll" (11 items), "Beat Lab", "Sci-Fi & Geek Lore" and "Random
    Fixations" — evocative, and useless for finding anything. The failure is
    structural: a centroid has no name, so a model asked to supply one writes
    atmosphere, and no amount of prompt tightening turns an unnamed cluster into
    a recognisable subject. The named tiers do not have this problem because
    they never invent a name. Left in place, behind the flag, for the day
    embedding coverage is high enough to be worth revisiting.
    """
    from .grouping import MAX_COLLECTIONS, MIN_MEMBERS, discover

    started = time.monotonic()
    timings: Dict[str, int] = {}

    def _mark(name: str, since: float) -> float:
        timings[name] = int((time.monotonic() - since) * 1000)
        return time.monotonic()

    max_collections = max_collections or MAX_COLLECTIONS
    phase = started
    rejected, removed = _feedback(db, user_id)
    phase = _mark("feedback", phase)
    candidates, library = discover(db, user_id, limit=max_collections,
                                   rejected=rejected, removed=removed)
    phase = _mark("discover", phase)

    if not library:
        return {"status": "empty_library", "collections": [],
                "saves_considered": 0, "created": 0, "updated": 0, "removed": 0,
                "timings_ms": {**timings, "total": int((time.monotonic() - started) * 1000)}}

    # How much of the library carries anything to group *on*.
    #
    # Every tier reads a signal: the creator, a hashtag in the title, an entity
    # or topic from the understanding pass. A save whose metadata never landed
    # has none of them, and no threshold change can group it — it is invisible
    # to the algorithm by construction.
    #
    # This is the reported production failure. Thirteen real saves returned
    # `items_covered: 0`, and reproducing that shape locally gave 0 candidates
    # in all five tiers because every save had creator=None, no title and no
    # understanding row. The same thirteen with metadata produce three groups
    # covering nine of them. The grouping was never the problem; the saves had
    # not been read.
    with_signal = sum(1 for i in library
                      if i.creator or i.topics or i.entities or i.hashtags)
    timings["signals"] = with_signal

    # Below the minimum cluster size no grouping can exist, and saying "no new
    # groups" to somebody with two saves is a non-answer — it sounds like Sava
    # looked and found nothing, when in fact there was nothing to look at.
    if len(library) < MIN_MEMBERS:
        return {"status": "not_enough_content", "collections": [],
                "saves_considered": len(library), "minimum": MIN_MEMBERS,
                "created": 0, "updated": 0, "removed": 0,
                "timings_ms": {**timings, "total": int((time.monotonic() - started) * 1000)}}

    if use_clusters and len(candidates) < max_collections:
        covered = {m for c in candidates for m in c.members}
        try:
            extra = _cluster_candidates(
                db, user_id, covered, library,
                limit=max_collections - len(candidates))
        except Exception as e:
            logger.warning("cluster tier failed: %s", e)
            extra = []
        for cand in extra:
            if cand.signature in rejected:
                continue
            cand.members -= removed.get(cand.signature, set())
            if len(cand.members) >= MIN_CLUSTER_SIZE:
                candidates.append(cand)

    # Manual collections are untouchable, including their names — an automatic
    # collection must never collide with one the user made.
    manual = db.query(Collection).filter(Collection.user_id == user_id,
                                         Collection.kind == "manual").all()
    manual_names = {(c.name or "").strip().lower() for c in manual}

    # And their *signatures* are taken too. Renaming an automatic collection
    # converts it to manual but leaves the signature on the row, which records
    # that this grouping already has a home. Without this the next rebuild finds
    # no automatic collection for the signature, decides it is missing, and
    # creates a second collection holding exactly the same saves — the user
    # renames "Kai Cenat Live" and gets a fresh "Kai Cenat Live" beside it.
    claimed_signatures = {c.signature for c in manual if c.signature}

    existing = {c.signature: c for c in db.query(Collection).filter(
        Collection.user_id == user_id, Collection.kind == "auto",
        Collection.signature.isnot(None)).all()}
    existing_ids = {c.id for c in existing.values()}

    kept_signatures = set()
    report: List[Dict[str, Any]] = []
    created_count = 0
    updated_count = 0

    for cand in candidates:
        if cand.label.strip().lower() in manual_names:
            continue
        if cand.signature in claimed_signatures:
            continue
        kept_signatures.add(cand.signature)
        coll = existing.get(cand.signature)

        if coll is None:
            name = _unique_name(db, user_id, cand.label)
            coll = Collection(user_id=user_id, name=name, kind="auto",
                              signature=cand.signature)
            db.add(coll)
            db.commit()
            db.refresh(coll)
            created_count += 1
        elif coll.name != cand.label and _name_free(db, user_id, cand.label, coll.id):
            # The grouping is the same; the best available name for it improved
            # (an acronym expanded, say). Follow it rather than freezing the
            # first name ever chosen.
            coll.name = cand.label
            db.commit()

        # `updated` means an *existing* group's membership moved. A group
        # created moments ago always "changes" when its members are written, and
        # counting that as an update produced "2 new groups, 2 updated" for two
        # brand-new groups.
        was_existing = coll.id in existing_ids
        changed = _sync_members(db, coll, cand.members)
        if changed and was_existing:
            updated_count += 1
        _set_cover(db, coll)
        report.append({"id": coll.id, "name": coll.name, "size": len(cand.members),
                       "source": cand.source, "signature": cand.signature,
                       "created": not was_existing})

    # A signature that no longer describes anything is retired. Its feedback
    # rows stay, so a rejection still holds if the grouping ever returns.
    removed_count = 0
    for signature, coll in existing.items():
        if signature in kept_signatures:
            continue
        db.query(CollectionItem).filter(
            CollectionItem.collection_id == coll.id).delete()
        db.delete(coll)
        removed_count += 1
    db.commit()
    phase = _mark("reconcile", phase)

    _queue_cover_selection(db, [r["id"] for r in report])

    # Nothing to group *yet*, as distinct from nothing to group.
    #
    # "No new groups" implies Sava looked at the content and found no pattern.
    # When almost nothing has been read, the honest answer is that it has not
    # looked yet — and the fix is processing, not a lower threshold.
    if not report and with_signal * 2 < len(library):
        timings["total"] = int((time.monotonic() - started) * 1000)
        logger.info("regroup user=%s saves=%s with_signal=%s removed=%s "
                    "-> awaiting understanding",
                    user_id, len(library), with_signal, removed_count)
        # The real counters, not zeros.
        #
        # Retirement happens above and is already committed by the time this
        # returns, so a run that retires a stale grouping and *then* finds the
        # library unread had genuinely deleted rows — reporting `removed: 0`
        # would have denied it. `proposed` is likewise real: a candidate that
        # collides with a manual name or an already-claimed signature is
        # skipped before it reaches `report`, so `candidates` can be non-empty
        # here. `created` and `updated` are necessarily 0 when `report` is
        # empty, but they are read from the counters rather than asserted, so
        # this stays correct if the loop changes.
        #
        # `items_covered` is included because every other success path returns
        # it, and a client should not have to know which branch it got.
        return {"status": "awaiting_understanding", "collections": [],
                "saves_considered": len(library), "with_signal": with_signal,
                "items_covered": len({m for c in candidates for m in c.members}),
                "proposed": len(candidates), "created": created_count,
                "updated": updated_count, "removed": removed_count,
                "timings_ms": timings}

    timings["total"] = int((time.monotonic() - started) * 1000)
    # Counts, not content: how much was looked at and what changed. No titles,
    # no captions, no transcripts.
    # Counts and reasons, never content: how much was looked at, how much of it
    # carried a usable signal, and what changed.
    logger.info("regrouped user=%s saves=%s with_signal=%s proposed=%s created=%s "
                "updated=%s removed=%s timings=%s",
                user_id, len(library), with_signal, len(candidates),
                created_count, updated_count, removed_count, timings)

    return {"status": "ok", "collections": report,
            "saves_considered": len(library),
            "items_covered": len({m for c in candidates for m in c.members}),
            "proposed": len(candidates),
            "created": created_count, "updated": updated_count,
            "removed": removed_count,
            "timings_ms": timings}


def _queue_cover_selection(db, collection_ids: List[int]) -> None:
    """Ask for covers to be chosen, later and elsewhere.

    Enqueued rather than run inline so a rebuild returns immediately, and keyed
    per collection so replaying a rebuild cannot schedule the same search twice.
    A collection whose cover is still valid costs nothing when the job runs —
    `select_cover` checks before it searches.
    """
    from ..jobs import enqueue
    from . import collection_covers as cover_svc

    for collection_id in collection_ids:
        coll = db.query(Collection).filter(Collection.id == collection_id).first()
        if coll is None or not cover_svc.needs_reselection(db, coll):
            continue
        try:
            enqueue(db, "collection.cover", {"collection_id": collection_id},
                    idempotency_key=f"cover:{collection_id}:"
                                    f"{cover_svc.cover_signature(db, coll)}",
                    priority=200)   # behind anything a user is waiting on
        except Exception as e:
            logger.warning("could not queue cover selection for %s: %s",
                           collection_id, e)


def _name_free(db, user_id: int, name: str, own_id: int) -> bool:
    clash = (db.query(Collection)
             .filter(Collection.user_id == user_id, Collection.name == name,
                     Collection.id != own_id).first())
    return clash is None


def _unique_name(db, user_id: int, base: str) -> str:
    name, n = base, 2
    while db.query(Collection).filter(Collection.user_id == user_id,
                                      Collection.name == name).first():
        name = f"{base} {n}"
        n += 1
    return name


def _sync_members(db, coll: Collection, wanted: set) -> bool:
    """Make membership match, without disturbing anything the user added.

    An item the user put in by hand stays even if the signal no longer picks
    it, and only rows this process added are ever taken away.

    Returns whether anything actually moved, so the caller can tell the user
    "no new groups" instead of implying work happened when nothing changed.
    """
    rows = db.query(CollectionItem).filter(
        CollectionItem.collection_id == coll.id).all()
    current_auto = {r.bookmark_id for r in rows if r.added_by == "auto"}
    manual = {r.bookmark_id for r in rows if r.added_by != "auto"}

    added = wanted - current_auto - manual
    dropped = current_auto - wanted

    for bid in added:
        db.add(CollectionItem(collection_id=coll.id, bookmark_id=bid,
                              added_by="auto", score=1.0))
    for bid in dropped:
        db.query(CollectionItem).filter(
            CollectionItem.collection_id == coll.id,
            CollectionItem.bookmark_id == bid,
            CollectionItem.added_by == "auto").delete()
    db.commit()
    return bool(added or dropped)


def list_collections(db, user_id: int) -> List[Dict[str, Any]]:
    rows = db.execute(sql_text("""
        SELECT c.id, c.name, c.kind, c.signature, c.description,
               c.cover_bookmark_id, c.cover_url, c.cover_mosaic, c.cover_source,
               c.created_at, COUNT(ci.bookmark_id) AS n
        FROM collections c
        LEFT JOIN collection_items ci ON ci.collection_id = c.id
        WHERE c.user_id = :uid
        GROUP BY c.id, c.name, c.kind, c.signature, c.description,
                 c.cover_bookmark_id, c.cover_url, c.cover_mosaic, c.cover_source,
                 c.created_at
        HAVING COUNT(ci.bookmark_id) > 0 OR c.kind = 'manual'
        ORDER BY n DESC, c.name ASC
    """), {"uid": user_id}).mappings().all()

    out = []
    for r in rows:
        # A collection's cover is built from the media actually inside it — a
        # folder glyph would tell the user nothing. Up to four real thumbnails,
        # the designated cover first when one has been chosen.
        # Covers are ordered by how likely the image is to still exist, not just
        # by how representative it is. A signed TikTok or Instagram CDN URL
        # expires within days, so a mosaic built from "best" members was coming
        # back with holes in it — a collection of five YouTube saves and one
        # TikTok would front itself with the one dead image. Durable sources
        # first, expiring ones only if nothing else is available.
        covers = [t for (t,) in db.execute(sql_text("""
            SELECT COALESCE(cc.thumbnail_url, b.thumbnail_url) AS thumb
            FROM collection_items ci
            JOIN bookmarks b ON b.id = ci.bookmark_id
            LEFT JOIN canonical_content cc ON cc.id = b.canonical_content_id
            WHERE ci.collection_id = :c
              AND COALESCE(cc.thumbnail_url, b.thumbnail_url) IS NOT NULL
              AND COALESCE(cc.thumbnail_url, b.thumbnail_url) != ''
            ORDER BY
              (b.id = COALESCE(:cover, -1)) DESC,
              CASE
                WHEN cc.thumbnail_stored_key IS NOT NULL THEN 0
                WHEN COALESCE(cc.thumbnail_url, b.thumbnail_url) LIKE '%ytimg.com%' THEN 1
                WHEN COALESCE(cc.thumbnail_url, b.thumbnail_url) LIKE '%x-expires%' THEN 3
                WHEN COALESCE(cc.thumbnail_url, b.thumbnail_url) LIKE '%tiktokcdn%' THEN 3
                WHEN COALESCE(cc.thumbnail_url, b.thumbnail_url) LIKE '%cdninstagram%' THEN 3
                WHEN COALESCE(cc.thumbnail_url, b.thumbnail_url) LIKE '%fbcdn.net%' THEN 3
                ELSE 2
              END ASC,
              ci.score DESC, b.created_at DESC
            LIMIT 4
        """), {"c": r["id"], "cover": r["cover_bookmark_id"]}).all()]

        # A selected cover wins over member thumbnails. It was chosen for this
        # collection rather than merely belonging to it, it is already mirrored
        # into Sava's storage, and it does not change when the membership does.
        selected: List[str] = []
        if r["cover_mosaic"]:
            try:
                selected = [u for u in json.loads(r["cover_mosaic"]) if u]
            except Exception:
                selected = []
        elif r["cover_url"]:
            selected = [r["cover_url"]]

        out.append({"id": r["id"], "name": r["name"], "kind": r["kind"],
                    "signature": r["signature"],
                    "description": r["description"], "count": int(r["n"]),
                    "cover_source": r["cover_source"] or "automatic",
                    "cover_thumbnail_url": (selected or covers or [None])[0],
                    "cover_thumbnails": selected or covers,
                    "created_at": r["created_at"].isoformat()
                    if hasattr(r["created_at"], "isoformat") else r["created_at"]})
    return out
