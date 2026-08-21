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

    # Whether this save was ever actually returned to.
    #
    # Resurfacing needs to distinguish "kept and re-read" from "saved and never
    # opened again", and the second is the larger and more interesting group —
    # the whole point of a library is that things in it can be found later.
    # Null means never opened since saving.
    last_opened_at = Column(DateTime(timezone=True))
    open_count = Column(Integer, nullable=False, default=0)
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
        # `raw` is TEXT, and Postgres has no default GIN operator class for TEXT,
        # so declaring a plain GIN index on it makes `create_all` fail outright —
        # the whole schema, not just this index. It never showed up because
        # every test ran on SQLite, which ignores `postgresql_using`. If this
        # column is ever wanted for search it needs either JSONB (`jsonb_ops`)
        # or a trigram index (`gin_trgm_ops`, requires pg_trgm); until then the
        # column is only ever read by primary key and needs no index at all.
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

    # Native pixel dimensions of the source media. Stored because orientation is
    # not derivable from anything else we keep, and orientation is what decides
    # whether an item belongs in the short-form viewer at all. `is_short` is the
    # decision itself, cached so that neither the feed query nor the client has
    # to re-derive it per row.
    width = Column(Integer)
    height = Column(Integer)
    is_short = Column(Boolean, nullable=False, default=False)

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

    # Per-stage versions. One global `pipeline_version` forces an all-or-nothing
    # reprocess: improve the summariser and you also re-download every video.
    # Versioning each stage separately means an upgrade sweep can re-run exactly
    # the stage that changed and reuse everything else.
    acquisition_version = Column(Integer, nullable=False, default=0)
    transcript_version = Column(Integer, nullable=False, default=0)
    vision_version = Column(Integer, nullable=False, default=0)
    understanding_version = Column(Integer, nullable=False, default=0)
    embedding_version = Column(Integer, nullable=False, default=0)

    # Comments are enrichment on a separate clock — fetched by their own job,
    # refreshed on their own TTL, and never a precondition for READY.
    comment_version = Column(Integer, nullable=False, default=0)
    comments_fetched_at = Column(DateTime(timezone=True))
    comments_state = Column(String(16), nullable=False, default="none")  # none|ok|failed|disabled
    comment_count = Column(Integer)
    stage_status = Column(Text, nullable=False, default="{}")   # per-stage ok/failed/skipped
    last_error = Column(Text)

    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_cc_platform_pid", "platform", "platform_content_id"),
        Index("idx_cc_state", "processing_state"),
        Index("idx_cc_short", "is_short"),
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


class ContentAsset(Base):
    """One durable image belonging to a piece of content.

    Exists for TikTok photo posts, where the content *is* an ordered set of
    images. Modelling them as assets rather than as frames matters: frames are
    samples Sava chose out of a video and are disposable, whereas these slides
    are the work itself, they have a meaningful order, and slide 1 is the cover
    the creator picked.

    `storage_key` points into object storage, so the asset survives the source
    CDN URL expiring — which for TikTok is a matter of days, not years.
    """
    __tablename__ = "content_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_content_id = Column(Integer, ForeignKey("canonical_content.id", ondelete="CASCADE"), nullable=False)
    asset_index = Column(Integer, nullable=False, default=0)     # 0 = cover
    kind = Column(String(16), nullable=False, default="image")   # image | cover
    source_url = Column(Text)                                    # may expire
    storage_key = Column(Text)                                   # durable, ours
    width = Column(Integer)
    height = Column(Integer)
    ocr_text = Column(Text)
    vision_caption = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (
        UniqueConstraint("canonical_content_id", "asset_index", name="uq_asset_content_index"),
        Index("idx_assets_content_index", "canonical_content_id", "asset_index"),
    )


class ContentComment(Base):
    """A community comment, cached against the *content* rather than a save.

    The pre-existing `comments` table hangs off `bookmarks`, which means ten
    thousand people saving one video would fetch and store that video's comments
    ten thousand times. This table is keyed on canonical content, so it is
    fetched once and read by everyone — the same rule the rest of the pipeline
    already follows.

    Comments are explicitly *secondary*: `is_creator` and the separate modality
    on retrieval chunks keep audience opinion from being mistaken for what the
    video actually said.
    """
    __tablename__ = "content_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_content_id = Column(Integer, ForeignKey("canonical_content.id", ondelete="CASCADE"), nullable=False)
    platform_comment_id = Column(String(64))
    author = Column(String(255))
    text = Column(Text, nullable=False)
    like_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    is_creator = Column(Boolean, nullable=False, default=False)
    rank = Column(Integer, nullable=False, default=0)            # position in the fetched sample
    source = Column(String(24), nullable=False, default="top")   # top | recent
    published_at = Column(DateTime(timezone=True))
    fetched_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (
        UniqueConstraint("canonical_content_id", "platform_comment_id",
                         name="uq_comment_content_platform_id"),
        Index("idx_comments_content_rank", "canonical_content_id", "rank"),
    )


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

    # What an automatic collection *is*, independently of its name or row id —
    # "creator:penguinz0", "tag:attackontitan". Rebuilds match on this, so a
    # collection keeps its identity, its cover and its edits across runs instead
    # of being deleted and recreated with a new id every time. Null for manual
    # collections, which are defined by the user rather than by a signal.
    signature = Column(String(160))
    cover_bookmark_id = Column(Integer, ForeignKey("bookmarks.id", ondelete="SET NULL"))

    # ── Cover ────────────────────────────────────────────────────────────────
    # Where the cover came from, and therefore who is allowed to change it.
    # "automatic" is Sava's; everything else is the user's and is never
    # overwritten by reselection.
    cover_source = Column(String(20), nullable=False, default="automatic")
    # The durable copy. External imagery is mirrored on selection, so a cover
    # chosen today does not vanish when the source page changes next month.
    cover_storage_key = Column(Text)
    cover_url = Column(Text)
    # 2–4 durable keys when the cover is an editorial mosaic rather than one
    # image, as JSON.
    cover_mosaic = Column(Text)
    # What the cover was chosen *for*. Reselection happens when this changes,
    # and not when the user merely opened the screen — which is what keeps a
    # normal Collections read free of any search or inference.
    cover_signature = Column(String(200))
    cover_confidence = Column(Float)
    # Source page, domain, licence and attribution, as JSON. Kept because an
    # image is only usable if we can still say where it came from.
    cover_provenance = Column(Text)
    cover_updated_at = Column(DateTime(timezone=True))
    embedding = Column(VectorColumn(EMBED_DIM))
    is_pinned = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_collection_user_name"),
        Index("idx_collections_user", "user_id"),
        Index("idx_collections_signature", "user_id", "signature"),
    )


class CollectionItem(Base):
    __tablename__ = "collection_items"

    collection_id = Column(Integer, ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True)
    bookmark_id = Column(Integer, ForeignKey("bookmarks.id", ondelete="CASCADE"), primary_key=True)
    added_by = Column(String(10), nullable=False, default="user")   # user | auto
    score = Column(Float)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())


class CollectionFeedback(Base):
    """What the user has told us about automatic grouping, by correcting it.

    Automatic collections are rebuilt from scratch as the library grows, which
    means every rebuild is an opportunity to undo a correction the user already
    made — to put back the item they removed, or to resurrect the collection
    they deleted. That is the single most irritating thing an "smart" feature
    can do, so corrections are recorded here and consulted on every rebuild.

    Keyed by *signature* rather than by collection id, because the collection
    row may not survive a rebuild but the grouping it represented will. Deleting
    "Attack on Titan" has to mean "stop suggesting this grouping", not "delete
    this row so it can be recreated in thirty seconds".
    """
    __tablename__ = "collection_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    signature = Column(String(160), nullable=False)
    # remove_item -> bookmark_id is set. reject_collection -> it is null.
    action = Column(String(20), nullable=False)
    bookmark_id = Column(Integer, ForeignKey("bookmarks.id", ondelete="CASCADE"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "signature", "action", "bookmark_id",
                         name="uq_feedback_unique"),
        Index("idx_feedback_user_sig", "user_id", "signature"),
    )


class CollectionView(Base):
    """When the user last opened a collection.

    Feeds resurfacing: something belonging to a collection opened recently is
    more worth bringing back than something from a corner of the library that
    has not been touched in months.
    """
    __tablename__ = "collection_views"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    collection_id = Column(Integer, ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True)
    viewed_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    view_count = Column(Integer, nullable=False, default=1)


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
