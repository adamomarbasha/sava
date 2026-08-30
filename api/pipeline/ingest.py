"""Platform-aware ingestion pipeline.

The processing ladder (Part 8). Each level is independently recoverable, and a
failure at one level never discards the levels below it — a save whose visual
analysis failed is still fully searchable from its transcript.

  L0  canonical identity                  (free, deterministic)
  L1  platform metadata                   (cheap, one network call)
  L2  transcript                          (captions free / ASR paid)
  L3  selected frames + OCR + vision      (conditional — the expensive one)
  L4  structured understanding + vectors  (cheap inference)
  L5  deep reasoning                      (only on user request)

Platform strategy decides *which* levels run:

  YouTube    transcript-first. Captions are free and usually present, so
             visual analysis is off by default — a 30-minute talking head does
             not need frames.
  TikTok     multimodal-first. Speech alone routinely misses the point; meaning
             lives in overlays, demonstrations, and on-screen text.
  Reels      treated as TikTok.
  IG image   no audio at all — vision is the only signal.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..ai import telemetry
from ..ai.base import Mode
from ..config import (
    INSTAGRAM_VISION_MODE, LAZY_SUMMARY_OVER_SECONDS, MAX_FRAMES_PER_VIDEO,
    PIPELINE_VERSION, TIKTOK_VISION_MODE, UNDERSTANDING_SCHEMA_VERSION,
    YOUTUBE_VISION_MODE,
)
from ..concurrency import (
    ContentBusy, acquire_content_lease, insert_or_ignore, insert_or_update,
    release_content_lease, safe_rollback, worker_identity,
)
from ..content.identity import resolve_identity
from ..platform_budget import PlatformUnavailable, guarded
from ..models import (
    CanonicalContent, ContentChunk, ContentEmbedding, ContentFrame,
    ContentTranscript, ContentUnderstanding, ProcessingState,
)
from . import acquire, fastmeta, frames as frames_mod, route, understanding
from .chunking import build_document_text, chunk_text, chunk_transcript

logger = logging.getLogger(__name__)

VISION_DEPENDENCY_THRESHOLD = 0.6


# ─── Platform strategy ───────────────────────────────────────────────────────

@dataclass
class PlatformStrategy:
    name: str
    try_native_captions: bool
    allow_asr: bool
    vision_mode: str                 # always | conditional | never
    asr_max_seconds: int
    prefers_comments: bool = False

    def wants_vision(self, *, visual_dependency: float, has_transcript: bool,
                     media_kind: str) -> bool:
        # Capability first, preference second.
        #
        # `vision_mode` answers "would vision help this item?"; the capability
        # answers "may this deployment download this platform's media?".
        # Conflating them is what made turning extraction down also turn the
        # product down. With media analysis off a deployment still gets captions,
        # understanding from text, embeddings, Ask and Scroll — it just does not
        # fetch the video.
        #
        # The gate is scoped to *video* deliberately. Vision on an image or a
        # carousel reads frames Sava already mirrored, which involves no platform
        # access at all — so gating it would remove Instagram understanding for
        # no safety benefit whatsoever.
        from .. import providers

        if self.vision_mode == "never":
            return False
        if media_kind in ("image", "carousel"):
            return True          # already-stored imagery; nothing else to go on

        if not providers.media_analysis_allowed(self.name):
            return False
        if self.vision_mode == "always":
            return True
        # conditional
        if not has_transcript:
            return True          # transcript failed or content is silent
        return visual_dependency >= VISION_DEPENDENCY_THRESHOLD


YOUTUBE = PlatformStrategy(
    name="youtube", try_native_captions=True, allow_asr=True,
    vision_mode=YOUTUBE_VISION_MODE, asr_max_seconds=3600, prefers_comments=True,
)
TIKTOK = PlatformStrategy(
    name="tiktok", try_native_captions=False, allow_asr=True,
    vision_mode=TIKTOK_VISION_MODE, asr_max_seconds=900, prefers_comments=True,
)
INSTAGRAM = PlatformStrategy(
    name="instagram", try_native_captions=False, allow_asr=True,
    vision_mode=INSTAGRAM_VISION_MODE, asr_max_seconds=900,
)
GENERIC = PlatformStrategy(
    name="other", try_native_captions=False, allow_asr=False,
    vision_mode="never", asr_max_seconds=0,
)

_STRATEGIES = {"youtube": YOUTUBE, "tiktok": TIKTOK, "instagram": INSTAGRAM}


def strategy_for(platform: str) -> PlatformStrategy:
    return _STRATEGIES.get((platform or "").lower(), GENERIC)


# ─── Canonical resolution ────────────────────────────────────────────────────

def resolve_or_create_canonical(db, url: str, platform_hint: Optional[str] = None
                                ) -> Tuple[Optional[CanonicalContent], bool]:
    """Find or create the canonical row for a URL. Returns (content, created).

    This is where cross-user reuse happens: the second user to save a TikTok
    gets the first user's fully processed record for free.
    """
    ident = resolve_identity(url, platform_hint=platform_hint)
    if ident is None:
        return None, False

    existing = (
        db.query(CanonicalContent)
        .filter(CanonicalContent.content_key == ident.content_key)
        .first()
    )
    if existing:
        return existing, False

    # `resolve_identity` normalizes `/shorts/<id>` to `watch?v=<id>` so a Short
    # and a watch link stay one canonical row — correct for identity, but it
    # destroys the only unambiguous evidence that this is a Short. Record the
    # hint now, while the URL the user actually saved is still in hand.
    from ..content.shortform import is_short_form, is_shorts_url
    shorts_hint = is_shorts_url(url)

    cc = CanonicalContent(
        content_key=ident.content_key,
        platform=ident.platform,
        platform_content_id=ident.platform_content_id,
        canonical_url=ident.canonical_url,
        media_kind=ident.media_kind,
        # A poster before any network call has been made. On YouTube the
        # thumbnail URL is a function of the video id, so the card the user is
        # about to see already has an image on it — rather than a placeholder
        # that persists for as long as the worker queue is deep, or forever if
        # the extractor is being blocked. See `fastmeta.derived_thumbnail`.
        thumbnail_url=fastmeta.derived_thumbnail(ident.platform,
                                                 ident.platform_content_id),
        is_short=is_short_form(ident.platform, media_kind=ident.media_kind,
                               url_hint=url),
        metadata_json=json.dumps({"shorts_url": True}) if shorts_hint else "{}",
        processing_state=ProcessingState.QUEUED,
        processing_level=0,
        pipeline_version=PIPELINE_VERSION,
        stage_status="{}",
    )
    db.add(cc)
    try:
        db.commit()
        db.refresh(cc)
        return cc, True
    except Exception:
        db.rollback()
        found = (db.query(CanonicalContent)
                 .filter(CanonicalContent.content_key == ident.content_key).first())
        return found, False


def _set_stage(cc: CanonicalContent, stage: str, status: str, detail: str = "") -> None:
    try:
        data = json.loads(cc.stage_status or "{}")
    except Exception:
        data = {}
    data[stage] = {"status": status, "detail": detail[:200], "at": int(time.time())}
    cc.stage_status = json.dumps(data)


def _stage_ok(cc: CanonicalContent, stage: str) -> bool:
    """Has this stage already completed successfully?

    The full-extraction stages used to be gated on `not cc.title`, which was a
    fine proxy for "we have not fetched anything yet" right up until Stage A
    started filling the title from oEmbed in a quarter of a second. After that
    the proxy inverted: a successful cheap fetch would have *skipped* the yt-dlp
    pass, and with it duration, geometry, view counts and the caption track —
    trading a blank card for a permanently shallow one.

    Asking the stage record directly says what was actually meant.
    """
    try:
        data = json.loads(cc.stage_status or "{}")
    except Exception:
        return False
    entry = data.get(stage) or {}
    return str(entry.get("status", "")).startswith("ok")


def _state(db, cc: CanonicalContent, state: str, level: Optional[int] = None) -> None:
    cc.processing_state = state
    if level is not None:
        cc.processing_level = max(cc.processing_level or 0, level)
    db.commit()


# ─── The pipeline ────────────────────────────────────────────────────────────

def _meta_from_info(info: Dict[str, Any], nbytes: int):
    """Build the metadata result from an extract_info payload we already have."""
    from .acquire import AcquisitionResult
    meta = {
        "title": info.get("title"), "description": info.get("description"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "uploader_id": info.get("uploader_id") or info.get("channel_id"),
        "thumbnail": info.get("thumbnail"), "upload_date": info.get("upload_date"),
        "width": info.get("width"), "height": info.get("height"),
        "view_count": info.get("view_count"), "like_count": info.get("like_count"),
        "tags": info.get("tags") or [], "categories": info.get("categories") or [],
        "webpage_url": info.get("webpage_url"), "extractor": info.get("extractor"),
    }
    return AcquisitionResult(True, "metadata", bytes_moved=nbytes,
                             duration_s=info.get("duration"), metadata=meta)


def _read_carousel_slides(db, cc: CanonicalContent, *, router,
                          user_id: Optional[int] = None,
                          force: bool = False) -> str:
    """Read a photo post's slides as one ordered document.

    Slide order carries the meaning — title, ingredients, method, result — so
    the text is emitted with explicit indices and the whole set goes to the model
    in a single call. Analysing slides independently produces four unrelated
    image descriptions and loses the post.

    Slides already stored durably are read from object storage, so this costs no
    platform requests at all on a re-run.
    """
    from ..models import ContentAsset
    from ..storage import get_storage

    assets = (db.query(ContentAsset)
              .filter(ContentAsset.canonical_content_id == cc.id)
              .order_by(ContentAsset.asset_index).all())
    if not assets:
        return ""

    already_read = [a for a in assets if a.ocr_text or a.vision_caption]
    if already_read and not force:
        return _carousel_text(assets)

    storage = get_storage()
    images: List[bytes] = []
    usable: List[Any] = []
    for asset in assets:
        blob = storage.get(asset.storage_key) if asset.storage_key else None
        if blob is None and asset.source_url:
            from ..services import thumbnails as thumb_svc
            blob, _ = thumb_svc.fetch(asset.source_url, platform=cc.platform)
        if blob:
            images.append(blob)
            usable.append(asset)

    if not images:
        _set_stage(cc, "vision", "failed", "no slide images available")
        return ""

    try:
        from ..ai.base import Mode, TaskType

        completion = router.complete(
            TaskType.VISION_ANALYSIS,
            system=(
                "You are reading a swipeable photo post as ONE piece of content. "
                "The images are slides in the creator's order. Return STRICT JSON: "
                '{"slides":[{"i":int,"ocr":str,"caption":str}],"post_summary":str}. '
                "`ocr` is text visible on that slide, verbatim. `caption` is what "
                "the slide shows. `post_summary` is what the post as a whole is "
                "saying across all slides — treat them as a sequence, not as "
                "unrelated pictures."
            ),
            prompt=(f"{len(images)} slides follow in order (indices 0..{len(images)-1}). "
                    f"{'This appears to be ' + cc.content_type + ' content. ' if cc.content_type else ''}"
                    "Return one entry per slide plus the post-level summary."),
            mode=Mode.AUTO, json_mode=True, images=images,
            temperature=0.1, max_output_tokens=4096,
        )
        data = json.loads(completion.text or "{}")
        for entry in data.get("slides", []):
            i = int(entry.get("i", -1))
            if 0 <= i < len(usable):
                usable[i].ocr_text = (entry.get("ocr") or "").strip() or None
                usable[i].vision_caption = (entry.get("caption") or "").strip() or None
        summary = (data.get("post_summary") or "").strip()
        db.commit()

        telemetry.record_completion(
            db, completion, operation="vision.carousel", canonical_content_id=cc.id,
            user_id=user_id, platform=cc.platform, frames_processed=len(images),
        )
        _set_stage(cc, "vision", "ok", f"{len(images)} slides")
        text = _carousel_text(assets)
        return (f"Post overall: {summary}\n{text}" if summary else text)
    except Exception as e:
        logger.warning("carousel reading failed for %s: %s", cc.id, e)
        _set_stage(cc, "vision", "failed", str(e)[:160])
        return _carousel_text(assets)


def _carousel_text(assets) -> str:
    """Slide text with its position preserved."""
    lines = []
    for asset in assets:
        parts = []
        if asset.ocr_text:
            parts.append(f"on-screen: {asset.ocr_text}")
        if asset.vision_caption:
            parts.append(asset.vision_caption)
        if parts:
            lines.append(f"[slide {asset.asset_index + 1}] " + " ".join(parts))
    return "\n".join(lines)


def _mirror_cover(db, cc: CanonicalContent, *, user_id: Optional[int] = None) -> bool:
    """Put the cover image somewhere it cannot expire.

    Runs the instant metadata produces a thumbnail, not on first view. TikTok
    signs its cover URLs with an expiry measured in days, so a save that is
    never opened for a week loses its picture entirely under a lazy strategy.
    Costs one small fetch per *canonical item*, shared by every user who saves it.
    """
    from ..services import thumbnails as thumb_svc

    if not cc.thumbnail_url or cc.thumbnail_stored_key:
        return False
    try:
        mirrored = thumb_svc.mirror_to_storage(
            cc.thumbnail_url, namespace="thumbnails", platform=cc.platform)
    except Exception as e:
        logger.warning("cover mirror failed for %s: %s", cc.id, e)
        return False
    if not mirrored:
        telemetry.record(db, operation="thumbnail.mirror", canonical_content_id=cc.id,
                         user_id=user_id, platform=cc.platform, success=False,
                         error="source image unavailable")
        return False

    key, public_url = mirrored
    cc.thumbnail_stored_key = key
    cc.thumbnail_url = public_url
    db.commit()
    telemetry.record(db, operation="thumbnail.mirror", canonical_content_id=cc.id,
                     user_id=user_id, platform=cc.platform, success=True)
    return True


def _ingest_instagram(db, cc: CanonicalContent, *, user_id: Optional[int] = None,
                      force: bool = False) -> Dict[str, Any]:
    """Fill in an Instagram post through the provider chain.

    Two rules shape this, both from what Instagram actually does rather than
    from what would be convenient:

      * **Never invent.** Every field is written only when a provider supplied
        it, and `field_sources` records which one. A post whose caption could
        not be read keeps a null caption; it does not get "Instagram Post".
      * **Never lose the save.** A provider failure sets PARTIAL with a
        structured reason and returns. The canonical identity, the user's
        library reference and the original URL all survive, so the card still
        opens in Instagram and can be upgraded later without the user doing
        anything.
    """
    from ..config import INSTAGRAM_MAX_CAROUSEL_ITEMS, INSTAGRAM_MIRROR_MEDIA
    from ..models import ContentAsset
    from ..services import instagram as ig

    shortcode = cc.platform_content_id
    if not shortcode:
        # A `/share/` link that has not been followed yet. Honest, and retryable.
        _set_stage(cc, "metadata", "failed", "share link not yet resolved")
        cc.last_error = "instagram share link has not been resolved to a post id"
        db.commit()
        return {"status": "unresolved_share_link", "bytes": 0}

    result = guarded("instagram", "metadata", ig.extract_metadata,
                     shortcode, cc.canonical_url, db=db,
                     canonical_content_id=cc.id, user_id=user_id)

    if not result.ok:
        # Failure is a product state, not an error path. Say exactly why, so a
        # deleted post and a rate limit are distinguishable by anything that
        # later decides whether to retry.
        _set_stage(cc, "metadata", "failed",
                   f"{result.failure_reason}: {result.error or ''}")
        cc.last_error = f"[{result.failure_reason}] {result.error or 'extraction failed'}"[:400]
        db.commit()
        return {"status": f"failed:{result.failure_reason}",
                "bytes": result.bytes_moved, "provider": result.provider}

    meta = result.metadata
    # Only ever fills gaps. A value a provider could not supply must not erase
    # one an earlier run did.
    if meta.creator_name and not cc.creator_name:
        cc.creator_name = meta.creator_name[:255]
    if meta.creator_handle and not cc.creator_handle:
        cc.creator_handle = meta.creator_handle[:255]
    if meta.caption and not cc.description:
        cc.description = meta.caption
    if meta.caption and not cc.title:
        cc.title = meta.caption.replace("\n", " ")[:200]
    if meta.published_at and not cc.published_at:
        cc.published_at = meta.published_at
    if meta.thumbnail_url and not cc.thumbnail_url:
        cc.thumbnail_url = meta.thumbnail_url
    if meta.width and not cc.width:
        cc.width, cc.height = meta.width, meta.height
    if meta.duration_seconds and not cc.duration_seconds:
        cc.duration_seconds = int(meta.duration_seconds)
    if meta.media_kind:
        cc.media_kind = meta.media_kind
    # One classifier, shared with YouTube and TikTok, rather than a second
    # rule here that would drift from it.
    from ..content.shortform import is_short_form
    cc.is_short = is_short_form(
        cc.platform, media_kind=cc.media_kind,
        duration_seconds=cc.duration_seconds, width=cc.width, height=cc.height,
        url_hint=cc.canonical_url)

    try:
        existing_meta = json.loads(cc.metadata_json or "{}")
    except Exception:
        existing_meta = {}
    existing_meta.update({
        "shortcode": shortcode,
        "like_count": meta.like_count,
        "comment_count": meta.comment_count,
        "carousel_count": meta.carousel_count,
        # How we know each thing. Without this there is no way to tell a value
        # a provider asserted from one a later heuristic guessed.
        "field_sources": meta.provenance,
        "extracted_by": result.provider,
    })
    cc.metadata_json = json.dumps(existing_meta, default=str)[:60000]
    db.commit()

    # Instagram signs its CDN URLs with a short expiry, so the copy has to be
    # taken now rather than on first view.
    if INSTAGRAM_MIRROR_MEDIA:
        _mirror_cover(db, cc, user_id=user_id)

    stored_children = 0
    if meta.children:
        cc.media_kind = "carousel"
        existing = (db.query(ContentAsset)
                    .filter(ContentAsset.canonical_content_id == cc.id).count())
        if existing and not force:
            stored_children = existing
        else:
            (db.query(ContentAsset)
               .filter(ContentAsset.canonical_content_id == cc.id)
               .delete(synchronize_session=False))
            stored_children = _store_instagram_children(
                db, cc, meta.children[:INSTAGRAM_MAX_CAROUSEL_ITEMS],
                mirror=INSTAGRAM_MIRROR_MEDIA)
        db.commit()

    _set_stage(cc, "metadata", "ok",
               f"{result.provider}"
               + (f", {stored_children} carousel items" if stored_children else ""))
    db.commit()
    return {"status": "ok", "bytes": result.bytes_moved,
            "provider": result.provider, "carousel_items": stored_children}


def _store_instagram_children(db, cc: CanonicalContent, children: List[Dict[str, Any]],
                              *, mirror: bool) -> int:
    """Persist carousel children in order, mirroring each image.

    Same `ContentAsset` table TikTok photo posts use — the shape is identical
    (an ordered set of images belonging to one post) and a second model would
    mean the gallery viewer needing to know which platform it was rendering.
    Index 0 is the cover, which is the image the creator chose.
    """
    from ..models import ContentAsset
    from ..services import thumbnails as thumb_svc

    stored = 0
    for child in children:
        source = child.get("source_url")
        if not source:
            continue
        key = public = None
        if mirror:
            try:
                mirrored = thumb_svc.mirror_to_storage(
                    source, namespace="instagram", platform="instagram")
                if mirrored:
                    key, public = mirrored
            except Exception as e:
                logger.warning("carousel child mirror failed (%s): %s", cc.id, e)
        db.add(ContentAsset(
            canonical_content_id=cc.id,
            asset_index=int(child.get("index", stored)),
            kind="cover" if int(child.get("index", stored)) == 0 else "image",
            source_url=public or source, storage_key=key,
            width=child.get("width"), height=child.get("height"),
        ))
        stored += 1
    if stored:
        cc.metadata_json = json.dumps({
            **(json.loads(cc.metadata_json or "{}") if cc.metadata_json else {}),
            "carousel_count": stored,
        }, default=str)[:60000]
    return stored


def _ingest_carousel(db, cc: CanonicalContent, *, user_id: Optional[int] = None,
                     force: bool = False) -> Dict[str, Any]:
    """Read a TikTok photo post: metadata, ordered slides, durable copies.

    The slides *are* the content, so they are stored as assets rather than as
    disposable frames, and slide one becomes the cover — the creator chose that
    image, and picking a different one would misrepresent the post in the grid.
    """
    from ..config import CAROUSEL_MAX_SLIDES
    from ..models import ContentAsset
    from ..services import thumbnails as thumb_svc

    existing = (db.query(ContentAsset)
                .filter(ContentAsset.canonical_content_id == cc.id).count())
    if existing and not force:
        _set_stage(cc, "carousel", "ok", f"{existing} slides cached")
        return {"status": f"cached ({existing} slides)", "bytes": 0}

    fetched = guarded(cc.platform, "carousel", acquire.fetch_carousel,
                      cc.canonical_url, CAROUSEL_MAX_SLIDES,
                      db=db, canonical_content_id=cc.id, user_id=user_id)
    telemetry.record(
        db, operation="acquire.carousel", canonical_content_id=cc.id, user_id=user_id,
        platform=cc.platform, proxy_bytes=fetched.bytes_moved, wall_ms=fetched.wall_ms,
        success=fetched.ok, error=fetched.error,
    )
    if not fetched.ok:
        _set_stage(cc, "carousel", "failed", fetched.error or "")
        return {"status": f"failed: {fetched.error}", "bytes": fetched.bytes_moved}

    meta = fetched.metadata
    cc.title = cc.title or meta.get("title")
    cc.description = cc.description or meta.get("description")
    cc.creator_name = cc.creator_name or meta.get("uploader")
    cc.creator_handle = cc.creator_handle or meta.get("uploader_id")
    cc.media_kind = "carousel"
    cc.is_short = True
    cc.metadata_json = json.dumps(meta, default=str)[:60000]
    db.commit()

    (db.query(ContentAsset)
       .filter(ContentAsset.canonical_content_id == cc.id)
       .delete(synchronize_session=False))

    stored = 0
    for index, slide in enumerate(meta.get("slides") or []):
        source = slide.get("url")
        if not source:
            continue
        key = public = None
        try:
            mirrored = thumb_svc.mirror_to_storage(
                source, namespace="slides", platform=cc.platform)
            if mirrored:
                key, public = mirrored
        except Exception as e:
            logger.warning("slide %s mirror failed for %s: %s", index, cc.id, e)

        db.add(ContentAsset(
            canonical_content_id=cc.id, asset_index=index,
            kind="cover" if index == 0 else "image",
            source_url=source, storage_key=key,
            width=slide.get("width"), height=slide.get("height"),
        ))
        stored += 1
        if index == 0:
            # Slide one is the cover, always.
            cc.thumbnail_url = public or source
            cc.thumbnail_stored_key = key
    db.commit()

    _set_stage(cc, "carousel", "ok", f"{stored} slides")
    telemetry.record(db, operation="carousel.slides", canonical_content_id=cc.id,
                     user_id=user_id, platform=cc.platform,
                     frames_processed=stored, success=True)
    return {"status": f"ok ({stored} slides)", "bytes": fetched.bytes_moved}


def _queue_comments(db, cc: CanonicalContent, *, user_id: Optional[int] = None) -> None:
    """Schedule the comment sample if the platform supports it and it is stale."""
    from ..config import COMMENTS_ENABLED
    from ..jobs import enqueue
    from ..services.comments import is_stale, provider_for

    if not COMMENTS_ENABLED:
        return
    provider = provider_for(cc.platform)
    if provider is None or not provider.available or not is_stale(cc):
        return
    try:
        enqueue(
            db, "content.comments",
            {"canonical_id": cc.id, "user_id": user_id},
            # Versioned so a comment-policy change re-queues, while an ordinary
            # second save of the same video does not.
            idempotency_key=f"content.comments:{cc.id}:v{cc.comment_version or 0}",
            platform=cc.platform,
            priority=900,          # behind every piece of user-visible work
            max_attempts=2,        # optional enrichment does not deserve five tries
        )
    except Exception as e:
        logger.warning("could not queue comments for %s: %s", cc.id, e)


def _metadata_lists_captions(cc: CanonicalContent) -> bool:
    """Did the metadata call report a caption track for this item?

    The metadata response already carries `subtitles` and `automatic_captions`.
    Consulting it costs nothing and is the difference between "this platform
    generally has captions" (the old per-platform boolean) and "this *item*
    has captions" — which is what actually decides whether free text exists.

    It matters most where the old flag said no: TikTok and Instagram do expose
    caption tracks on some items, and the previous code never looked.
    """
    try:
        meta = json.loads(cc.metadata_json or "{}")
    except Exception:
        return False
    return bool(meta.get("subtitles") or meta.get("automatic_captions"))


def _asr_available() -> bool:
    """Is speech-to-text actually configured on this deployment?

    Asked before routing, so an item is never sent down the audio path on a
    server that cannot transcribe — which would download the bytes and then
    throw them away.
    """
    try:
        from ..asr import get_asr
        return bool(get_asr().available)
    except Exception:
        return False


def _read_cover(db, cc: CanonicalContent, *, router,
                user_id: Optional[int] = None,
                force: bool = False) -> Tuple[Dict[str, Any], str]:
    """Read the stored cover image. The cheapest visual understanding we have.

    Zero bandwidth: the cover was already mirrored into object storage so the
    library grid could draw it. Reading it is one small vision call on an image
    Sava already owns.

    Persisted as a `ContentFrame` at ts=0 so it is cached like any other frame,
    survives a re-run, and flows into embeddings through the existing path.
    """
    from ..storage import get_storage

    existing = (db.query(ContentFrame)
                .filter(ContentFrame.canonical_content_id == cc.id,
                        ContentFrame.ts_ms == 0).first())
    if existing is not None and not force:
        return ({"enough": True},
                frames_mod.cover_text({"ocr": existing.ocr_text,
                                       "caption": existing.vision_caption}))

    blob = None
    if cc.thumbnail_stored_key:
        try:
            blob = get_storage().get(cc.thumbnail_stored_key)
        except Exception as e:
            logger.debug("cover fetch from storage failed for %s: %s", cc.id, e)
    if blob is None and cc.thumbnail_url:
        try:
            from ..services import thumbnails as thumb_svc
            blob, _ = thumb_svc.fetch(cc.thumbnail_url, platform=cc.platform)
        except Exception as e:
            logger.debug("cover fetch from source failed for %s: %s", cc.id, e)

    if not blob:
        _set_stage(cc, "cover", "skipped", "no cover image available")
        return {}, ""

    try:
        read, completion = frames_mod.analyze_cover(
            blob, router=router, content_hint=cc.content_type)
    except Exception as e:
        logger.warning("cover read failed for %s: %s", cc.id, e)
        _set_stage(cc, "cover", "failed", str(e)[:160])
        return {}, ""

    if completion is not None:
        telemetry.record_completion(
            db, completion, operation="vision.cover", canonical_content_id=cc.id,
            user_id=user_id, platform=cc.platform, frames_processed=1)

    text = frames_mod.cover_text(read)
    if text:
        if existing is None:
            db.add(ContentFrame(canonical_content_id=cc.id, ts_ms=0,
                                ocr_text=read.get("ocr") or None,
                                vision_caption=read.get("caption") or None))
        else:
            existing.ocr_text = read.get("ocr") or None
            existing.vision_caption = read.get("caption") or None
        db.commit()
        _set_stage(cc, "cover", "ok", f"{len(text)} chars")
    else:
        _set_stage(cc, "cover", "ok", "nothing readable")
    return read, text


def process_content(canonical_id: int, db, *, force: bool = False,
                    user_id: Optional[int] = None,
                    deep: bool = False,
                    want_vision: bool = False) -> Dict[str, Any]:
    """Run the ladder for one canonical item. Idempotent and resumable.

    `deep` is the only way to reach the widest frame budget. It comes from an
    explicit user request (Pro enhanced analysis), never from a heuristic — the
    whole point of the routing layer is that the expensive path is not the
    default for an ordinary TikTok.

    `want_vision` is lazy escalation: an item understood from text alone, which
    somebody has now asked a question about that only the picture can answer.
    It forces the light frames route and nothing else — every earlier stage is
    already guarded on `force`, so a re-run reuses the cached metadata,
    transcript and classification and pays only for the frames.
    """
    from ..ai.router import get_router

    cc = db.query(CanonicalContent).get(canonical_id)
    if cc is None:
        return {"ok": False, "error": "canonical content not found"}

    if (cc.processing_state == ProcessingState.READY
            and cc.pipeline_version == PIPELINE_VERSION and not force
            and not (want_vision or deep)):
        return {"ok": True, "skipped": "already ready", "cache_hit": True}

    # Escalation on an item that already has frames is a no-op, not a re-run.
    # Two Asks racing, a retried job, or a user asking three visual questions in
    # a row must not each pay for another download.
    if (want_vision and not force and not deep
            and db.query(ContentFrame)
                 .filter(ContentFrame.canonical_content_id == cc.id,
                         ContentFrame.ts_ms > 0).count() > 0):
        return {"ok": True, "skipped": "visual intelligence already cached",
                "cache_hit": True, "route": cc.route}

    result: Dict[str, Any] = {"stages": {}, "canonical_id": cc.id}
    total_bytes = 0
    workdirs: List[str] = []

    # ── One worker per canonical item, from here on ──────────────────────────
    #
    # Everything above this line is a cache check and costs nothing, so it stays
    # lock-free. Everything below it spends money: a proxied download, an ASR
    # call, frame extraction, model calls. Two workers reaching this point for
    # the same content — two jobs with different idempotency keys (a save and a
    # visual-Ask escalation), or a retry overlapping a slow first attempt — used
    # to run the whole ladder twice and then collide on a UNIQUE constraint at
    # the very end, after both had already paid.
    #
    # The lease is taken with a compare-and-swap UPDATE, so it is one statement
    # and one winner, on SQLite and Postgres alike. The loser raises ContentBusy
    # and the queue parks its job for a moment rather than failing it.
    lease_owner = worker_identity()
    if not acquire_content_lease(db, cc.id, owner=lease_owner):
        raise ContentBusy(cc.id)

    try:
        # Inside the lease and inside the try, both deliberately. Everything
        # from here is either billable or capable of failing, and the failure
        # handling below is what turns that into a recorded partial state
        # rather than an exception escaping to the queue.
        router = get_router()
        strat = strategy_for(cc.platform)

        # ── Stage A: cheap metadata, before anything expensive ──────────────
        #
        # One small public request, no auth and no proxy, so the card stops
        # saying "youtube.com" within about a quarter of a second of the worker
        # picking the job up — instead of after a yt-dlp extraction that, in
        # production, is currently losing to an anti-bot challenge.
        #
        # Runs first *and* unconditionally-ish so that everything after it is
        # an improvement rather than a prerequisite: if the full extraction
        # below fails outright, the save still has a title, a creator and a
        # poster, and reads as a real item rather than as breakage.
        if force or not cc.title or not cc.thumbnail_url:
            fast = fastmeta.fetch(cc.platform, cc.canonical_url,
                                  content_id=cc.platform_content_id)
            if fast.useful and fastmeta.apply(cc, fast):
                _set_stage(cc, "fastmeta", "ok", fast.source)
                db.commit()
                logger.info("fastmeta %s: %s in %dms (canonical %s)",
                            cc.platform, fast.source, fast.wall_ms, cc.id)
            result["stages"]["fastmeta"] = fast.source
            telemetry.record(
                db, operation="acquire.fastmeta", canonical_content_id=cc.id,
                user_id=user_id, platform=cc.platform, wall_ms=fast.wall_ms,
                success=fast.useful, error=None if fast.useful else "no_cheap_metadata")

        # ── L1+L2 combined for caption platforms ────────────────────────────
        # On YouTube, one yt-dlp extract_info yields metadata AND the caption
        # track list. Fetching them separately would double the bytes pulled
        # through a paid residential proxy for zero benefit.
        prefetched_captions = None
        if strat.try_native_captions and (force or not _stage_ok(cc, "metadata")):
            prefetched_captions = guarded(
                cc.platform, "captions", acquire.fetch_captions_via_ytdlp,
                cc.canonical_url, db=db, canonical_content_id=cc.id, user_id=user_id)

        # ── L1: metadata ────────────────────────────────────────────────────
        _state(db, cc, ProcessingState.FETCHING, 1)
        if cc.platform == "instagram":
            # Instagram never goes through yt-dlp metadata: that path requires
            # an operator account, and the provider chain exists precisely so
            # this decision lives in one place.
            ig_result = _ingest_instagram(db, cc, user_id=user_id, force=force)
            total_bytes += ig_result.get("bytes", 0)
            result["stages"]["metadata"] = ig_result.get("status", "skipped")
            if not str(ig_result.get("status", "")).startswith("ok"):
                # Keep the content, describe the state honestly, and stop —
                # there is nothing downstream that can run without metadata.
                _state(db, cc, ProcessingState.PARTIAL, 1)
                result["partial"] = True
                return result
        elif cc.media_kind == "carousel" and cc.platform == "tiktok":
            # A photo post is read as an ordered image set, not as a video with
            # a missing file. Handled before the video path so nothing tries to
            # download an MP4 that was never going to exist.
            carousel = _ingest_carousel(db, cc, user_id=user_id, force=force)
            total_bytes += carousel.get("bytes", 0)
            result["stages"]["carousel"] = carousel.get("status", "skipped")
        elif force or not _stage_ok(cc, "metadata"):
            if prefetched_captions is not None and prefetched_captions.ok:
                meta = _meta_from_info(prefetched_captions.metadata.get("info") or {},
                                       prefetched_captions.bytes_moved)
            else:
                meta = guarded(
                    cc.platform, "metadata", acquire.fetch_metadata,
                    cc.canonical_url, db=db, canonical_content_id=cc.id,
                    user_id=user_id)
            total_bytes += meta.bytes_moved
            if meta.ok:
                m = meta.metadata
                cc.title = cc.title or m.get("title")
                cc.description = cc.description or m.get("description")
                cc.creator_name = cc.creator_name or m.get("uploader")
                cc.creator_handle = cc.creator_handle or m.get("uploader_id")
                cc.duration_seconds = cc.duration_seconds or m.get("duration")
                cc.thumbnail_url = cc.thumbnail_url or m.get("thumbnail")
                if m.get("duration"):
                    cc.media_kind = "video"
                cc.width = cc.width or m.get("width")
                cc.height = cc.height or m.get("height")

                # Now that duration and geometry are known, the classification
                # can be made properly rather than guessed from the URL.
                from ..content.shortform import is_short_form
                try:
                    prior = json.loads(cc.metadata_json or "{}")
                except Exception:
                    prior = {}
                hint = m.get("webpage_url") or cc.canonical_url
                cc.is_short = bool(prior.get("shorts_url")) or is_short_form(
                    cc.platform, media_kind=cc.media_kind,
                    duration_seconds=cc.duration_seconds,
                    width=cc.width, height=cc.height, url_hint=hint)
                if prior.get("shorts_url"):
                    m = {**m, "shorts_url": True}
                cc.metadata_json = json.dumps(m, default=str)[:60000]

                # Canonicalize short links. A TikTok /t/, vm., or vt. URL has
                # no video id in it, so the row was keyed on a URL hash. Now
                # that the platform has told us the real page, upgrade the key
                # so every URL shape for this video shares one canonical row.
                real_url = m.get("webpage_url")
                if real_url:
                    from ..content.identity import upgrade_identity
                    better = upgrade_identity(cc.content_key, real_url)
                    if better is not None:
                        merged = (db.query(CanonicalContent)
                                  .filter(CanonicalContent.content_key == better.content_key)
                                  .filter(CanonicalContent.id != cc.id).first())
                        if merged is None:
                            logger.info("canonical %s upgraded: %s -> %s",
                                        cc.id, cc.content_key, better.content_key)
                            cc.content_key = better.content_key
                            cc.platform_content_id = better.platform_content_id
                            cc.canonical_url = better.canonical_url
                            db.commit()
                        else:
                            # Another row already owns the real id — point this
                            # user's saves at it rather than duplicating work.
                            logger.info("canonical %s merges into %s (%s)",
                                        cc.id, merged.id, better.content_key)
                            from ..models import Bookmark as _BM
                            (db.query(_BM)
                               .filter(_BM.canonical_content_id == cc.id)
                               .update({_BM.canonical_content_id: merged.id},
                                       synchronize_session=False))
                            db.commit()
                            result["merged_into"] = merged.id
                            return {"ok": True, "merged_into": merged.id,
                                    "canonical_id": merged.id,
                                    "stages": result["stages"]}

                _mirror_cover(db, cc, user_id=user_id)
                _set_stage(cc, "metadata", "ok")
                result["stages"]["metadata"] = "ok"
            else:
                _set_stage(cc, "metadata", "failed", meta.error or "")
                result["stages"]["metadata"] = f"failed: {meta.error}"
            telemetry.record(
                db, operation="acquire.metadata", canonical_content_id=cc.id,
                user_id=user_id, platform=cc.platform, proxy_bytes=meta.bytes_moved,
                wall_ms=meta.wall_ms, estimated_usd=telemetry.proxy_cost(meta.bytes_moved),
                success=meta.ok, error=meta.error,
            )
            db.commit()
        else:
            result["stages"]["metadata"] = "cached"

        duration = float(cc.duration_seconds or 0)

        # ── Classification, BEFORE any media is fetched ─────────────────────
        #
        # This used to run *after* transcript acquisition, which is what made
        # TikTok expensive: the transcript stage asked `wants_vision()` while
        # `visual_dependency` was still None, defaulted it to 0.5, saw no
        # transcript, and downloaded the whole video — every time, on every
        # TikTok, before anything had an opinion about whether the video was
        # needed. See the note at the top of `pipeline/route.py`.
        #
        # Classification needs only metadata: title, creator, caption, duration.
        # It costs ~$0.0002 on the cheap model. Running it here buys the routing
        # decision for a fraction of a percent of what the download costs.
        _state(db, cc, ProcessingState.ANALYZING, 2)
        transcript_row = (db.query(ContentTranscript)
                          .filter(ContentTranscript.canonical_content_id == cc.id)
                          .first())
        transcript_text = transcript_row.text if transcript_row else ""

        if not cc.content_type or force:
            cls, comp = understanding.classify(
                router=router, title=cc.title, creator=cc.creator_name,
                caption=cc.description, transcript_head=transcript_text[:1500],
                platform=cc.platform, duration_s=duration,
            )
            cc.content_type = cls.get("content_type")
            cc.content_type_confidence = cls.get("confidence")
            cc.visual_dependency = cls.get("visual_dependency")
            db.commit()
            if comp is not None:
                telemetry.record_completion(
                    db, comp, operation="classify", canonical_content_id=cc.id,
                    user_id=user_id, platform=cc.platform,
                )
            result["stages"]["classify"] = (
                f"{cc.content_type} (vd={cc.visual_dependency:.2f})"
                if cc.visual_dependency is not None else f"{cc.content_type}")
        else:
            result["stages"]["classify"] = "cached"

        # ── Route: the cheapest path that should be good enough ─────────────
        from .. import providers

        caption_track_available = bool(
            strat.try_native_captions
            or (prefetched_captions is not None and prefetched_captions.ok)
            or _metadata_lists_captions(cc))

        signals = route.Signals(
            platform=cc.platform,
            media_kind=cc.media_kind or "unknown",
            duration_seconds=duration,
            caption_chars=len(cc.description or ""),
            transcript_chars=len(transcript_text),
            has_caption_track=caption_track_available,
            has_cover=bool(cc.thumbnail_stored_key or cc.thumbnail_url),
            visual_dependency=cc.visual_dependency,
            content_type=cc.content_type,
            media_allowed=providers.media_analysis_allowed(cc.platform),
            asr_available=(strat.allow_asr and _asr_available()
                           and 0 < duration <= strat.asr_max_seconds),
            force_deep=bool(deep),
            force_vision=bool(want_vision),
        )
        plan = route.decide(signals)
        cc.route = plan.route.value
        cc.route_reason = plan.reason[:200]
        db.commit()
        result["route"] = plan.route.value
        result["route_reason"] = plan.reason
        logger.info("canonical %s (%s) -> route=%s (%s)",
                    cc.id, cc.platform, plan.route.value, plan.reason)

        # ── L2: transcript, only as far as the route requires ───────────────
        video_path: Optional[str] = None

        # Re-read before deciding to spend money.
        #
        # `transcript_row` was read ~150 lines ago, before classification and
        # routing. On a retry — or alongside a worker that started moments
        # earlier — the transcript can have arrived in the meantime, and acting
        # on the stale answer meant re-downloading the video and re-running ASR
        # to produce a row that already existed. The duplicate INSERT was the
        # symptom; this re-read is where the money was actually lost.
        if not force:
            transcript_row = (db.query(ContentTranscript)
                              .filter(ContentTranscript.canonical_content_id == cc.id)
                              .first())
            if transcript_row is not None:
                transcript_text = transcript_row.text or transcript_text

        if transcript_row and not force:
            result["stages"]["transcript"] = "cached"
        elif cc.media_kind in ("image", "carousel"):
            result["stages"]["transcript"] = "skipped: no audio"
            _set_stage(cc, "transcript", "skipped", "image content")
        else:
            _state(db, cc, ProcessingState.TRANSCRIBING, 2)
            segments, source, lang, asr_seconds = [], None, "en", 0.0
            asr_provider = "none"

            # Captions are free text and are always worth asking for when the
            # platform lists a track. The metadata call already returned the
            # track list, so this costs nothing when there is nothing to fetch.
            if caption_track_available:
                cap = prefetched_captions or guarded(
                    cc.platform, "captions", acquire.fetch_native_captions,
                    cc.canonical_url, db=db, canonical_content_id=cc.id,
                    user_id=user_id)
                if prefetched_captions is None:
                    total_bytes += cap.bytes_moved
                    telemetry.record(
                        db, operation="acquire.captions", canonical_content_id=cc.id,
                        user_id=user_id, platform=cc.platform,
                        proxy_bytes=cap.bytes_moved, wall_ms=cap.wall_ms,
                        success=cap.ok, error=cap.error,
                    )
                if cap.ok:
                    segments = cap.metadata.get("segments") or []
                    source, lang = "captions", cap.metadata.get("language", "en")

            # Media is fetched ONLY when the route asked for it, and audio-only
            # whenever frames are not also needed. The old code downloaded the
            # full video whenever it thought vision was *likely*, which on
            # TikTok was always.
            if not segments and plan.route.needs_transcript:
                dl = None
                audio_path = None
                if plan.needs_video:
                    dl = guarded(
                        cc.platform, "download_video", acquire.download_video_lowres,
                        cc.canonical_url, db=db, canonical_content_id=cc.id,
                        user_id=user_id)
                    if dl.ok:
                        video_path = dl.path
                        workdirs.append(dl.path)
                        audio_path = acquire.extract_audio_from_video(dl.path) or dl.path
                elif plan.needs_audio:
                    dl = guarded(
                        cc.platform, "download_audio", acquire.download_audio,
                        cc.canonical_url, db=db, canonical_content_id=cc.id,
                        user_id=user_id)
                    if dl.ok:
                        audio_path = dl.path
                        workdirs.append(dl.path)

                if dl is not None:
                    total_bytes += dl.bytes_moved
                    telemetry.record(
                        db, operation=f"acquire.{dl.kind}", canonical_content_id=cc.id,
                        user_id=user_id, platform=cc.platform,
                        proxy_bytes=dl.bytes_moved, wall_ms=dl.wall_ms,
                        estimated_usd=telemetry.proxy_cost(dl.bytes_moved),
                        success=dl.ok, error=dl.error,
                    )

                if audio_path:
                    asr = guarded("asr", "transcribe", acquire.transcribe_audio,
                                  audio_path, db=db, canonical_content_id=cc.id,
                                  user_id=user_id)
                    asr_provider = asr.metadata.get("provider") or "none"
                    if asr.ok:
                        segments = asr.metadata.get("segments") or []
                        source = "asr"
                        lang = asr.metadata.get("language", "en")
                        asr_seconds = asr.duration_s or duration
                    telemetry.record(
                        db, operation="asr", canonical_content_id=cc.id, user_id=user_id,
                        platform=cc.platform, audio_seconds=asr_seconds,
                        wall_ms=asr.wall_ms,
                        estimated_usd=telemetry.asr_cost(
                            asr_seconds, local=(asr_provider == "local-whisper")),
                        model=asr.metadata.get("model"), provider=asr_provider,
                        success=asr.ok, error=asr.error,
                    )
            elif not segments:
                _set_stage(cc, "transcript", "skipped", f"route={plan.route.value}")
                result["stages"]["transcript"] = f"skipped (route={plan.route.value})"

            if segments:
                full_text = " ".join(s.get("text", "") for s in segments).strip()
                # INSERT ... ON CONFLICT DO NOTHING, not add-then-commit.
                #
                # The UNIQUE constraint on (content, lang, source) is correct and
                # stays. What was wrong was asking the database to enforce it by
                # raising: another worker finishing this same transcript first is
                # an ordinary outcome, not an exception, and treating it as one
                # killed the job *and* left the session needing a rollback.
                insert_or_ignore(
                    db, ContentTranscript,
                    {"canonical_content_id": cc.id, "source": source or "asr",
                     "lang": lang, "text": full_text,
                     "segments": json.dumps(segments, default=str),
                     "provider": "youtube" if source == "captions" else asr_provider,
                     "audio_seconds": asr_seconds or duration, "is_complete": True},
                    index_elements=["canonical_content_id", "lang", "source"],
                )
                # Read back whichever row won. Ours or theirs, it is the same
                # transcript of the same content.
                transcript_row = (db.query(ContentTranscript)
                                  .filter(ContentTranscript.canonical_content_id == cc.id,
                                          ContentTranscript.lang == lang,
                                          ContentTranscript.source == (source or "asr"))
                                  .first())
                full_text = transcript_row.text if transcript_row else full_text
                transcript_text = full_text
                _set_stage(cc, "transcript", "ok", source or "")
                result["stages"]["transcript"] = f"ok ({source}, {len(segments)} segments)"
            elif plan.route.needs_transcript:
                _set_stage(cc, "transcript", "failed", "no transcript obtainable")
                result["stages"]["transcript"] = "failed"

        transcript_text = transcript_row.text if transcript_row else transcript_text

        # ── The cover read: visual understanding for zero bandwidth ─────────
        visual_text = ""
        cover_read: Dict[str, Any] = {}
        if plan.reads_cover and cc.media_kind not in ("carousel",):
            cover_read, cover_text_value = _read_cover(
                db, cc, router=router, user_id=user_id, force=force)
            visual_text = cover_text_value
            result["stages"]["cover"] = (
                f"ok ({len(visual_text)} chars)" if visual_text else "nothing readable")

        # ── Escalate, but only on evidence ──────────────────────────────────
        escalation = route.should_escalate_after_text(
            signals, plan,
            transcript_chars=len(transcript_text),
            cover_text_chars=len(visual_text),
        )
        if escalation is not None and not (cover_read.get("enough", True) is True
                                           and len(visual_text) >= route.MIN_TRANSCRIPT_CHARS):
            plan = escalation
            cc.route = plan.route.value
            cc.route_reason = plan.reason[:200]
            db.commit()
            result["route"] = plan.route.value
            result["route_reason"] = plan.reason
            logger.info("canonical %s escalated -> %s (%s)",
                        cc.id, plan.route.value, plan.reason)

        # ── L3: frames, only when the route ended up needing them ───────────
        existing_frames = (db.query(ContentFrame)
                           .filter(ContentFrame.canonical_content_id == cc.id).count())

        if cc.media_kind == "carousel":
            visual_text = _read_carousel_slides(db, cc, router=router,
                                                user_id=user_id, force=force)
            result["stages"]["vision"] = (f"slides ({len(visual_text.splitlines())} read)"
                                          if visual_text else "slides: nothing readable")
        elif existing_frames and not force:
            rows = (db.query(ContentFrame)
                    .filter(ContentFrame.canonical_content_id == cc.id)
                    .order_by(ContentFrame.ts_ms).all())
            visual_text = "\n".join(
                f"[{r.ts_ms//60000}:{(r.ts_ms//1000)%60:02d}] "
                + " ".join(filter(None, [
                    f"on-screen: {r.ocr_text}" if r.ocr_text else "",
                    r.vision_caption or ""]))
                for r in rows
            ).strip()
            result["stages"]["vision"] = "cached"
        elif not plan.needs_video:
            result["stages"]["vision"] = f"skipped (route={plan.route.value})"
            _set_stage(cc, "vision", "skipped", plan.route.value)
        elif not frames_mod.ffmpeg_available():
            result["stages"]["vision"] = "skipped: ffmpeg unavailable"
            _set_stage(cc, "vision", "skipped", "ffmpeg missing")
        else:
            try:
                if video_path is None:
                    dl = guarded(
                        cc.platform, "download_video", acquire.download_video_lowres,
                        cc.canonical_url, db=db, canonical_content_id=cc.id,
                        user_id=user_id)
                    total_bytes += dl.bytes_moved
                    telemetry.record(
                        db, operation="acquire.video", canonical_content_id=cc.id,
                        user_id=user_id, platform=cc.platform, proxy_bytes=dl.bytes_moved,
                        wall_ms=dl.wall_ms,
                        estimated_usd=telemetry.proxy_cost(dl.bytes_moved),
                        success=dl.ok, error=dl.error,
                    )
                    video_path = dl.path if dl.ok else None
                    if dl.ok:
                        workdirs.append(dl.path)

                if video_path:
                    ts = frames_mod.select_timestamps(
                        video_path, duration or None,
                        max_frames=plan.frame_budget or MAX_FRAMES_PER_VIDEO)
                    picked = frames_mod.extract_frames(video_path, ts)
                    picked = frames_mod.deduplicate(picked)
                    if picked:
                        picked, vcomp = frames_mod.analyze_frames(
                            picked, router=router, content_hint=cc.content_type
                        )
                        for f in picked:
                            db.add(ContentFrame(
                                canonical_content_id=cc.id, ts_ms=f.ts_ms, phash=f.phash,
                                ocr_text=f.ocr_text, vision_caption=f.vision_caption,
                            ))
                        db.commit()
                        frame_text = frames_mod.collect_visual_text(picked)
                        # Keep the cover reading: it is already paid for and it
                        # describes frame one, which the sampler skips.
                        visual_text = "\n".join(filter(None, [visual_text, frame_text]))
                        if vcomp is not None:
                            telemetry.record_completion(
                                db, vcomp, operation="vision", canonical_content_id=cc.id,
                                user_id=user_id, platform=cc.platform,
                                frames_processed=len(picked),
                            )
                        frames_mod.cleanup_frames(picked)
                        _set_stage(cc, "vision", "ok", f"{len(picked)} frames")
                        result["stages"]["vision"] = f"ok ({len(picked)} frames from {len(ts)} candidates)"
                    else:
                        _set_stage(cc, "vision", "failed", "no frames extracted")
                        result["stages"]["vision"] = "failed: no frames"
                else:
                    _set_stage(cc, "vision", "failed", "download failed")
                    result["stages"]["vision"] = "failed: download"
            except Exception as e:
                logger.warning("vision stage failed for %s: %s", cc.id, e)
                _set_stage(cc, "vision", "failed", str(e))
                result["stages"]["vision"] = f"failed: {e}"

        # ── L4: structured understanding ────────────────────────────────────
        long_form = duration > LAZY_SUMMARY_OVER_SECONDS
        has_understanding = (db.query(ContentUnderstanding)
                             .filter(ContentUnderstanding.canonical_content_id == cc.id)
                             .first())
        if has_understanding and not force:
            result["stages"]["understanding"] = "cached"
        elif long_form and not force:
            # Deferred to first open: a 30-minute video is the expensive case and
            # most long saves are never reopened.
            result["stages"]["understanding"] = "deferred (long-form, on first open)"
            _set_stage(cc, "understanding", "deferred", f"{int(duration)}s")
        elif not (transcript_text or visual_text or cc.description or cc.title):
            result["stages"]["understanding"] = "skipped: no signal"
        else:
            rec, comp = understanding.extract(
                router=router, content_type=cc.content_type or "other",
                title=cc.title, creator=cc.creator_name, caption=cc.description,
                description=None, transcript=transcript_text, visual_text=visual_text,
                long_form=False,
            )
            if rec:
                _upsert_understanding(db, cc.id, rec)
                _set_stage(cc, "understanding", "ok")
                result["stages"]["understanding"] = f"ok (sources: {','.join(rec.get('sources_used', []))})"
            else:
                _set_stage(cc, "understanding", "failed")
                result["stages"]["understanding"] = "failed"
            if comp is not None:
                telemetry.record_completion(
                    db, comp, operation="understanding", canonical_content_id=cc.id,
                    user_id=user_id, platform=cc.platform,
                )

        # ── L4b: chunks + embeddings ────────────────────────────────────────
        emb_stats = build_embeddings(db, cc.id, user_id=user_id, force=force,
                                     visual_text=visual_text)
        result["stages"]["embeddings"] = emb_stats

        # ── Final state ─────────────────────────────────────────────────────
        stages = json.loads(cc.stage_status or "{}")
        failed = [k for k, v in stages.items() if v.get("status") == "failed"]
        cc.processing_state = ProcessingState.PARTIAL if failed else ProcessingState.READY
        cc.processing_level = 4
        cc.pipeline_version = PIPELINE_VERSION
        cc.last_error = ", ".join(failed) if failed else None
        db.commit()

        # Comments are enrichment, so they are queued *after* the item is
        # already READY, at a priority that never competes with a fresh save.
        _queue_comments(db, cc, user_id=user_id)

        result["ok"] = True
        result["state"] = cc.processing_state
        result["proxy_bytes"] = total_bytes
        return result

    except PlatformUnavailable:
        # The platform is throttled or circuit-open. Keep the content in a
        # truthful "still working" state and let the queue retry later —
        # this is not a failure of the save.
        safe_rollback(db)
        cc.processing_state = ProcessingState.QUEUED
        db.commit()
        raise
    except Exception as e:
        logger.exception("pipeline failed for canonical %s", canonical_id)
        # The session may be mid-failed-transaction — an IntegrityError from a
        # racing writer is the common case — and recording the outcome requires
        # a commit. Roll back first, or this handler dies with
        # PendingRollbackError and hides the failure it was reporting.
        safe_rollback(db)
        # Preserve whatever we did acquire. If any stage succeeded the item is
        # still partially useful, so never downgrade it to a bare failure.
        try:
            stages = json.loads(cc.stage_status or "{}")
        except Exception:
            stages = {}
        any_ok = any(v.get("status") == "ok" for v in stages.values())
        cc.processing_state = (ProcessingState.PARTIAL if any_ok
                               else ProcessingState.FAILED)
        cc.last_error = str(e)[:1000]
        db.commit()
        return {"ok": False, "error": str(e), "canonical_id": canonical_id}
    finally:
        release_content_lease(db, cc.id, owner=lease_owner)
        for w in workdirs:
            acquire.cleanup(w)


def _upsert_understanding(db, canonical_id: int, rec: Dict[str, Any]) -> None:
    """Write the understanding record, whether or not one already exists.

    Named "upsert" and now actually one. `canonical_content_id` is this table's
    primary key, so the previous check-then-insert had the same race as the
    embedding write: two workers both saw no row and both inserted. The lease in
    `process_content` should prevent them from ever getting here together, but a
    write that cannot be made to fail is better than a write that relies on a
    lock upstream staying correct forever.
    """
    insert_or_update(
        db, ContentUnderstanding,
        {"canonical_content_id": canonical_id,
         "schema_version": UNDERSTANDING_SCHEMA_VERSION,
         "content_type": rec.get("content_type"),
         "tl_dr": rec.get("tl_dr"),
         "key_points": json.dumps(rec.get("key_points", [])),
         "topics": json.dumps(rec.get("topics", [])),
         "entities": json.dumps(rec.get("entities", {})),
         "typed_data": json.dumps(rec.get("typed_data", {})),
         "chapters": json.dumps(rec.get("chapters", [])),
         "sources_used": json.dumps(rec.get("sources_used", []))},
        index_elements=["canonical_content_id"],
    )


def build_embeddings(db, canonical_id: int, *, user_id: Optional[int] = None,
                     force: bool = False, visual_text: str = "") -> str:
    """Chunk + embed. Batched, and skipped entirely when already current."""
    from ..ai.router import get_router
    from ..config import EMBED_BATCH, EMBED_DIM
    from ..vectors import to_storage

    cc = db.query(CanonicalContent).get(canonical_id)
    if cc is None:
        return "no content"

    existing = (db.query(ContentChunk)
                .filter(ContentChunk.canonical_content_id == canonical_id).count())
    doc_row = (db.query(ContentEmbedding)
               .filter(ContentEmbedding.canonical_content_id == canonical_id).first())
    if existing and doc_row is not None and not force:
        return "cached"

    router = get_router()
    if not router.is_available():
        return "skipped: no AI provider"

    tr = (db.query(ContentTranscript)
          .filter(ContentTranscript.canonical_content_id == canonical_id).first())
    und = (db.query(ContentUnderstanding)
           .filter(ContentUnderstanding.canonical_content_id == canonical_id).first())

    chunks = []
    if tr and tr.segments:
        try:
            chunks.extend(chunk_transcript(json.loads(tr.segments)))
        except Exception as e:
            logger.warning("chunking transcript failed: %s", e)
    if cc.media_kind == "carousel":
        # A photo post's slides are the content. They keep their own modality so
        # retrieval and Ask can say "slide 3 showed…" rather than pretending the
        # text was spoken.
        from ..models import ContentAsset
        slides = (db.query(ContentAsset)
                  .filter(ContentAsset.canonical_content_id == canonical_id)
                  .order_by(ContentAsset.asset_index).all())
        for slide in slides:
            blob = " ".join(filter(None, [
                f"on-screen: {slide.ocr_text}" if slide.ocr_text else "",
                slide.vision_caption or ""]))
            if blob:
                chunks.extend(chunk_text(f"[slide {slide.asset_index + 1}] {blob}",
                                         modality="carousel"))
    elif visual_text:
        chunks.extend(chunk_text(visual_text, modality="vision"))
    elif not tr:
        for row in (db.query(ContentFrame)
                    .filter(ContentFrame.canonical_content_id == canonical_id).all()):
            blob = " ".join(filter(None, [row.ocr_text, row.vision_caption]))
            if blob:
                chunks.extend(chunk_text(blob, modality="vision"))
    if cc.description and (not tr or cc.media_kind == "carousel"):
        chunks.extend(chunk_text(cc.description, modality="caption"))

    if force:
        db.query(ContentChunk).filter(
            ContentChunk.canonical_content_id == canonical_id).delete()
        db.commit()

    written = 0
    if chunks and (force or not existing):
        for start in range(0, len(chunks), EMBED_BATCH):
            batch = chunks[start:start + EMBED_BATCH]
            try:
                res = router.embed([c.text for c in batch])
                telemetry.record_embedding(
                    db, res, operation="embedding.chunks",
                    canonical_content_id=canonical_id, user_id=user_id,
                    platform=cc.platform,
                )
                for offset, (chunk, vec) in enumerate(zip(batch, res.vectors)):
                    db.add(ContentChunk(
                        canonical_content_id=canonical_id,
                        chunk_index=start + offset, modality=chunk.modality,
                        text=chunk.text, start_s=chunk.start_s, end_s=chunk.end_s,
                        token_count=chunk.token_count,
                        embedding=to_storage(vec), embed_model=res.model,
                        embed_dim=res.dim,
                    ))
                    written += 1
                db.commit()
            except Exception as e:
                logger.warning("embedding batch failed (%s-%s): %s",
                               start, start + len(batch), e)

    # Document-level vector for library search / related / clustering.
    topics, key_points, tl_dr = [], [], None
    if und:
        try:
            topics = json.loads(und.topics or "[]")
            key_points = json.loads(und.key_points or "[]")
            tl_dr = und.tl_dr
        except Exception:
            pass
        try:
            ent_text = understanding.entities_to_text(
                json.loads(und.entities or "{}"), json.loads(und.typed_data or "{}"))
        except Exception:
            ent_text = ""
    else:
        ent_text = ""

    doc_text = build_document_text(
        title=cc.title, creator=cc.creator_name, description=cc.description,
        topics=topics, tl_dr=tl_dr, key_points=key_points,
        ocr_text=(visual_text or None),
        transcript_head=(tr.text[:1500] if tr else None),
    )
    if ent_text:
        doc_text = (doc_text + "\n" + ent_text)[:8000]

    if doc_text:
        try:
            res = router.embed([doc_text])
            telemetry.record_embedding(
                db, res, operation="embedding.document",
                canonical_content_id=canonical_id, user_id=user_id, platform=cc.platform,
            )
            # Upsert rather than check-then-insert. `canonical_content_id` is the
            # primary key, so a competing worker's row is a conflict, not a
            # second row — and the right resolution is to overwrite, because a
            # re-derived vector for the same document supersedes the old one.
            insert_or_update(
                db, ContentEmbedding,
                {"canonical_content_id": canonical_id,
                 "embedding": to_storage(res.vectors[0]),
                 "model": res.model, "dim": res.dim},
                index_elements=["canonical_content_id"],
            )
        except Exception as e:
            # Rollback, then carry on. Without it this handled failure left the
            # session unusable and the *next* commit — in the caller, or in the
            # queue recording the outcome — died with PendingRollbackError,
            # reporting a rollback problem instead of an embedding one.
            safe_rollback(db)
            logger.warning("document embedding failed: %s", e)

    return f"{written} chunks + doc vector" if written else "doc vector only"
