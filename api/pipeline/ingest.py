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
    INSTAGRAM_VISION_MODE, LAZY_SUMMARY_OVER_SECONDS, PIPELINE_VERSION,
    TIKTOK_VISION_MODE, UNDERSTANDING_SCHEMA_VERSION, YOUTUBE_VISION_MODE,
)
from ..content.identity import resolve_identity
from ..platform_budget import PlatformUnavailable, guarded
from ..models import (
    CanonicalContent, ContentChunk, ContentEmbedding, ContentFrame,
    ContentTranscript, ContentUnderstanding, ProcessingState,
)
from . import acquire, frames as frames_mod, understanding
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
        if self.vision_mode == "never":
            return False
        if media_kind in ("image", "carousel"):
            return True          # nothing else to go on
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

    cc = CanonicalContent(
        content_key=ident.content_key,
        platform=ident.platform,
        platform_content_id=ident.platform_content_id,
        canonical_url=ident.canonical_url,
        media_kind=ident.media_kind,
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
        "view_count": info.get("view_count"), "like_count": info.get("like_count"),
        "tags": info.get("tags") or [], "categories": info.get("categories") or [],
        "webpage_url": info.get("webpage_url"), "extractor": info.get("extractor"),
    }
    return AcquisitionResult(True, "metadata", bytes_moved=nbytes,
                             duration_s=info.get("duration"), metadata=meta)


def process_content(canonical_id: int, db, *, force: bool = False,
                    user_id: Optional[int] = None) -> Dict[str, Any]:
    """Run the ladder for one canonical item. Idempotent and resumable."""
    from ..ai.router import get_router

    cc = db.query(CanonicalContent).get(canonical_id)
    if cc is None:
        return {"ok": False, "error": "canonical content not found"}

    if (cc.processing_state == ProcessingState.READY
            and cc.pipeline_version == PIPELINE_VERSION and not force):
        return {"ok": True, "skipped": "already ready", "cache_hit": True}

    router = get_router()
    strat = strategy_for(cc.platform)
    result: Dict[str, Any] = {"stages": {}, "canonical_id": cc.id}
    total_bytes = 0
    workdirs: List[str] = []

    try:
        # ── L1+L2 combined for caption platforms ────────────────────────────
        # On YouTube, one yt-dlp extract_info yields metadata AND the caption
        # track list. Fetching them separately would double the bytes pulled
        # through a paid residential proxy for zero benefit.
        prefetched_captions = None
        if strat.try_native_captions and (force or not cc.title):
            prefetched_captions = guarded(
                cc.platform, "captions", acquire.fetch_captions_via_ytdlp,
                cc.canonical_url, db=db, canonical_content_id=cc.id, user_id=user_id)

        # ── L1: metadata ────────────────────────────────────────────────────
        _state(db, cc, ProcessingState.FETCHING, 1)
        if force or not cc.title:
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
                cc.metadata_json = json.dumps(m, default=str)[:60000]
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

        # ── L2: transcript ──────────────────────────────────────────────────
        transcript_row = (db.query(ContentTranscript)
                          .filter(ContentTranscript.canonical_content_id == cc.id)
                          .first())
        video_path: Optional[str] = None

        if transcript_row and not force:
            result["stages"]["transcript"] = "cached"
        elif cc.media_kind in ("image", "carousel"):
            result["stages"]["transcript"] = "skipped: no audio"
            _set_stage(cc, "transcript", "skipped", "image content")
        else:
            _state(db, cc, ProcessingState.TRANSCRIBING, 2)
            segments, source, lang, asr_seconds = [], None, "en", 0.0

            if strat.try_native_captions:
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

            if not segments and strat.allow_asr and 0 < duration <= strat.asr_max_seconds:
                # If vision is likely, download video once and reuse it for audio.
                will_need_vision = strat.wants_vision(
                    visual_dependency=(cc.visual_dependency
                                       if cc.visual_dependency is not None else 0.5),
                    has_transcript=False, media_kind=cc.media_kind,
                )
                if will_need_vision:
                    dl = guarded(
                        cc.platform, "download_video", acquire.download_video_lowres,
                        cc.canonical_url, db=db, canonical_content_id=cc.id,
                        user_id=user_id)
                    if dl.ok:
                        video_path = dl.path
                        workdirs.append(dl.path)
                        audio_path = acquire.extract_audio_from_video(dl.path) or dl.path
                    else:
                        audio_path = None
                else:
                    dl = guarded(
                        cc.platform, "download_audio", acquire.download_audio,
                        cc.canonical_url, db=db, canonical_content_id=cc.id,
                        user_id=user_id)
                    audio_path = dl.path if dl.ok else None
                    if dl.ok:
                        workdirs.append(dl.path)

                total_bytes += dl.bytes_moved
                telemetry.record(
                    db, operation=f"acquire.{dl.kind}", canonical_content_id=cc.id,
                    user_id=user_id, platform=cc.platform, proxy_bytes=dl.bytes_moved,
                    wall_ms=dl.wall_ms,
                    estimated_usd=telemetry.proxy_cost(dl.bytes_moved),
                    success=dl.ok, error=dl.error,
                )

                if audio_path:
                    asr = acquire.transcribe_audio(audio_path)
                    if asr.ok:
                        segments = asr.metadata.get("segments") or []
                        source = "asr"
                        lang = asr.metadata.get("language", "en")
                        asr_seconds = asr.duration_s or duration
                    telemetry.record(
                        db, operation="asr", canonical_content_id=cc.id, user_id=user_id,
                        platform=cc.platform, audio_seconds=asr_seconds,
                        wall_ms=asr.wall_ms,
                        estimated_usd=telemetry.asr_cost(asr_seconds, local=True),
                        model=asr.metadata.get("model"), provider="local-whisper",
                        success=asr.ok, error=asr.error,
                    )

            if segments:
                full_text = " ".join(s.get("text", "") for s in segments).strip()
                transcript_row = ContentTranscript(
                    canonical_content_id=cc.id, source=source or "asr", lang=lang,
                    text=full_text, segments=json.dumps(segments, default=str),
                    provider="youtube" if source == "captions" else "local-whisper",
                    audio_seconds=asr_seconds or duration, is_complete=True,
                )
                db.add(transcript_row)
                db.commit()
                _set_stage(cc, "transcript", "ok", source or "")
                result["stages"]["transcript"] = f"ok ({source}, {len(segments)} segments)"
            else:
                _set_stage(cc, "transcript", "failed", "no transcript obtainable")
                result["stages"]["transcript"] = "failed"

        # ── Classification (drives the visual decision) ─────────────────────
        _state(db, cc, ProcessingState.ANALYZING, 3)
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
            result["stages"]["classify"] = f"{cc.content_type} (vd={cc.visual_dependency:.2f})"
        else:
            result["stages"]["classify"] = "cached"

        # ── L3: visual (conditional) ────────────────────────────────────────
        visual_text = ""
        existing_frames = (db.query(ContentFrame)
                           .filter(ContentFrame.canonical_content_id == cc.id).count())
        wants_vision = strat.wants_vision(
            visual_dependency=cc.visual_dependency or 0.4,
            has_transcript=bool(transcript_text),
            media_kind=cc.media_kind,
        )

        if existing_frames and not force:
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
        elif not wants_vision:
            result["stages"]["vision"] = f"skipped (mode={strat.vision_mode}, vd={cc.visual_dependency})"
            _set_stage(cc, "vision", "skipped", strat.vision_mode)
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
                    ts = frames_mod.select_timestamps(video_path, duration or None)
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
                        visual_text = frames_mod.collect_visual_text(picked)
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
                # Optional enrichment must never fail the whole save.
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

        result["ok"] = True
        result["state"] = cc.processing_state
        result["proxy_bytes"] = total_bytes
        return result

    except PlatformUnavailable:
        # The platform is throttled or circuit-open. Keep the content in a
        # truthful "still working" state and let the queue retry later —
        # this is not a failure of the save.
        cc.processing_state = ProcessingState.QUEUED
        db.commit()
        raise
    except Exception as e:
        logger.exception("pipeline failed for canonical %s", canonical_id)
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
        for w in workdirs:
            acquire.cleanup(w)


def _upsert_understanding(db, canonical_id: int, rec: Dict[str, Any]) -> None:
    row = (db.query(ContentUnderstanding)
           .filter(ContentUnderstanding.canonical_content_id == canonical_id).first())
    if row is None:
        row = ContentUnderstanding(canonical_content_id=canonical_id)
        db.add(row)
    row.schema_version = UNDERSTANDING_SCHEMA_VERSION
    row.content_type = rec.get("content_type")
    row.tl_dr = rec.get("tl_dr")
    row.key_points = json.dumps(rec.get("key_points", []))
    row.topics = json.dumps(rec.get("topics", []))
    row.entities = json.dumps(rec.get("entities", {}))
    row.typed_data = json.dumps(rec.get("typed_data", {}))
    row.chapters = json.dumps(rec.get("chapters", []))
    row.sources_used = json.dumps(rec.get("sources_used", []))
    db.commit()


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
    if visual_text:
        chunks.extend(chunk_text(visual_text, modality="vision"))
    elif not tr:
        for row in (db.query(ContentFrame)
                    .filter(ContentFrame.canonical_content_id == canonical_id).all()):
            blob = " ".join(filter(None, [row.ocr_text, row.vision_caption]))
            if blob:
                chunks.extend(chunk_text(blob, modality="vision"))
    if cc.description and not tr:
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
            if doc_row is None:
                doc_row = ContentEmbedding(canonical_content_id=canonical_id)
                db.add(doc_row)
            doc_row.embedding = to_storage(res.vectors[0])
            doc_row.model = res.model
            doc_row.dim = res.dim
            db.commit()
        except Exception as e:
            logger.warning("document embedding failed: %s", e)

    return f"{written} chunks + doc vector" if written else "doc vector only"
