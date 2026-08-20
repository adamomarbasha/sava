"""Cost and usage telemetry.

One coherent entry point rather than logging scattered through the codebase.
Every expensive operation is recorded as a row in `usage_events`, which is the
ledger the unit-economics model is meant to be replaced by.

Recording must never break a user-facing request: all writes are best-effort.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Optional

from ..models import UsageEvent

logger = logging.getLogger(__name__)

# Non-LLM unit costs, verified 2026-08-18.
USD_PER_PROXY_GB = 3.00
USD_PER_ASR_MINUTE_HOSTED = 0.04 / 60      # Groq whisper-large-v3-turbo
USD_PER_ASR_MINUTE_LOCAL = 0.0005          # amortized CPU on a small instance


def record(
    db,
    *,
    operation: str,
    user_id: Optional[int] = None,
    canonical_content_id: Optional[int] = None,
    bookmark_id: Optional[int] = None,
    platform: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    audio_seconds: float = 0.0,
    frames_processed: int = 0,
    proxy_bytes: int = 0,
    wall_ms: int = 0,
    estimated_usd: float = 0.0,
    cache_hit: bool = False,
    success: bool = True,
    error: Optional[str] = None,
) -> None:
    """Write one usage event. Never raises."""
    try:
        db.add(UsageEvent(
            operation=operation, user_id=user_id,
            canonical_content_id=canonical_content_id, bookmark_id=bookmark_id,
            platform=platform, provider=provider, model=model,
            input_tokens=int(input_tokens or 0), output_tokens=int(output_tokens or 0),
            audio_seconds=float(audio_seconds or 0.0),
            frames_processed=int(frames_processed or 0),
            proxy_bytes=int(proxy_bytes or 0), wall_ms=int(wall_ms or 0),
            estimated_usd=float(estimated_usd or 0.0),
            cache_hit=bool(cache_hit), success=bool(success),
            error=(error or None),
        ))
        db.commit()
    except Exception as e:
        logger.warning("telemetry write failed for %s: %s", operation, e)
        try:
            db.rollback()
        except Exception:
            pass


def record_completion(db, completion, *, operation: str, **kw) -> None:
    """Convenience wrapper for a ModelRouter completion."""
    record(
        db, operation=operation,
        provider=getattr(completion, "provider", None),
        model=getattr(completion, "model", None),
        input_tokens=getattr(completion, "input_tokens", 0),
        output_tokens=getattr(completion, "output_tokens", 0),
        wall_ms=getattr(completion, "wall_ms", 0),
        estimated_usd=getattr(completion, "estimated_usd", 0.0),
        **kw,
    )


def record_embedding(db, result, *, operation: str = "embedding", **kw) -> None:
    record(
        db, operation=operation,
        provider=getattr(result, "provider", None),
        model=getattr(result, "model", None),
        input_tokens=getattr(result, "input_tokens", 0),
        wall_ms=getattr(result, "wall_ms", 0),
        estimated_usd=getattr(result, "_usd", 0.0),
        **kw,
    )


@contextmanager
def timed():
    """`with timed() as t:` then `t.ms`."""
    class _T:
        ms = 0
    t = _T()
    start = time.monotonic()
    try:
        yield t
    finally:
        t.ms = int((time.monotonic() - start) * 1000)


def proxy_cost(nbytes: int) -> float:
    return (nbytes / (1024 ** 3)) * USD_PER_PROXY_GB


def asr_cost(seconds: float, *, local: bool = True) -> float:
    minutes = max(0.0, seconds) / 60.0
    return minutes * (USD_PER_ASR_MINUTE_LOCAL if local else USD_PER_ASR_MINUTE_HOSTED)


# ─── Aggregation for the ops/cost endpoint ──────────────────────────────────

def summarize(db, *, user_id: Optional[int] = None, days: int = 30) -> dict:
    """Roll up the ledger. Plain SQL so it works on SQLite and Postgres alike."""
    from sqlalchemy import text as sql_text

    where = "created_at >= :since"
    params = {"since": _since(days)}
    if user_id is not None:
        where += " AND user_id = :uid"
        params["uid"] = user_id

    totals = db.execute(sql_text(
        f"SELECT COUNT(*) n, COALESCE(SUM(estimated_usd),0) usd, "
        f"COALESCE(SUM(input_tokens),0) tin, COALESCE(SUM(output_tokens),0) tout, "
        f"COALESCE(SUM(audio_seconds),0) audio, COALESCE(SUM(proxy_bytes),0) bytes, "
        f"COALESCE(SUM(frames_processed),0) frames, "
        f"COALESCE(SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END),0) hits, "
        f"COALESCE(SUM(CASE WHEN success THEN 0 ELSE 1 END),0) failures "
        f"FROM usage_events WHERE {where}"
    ), params).mappings().first() or {}

    by_op = [dict(r) for r in db.execute(sql_text(
        f"SELECT operation, COUNT(*) n, COALESCE(SUM(estimated_usd),0) usd "
        f"FROM usage_events WHERE {where} GROUP BY operation ORDER BY usd DESC"
    ), params).mappings()]

    by_platform = [dict(r) for r in db.execute(sql_text(
        f"SELECT COALESCE(platform,'unknown') platform, COUNT(*) n, "
        f"COALESCE(SUM(estimated_usd),0) usd FROM usage_events WHERE {where} "
        f"GROUP BY platform ORDER BY usd DESC"
    ), params).mappings()]

    n = int(totals.get("n", 0) or 0)
    return {
        "window_days": days,
        "events": n,
        "estimated_usd": round(float(totals.get("usd", 0) or 0), 6),
        "input_tokens": int(totals.get("tin", 0) or 0),
        "output_tokens": int(totals.get("tout", 0) or 0),
        "audio_seconds": round(float(totals.get("audio", 0) or 0), 1),
        "proxy_bytes": int(totals.get("bytes", 0) or 0),
        "proxy_usd": round(proxy_cost(int(totals.get("bytes", 0) or 0)), 6),
        "frames_processed": int(totals.get("frames", 0) or 0),
        "cache_hit_rate": round((int(totals.get("hits", 0) or 0) / n), 4) if n else 0.0,
        "failures": int(totals.get("failures", 0) or 0),
        "by_operation": by_op,
        "by_platform": by_platform,
    }


def _since(days: int):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) - timedelta(days=days)


def platform_health(db, *, days: int = 1) -> dict:
    """Per-platform request health.

    Merges the live in-process counters (exact, but reset on restart) with the
    durable `usage_events` ledger (survives restarts, aggregates across hosts).
    """
    from sqlalchemy import text as sql_text
    from ..platform_budget import get_manager

    live = get_manager().snapshot()

    rows = db.execute(sql_text("""
        SELECT COALESCE(platform,'unknown') AS platform,
               COUNT(*) AS n,
               SUM(CASE WHEN success THEN 1 ELSE 0 END) AS ok,
               SUM(COALESCE(proxy_bytes,0)) AS bytes,
               SUM(COALESCE(estimated_usd,0)) AS usd,
               AVG(COALESCE(wall_ms,0)) AS avg_ms
        FROM usage_events
        WHERE created_at >= :since AND operation LIKE 'platform.%'
        GROUP BY platform
    """), {"since": _since(days)}).mappings().all()

    persisted = {
        r["platform"]: {
            "requests": int(r["n"]),
            "ok": int(r["ok"] or 0),
            "success_rate": round((r["ok"] or 0) / r["n"], 4) if r["n"] else None,
            "proxy_bytes": int(r["bytes"] or 0),
            "proxy_usd": round(proxy_cost(int(r["bytes"] or 0)), 6),
            "estimated_usd": round(float(r["usd"] or 0), 6),
            "avg_wall_ms": round(float(r["avg_ms"] or 0), 1),
        }
        for r in rows
    }

    for name, block in live.items():
        block["persisted_window"] = persisted.get(name, {})
    return {"window_days": days, "platforms": live}


def dedup_economics(db, *, days: int = 30) -> dict:
    """User saves vs unique content processed — the ratio that decides margin.

    A ratio near 1.0 means every save costs a full processing run. Above ~1.3
    the canonical cache is meaningfully paying for itself; viral content pushes
    it higher.
    """
    from sqlalchemy import text as sql_text

    totals = db.execute(sql_text("""
        SELECT
          (SELECT COUNT(*) FROM bookmarks) AS user_saves,
          (SELECT COUNT(*) FROM canonical_content) AS unique_content,
          (SELECT COUNT(*) FROM canonical_content WHERE processing_state IN ('ready','partial'))
            AS processed_content,
          (SELECT COUNT(*) FROM content_transcripts) AS transcripts,
          (SELECT COUNT(*) FROM content_frames) AS frames
    """)).mappings().first() or {}

    saves = int(totals.get("user_saves", 0) or 0)
    unique = int(totals.get("unique_content", 0) or 0)
    processed = int(totals.get("processed_content", 0) or 0)

    since = _since(days)
    hits = db.execute(sql_text(
        "SELECT COUNT(*) FROM usage_events "
        "WHERE created_at >= :since AND operation IN ('save.cache_hit','content.cache_hit')"
    ), {"since": since}).scalar() or 0
    queued = db.execute(sql_text(
        "SELECT COUNT(*) FROM usage_events "
        "WHERE created_at >= :since AND operation = 'save.queued'"
    ), {"since": since}).scalar() or 0

    spend = db.execute(sql_text(
        "SELECT COALESCE(SUM(estimated_usd),0) FROM usage_events WHERE created_at >= :since"
    ), {"since": since}).scalar() or 0.0

    visual = db.execute(sql_text(
        "SELECT COUNT(DISTINCT canonical_content_id) FROM content_frames"
    )).scalar() or 0

    return {
        "user_saves": saves,
        "unique_content": unique,
        "processed_content": processed,
        "dedup_ratio": round(saves / unique, 3) if unique else None,
        "saves_avoided": max(0, saves - unique),
        "cache_hits": int(hits),
        "newly_queued": int(queued),
        "cache_hit_rate": round(hits / (hits + queued), 4) if (hits + queued) else None,
        "transcript_reuse_rate": (
            round(int(totals.get("transcripts", 0) or 0) / unique, 4) if unique else None),
        "visual_processing_rate": round(visual / unique, 4) if unique else None,
        "avg_frames_per_visual_item": (
            round(int(totals.get("frames", 0) or 0) / visual, 2) if visual else 0),
        "estimated_usd": round(float(spend), 6),
        "cost_per_unique_item": round(float(spend) / processed, 6) if processed else None,
        "cost_per_user_save": round(float(spend) / saves, 6) if saves else None,
    }


def queue_health(db) -> dict:
    """Queue depth, oldest waiting job, and per-platform backlog."""
    from sqlalchemy import text as sql_text
    import datetime as _dt

    by_state = {r["state"]: int(r["n"]) for r in db.execute(sql_text(
        "SELECT state, COUNT(*) n FROM jobs GROUP BY state")).mappings()}

    by_platform = [dict(r) for r in db.execute(sql_text(
        "SELECT COALESCE(platform,'none') platform, state, COUNT(*) n "
        "FROM jobs GROUP BY platform, state")).mappings()]

    oldest = db.execute(sql_text(
        "SELECT MIN(created_at) FROM jobs WHERE state = 'queued'")).scalar()
    oldest_age = None
    if oldest is not None:
        if isinstance(oldest, str):
            try:
                oldest = _dt.datetime.fromisoformat(oldest)
            except Exception:
                oldest = None
        if oldest is not None:
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=_dt.timezone.utc)
            oldest_age = round(
                (_dt.datetime.now(_dt.timezone.utc) - oldest).total_seconds(), 1)

    return {
        "by_state": by_state,
        "total": sum(by_state.values()),
        "depth": by_state.get("queued", 0),
        "oldest_queued_age_s": oldest_age,
        "by_platform": by_platform,
    }


def processing_latency(db, *, days: int = 7) -> dict:
    """p50/p95/p99 wall time for pipeline operations."""
    from sqlalchemy import text as sql_text

    rows = db.execute(sql_text(
        "SELECT operation, wall_ms FROM usage_events "
        "WHERE created_at >= :since AND wall_ms > 0"
    ), {"since": _since(days)}).mappings().all()

    buckets: dict = {}
    for r in rows:
        buckets.setdefault(r["operation"], []).append(float(r["wall_ms"]))

    def pct(vals, p):
        if not vals:
            return 0.0
        vals = sorted(vals)
        return round(vals[min(len(vals) - 1, int(len(vals) * p))], 1)

    return {
        op: {"n": len(v), "p50_ms": pct(v, 0.5), "p95_ms": pct(v, 0.95),
             "p99_ms": pct(v, 0.99)}
        for op, v in sorted(buckets.items(), key=lambda kv: -len(kv[1]))
    }


def extraction_health(db, *, platform: str, days: int = 7) -> dict:
    """Per-platform extraction report, at the granularity the pipeline works in.

    `platform_health` answers "is the platform letting us in?". This answers
    "what is Sava actually getting out of it?" — which stages succeed, how much
    is reused rather than re-fetched, and what one unique item costs. Those are
    different questions and they fail in different ways: captions can be at 98%
    while thumbnail mirroring is at 40% and the library slowly loses its images.
    """
    from sqlalchemy import text as sql_text

    since = _since(days)
    plat = (platform or "").lower()

    def _ops(prefix: str) -> dict:
        rows = db.execute(sql_text("""
            SELECT operation,
                   COUNT(*) AS n,
                   SUM(CASE WHEN success THEN 1 ELSE 0 END) AS ok,
                   COALESCE(SUM(proxy_bytes), 0) AS bytes,
                   COALESCE(AVG(wall_ms), 0) AS avg_ms,
                   COALESCE(SUM(estimated_usd), 0) AS usd
            FROM usage_events
            WHERE created_at >= :since AND platform = :plat
              AND operation LIKE :prefix
            GROUP BY operation
        """), {"since": since, "plat": plat, "prefix": prefix + "%"}).mappings().all()
        return {r["operation"]: {
            "count": int(r["n"]), "ok": int(r["ok"] or 0),
            "success_rate": round((r["ok"] or 0) / r["n"], 4) if r["n"] else None,
            "bytes": int(r["bytes"] or 0),
            "avg_ms": round(float(r["avg_ms"] or 0)),
            "usd": round(float(r["usd"] or 0), 6),
        } for r in rows}

    content = db.execute(sql_text("""
        SELECT
          COUNT(*) AS unique_items,
          SUM(CASE WHEN processing_state IN ('ready','partial') THEN 1 ELSE 0 END) AS processed,
          SUM(CASE WHEN media_kind = 'carousel' THEN 1 ELSE 0 END) AS carousels,
          SUM(CASE WHEN media_kind = 'video' THEN 1 ELSE 0 END) AS videos,
          SUM(CASE WHEN thumbnail_stored_key IS NOT NULL THEN 1 ELSE 0 END) AS mirrored,
          SUM(CASE WHEN comments_state = 'ok' THEN 1 ELSE 0 END) AS with_comments,
          SUM(CASE WHEN comments_state = 'failed' THEN 1 ELSE 0 END) AS comments_failed
        FROM canonical_content WHERE platform = :plat
    """), {"plat": plat}).mappings().first() or {}

    unique = int(content.get("unique_items", 0) or 0)

    saves = db.execute(sql_text(
        "SELECT COUNT(*) FROM bookmarks WHERE platform = :plat"), {"plat": plat}).scalar() or 0

    transcripts = db.execute(sql_text("""
        SELECT t.source, COUNT(*) AS n
        FROM content_transcripts t
        JOIN canonical_content c ON c.id = t.canonical_content_id
        WHERE c.platform = :plat GROUP BY t.source
    """), {"plat": plat}).mappings().all()

    slides = db.execute(sql_text("""
        SELECT COUNT(*) FROM content_assets a
        JOIN canonical_content c ON c.id = a.canonical_content_id
        WHERE c.platform = :plat
    """), {"plat": plat}).scalar() or 0

    spend = db.execute(sql_text(
        "SELECT COALESCE(SUM(estimated_usd),0) FROM usage_events "
        "WHERE created_at >= :since AND platform = :plat"),
        {"since": since, "plat": plat}).scalar() or 0.0

    carousels = int(content.get("carousels", 0) or 0)

    return {
        "platform": plat,
        "window_days": days,
        "user_saves": int(saves),
        "unique_items": unique,
        "dedup_ratio": round(saves / unique, 3) if unique else None,
        "processed": int(content.get("processed", 0) or 0),
        "media_mix": {"video": int(content.get("videos", 0) or 0), "carousel": carousels},
        "carousel_slides_total": int(slides),
        "avg_slides_per_carousel": round(slides / carousels, 2) if carousels else None,
        "thumbnail_mirror_rate": (
            round(int(content.get("mirrored", 0) or 0) / unique, 4) if unique else None),
        "transcript_sources": {r["source"]: int(r["n"]) for r in transcripts},
        "comments": {
            "items_with_comments": int(content.get("with_comments", 0) or 0),
            "items_failed": int(content.get("comments_failed", 0) or 0),
            "operations": _ops("comments."),
        },
        "acquisition": _ops("acquire."),
        "asr": _ops("asr"),
        "vision": _ops("vision"),
        "estimated_usd": round(float(spend), 6),
        "cost_per_unique_item": round(float(spend) / unique, 6) if unique else None,
        "cost_per_user_save": round(float(spend) / saves, 6) if saves else None,
    }
