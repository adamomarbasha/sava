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

    # The user is out of Processing Units, so the expensive understanding pass
    # was never started. Deliberately NOT a failure: the save exists, it is in
    # the library, its metadata is whatever we already had, and it becomes
    # processable the moment the allowance resets or the user upgrades.
    #
    # A distinct state rather than leaving it QUEUED, because QUEUED means "a
    # worker will get to this" and nothing ever will. The client shows
    # "AI processing limit reached" and an upgrade affordance off this value.
    LIMIT_REACHED = "limit_reached"

    #: States from which a save can still be processed later.
    RESUMABLE = (QUEUED, LIMIT_REACHED, FAILED)


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

    # ── Processing lease ─────────────────────────────────────────────────────
    # Which worker is currently running the pipeline for this item, and since
    # when. Taken with a compare-and-swap UPDATE (see `api/concurrency.py`), so
    # two workers that claim two different jobs for the *same* content cannot
    # both download the video and both pay for ASR.
    #
    # Not a state, deliberately: `processing_state` describes the content and is
    # shown to users, while this describes a worker and is invisible. Overloading
    # one field with both meanings is how "processing" ends up meaning "stuck"
    # after a crash. An expired lease is stealable, so a killed worker costs one
    # delayed retry rather than an item that can never be processed again.
    processing_lock_owner = Column(String(64))
    processing_lock_at = Column(DateTime(timezone=True))

    # Which pipeline route actually ran, and why.
    #
    # Recorded rather than inferred because it is the number the whole cost
    # model turns on: what fraction of TikToks are served by text-only versus
    # by a video download is the difference between a 25% and a 130% cost
    # ratio, and it can only be known by measuring it. `/api/ops/routes`
    # aggregates these so the escalation thresholds can be retuned from a real
    # corpus instead of from the 129 items we had when they were chosen.
    #
    # Also what the meter charges against: a save is billed for the route it
    # took, not for how long the video was.
    route = Column(String(16))
    route_reason = Column(String(200))

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
    the claim uses FOR UPDATE SKIP LOCKED; elsewhere a compare-and-swap UPDATE
    guarded on (state, attempts, locked_at). Both let exactly one worker win.
    """
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(48), nullable=False)
    idempotency_key = Column(String(200), nullable=False, unique=True)
    # Which external platform this job will talk to, so the claimer can skip
    # work for a platform that is currently throttled or circuit-open.
    platform = Column(String(20))
    payload = Column(Text, nullable=False, default="{}")

    # Who caused this job. Denormalised out of `payload` so per-user fairness is
    # an indexed query rather than a LIKE over JSON text.
    #
    # Note this is *the user who triggered the work*, not an owner: one job
    # serves everyone who saved the same content, which is the whole point of
    # the canonical cache. It exists so one account cannot occupy every worker
    # slot, and so a Pro subscriber's concurrency allowance is enforceable.
    user_id = Column(Integer)

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
        Index("idx_jobs_user_state", "user_id", "state"),
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


# ═══════════════════════════════════════════════════════════════════════════
# SUBSCRIPTION & METERING
#
# Two tables, and the split between them is the important part.
#
# `Subscription` is *what Apple told us*. It is written only by verified App
# Store transaction data and is the sole thing that decides whether an account
# is Pro. Nothing a client asserts reaches it.
#
# `BillingPeriod` is *what the account has spent*. One row per user per billing
# month, holding the counters that the atomic reserve/refund runs against. It is
# separate from `UsageEvent` on purpose: usage events are a best-effort cost
# ledger written after the fact, whereas an allowance has to be decremented
# transactionally before the money is spent. Counting events to decide whether
# to start work would race with itself under concurrent saves.
# ═══════════════════════════════════════════════════════════════════════════


class SubscriptionStatus:
    """Where an account stands with Apple.

    Mirrors the states Apple actually exposes rather than inventing our own.
    `GRACE` matters commercially: a renewal that failed on a expired card is
    still a paying customer, and Apple keeps retrying for days. Cutting them off
    at the first failed charge is how a subscription business loses people it
    had already won.
    """
    NONE = "none"          # never subscribed
    ACTIVE = "active"      # paid and current
    GRACE = "grace"        # billing retry / grace period — still entitled
    EXPIRED = "expired"    # lapsed
    REVOKED = "revoked"    # refunded or family-sharing withdrawn — not entitled

    #: The states that grant the `pro` entitlement. Everything else does not.
    ENTITLED = (ACTIVE, GRACE)


class Subscription(Base):
    """One row per user. Written only from verified Apple transaction data."""
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, unique=True)

    plan = Column(String(16), nullable=False, default="free")
    status = Column(String(16), nullable=False, default=SubscriptionStatus.NONE)
    product_id = Column(String(120))

    # Apple's stable identity for a subscription across every renewal. This is
    # the anti-sharing key: it is unique here, so the same purchase cannot be
    # replayed to grant Pro on a second account.
    original_transaction_id = Column(String(64), unique=True)
    # The most recent transaction seen, for idempotent re-verification.
    latest_transaction_id = Column(String(64))

    purchased_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))
    auto_renew = Column(Boolean, nullable=False, default=False)

    # "Production" | "Sandbox" | "LocalTesting". Recorded because a Sandbox
    # transaction must never be mistaken for a real one in production, and
    # because a support question always starts with "which environment?".
    environment = Column(String(16), nullable=False, default="Production")

    # How the entitlement was established. "apple_jws" is the real path;
    # "local_testing" is only reachable when explicitly enabled outside
    # production. Stored so an audit can tell them apart forever.
    verification = Column(String(24), nullable=False, default="apple_jws")

    last_verified_at = Column(DateTime(timezone=True))
    # The decoded claim set of the last accepted transaction, as JSON. Not the
    # raw JWS: it is large, it is a bearer-ish credential, and we have already
    # extracted everything we act on.
    last_claims = Column(Text, nullable=False, default="{}")

    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_subscriptions_user", "user_id"),
        Index("idx_subscriptions_expiry", "status", "expires_at"),
    )


class BillingPeriod(Base):
    """One user's allowance counters for one billing month.

    The period boundaries are computed and stored server-side and never come
    from a client, so a phone with its clock wound back gets the same answer as
    everybody else.

    `units_used` and `ask_used` are moved only by conditional UPDATE statements
    (`UPDATE ... SET units_used = units_used + n WHERE units_used + n <= limit`),
    which is what makes concurrent saves unable to overspend the account: the
    database decides the winner, not a read-then-write in Python.
    """
    __tablename__ = "billing_periods"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)

    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)

    #: The plan in force when this period opened. Informational — the live plan
    #: from `entitlements` is what limits are read from, so upgrading mid-month
    #: raises the ceiling immediately rather than at the next reset.
    plan = Column(String(16), nullable=False, default="free")

    units_used = Column(Integer, nullable=False, default=0)
    ask_used = Column(Integer, nullable=False, default=0)

    #: Units handed back after an infrastructure failure. Tracked separately so
    #: refund abuse is visible rather than merely absent from `units_used`.
    units_refunded = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "period_start", name="uq_period_user_start"),
        Index("idx_periods_user_start", "user_id", "period_start"),
    )


class UnitReservation(Base):
    """One debit against a billing period, tied to the work that caused it.

    Exists so a refund can be *specific*. Without it the only way to give units
    back would be to decrement the counter and hope the amount was right, and
    nothing would stop the same failure refunding twice. Here a reservation is
    consumed exactly once: `state` moves queued -> settled or queued -> refunded,
    and the refund path only fires on a row still in `queued`.

    Keyed on the canonical content id, matching the job that does the work, so
    the reservation and the thing it paid for cannot drift apart.
    """
    __tablename__ = "unit_reservations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)
    period_id = Column(Integer, ForeignKey("billing_periods.id", ondelete="CASCADE"),
                       nullable=False)
    canonical_content_id = Column(Integer)
    bookmark_id = Column(Integer)

    units = Column(Integer, nullable=False, default=0)
    #: queued -> the work has not finished; settled -> spent; refunded -> given back.
    state = Column(String(12), nullable=False, default="queued")
    reason = Column(String(64))

    #: Units an *earlier* run already paid toward the route this reservation is
    #: completing. Zero for a fresh run.
    #
    # Only lazy visual escalation sets it. When an Ask upgrades a text-routed
    # item (1 unit, already settled) to frames (8 units), `_escalate` reserves
    # the 7-unit difference and records the 1 that was already paid. Without
    # that record, settlement compares the 8-unit route against a 7-unit
    # reservation and tops it up by 1 — the account pays 9 for an 8-unit item.
    #
    # A period-wide sum would fix that and break something worse: a reprocess is
    # a genuinely new run of the same route, and reconciling it against the
    # earlier run's charge makes every reprocess after the first cost nothing
    # while still downloading the video. The distinction is *intent*, which only
    # the caller knows, so the caller states it here.
    baseline_units = Column(Integer, nullable=False, default=0)

    #: Which run of this content this reservation paid for. 0 is the original
    #: save; a reprocess opens attempt 1, and so on.
    #
    # It exists to keep the unique key useful across repeat work. Keying only on
    # (user, content) would make the *second* expensive run free — the row from
    # the first would be found and treated as already paid — which is a free
    # "reprocess" button on every item in the library. Including the attempt
    # means each genuine run is charged once and only once, while a retried
    # enqueue of the same run still collides and cannot double-debit.
    attempt = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    settled_at = Column(DateTime(timezone=True))

    __table_args__ = (
        # One reservation per user, per content item, per run. This is what makes
        # the debit idempotent: a re-enqueued job, a retried save, or two devices
        # saving the same link at once all collide here rather than paying twice.
        UniqueConstraint("user_id", "canonical_content_id", "attempt",
                         name="uq_reservation_user_content_attempt"),
        Index("idx_reservations_period_state", "period_id", "state"),
        Index("idx_reservations_user_content", "user_id", "canonical_content_id"),
    )
