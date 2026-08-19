"""Collections — manual and automatic.

Automatic collections must reflect *this* user's actual interests. Someone who
never saves cooking content must never be handed a "Recipes" collection. That
rules out a fixed taxonomy, so grouping is discovered from the user's own
embeddings.

The clustering itself uses no model — it is k-means over vectors we already
store. A cheap model is used only to put a human name on a cluster once it
exists, which costs a fraction of a cent per rebuild.
"""
from __future__ import annotations

import json
import logging
import re
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


def rebuild_auto_collections(db, user_id: int, *, max_collections: int = MAX_AUTO_COLLECTIONS
                             ) -> Dict[str, Any]:
    """Discover this user's natural groupings and materialise them."""
    from ..ai.router import get_router

    rows = db.execute(sql_text("""
        SELECT b.id AS bid, cc.id AS cid, cc.title, cc.creator_name, cc.content_type,
               e.embedding, u.topics
        FROM bookmarks b
        JOIN canonical_content cc ON cc.id = b.canonical_content_id
        JOIN content_embeddings e ON e.canonical_content_id = cc.id
        LEFT JOIN content_understanding u ON u.canonical_content_id = cc.id
        WHERE b.user_id = :uid AND e.embedding IS NOT NULL
    """), {"uid": user_id}).mappings().all()

    if len(rows) < MIN_SAVES_FOR_AUTO:
        return {"status": "not_enough_saves", "saves": len(rows),
                "required": MIN_SAVES_FOR_AUTO}

    vectors, items = [], []
    for r in rows:
        v = normalize(from_storage(r["embedding"]))
        if v is None:
            continue
        vectors.append(v)
        topics = []
        if r["topics"]:
            try:
                topics = json.loads(r["topics"])
            except Exception:
                pass
        items.append({"bookmark_id": r["bid"], "canonical_id": r["cid"],
                      "title": r["title"], "creator": r["creator_name"],
                      "content_type": r["content_type"], "topics": topics})
    if len(vectors) < MIN_SAVES_FOR_AUTO:
        return {"status": "not_enough_embeddings", "saves": len(vectors)}

    X = np.vstack(vectors).astype(np.float32)
    k = max(2, min(max_collections, len(X) // MIN_CLUSTER_SIZE))
    labels, centers = _kmeans(X, k)

    router = get_router()
    # Replace previous auto collections; manual ones are never touched.
    old = db.query(Collection).filter(Collection.user_id == user_id,
                                      Collection.kind == "auto").all()
    for c in old:
        db.query(CollectionItem).filter(CollectionItem.collection_id == c.id).delete()
        db.delete(c)
    db.commit()

    created: List[Dict[str, Any]] = []
    for j in range(k):
        member_idx = [i for i, lab in enumerate(labels) if lab == j]
        if len(member_idx) < MIN_CLUSTER_SIZE:
            continue
        members = [items[i] for i in member_idx]
        # Cohesion guard: a loose cluster is noise, not a collection.
        cohesion = float(np.mean(X[member_idx] @ centers[j]))
        if cohesion < 0.55:
            continue

        name, desc = _name_cluster(router, db, user_id, members)
        base, n = name, 2
        while db.query(Collection).filter(Collection.user_id == user_id,
                                          Collection.name == name).first():
            name = f"{base} {n}"
            n += 1

        coll = Collection(user_id=user_id, name=name, kind="auto", description=desc,
                          embedding=to_storage(centers[j]))
        db.add(coll)
        db.commit()
        db.refresh(coll)
        for m in members:
            db.add(CollectionItem(collection_id=coll.id, bookmark_id=m["bookmark_id"],
                                  added_by="auto", score=cohesion))
        db.commit()
        _set_cover(db, coll)
        created.append({"id": coll.id, "name": name, "size": len(members),
                        "cohesion": round(cohesion, 3)})

    return {"status": "ok", "clusters": len(created), "collections": created,
            "saves_considered": len(X)}


def list_collections(db, user_id: int) -> List[Dict[str, Any]]:
    rows = db.execute(sql_text("""
        SELECT c.id, c.name, c.kind, c.description, c.cover_bookmark_id,
               c.created_at, COUNT(ci.bookmark_id) AS n
        FROM collections c
        LEFT JOIN collection_items ci ON ci.collection_id = c.id
        WHERE c.user_id = :uid
        GROUP BY c.id, c.name, c.kind, c.description, c.cover_bookmark_id, c.created_at
        ORDER BY c.kind ASC, n DESC
    """), {"uid": user_id}).mappings().all()

    out = []
    for r in rows:
        cover = None
        if r["cover_bookmark_id"]:
            cov = db.execute(sql_text("""
                SELECT COALESCE(cc.thumbnail_url, b.thumbnail_url) AS thumb
                FROM bookmarks b LEFT JOIN canonical_content cc
                  ON cc.id = b.canonical_content_id WHERE b.id = :b
            """), {"b": r["cover_bookmark_id"]}).first()
            cover = cov[0] if cov else None
        out.append({"id": r["id"], "name": r["name"], "kind": r["kind"],
                    "description": r["description"], "count": int(r["n"]),
                    "cover_thumbnail_url": cover,
                    "created_at": r["created_at"].isoformat()
                    if hasattr(r["created_at"], "isoformat") else r["created_at"]})
    return out
