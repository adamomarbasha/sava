from sqlalchemy import (
    Column, BigInteger, Integer, String, Text, DateTime, Boolean, Float,
    ForeignKey, Index, CheckConstraint, UniqueConstraint, func
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy import DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
import os

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())
    
    bookmarks = relationship("Bookmark", back_populates="user", cascade="all, delete-orphan")

class Bookmark(Base):
    __tablename__ = "bookmarks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(
        String(20), 
        nullable=False,
        default="other"
    )
    # NOT globally unique. A URL is unique PER USER — two different people
    # saving the same public video is the normal case and the basis of the
    # canonical content cache. A global UNIQUE here would make the second user
    # to save any viral item fail with an IntegrityError.
    url = Column(Text, nullable=False)
    title = Column(Text)
    author = Column(Text)
    thumbnail_url = Column(Text)
    description = Column(Text)
    note = Column(Text)
    published_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())
    raw = Column(Text, nullable=False, default='{}')
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Intelligence layer. The user save points at shared canonical content; the
    # mirrored state lets clients render Saving/Processing/Ready without a join.
    canonical_content_id = Column(
        Integer, ForeignKey("canonical_content.id", ondelete="SET NULL"), nullable=True)
    processing_state = Column(String(16))
    
    __table_args__ = (
        CheckConstraint(
            "platform IN ('youtube','tiktok','instagram','twitter','linkedin','reddit','pinterest','snapchat','facebook','other','web')",
            name='check_platform_values'
        ),
        UniqueConstraint('user_id', 'url', name='uq_bookmark_user_url'),
        Index('idx_bookmarks_platform_created_at', 'platform', 'created_at'),
        Index('idx_bookmarks_raw_gin', 'raw', postgresql_using='gin'),
        Index('idx_bookmarks_user_created', 'user_id', 'created_at'),
    )
    
    user = relationship("User", back_populates="bookmarks")
    youtube_details = relationship("YouTubeDetails", back_populates="bookmark", cascade="all, delete-orphan")

class YouTubeDetails(Base):
    __tablename__ = "youtube_details"
    
    bookmark_id = Column(Integer, ForeignKey("bookmarks.id", ondelete="CASCADE"), primary_key=True)
    video_id = Column(String(20), nullable=False, unique=True)
    channel_id = Column(String(50))
    duration_seconds = Column(Integer)
    view_count = Column(Integer)
    like_count = Column(Integer)
    tags = Column(Text)
    extra = Column(Text, nullable=False, default='{}')
    
    __table_args__ = (
        Index('idx_youtube_video_id', 'video_id', unique=True),
    )
    
    bookmark = relationship("Bookmark", back_populates="youtube_details")

class Caption(Base):
    __tablename__ = "captions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    bookmark_id = Column(Integer, ForeignKey("bookmarks.id", ondelete="CASCADE"), nullable=False)
    source = Column(String(20), nullable=False)
    lang = Column(String(10), nullable=False, default='en')
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    
    __table_args__ = (
        CheckConstraint(
            "source IN ('whisper','api','manual')",
            name='check_caption_source'
        ),
    )

class Comment(Base):
    __tablename__ = "comments"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    bookmark_id = Column(Integer, ForeignKey("bookmarks.id", ondelete="CASCADE"), nullable=False)
    platform_author = Column(String(255))
    text = Column(Text, nullable=False)
    like_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    raw = Column(Text, nullable=False, default='{}')
    
    __table_args__ = (
        Index('idx_comments_bookmark_created', 'bookmark_id', 'created_at'),
    ) 

# ═══════════════════════════════════════════════════════════════════════════
# INTELLIGENCE LAYER
#
# Two-layer design. `CanonicalContent` is the *content* — one row per real
# video/post in the world, shared by every user who saves it. `Bookmark` stays
# the *user save* and gains a nullable FK into it, so existing rows, existing
# API responses, and the iOS capture flow keep working untouched.
#
# Expensive work (download, transcription, frames, OCR, vision, summary,
# embeddings) hangs off CanonicalContent and is therefore done once, globally.
# ═══════════════════════════════════════════════════════════════════════════

from .vectors import VectorColumn  # noqa: E402
from .config import EMBED_DIM      # noqa: E402


class ProcessingState:
    QUEUED = "queued"
    FETCHING = "fetching"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    READY = "ready"
    PARTIAL = "partial"      # usable, but some enrichment failed
    FAILED = "failed"


class CanonicalContent(Base):
    """One row per distinct piece of content, shared across all users."""
    __tablename__ = "canonical_content"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Identity. `content_key` is the dedupe key: "<platform>:<platform_id>",
    # falling back to a hash of the normalized URL when no id is extractable.
    content_key = Column(String(200), nullable=False, unique=True)
    platform = Column(String(20), nullable=False, default="other")
    platform_content_id = Column(String(160))
    canonical_url = Column(Text, nullable=False)

    media_kind = Column(String(16), nullable=False, default="unknown")  # video|image|carousel|article
    creator_handle = Column(String(255))
    creator_name = Column(String(255))
    title = Column(Text)
    description = Column(Text)
    duration_seconds = Column(Integer)
    published_at = Column(DateTime(timezone=True))
    thumbnail_url = Column(Text)
    thumbnail_stored_key = Column(Text)
    metadata_json = Column(Text, nullable=False, default="{}")

    content_type = Column(String(32))              # recipe|restaurant|travel|product|...
    content_type_confidence = Column(Float)
    visual_dependency = Column(Float)              # 0..1 — how much meaning lives on screen

    processing_state = Column(String(16), nullable=False, default=ProcessingState.QUEUED)
    processing_level = Column(Integer, nullable=False, default=0)
    pipeline_version = Column(Integer, nullable=False, default=1)
    stage_status = Column(Text, nullable=False, default="{}")   # per-stage ok/failed/skipped
    last_error = Column(Text)

    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_cc_platform_pid", "platform", "platform_content_id"),
        Index("idx_cc_state", "processing_state"),
    )


class ContentTranscript(Base):
    """Persisted transcript. Acquired once; every later AI call reads this."""
    __tablename__ = "content_transcripts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_content_id = Column(Integer, ForeignKey("canonical_content.id", ondelete="CASCADE"), nullable=False)
    source = Column(String(16), nullable=False)          # captions | asr
    lang = Column(String(10), nullable=False, default="en")
    text = Column(Text, nullable=False, default="")
    segments = Column(Text, nullable=False, default="[]")  # [{text,start,duration}]
    provider = Column(String(32))
    model = Column(String(64))
    audio_seconds = Column(Float)
    is_complete = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (
        UniqueConstraint("canonical_content_id", "lang", "source", name="uq_transcript_content_lang_source"),
    )


class ContentFrame(Base):
    """A sampled video frame plus what we read off it."""
    __tablename__ = "content_frames"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_content_id = Column(Integer, ForeignKey("canonical_content.id", ondelete="CASCADE"), nullable=False)
    ts_ms = Column(Integer, nullable=False, default=0)
    phash = Column(String(32))                 # perceptual hash, for dedupe
    ocr_text = Column(Text)
    vision_caption = Column(Text)
    stored_key = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (Index("idx_frames_content_ts", "canonical_content_id", "ts_ms"),)


class ContentChunk(Base):
    """Retrievable unit. Multi-modal: transcript, OCR, caption, or vision text."""
    __tablename__ = "content_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_content_id = Column(Integer, ForeignKey("canonical_content.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False, default=0)
    modality = Column(String(12), nullable=False, default="transcript")
    text = Column(Text, nullable=False)
    start_s = Column(Integer)
    end_s = Column(Integer)
    token_count = Column(Integer)
    embedding = Column(VectorColumn(EMBED_DIM))
    embed_model = Column(String(64))
    embed_dim = Column(Integer)

    __table_args__ = (Index("idx_chunks_content_idx", "canonical_content_id", "chunk_index"),)


class ContentUnderstanding(Base):
    """Structured understanding — the common schema plus typed extras."""
    __tablename__ = "content_understanding"

    canonical_content_id = Column(Integer, ForeignKey("canonical_content.id", ondelete="CASCADE"), primary_key=True)
    schema_version = Column(Integer, nullable=False, default=1)
    content_type = Column(String(32))
    tl_dr = Column(Text)
    key_points = Column(Text, nullable=False, default="[]")
    topics = Column(Text, nullable=False, default="[]")
    entities = Column(Text, nullable=False, default="{}")    # common entity layer
    typed_data = Column(Text, nullable=False, default="{}")  # recipe/product/place specifics
    chapters = Column(Text, nullable=False, default="[]")
    sources_used = Column(Text, nullable=False, default="[]")
    provider = Column(String(32))
    model = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())


class ContentEmbedding(Base):
    """Document-level vector for a canonical item (search / related / clustering)."""
    __tablename__ = "content_embeddings"

    canonical_content_id = Column(Integer, ForeignKey("canonical_content.id", ondelete="CASCADE"), primary_key=True)
    embedding = Column(VectorColumn(EMBED_DIM))
    model = Column(String(64))
    dim = Column(Integer)
    source_hash = Column(String(64))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())


class Collection(Base):
    __tablename__ = "collections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(120), nullable=False)
    kind = Column(String(12), nullable=False, default="manual")  # manual | auto
    description = Column(Text)
    cover_bookmark_id = Column(Integer, ForeignKey("bookmarks.id", ondelete="SET NULL"))
    embedding = Column(VectorColumn(EMBED_DIM))
    is_pinned = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_collection_user_name"),
        Index("idx_collections_user", "user_id"),
    )


class CollectionItem(Base):
    __tablename__ = "collection_items"

    collection_id = Column(Integer, ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True)
    bookmark_id = Column(Integer, ForeignKey("bookmarks.id", ondelete="CASCADE"), primary_key=True)
    added_by = Column(String(10), nullable=False, default="user")   # user | auto
    score = Column(Float)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())


class Job(Base):
    """Database-backed work queue.

    Deliberately not Celery/Redis: a transactional queue in the database we
    already run needs no extra infrastructure, survives restarts, and makes
    idempotency a UNIQUE constraint rather than application logic. On Postgres
    the claim uses FOR UPDATE SKIP LOCKED; on SQLite a short transaction.
    """
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(48), nullable=False)
    idempotency_key = Column(String(200), nullable=False, unique=True)
    # Which external platform this job will talk to, so the claimer can skip
    # work for a platform that is currently throttled or circuit-open.
    platform = Column(String(20))
    payload = Column(Text, nullable=False, default="{}")
    state = Column(String(16), nullable=False, default="queued")   # queued|running|done|failed|dead
    priority = Column(Integer, nullable=False, default=100)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=4)
    run_after = Column(DateTime(timezone=True), nullable=False, default=func.now())
    locked_by = Column(String(64))
    locked_at = Column(DateTime(timezone=True))
    last_error = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_jobs_claim", "state", "run_after", "priority"),
        Index("idx_jobs_platform", "platform", "state"),
    )


class UsageEvent(Base):
    """One row per billable or expensive operation. The unit-economics ledger."""
    __tablename__ = "usage_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    user_id = Column(Integer)
    canonical_content_id = Column(Integer)
    bookmark_id = Column(Integer)
    operation = Column(String(48), nullable=False)
    platform = Column(String(20))
    provider = Column(String(32))
    model = Column(String(64))
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    audio_seconds = Column(Float, nullable=False, default=0.0)
    frames_processed = Column(Integer, nullable=False, default=0)
    proxy_bytes = Column(BigInteger, nullable=False, default=0)
    wall_ms = Column(Integer, nullable=False, default=0)
    estimated_usd = Column(Float, nullable=False, default=0.0)
    cache_hit = Column(Boolean, nullable=False, default=False)
    success = Column(Boolean, nullable=False, default=True)
    error = Column(Text)

    __table_args__ = (
        Index("idx_usage_user_created", "user_id", "created_at"),
        Index("idx_usage_operation", "operation", "created_at"),
    )


class ChatThread(Base):
    __tablename__ = "chat_threads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    bookmark_id = Column(Integer, ForeignKey("bookmarks.id", ondelete="CASCADE"))
    scope = Column(String(16), nullable=False, default="save")   # save | library | collection
    collection_id = Column(Integer, ForeignKey("collections.id", ondelete="CASCADE"))
    title = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (Index("idx_threads_user_scope", "user_id", "scope"),)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(Integer, ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    citations = Column(Text, nullable=False, default="[]")
    mode = Column(String(16))                      # auto | fast | advanced
    model = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (Index("idx_messages_thread_created", "thread_id", "created_at"),)
