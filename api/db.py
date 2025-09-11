import os
from pathlib import Path

from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# 1) Explicitly load the .env that sits next to this file
ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=ENV_PATH)

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        f"DATABASE_URL missing. Expected it in {ENV_PATH}.\n"
        "Example:\n"
        "DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require"
    )

# 2) Create engine and eagerly test a simple query so failures show now
try:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    # quick sanity ping to reveal SSL/driver/URL issues immediately
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
except Exception as e:
    raise RuntimeError(f"Failed to initialize SQLAlchemy engine: {e}") from e


def init_db():
    """Create tables if they don't exist."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bookmarks (
                id SERIAL PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT,
                note TEXT,
                platform TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """))
