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
        logger.error(f"Failed to connect to database: {e}")
        return False

def init_db():
    try:
        if not test_connection():
            raise RuntimeError("Cannot connect to database")
        
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
        
        if DATABASE_URL.startswith("postgresql"):
            with engine.connect() as conn:
                try:
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm;"))
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gin;"))
                    logger.info("PostgreSQL extensions enabled")
                except Exception as e:
                    logger.warning(f"Could not enable PostgreSQL extensions: {e}")
                conn.commit()
                
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise RuntimeError(f"Database initialization failed: {e}")

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