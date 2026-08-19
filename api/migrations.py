"""Idempotent schema migrations.

Deliberately not Alembic. The live schema has already drifted from `models.py`
(the root database contains `platform='web'`, which the model's CHECK constraint
forbids), and there are two SQLite files in the tree. Retrofitting Alembic to a
drifted schema would require a hand-written baseline revision that lies about
the current state — the exact situation Alembic is bad at.

What this does instead:
  * `create_all()` for new tables — inherently idempotent, never touches or
    rewrites an existing table, so existing user data cannot be harmed.
  * introspect-then-ALTER for new columns on existing tables.
  * Postgres-only: pgvector extension + HNSW indexes.

Every step is safe to run repeatedly and safe to run on a database that is
already partially migrated.
"""
from __future__ import annotations

import logging
from typing import List

from sqlalchemy import inspect, text

from .config import EMBED_DIM, IS_POSTGRES
from .models import Base

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 4


def _existing_columns(conn, table: str) -> set:
    try:
        return {c["name"] for c in inspect(conn).get_columns(table)}
    except Exception:
        return set()


def _has_table(conn, table: str) -> bool:
    try:
        return inspect(conn).has_table(table)
    except Exception:
        return False


def _add_column(conn, table: str, column: str, ddl: str) -> bool:
    """ALTER TABLE ... ADD COLUMN, only if missing. Returns True if added."""
    if not _has_table(conn, table):
        return False
    if column in _existing_columns(conn, table):
        return False
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    logger.info("migration: added %s.%s", table, column)
    return True


def _ensure_index(conn, name: str, ddl: str) -> None:
    try:
        conn.execute(text(ddl))
    except Exception as e:
        # Index already present, or the engine reports a benign duplicate.
        logger.debug("index %s skipped: %s", name, e)


def run_migrations(engine) -> List[str]:
    """Bring the database up to SCHEMA_VERSION. Returns applied step names."""
    applied: List[str] = []

    # ── Postgres prerequisites ───────────────────────────────────────────────
    if IS_POSTGRES:  # pragma: no cover - needs a live Postgres
        with engine.begin() as conn:
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                applied.append("pgvector-extension")
            except Exception as e:
                logger.error(
                    "Could not enable pgvector (%s). Vector columns will fail. "
                    "Install pgvector on the server or use a provider that ships it.", e
                )

    # ── New tables ───────────────────────────────────────────────────────────
    before = set(inspect(engine).get_table_names())
    Base.metadata.create_all(bind=engine)
    after = set(inspect(engine).get_table_names())
    for t in sorted(after - before):
        applied.append(f"create-table:{t}")
        logger.info("migration: created table %s", t)

    # ── New columns on existing tables ───────────────────────────────────────
    with engine.begin() as conn:
        if _add_column(conn, "bookmarks", "canonical_content_id", "INTEGER"):
            applied.append("bookmarks.canonical_content_id")
        if _add_column(conn, "bookmarks", "processing_state", "VARCHAR(16)"):
            applied.append("bookmarks.processing_state")
        if _add_column(conn, "jobs", "platform", "VARCHAR(20)"):
            applied.append("jobs.platform")

    # Legacy schemas declared bookmarks.url globally UNIQUE, which prevents two
    # users from saving the same public video. Detect and drop it — the correct
    # constraint is (user_id, url).
    with engine.begin() as conn:
        try:
            for idx in inspect(conn).get_indexes("bookmarks"):
                cols = idx.get("column_names") or []
                if idx.get("unique") and cols == ["url"]:
                    conn.execute(text(f'DROP INDEX IF EXISTS "{idx["name"]}"'))
                    applied.append("drop-global-unique:bookmarks.url")
                    logger.warning(
                        "dropped legacy global UNIQUE index %s on bookmarks.url — "
                        "it blocked cross-user saves of the same content", idx["name"])
        except Exception as e:
            logger.debug("bookmarks.url index check skipped: %s", e)

    with engine.begin() as conn:
        _ensure_index(
            conn, "idx_bookmarks_canonical",
            "CREATE INDEX IF NOT EXISTS idx_bookmarks_canonical "
            "ON bookmarks (canonical_content_id)",
        )
        _ensure_index(
            conn, "idx_bookmarks_user_url",
            "CREATE INDEX IF NOT EXISTS idx_bookmarks_user_url ON bookmarks (user_id, url)",
        )

    # ── Vector indexes (Postgres only; SQLite uses a NumPy scan) ─────────────
    if IS_POSTGRES:  # pragma: no cover
        with engine.begin() as conn:
            for table, col in (("content_chunks", "embedding"),
                               ("content_embeddings", "embedding"),
                               ("collections", "embedding")):
                _ensure_index(
                    conn, f"hnsw_{table}",
                    f"CREATE INDEX IF NOT EXISTS hnsw_{table}_{col} ON {table} "
                    f"USING hnsw ({col} vector_cosine_ops) "
                    f"WITH (m = 16, ef_construction = 64)",
                )
                applied.append(f"hnsw:{table}")

    # ── Full-text support for the keyword half of hybrid search ─────────────
    if IS_POSTGRES:  # pragma: no cover
        with engine.begin() as conn:
            _ensure_index(
                conn, "idx_cc_fts",
                "CREATE INDEX IF NOT EXISTS idx_cc_fts ON canonical_content "
                "USING gin (to_tsvector('english', "
                "coalesce(title,'') || ' ' || coalesce(description,'')))",
            )

    if applied:
        logger.info("migrations applied: %s", ", ".join(applied))
    else:
        logger.info("migrations: schema already current (v%d)", SCHEMA_VERSION)
    return applied


def backfill_canonical_content(db, limit: int = 5000) -> dict:
    """Attach existing bookmarks to canonical content rows.

    Metadata-only: it derives identity from the URL that is already stored and
    copies across the title/author/thumbnail the bookmark already has. It does
    no network I/O, no downloads, and no AI. Existing bookmark rows are never
    deleted or rewritten — only `canonical_content_id` is populated.
    """
    from .models import Bookmark, CanonicalContent
    from .content.identity import resolve_identity

    stats = {"scanned": 0, "linked": 0, "created": 0, "skipped": 0, "errors": 0}
    rows = (
        db.query(Bookmark)
        .filter(Bookmark.canonical_content_id.is_(None))
        .limit(limit)
        .all()
    )
    for bm in rows:
        stats["scanned"] += 1
        try:
            ident = resolve_identity(bm.url, platform_hint=bm.platform)
            if not ident:
                stats["skipped"] += 1
                continue
            cc = (
                db.query(CanonicalContent)
                .filter(CanonicalContent.content_key == ident.content_key)
                .first()
            )
            if not cc:
                cc = CanonicalContent(
                    content_key=ident.content_key,
                    platform=ident.platform,
                    platform_content_id=ident.platform_content_id,
                    canonical_url=ident.canonical_url,
                    media_kind=ident.media_kind,
                    title=bm.title,
                    description=bm.description,
                    creator_handle=bm.author,
                    thumbnail_url=bm.thumbnail_url,
                    published_at=bm.published_at,
                    processing_state="queued",
                    processing_level=0,
                )
                db.add(cc)
                db.flush()
                stats["created"] += 1
            bm.canonical_content_id = cc.id
            stats["linked"] += 1
        except Exception as e:
            stats["errors"] += 1
            logger.warning("backfill failed for bookmark %s: %s", bm.id, e)
    db.commit()
    return stats
