import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import OperationalError
from .models import Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# One source of truth for the database URL.
#
# This module used to read `DATABASE_URL` itself with its *own* default —
# `sqlite:///./api/bookmarks.db` — while `api/config.py` defaulted to
# `sqlite:///./bookmarks.db` and additionally resolved relative paths against
# the repository root. With the variable set they agreed; with it unset they
# opened two different files depending on which module you asked, and on the
# working directory the process happened to start in.
#
# That is almost certainly why two separate SQLite databases ended up committed
# to this repository. `api.config` already loads `.env`, resolves the path, and
# decides `IS_POSTGRES`; there is no reason for a second opinion.
from .config import DATABASE_URL, IS_POSTGRES

if IS_POSTGRES:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        echo=os.getenv("DEBUG", "").lower() == "true"
    )
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def test_connection():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection successful")
        return True
    except Exception as e:
        # `OperationalError` embeds the connection URL, password included.
        from .observability import scrub_secrets
        logger.error("Failed to connect to database: %s", scrub_secrets(str(e)))
        return False

def ensure_extensions(bind) -> None:
    """Create the Postgres extensions the schema depends on. No-op on SQLite.

    `vector` is required before `create_all`, because the embedding table
    declares a column of that type. The other two back trigram and composite
    indexes used by search.
    """
    if not IS_POSTGRES:
        return
    with bind.connect() as conn:
        for extension in ("vector", "pg_trgm", "btree_gin"):
            try:
                conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))
            except Exception as e:
                # `vector` is not optional; the others degrade to slower search.
                if extension == "vector":
                    raise RuntimeError(
                        "The pgvector extension is required but could not be "
                        f"created: {e}. Install it on the server "
                        "(e.g. the pgvector/pgvector image, or `CREATE EXTENSION "
                        "vector` as a superuser).") from e
                logger.warning("Could not enable extension %s: %s", extension, e)
        conn.commit()
    logger.info("PostgreSQL extensions ready")


def init_db():
    try:
        if not test_connection():
            raise RuntimeError("Cannot connect to database")
        
        # Extensions first, and `vector` among them.
        #
        # Two bugs lived here, and both only appear against a real Postgres —
        # which is why they survived a green SQLite suite:
        #
        #   1. `vector` was never created at all, only pg_trgm and btree_gin.
        #   2. Extensions were created *after* `create_all`, but ContentEmbedding
        #      declares a `vector` column, so `create_all` needs the type to
        #      already exist. Against a fresh database the first deploy failed
        #      with `type "vector" does not exist`.
        #
        # Ordering matters, so this runs before any table is created.
        ensure_extensions(engine)

        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")

    except Exception as e:
        # Both the log line and the raised message are scrubbed: the exception
        # propagates to the crash handler and to Sentry, so leaving the raw
        # string in the message would only move the credential somewhere else.
        from .observability import scrub_secrets
        safe = scrub_secrets(str(e))
        logger.error("Failed to initialize database: %s", safe)
        raise RuntimeError(f"Database initialization failed: {safe}") from None

def migrate_from_sqlite():
    if not DATABASE_URL.startswith("sqlite"):
        logger.info("Not using SQLite, skipping migration")
        return
    
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
        except:
            pass
        
        try:
            conn.execute(text("ALTER TABLE bookmarks ADD COLUMN author TEXT"))
            conn.execute(text("ALTER TABLE bookmarks ADD COLUMN thumbnail_url TEXT"))
            conn.execute(text("ALTER TABLE bookmarks ADD COLUMN description TEXT"))
            conn.execute(text("ALTER TABLE bookmarks ADD COLUMN note TEXT"))
            conn.execute(text("ALTER TABLE bookmarks ADD COLUMN published_at TIMESTAMP"))
            conn.execute(text("ALTER TABLE bookmarks ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
        except:
            pass
    
    logger.info("SQLite migration completed")