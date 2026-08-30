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


class ConfigurationError(RuntimeError):
    """A deployment is misconfigured in a way that must stop the process."""


# ─── Environment ─────────────────────────────────────────────────────────────
#
# Production is the default, and development must be asked for by name.
#
# It used to be the other way round, and that was the single most dangerous line
# in the codebase. Every production protection — the refusal to boot without a
# real `SECRET_KEY`, the strict CORS allowlist, the hidden API docs — was gated on
# `ENVIRONMENT != development`, while `ENVIRONMENT` itself defaulted to
# `development`. A deploy that set `SECRET_KEY` and `DATABASE_URL` but forgot
# `ENVIRONMENT` therefore came up serving traffic, passing its health check, and
# signing every token with a fallback secret printed in this repository. Nothing
# failed; one warning went into a log nobody was watching.
#
# Inverting it makes the unconfigured state the safe state. Forgetting the
# variable now costs a loud startup failure on a laptop instead of a silent
# authentication bypass in production.
_DEV_NAMES = {"development", "dev", "local"}
_TEST_NAMES = {"test", "testing", "ci"}
_PROD_NAMES = {"production", "prod", "staging"}


def _resolve_environment() -> str:
    raw = (os.getenv("ENVIRONMENT") or "").strip().lower()
    if not raw:
        # Unset means production. See the note above.
        return "production"
    if raw in _DEV_NAMES or raw in _TEST_NAMES or raw in _PROD_NAMES:
        return raw
    raise ConfigurationError(
        f"ENVIRONMENT={raw!r} is not a recognised environment. Use one of: "
        f"{', '.join(sorted(_DEV_NAMES | _TEST_NAMES | _PROD_NAMES))}. "
        "Refusing to guess, because guessing wrong means guessing 'development'.")


ENVIRONMENT = _resolve_environment()
IS_DEVELOPMENT = ENVIRONMENT in _DEV_NAMES
IS_TEST = ENVIRONMENT in _TEST_NAMES
IS_PRODUCTION = ENVIRONMENT in _PROD_NAMES

# Interactive API documentation. Off in production unless deliberately switched
# on: it is a complete, machine-readable map of the attack surface, and there is
# no browser client that needs it.
DOCS_ENABLED = (not IS_PRODUCTION) or os.getenv(
    "SAVA_ENABLE_DOCS", "").lower() in ("1", "true", "yes")


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

# 384, not 640.
#
# Gemini prices images by tile: an image whose dimensions both fall under the
# tile size costs one tile, and anything larger is split. At 640px a vertical
# 9:16 frame is 640x1138 and was measured costing ~1,134 input tokens; at 384px
# it is 384x683, both under the 768 tile edge, so it costs 258. Measured vision
# calls averaged 6,237 input tokens for 5.5 frames — that becomes ~1,400.
#
# The frames are read for on-screen text and gross composition, and 384px is
# comfortably enough for both. This is not a quality trade so much as not paying
# to send pixels the model tiles away anyway.
FRAME_MAX_WIDTH = int(os.getenv("SAVA_FRAME_MAX_WIDTH", "384"))
# 360, not 480.
#
# This file is fetched for one purpose: to cut frames out of it that are then
# scaled to 384px for the vision model. Downloading 480p to produce 384px stills
# pays for pixels that are discarded before anything looks at them.
#
# Measured TikTok downloads averaged 7.39 MB at 480p, which at $3/GB is $0.0216
# and was ~83% of the cost of understanding a TikTok. Encoded bitrate falls
# roughly with pixel count, so 360p is expected around 4.4 MB / $0.013.
# `acquire.video` telemetry records the real bytes, so the actual saving is
# measurable rather than assumed.
DOWNLOAD_MAX_HEIGHT = int(os.getenv("SAVA_DOWNLOAD_MAX_HEIGHT", "360"))

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

# ─── Comments enrichment ─────────────────────────────────────────────────────
# Comments are secondary context: useful for "what did people think", never the
# meaning of the content itself. They run as their own job on their own clock so
# a comments outage can never hold up a save.
COMMENTS_ENABLED = os.getenv("SAVA_COMMENTS_ENABLED", "1").lower() not in ("0", "false", "no")
COMMENTS_MAX_PER_ITEM = int(os.getenv("SAVA_COMMENTS_MAX", "40"))
COMMENTS_TTL_DAYS = int(os.getenv("SAVA_COMMENTS_TTL_DAYS", "30"))
COMMENTS_MIN_LIKES = int(os.getenv("SAVA_COMMENTS_MIN_LIKES", "0"))
# Per-platform switches. TikTok's path needs a session cookie it does not have
# by default, so it stays off until someone configures it deliberately.
COMMENTS_YOUTUBE_ENABLED = os.getenv("SAVA_COMMENTS_YOUTUBE", "1").lower() not in ("0", "false", "no")
COMMENTS_TIKTOK_ENABLED = os.getenv("SAVA_COMMENTS_TIKTOK", "0").lower() not in ("0", "false", "no")

# Per-stage versions. Bumping one causes only that stage to be re-run on the
# next upgrade sweep; everything else is reused.
ACQUISITION_VERSION = int(os.getenv("SAVA_ACQUISITION_VERSION", "1"))
TRANSCRIPT_VERSION = int(os.getenv("SAVA_TRANSCRIPT_VERSION", "1"))
VISION_VERSION = int(os.getenv("SAVA_VISION_VERSION", "1"))
EMBEDDING_VERSION = int(os.getenv("SAVA_EMBEDDING_VERSION", "1"))
COMMENT_VERSION = int(os.getenv("SAVA_COMMENT_VERSION", "1"))

# TikTok photo posts. A ceiling because a carousel is billed per slide read.
CAROUSEL_MAX_SLIDES = int(os.getenv("SAVA_CAROUSEL_MAX_SLIDES", "12"))


# ─── Instagram ───────────────────────────────────────────────────────────────
# Provider order. `opengraph` needs no account and is the only one that scales;
# `ytdlp` is richer (it can enumerate carousel children) but requires operator
# credentials, so it is deliberately not in the default chain.
INSTAGRAM_PROVIDERS = [
    p.strip() for p in
    os.getenv("SAVA_INSTAGRAM_PROVIDERS", "opengraph").split(",") if p.strip()
]
INSTAGRAM_YTDLP_ENABLED = os.getenv(
    "SAVA_INSTAGRAM_YTDLP", "").lower() in ("1", "true", "yes")

# Instagram comments have no unauthenticated path at all, so the provider
# exists and reports itself unavailable rather than pretending.
COMMENTS_INSTAGRAM_ENABLED = os.getenv(
    "SAVA_COMMENTS_INSTAGRAM", "0").lower() not in ("0", "false", "no")

# Mirror Instagram imagery into object storage on ingest. Instagram CDN URLs are
# signed and expire within days, so without this a library silently loses its
# thumbnails — which is the single most visible way this platform degrades.
INSTAGRAM_MIRROR_MEDIA = os.getenv(
    "SAVA_INSTAGRAM_MIRROR", "1").lower() not in ("0", "false", "no")
INSTAGRAM_MAX_CAROUSEL_ITEMS = int(os.getenv("SAVA_INSTAGRAM_MAX_CAROUSEL", "20"))


# ─── Collection covers ───────────────────────────────────────────────────────
# Rights-aware image sources, in order. Both publish machine-readable licence
# metadata, which is what makes a rights check possible at all.
COLLECTION_COVER_PROVIDERS = [
    p.strip() for p in
    os.getenv("SAVA_COVER_PROVIDERS", "openverse,wikimedia").split(",") if p.strip()
]


# ─── Production startup gate ─────────────────────────────────────────────────

def production_config_errors() -> list:
    """Everything wrong with this deployment, as a list of sentences.

    Returned rather than raised so a caller can report *all* of the problems at
    once. Discovering a missing variable, fixing it, redeploying, and finding the
    next one is how a ten-minute configuration job becomes an afternoon.

    Only meaningful when `IS_PRODUCTION`; development is allowed to be sloppy on
    purpose, which is the whole reason development has to be requested by name.
    """
    if not IS_PRODUCTION:
        return []

    problems = []

    secret = os.getenv("SECRET_KEY") or ""
    if not secret:
        problems.append(
            "SECRET_KEY is not set. Every issued token would be forgeable. "
            "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(64))'")
    elif secret == "your-secret-key-change-in-production":
        problems.append(
            "SECRET_KEY is still the development placeholder, which is published "
            "in this repository. Generate a real one.")
    elif len(secret) < 32:
        problems.append(
            f"SECRET_KEY is {len(secret)} characters; at least 32 are required.")

    if DATABASE_URL.startswith("sqlite"):
        problems.append(
            "DATABASE_URL points at SQLite. Production requires PostgreSQL: the "
            "hosts Sava targets have ephemeral filesystems, so a SQLite file is "
            "deleted on every deploy, and pgvector and FOR UPDATE SKIP LOCKED "
            "are both unavailable.")

    if not (os.getenv("SAVA_S3_BUCKET") and os.getenv("SAVA_S3_ACCESS_KEY_ID")
            and os.getenv("SAVA_S3_SECRET_ACCESS_KEY")):
        problems.append(
            "Object storage is not configured (SAVA_S3_BUCKET, "
            "SAVA_S3_ACCESS_KEY_ID, SAVA_S3_SECRET_ACCESS_KEY). Production must "
            "not fall back to local disk — stored thumbnails and covers would be "
            "lost on every deploy.")

    if not GEMINI_API_KEY:
        problems.append(
            "GEMINI_API_KEY is not set. Saves would be stored but never "
            "understood, which looks like silent breakage to a user.")

    return problems


def require_production_config() -> None:
    """Raise unless this process is safe to serve production traffic."""
    problems = production_config_errors()
    if not problems:
        return
    raise ConfigurationError(
        "Refusing to start in ENVIRONMENT=%s:\n  - %s"
        % (ENVIRONMENT, "\n  - ".join(problems)))
