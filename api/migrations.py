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
from typing import List, Optional

from sqlalchemy import inspect, text

from .config import EMBED_DIM, IS_POSTGRES
from .models import Base

_DIALECT = None  # set lazily in run_migrations

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5


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



def _column_ddl(col) -> "Optional[str]":
    """Render an ALTER-safe type for a model column.

    SQLite cannot add a NOT NULL column without a default, and cannot add a
    UNIQUE column at all, so those constraints are deliberately dropped here —
    the goal is to make the column exist so writes stop failing. Full constraint
    enforcement belongs to a fresh `create_all` (or Postgres).
    """
    try:
        from sqlalchemy.schema import CreateColumn
        rendered = str(CreateColumn(col).compile(dialect=_DIALECT))
        # "name TYPE ..." -> keep only the type portion
        parts = rendered.strip().split(None, 1)
        if len(parts) < 2:
            return None
        type_sql = parts[1]
        for banned in (" NOT NULL", " UNIQUE", " PRIMARY KEY"):
            type_sql = type_sql.replace(banned, "")
        if col.default is not None and getattr(col.default, "is_scalar", False):
            value = col.default.arg
            if isinstance(value, str):
                type_sql += f" DEFAULT '{value}'"
            elif isinstance(value, bool):
                type_sql += f" DEFAULT {1 if value else 0}"
            elif isinstance(value, (int, float)):
                type_sql += f" DEFAULT {value}"
        return type_sql.strip() or None
    except Exception:
        return None


def _ensure_index(conn, name: str, ddl: str) -> None:
    try:
        conn.execute(text(ddl))
    except Exception as e:
        # Index already present, or the engine reports a benign duplicate.
        logger.debug("index %s skipped: %s", name, e)


def run_migrations(engine) -> List[str]:
    """Bring the database up to SCHEMA_VERSION. Returns applied step names."""
    global _DIALECT
    _DIALECT = engine.dialect
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
    # Generic reconciliation: every column declared on a model but missing from
    # the live table is added. A hardcoded list cannot self-heal — if a table is
    # ever restored from an older copy (a git checkout of a tracked .db file, a
    # backup restore, a redeploy against a stale volume), drift reappears and
    # the app starts failing on writes. This closes that whole class of bug.
    with engine.begin() as conn:
        for table_name, table in Base.metadata.tables.items():
            if not _has_table(conn, table_name):
                continue
            have = _existing_columns(conn, table_name)
            if not have:
                continue
            for col in table.columns:
                if col.name in have or col.primary_key:
                    continue
                ddl = _column_ddl(col)
                if ddl is None:
                    logger.warning(
                        "cannot auto-add %s.%s (unsupported type %s) — "
                        "add it manually", table_name, col.name, col.type)
                    continue
                try:
                    conn.execute(text(
                        f"ALTER TABLE {table_name} ADD COLUMN {col.name} {ddl}"))
                    applied.append(f"{table_name}.{col.name}")
                    logger.info("migration: added %s.%s", table_name, col.name)
                except Exception as e:
                    logger.warning("could not add %s.%s: %s", table_name, col.name, e)

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
