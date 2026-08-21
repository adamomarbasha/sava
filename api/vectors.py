"""Portable vector storage and retrieval.

Production runs PostgreSQL + pgvector with an HNSW index and does the nearest-
neighbour search *in the database*. Local development runs SQLite, where vectors
are stored as packed float32 blobs (4 bytes/dim — versus ~12 bytes/dim as JSON
text) and searched with a vectorised NumPy dot product.

Both paths return the same thing, so callers never branch on the engine. What
they must never do is what the previous implementation did: load every row into
Python and cosine them one at a time in a for-loop.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Tuple

import numpy as np
from sqlalchemy import LargeBinary, text
from sqlalchemy.types import TypeDecorator

from .config import EMBED_DIM, IS_POSTGRES

logger = logging.getLogger(__name__)


class PackedVector(TypeDecorator):
    """float32 vector <-> BLOB. Used on SQLite."""

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return np.asarray(value, dtype=np.float32).tobytes()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return np.frombuffer(value, dtype=np.float32)


if IS_POSTGRES:  # pragma: no cover - exercised only against a live Postgres
    from pgvector.sqlalchemy import Vector as _PGVector

    def VectorColumn(dim: int = EMBED_DIM):
        return _PGVector(dim)
else:
    def VectorColumn(dim: int = EMBED_DIM):
        return PackedVector()


def _pgvector_literal(vec) -> str:
    """pgvector's text input format: `[0.1,0.2,...]`."""
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"


def normalize(vec: Sequence[float]) -> Optional[np.ndarray]:
    """L2-normalize so cosine similarity reduces to a dot product.

    Required for correctness: gemini-embedding-001 only pre-normalizes its full
    3072-dim output. Any Matryoshka-truncated vector must be normalized by us.
    """
    if vec is None:
        return None
    a = np.asarray(vec, dtype=np.float32)
    if a.size == 0:
        return None
    n = float(np.linalg.norm(a))
    if n == 0.0:
        return None
    return (a / n).astype(np.float32)


def to_storage(vec) -> Optional[object]:
    """Prepare a vector for the DB column (normalized)."""
    v = normalize(vec)
    if v is None:
        return None
    return v.tolist() if IS_POSTGRES else v


def from_storage(value) -> Optional[np.ndarray]:
    """Decode a stored vector.

    Handles both shapes we can get back: a packed float32 blob (SQLite, and any
    read that goes through raw SQL rather than the ORM type decorator) and a
    plain sequence of floats (pgvector, or an already-decoded column).
    """
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return np.frombuffer(bytes(value), dtype=np.float32)
    if isinstance(value, str):
        # pgvector's text output form, `[0.1,0.2,...]`, which is what comes back
        # when a column is read through raw SQL instead of the ORM type — the
        # path `related_saves` takes. Without this the whole bracketed string was
        # handed to numpy as a single value and raised ValueError, so every
        # related-saves lookup failed on Postgres.
        text_value = value.strip()
        if text_value.startswith("[") and text_value.endswith("]"):
            inner = text_value[1:-1].strip()
            if not inner:
                return np.empty(0, dtype=np.float32)
            return np.fromstring(inner, dtype=np.float32, sep=",")
        raise ValueError(f"unrecognised embedding text form: {text_value[:32]!r}")
    return np.asarray(value, dtype=np.float32)


# ─── Search ──────────────────────────────────────────────────────────────────

def knn(
    db,
    *,
    table: str,
    vector_column: str,
    id_column: str,
    query_vec: Sequence[float],
    k: int = 20,
    where_sql: str = "",
    params: Optional[dict] = None,
) -> List[Tuple[int, float]]:
    """Return [(id, similarity)] ordered by descending cosine similarity.

    Postgres: pgvector `<=>` (cosine distance) with an HNSW index — the ordering
    and limit happen in the database.
    SQLite: one bulk fetch of the candidate set followed by a single matmul.
    """
    q = normalize(query_vec)
    if q is None:
        return []
    params = dict(params or {})

    if IS_POSTGRES:
        # The parameter is bound as *text* and cast, not passed as a Python list.
        #
        # psycopg2 adapts a list to a Postgres array, so `embedding <=> :qvec`
        # became `vector <=> numeric[]` — an operator that does not exist. Every
        # semantic search on Postgres raised UndefinedFunction, and nothing
        # caught it because this branch was only reachable on Postgres and the
        # suite ran on SQLite. (It carried a `# pragma: no cover` marker saying
        # exactly that.) pgvector's own text form, '[1,2,3]'::vector, is the
        # documented way to bind one.
        clause = f"WHERE {where_sql}" if where_sql else ""
        sql = text(
            f"SELECT {id_column} AS id, "
            f"1 - ({vector_column} <=> CAST(:qvec AS vector)) AS sim "
            f"FROM {table} {clause} "
            f"{'AND' if where_sql else 'WHERE'} {vector_column} IS NOT NULL "
            f"ORDER BY {vector_column} <=> CAST(:qvec AS vector) LIMIT :k"
        )
        params.update({"qvec": _pgvector_literal(q), "k": k})
        return [(int(r.id), float(r.sim)) for r in db.execute(sql, params)]

    clause = f"WHERE {where_sql} AND" if where_sql else "WHERE"
    sql = text(
        f"SELECT {id_column} AS id, {vector_column} AS vec "
        f"FROM {table} {clause} {vector_column} IS NOT NULL"
    )
    rows = db.execute(sql, params).fetchall()
    if not rows:
        return []

    ids = np.empty(len(rows), dtype=np.int64)
    mat = np.empty((len(rows), q.shape[0]), dtype=np.float32)
    n = 0
    for r in rows:
        v = from_storage(r.vec)
        if v is None:
            continue
        if v.shape[0] != q.shape[0]:
            continue  # stale dimension from an older embedding model
        ids[n] = r.id
        mat[n] = v
        n += 1
    if n == 0:
        return []

    sims = mat[:n] @ q                      # rows are already normalized
    top = np.argsort(-sims)[:k]
    return [(int(ids[i]), float(sims[i])) for i in top]


def mmr(
    candidates: List[Tuple[int, float]],
    vectors: dict,
    *,
    k: int,
    lambda_: float = 0.7,
) -> List[Tuple[int, float]]:
    """Maximal Marginal Relevance — trades relevance against diversity.

    Ask Sava over a library full of near-duplicate saves otherwise returns the
    same video five times. Deterministic; no model involved.
    """
    if not candidates:
        return []
    selected: List[Tuple[int, float]] = []
    pool = list(candidates)
    while pool and len(selected) < k:
        best_i, best_score = 0, -1e9
        for i, (cid, rel) in enumerate(pool):
            if not selected:
                score = rel
            else:
                cv = vectors.get(cid)
                if cv is None:
                    score = lambda_ * rel
                else:
                    max_sim = max(
                        float(cv @ vectors[sid]) if vectors.get(sid) is not None else 0.0
                        for sid, _ in selected
                    )
                    score = lambda_ * rel - (1 - lambda_) * max_sim
            if score > best_score:
                best_i, best_score = i, score
        selected.append(pool.pop(best_i))
    return selected
