"""Central configuration.

Single source of truth for paths, providers, models, and pipeline behaviour.

Why this module exists: `DATABASE_URL=sqlite:///./bookmarks.db` is resolved
relative to the *current working directory*, so `uvicorn api.main:app` (from the
repo root) and `run_api.py` (which chdirs into `api/`) opened two different
SQLite files. That silently split the dataset. Relative SQLite paths are now
anchored to the repo root regardless of how the process is launched.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = Path(__file__).resolve().parent

load_dotenv(dotenv_path=API_DIR / ".env")


def _resolve_database_url() -> str:
    raw = os.getenv("DATABASE_URL", "sqlite:///./bookmarks.db")
    if not raw.startswith("sqlite"):
        return raw
    prefix, _, path_part = raw.partition("///")
    if not path_part:
        return raw
    p = Path(path_part)
    if p.is_absolute():
        return raw
    # Anchor relative SQLite paths to the repo root so the launch method
    # cannot change which database is opened.
    return f"{prefix}///{(REPO_ROOT / p).resolve()}"


DATABASE_URL = _resolve_database_url()
IS_POSTGRES = DATABASE_URL.startswith("postgres")

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# ─── AI providers ────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Embeddings. 1536 dims keeps pgvector HNSW comfortable and halves storage
# versus the 3072 default; gemini-embedding-001 is Matryoshka-truncatable, so
# this costs ~0% retrieval quality but requires explicit normalization.
EMBED_MODEL = os.getenv("SAVA_EMBED_MODEL", "gemini-embedding-001")
EMBED_DIM = int(os.getenv("SAVA_EMBED_DIM", "1536"))
EMBED_BATCH = int(os.getenv("SAVA_EMBED_BATCH", "32"))

# ─── Pipeline behaviour ──────────────────────────────────────────────────────
PIPELINE_VERSION = int(os.getenv("SAVA_PIPELINE_VERSION", "1"))
UNDERSTANDING_SCHEMA_VERSION = int(os.getenv("SAVA_UNDERSTANDING_SCHEMA_VERSION", "1"))

# Frame/vision policy. "conditional" is the default and the only economically
# defensible one: visual analysis needs the full video, which is the single
# largest cost driver per save (see the unit-economics audit). We download once
# at low resolution and extract audio + frames from that same file.
TIKTOK_VISION_MODE = os.getenv("SAVA_TIKTOK_VISION_MODE", "conditional")
YOUTUBE_VISION_MODE = os.getenv("SAVA_YOUTUBE_VISION_MODE", "never")
INSTAGRAM_VISION_MODE = os.getenv("SAVA_INSTAGRAM_VISION_MODE", "conditional")

MAX_FRAMES_PER_VIDEO = int(os.getenv("SAVA_MAX_FRAMES", "8"))
FRAME_MAX_WIDTH = int(os.getenv("SAVA_FRAME_MAX_WIDTH", "640"))
DOWNLOAD_MAX_HEIGHT = int(os.getenv("SAVA_DOWNLOAD_MAX_HEIGHT", "480"))

# Long-form content: defer the (more expensive) summary until first open.
LAZY_SUMMARY_OVER_SECONDS = int(os.getenv("SAVA_LAZY_SUMMARY_OVER_SECONDS", "1200"))

CHUNK_TARGET_TOKENS = int(os.getenv("SAVA_CHUNK_TARGET_TOKENS", "180"))
CHUNK_OVERLAP_TOKENS = int(os.getenv("SAVA_CHUNK_OVERLAP_TOKENS", "30"))

# ─── Worker ──────────────────────────────────────────────────────────────────
WORKER_POLL_SECONDS = float(os.getenv("SAVA_WORKER_POLL_SECONDS", "2.0"))
WORKER_CONCURRENCY = int(os.getenv("SAVA_WORKER_CONCURRENCY", "2"))
JOB_MAX_ATTEMPTS = int(os.getenv("SAVA_JOB_MAX_ATTEMPTS", "4"))
JOB_LEASE_SECONDS = int(os.getenv("SAVA_JOB_LEASE_SECONDS", "900"))

# Run jobs inline instead of via the worker. Used by tests and by developers who
# do not want a second process; never enable in production.
INLINE_JOBS = os.getenv("SAVA_INLINE_JOBS", "").lower() in ("1", "true", "yes")

# Accept saves without blocking on an external fetch. The legacy synchronous
# path (yt-dlp inside the request handler) is kept behind this flag only as an
# escape hatch; it does not survive concurrent load.
ASYNC_SAVE = os.getenv("SAVA_ASYNC_SAVE", "1").lower() not in ("0", "false", "no")

MEDIA_DIR = Path(os.getenv("SAVA_MEDIA_DIR", str(API_DIR / "static" / "media")))

# yt-dlp auth. YouTube blocks datacenter ASNs outright and challenges many
# residential IPs with "Sign in to confirm you're not a bot". In production the
# answer is SAVA_PROXY_URL (rotating residential); for local development,
# borrowing the developer's own browser cookies is the documented yt-dlp path.
YTDLP_COOKIES_FROM_BROWSER = os.getenv("SAVA_YTDLP_COOKIES_FROM_BROWSER")
YTDLP_COOKIEFILE = os.getenv("SAVA_YTDLP_COOKIEFILE")

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")


def ai_enabled() -> bool:
    return bool(GEMINI_API_KEY or OPENAI_API_KEY)
