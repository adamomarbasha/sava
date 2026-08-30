"""Retrieval. Everything generative depends on this, and search depends on it alone.

Search and Ask Sava are separate experiences built on the same index:
  * **Search** is retrieval only — no model runs. It must feel instant.
  * **Ask Sava** retrieves first, then hands *only* the selected context to a
    model. A user's library is never dumped into a prompt.

Scoping is enforced in SQL: a user can only ever retrieve canonical content
they have personally saved.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import text as sql_text

from ..models import (
    Bookmark, CanonicalContent, ContentChunk, ContentUnderstanding,
)
from ..vectors import from_storage, knn, mmr, normalize

logger = logging.getLogger(__name__)

# A hit must score at least this fraction of the best hit to count as a match.
RELEVANCE_RATIO = 0.55

#: The score floor for any result with a literal match. Above 0.72, which is the
#: most a perfect semantic similarity can contribute, so lexical hits are always
#: ranked ahead of similarity-only ones and are never cut by the relevance floor.
LEXICAL_BAND = 1.0

_USER_SCOPE = (
    "canonical_content_id IN (SELECT canonical_content_id FROM bookmarks "
    "WHERE user_id = :uid AND canonical_content_id IS NOT NULL)"
)


@dataclass
class RetrievedSave:
    bookmark_id: int
    canonical_id: int
    title: Optional[str]
    creator: Optional[str]
    platform: str
    url: str
    thumbnail_url: Optional[str]
    note: Optional[str]
    tl_dr: Optional[str] = None
    topics: List[str] = field(default_factory=list)
    content_type: Optional[str] = None
    score: float = 0.0
    matched_on: List[str] = field(default_factory=list)
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.bookmark_id,
            "canonical_id": self.canonical_id,
            "title": self.title,
            "author": self.creator,
            "platform": self.platform,
            "url": self.url,
            "thumbnail_url": self.thumbnail_url,
            "note": self.note,
            "tl_dr": self.tl_dr,
            "topics": self.topics,
            "content_type": self.content_type,
            "score": round(self.score, 4),
            "matched_on": self.matched_on,
            "created_at": self.created_at,
        }


def _embed_query(query: str):
    from ..ai.router import get_router
    router = get_router()
    if not router.is_available():
        return None
    try:
        res = router.embed([query], task_type="retrieval_query")
        return res.vectors[0] if res.vectors else None
    except Exception as e:
        logger.warning("query embedding failed: %s", e)
        return None


def _keyword_scores(db, user_id: int, query: str, limit: int = 200) -> Dict[int, float]:
    """Lexical recall. Guarantees exact-token matches survive vector ranking."""
    q = (query or "").strip().lower()
    if not q:
        return {}
    tokens = [t for t in re.findall(r"[a-z0-9']{2,}", q)][:8]
    if not tokens:
        return {}

    like_clauses, params = [], {"uid": user_id}
    for i, tok in enumerate(tokens):
        params[f"t{i}"] = f"%{tok}%"
        like_clauses.append(
            f"(LOWER(COALESCE(cc.title,'')) LIKE :t{i} OR "
            f" LOWER(COALESCE(cc.description,'')) LIKE :t{i} OR "
            f" LOWER(COALESCE(cc.creator_name,'')) LIKE :t{i} OR "
            f" LOWER(COALESCE(cc.creator_handle,'')) LIKE :t{i} OR "
            f" LOWER(COALESCE(b.note,'')) LIKE :t{i} OR "
            f" LOWER(COALESCE(u.topics,'')) LIKE :t{i} OR "
            f" LOWER(COALESCE(u.entities,'')) LIKE :t{i} OR "
            f" LOWER(COALESCE(u.tl_dr,'')) LIKE :t{i})"
        )
    params["lim"] = limit

    rows = db.execute(sql_text(f"""
        SELECT cc.id AS cid,
               ({' + '.join(f'CASE WHEN {c} THEN 1 ELSE 0 END' for c in like_clauses)}) AS hits,
               CASE WHEN LOWER(COALESCE(cc.title,'')) LIKE :t0 THEN 1 ELSE 0 END AS title_hit
        FROM bookmarks b
        JOIN canonical_content cc ON cc.id = b.canonical_content_id
        LEFT JOIN content_understanding u ON u.canonical_content_id = cc.id
        WHERE b.user_id = :uid AND ({' OR '.join(like_clauses)})
        LIMIT :lim
    """), params).mappings().all()

    n = max(1, len(tokens))
    return {int(r["cid"]): min(1.0, (r["hits"] / n) * 0.85 + r["title_hit"] * 0.15)
            for r in rows}


def _load_saves(db, user_id: int, canonical_ids: Sequence[int]) -> Dict[int, RetrievedSave]:
    if not canonical_ids:
        return {}
    rows = (
        db.query(Bookmark, CanonicalContent, ContentUnderstanding)
        .join(CanonicalContent, CanonicalContent.id == Bookmark.canonical_content_id)
        .outerjoin(ContentUnderstanding,
                   ContentUnderstanding.canonical_content_id == CanonicalContent.id)
        .filter(Bookmark.user_id == user_id,
                Bookmark.canonical_content_id.in_(list(canonical_ids)))
        .all()
    )
    out: Dict[int, RetrievedSave] = {}
    for bm, cc, und in rows:
        topics = []
        if und and und.topics:
            try:
                topics = json.loads(und.topics)
            except Exception:
                pass
        out[cc.id] = RetrievedSave(
            bookmark_id=bm.id, canonical_id=cc.id,
            title=cc.title or bm.title, creator=cc.creator_name or bm.author,
            platform=cc.platform or bm.platform, url=bm.url,
            thumbnail_url=cc.thumbnail_url or bm.thumbnail_url, note=bm.note,
            tl_dr=(und.tl_dr if und else None), topics=topics,
            content_type=cc.content_type,
            created_at=bm.created_at.isoformat() if bm.created_at else None,
        )
    return out


def search_library(
    db,
    user_id: int,
    query: str,
    *,
    limit: int = 30,
    platform: Optional[str] = None,
    content_type: Optional[str] = None,
    diversify: bool = True,
    restrict_to: Optional[Set[int]] = None,
) -> List[RetrievedSave]:
    """Hybrid search. No generative model runs here — this must stay fast.

    `restrict_to` limits the candidate set to a specific group of canonical ids,
    which is how a collection-scoped Ask stays inside its collection instead of
    quietly answering from the whole library.
    """
    query = (query or "").strip()

    if not query:
        rows = (db.query(Bookmark, CanonicalContent)
                .outerjoin(CanonicalContent, CanonicalContent.id == Bookmark.canonical_content_id)
                .filter(Bookmark.user_id == user_id)
                .order_by(Bookmark.created_at.desc()).limit(limit * 4 if restrict_to else limit)
                .all())
        if restrict_to is not None:
            rows = [(bm, cc) for bm, cc in rows
                    if cc is not None and cc.id in restrict_to][:limit]
        out = []
        for bm, cc in rows:
            out.append(RetrievedSave(
                bookmark_id=bm.id, canonical_id=cc.id if cc else 0,
                title=(cc.title if cc else None) or bm.title,
                creator=(cc.creator_name if cc else None) or bm.author,
                platform=(cc.platform if cc else None) or bm.platform, url=bm.url,
                thumbnail_url=(cc.thumbnail_url if cc else None) or bm.thumbnail_url,
                note=bm.note, content_type=cc.content_type if cc else None,
                created_at=bm.created_at.isoformat() if bm.created_at else None,
            ))
        return out

    semantic: Dict[int, float] = {}
    qvec = _embed_query(query)
    if qvec is not None:
        for cid, sim in knn(
            db, table="content_embeddings", vector_column="embedding",
            id_column="canonical_content_id", query_vec=qvec,
            k=max(limit * 3, 60), where_sql=_USER_SCOPE, params={"uid": user_id},
        ):
            semantic[cid] = sim

    keyword = _keyword_scores(db, user_id, query)

    candidates = set(semantic) | set(keyword)
    if restrict_to is not None:
        candidates &= restrict_to

    # Lexical matches occupy a band above every pure-semantic score.
    #
    # The old formula was `0.72*s + 0.42*k`, which let a semantic near-miss
    # outrank a literal one: an unrelated video scoring 0.8 similarity landed at
    # 0.576, while a save whose *title contains the query word* scored 0.42 with
    # no vector hit. Searching "Speed" therefore ranked a saved TikTok titled
    # "Speed was convinced…" below things that merely felt related — and the
    # relevance floor below then cut it entirely.
    #
    # Someone who types a word that is literally in a title is not asking for
    # word2vec. Any lexical hit starts at LEXICAL_BAND, above the 0.72 ceiling a
    # perfect semantic score can reach, so it can never be displaced by
    # similarity alone. Within the band, lexical quality leads and semantic
    # similarity breaks ties.
    fused: Dict[int, float] = {}
    for cid in candidates:
        s, k = semantic.get(cid, 0.0), keyword.get(cid, 0.0)
        if k > 0:
            fused[cid] = LEXICAL_BAND + 0.42 * k + 0.30 * s
        else:
            fused[cid] = 0.72 * s

    if not fused:
        return []

    ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)

    # Relevance floor, relative to the best match.
    #
    # Embeddings never score zero: an unrelated video still lands around 0.36
    # against any query, so an unfiltered top-10 is one real answer followed by
    # nine coincidences. That is visible twice — as junk under "Also related" in
    # search, and as context the model has to talk its way out of in Ask ("only
    # one of these is about food"). A *relative* floor adapts to the query: a
    # sharp question has one dominant score and everything else falls away, while
    # a broad one has a flat distribution and keeps its whole set.
    # The floor applies to the semantic tail only, and is measured against the
    # best *semantic* score rather than the overall best.
    #
    # Measuring against the overall best would mean one lexical hit (>= 1.0)
    # raising the bar to ~0.77 and silently deleting every semantic result,
    # since a perfect semantic score is only 0.72. And filtering lexical hits at
    # all is what hid the literal title match in the first place: a save that
    # contains the query word is a result, however lonely its score looks next
    # to a stronger one.
    lexical = [(cid, score) for cid, score in ranked if cid in keyword]
    semantic_only = [(cid, score) for cid, score in ranked if cid not in keyword]
    if semantic_only:
        best_semantic = semantic_only[0][1]
        semantic_only = [(cid, score) for cid, score in semantic_only
                         if score >= max(0.0, best_semantic * RELEVANCE_RATIO)]
    ranked = (lexical + semantic_only) or ranked[:1]
    ranked = ranked[: limit * 3]

    # Diversify the semantic tail only. MMR trades relevance for variety, which
    # is right for "things like this" and wrong for "the one containing this
    # word" — a lexical hit demoted for resembling another lexical hit is the
    # result the user came for, pushed off the end of the list.
    if diversify and qvec is not None and len(ranked) > limit:
        keep = lexical[:limit]
        remaining = limit - len(keep)
        if remaining > 0 and semantic_only:
            vecs = {}
            for cid, _ in semantic_only:
                row = db.execute(sql_text(
                    "SELECT embedding FROM content_embeddings WHERE canonical_content_id = :c"
                ), {"c": cid}).first()
                if row and row[0] is not None:
                    v = normalize(from_storage(row[0]))
                    if v is not None:
                        vecs[cid] = v
            keep = keep + mmr(semantic_only, vecs, k=remaining, lambda_=0.75)
        ranked = keep[:limit]
    else:
        ranked = ranked[:limit]

    saves = _load_saves(db, user_id, [cid for cid, _ in ranked])
    results: List[RetrievedSave] = []
    for cid, score in ranked:
        save = saves.get(cid)
        if save is None:
            continue
        if platform and save.platform != platform:
            continue
        if content_type and save.content_type != content_type:
            continue
        save.score = score
        save.matched_on = [m for m, present in
                           (("semantic", cid in semantic), ("keyword", cid in keyword))
                           if present]
        results.append(save)
    return results


def retrieve_chunks(
    db, canonical_id: int, query: str, *, k: int = 6
) -> List[Dict[str, Any]]:
    """Top-k chunks within ONE piece of content. Powers Ask This.

    Reads persisted chunks — it never re-acquires or re-transcribes the media.
    """
    qvec = _embed_query(query)
    if qvec is not None:
        hits = knn(
            db, table="content_chunks", vector_column="embedding", id_column="id",
            query_vec=qvec, k=k,
            where_sql="canonical_content_id = :cid", params={"cid": canonical_id},
        )
        if hits:
            rows = {c.id: c for c in db.query(ContentChunk)
                    .filter(ContentChunk.id.in_([h[0] for h in hits])).all()}
            return [{
                "chunk_id": cid, "text": rows[cid].text, "start_s": rows[cid].start_s,
                "end_s": rows[cid].end_s, "modality": rows[cid].modality,
                "score": round(sim, 4),
            } for cid, sim in hits if cid in rows]

    # Lexical fallback when embeddings are unavailable.
    ql = (query or "").lower()
    chunks = (db.query(ContentChunk)
              .filter(ContentChunk.canonical_content_id == canonical_id)
              .order_by(ContentChunk.chunk_index).all())
    scored = [c for c in chunks if any(t in c.text.lower()
                                       for t in re.findall(r"[a-z0-9']{3,}", ql))]
    picked = (scored or chunks)[:k]
    return [{"chunk_id": c.id, "text": c.text, "start_s": c.start_s, "end_s": c.end_s,
             "modality": c.modality, "score": 0.0} for c in picked]


def retrieve_for_library_question(
    db, user_id: int, question: str, *, max_saves: int = 10, chunks_per_save: int = 2,
    restrict_to: Optional[Set[int]] = None,
) -> Dict[str, Any]:
    """Two-stage retrieval for Ask Sava: find the saves, then the passages.

    Only the selected passages reach the model — never the whole library.
    """
    saves = search_library(db, user_id, question, limit=max_saves, diversify=True,
                           restrict_to=restrict_to)
    if not saves:
        return {"saves": [], "context_blocks": [], "canonical_ids": []}

    blocks: List[Dict[str, Any]] = []
    for save in saves:
        pieces = retrieve_chunks(db, save.canonical_id, question, k=chunks_per_save)
        blocks.append({
            "bookmark_id": save.bookmark_id,
            "canonical_id": save.canonical_id,
            "title": save.title,
            "creator": save.creator,
            "platform": save.platform,
            "content_type": save.content_type,
            "note": save.note,
            "tl_dr": save.tl_dr,
            "topics": save.topics,
            "excerpts": pieces,
        })
    return {
        "saves": saves,
        "context_blocks": blocks,
        "canonical_ids": [s.canonical_id for s in saves],
    }


def related_saves(db, user_id: int, canonical_id: int, *, limit: int = 8
                  ) -> List[RetrievedSave]:
    """Semantically similar saves. Pure vector similarity — zero LLM calls."""
    row = db.execute(sql_text(
        "SELECT embedding FROM content_embeddings WHERE canonical_content_id = :c"
    ), {"c": canonical_id}).first()
    if not row or row[0] is None:
        return []
    vec = from_storage(row[0])
    hits = knn(
        db, table="content_embeddings", vector_column="embedding",
        id_column="canonical_content_id", query_vec=vec, k=limit + 1,
        where_sql=_USER_SCOPE, params={"uid": user_id},
    )
    hits = [(cid, s) for cid, s in hits if cid != canonical_id][:limit]
    saves = _load_saves(db, user_id, [c for c, _ in hits])
    out = []
    for cid, score in hits:
        if cid in saves:
            saves[cid].score = score
            saves[cid].matched_on = ["semantic"]
            out.append(saves[cid])
    return out
