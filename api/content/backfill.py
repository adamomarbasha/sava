"""Legacy save repair.

Sava's metadata now lives on `canonical_content` — one row per piece of content,
shared by everyone who saved it. Saves created before that model existed were
never linked to it: `bookmarks.canonical_content_id` is NULL. Those rows still
render (the bookmark carries its own title/author/thumbnail) but they are invisible
to everything built on canonical identity — duration, content type, processing
state, search, collections, Ask Sava.

That is why older saves look thinner than new ones in the app. It is not a UI
bug and it is not a serialization bug: the link is genuinely missing.

This module repairs that, and only that:

  * `plan_link`   — read-only. Which saves are unlinked, and can identity be
                    resolved from the URL alone?
  * `run_link`    — resolve the canonical identity (deterministic string work,
                    zero network I/O, zero inference) and attach it. When the
                    canonical row is newly created and empty, seed it from the
                    metadata the bookmark *already* holds.
  * `plan_thumbnails` / `run_thumbnails` — mirror expiring CDN thumbnails into
                    local storage so they stop disappearing.

Deliberately not done here: re-extraction and re-summarisation. Those cost money
and already have an owner — `content/upgrade.py` queues them through the normal
budgeted pipeline. Nothing in this module invents a value it did not read from an
existing row or from the image at the URL that row already stored.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text as sql_text

from ..models import Bookmark, CanonicalContent

logger = logging.getLogger(__name__)


# ─── Canonical linking ───────────────────────────────────────────────────────

def _unlinked(db, *, user_id: Optional[int], limit: int) -> List[Bookmark]:
    query = db.query(Bookmark).filter(Bookmark.canonical_content_id.is_(None))
    if user_id is not None:
        query = query.filter(Bookmark.user_id == user_id)
    return query.order_by(Bookmark.created_at.desc()).limit(limit).all()


def plan_link(db, *, user_id: Optional[int] = None, limit: int = 200) -> Dict[str, Any]:
    """Read-only survey of unlinked saves."""
    from .identity import resolve_identity

    rows = _unlinked(db, user_id=user_id, limit=limit)
    resolvable, unresolvable = [], []
    for bookmark in rows:
        ident = resolve_identity(bookmark.url, platform_hint=bookmark.platform)
        target = resolvable if ident is not None else unresolvable
        target.append({
            "bookmark_id": bookmark.id,
            "platform": bookmark.platform,
            "url": bookmark.url[:120],
            "content_key": ident.content_key if ident else None,
        })

    total = db.query(Bookmark).filter(Bookmark.canonical_content_id.is_(None))
    if user_id is not None:
        total = total.filter(Bookmark.user_id == user_id)

    by_platform: Dict[str, int] = {}
    for item in resolvable:
        by_platform[item["platform"]] = by_platform.get(item["platform"], 0) + 1

    return {
        "unlinked_total": total.count(),
        "batch_size": len(rows),
        "resolvable": len(resolvable),
        "unresolvable": len(unresolvable),
        "by_platform": by_platform,
        "items": resolvable[:50],
        "skipped": unresolvable[:20],
    }


def _seed_canonical(cc: CanonicalContent, bookmark: Bookmark) -> bool:
    """Copy metadata the save already holds onto an empty canonical row.

    Only fills blanks. A canonical row populated by the real pipeline — or by
    another user's richer save — always wins, and nothing is fabricated: every
    value here was already stored against this bookmark.
    """
    from ..models import ProcessingState

    changed = False
    transfers = (
        ("title", bookmark.title),
        ("creator_name", bookmark.author),
        ("thumbnail_url", bookmark.thumbnail_url),
        ("description", bookmark.description),
        ("published_at", bookmark.published_at),
    )
    for field, value in transfers:
        if value and not getattr(cc, field, None):
            setattr(cc, field, value)
            changed = True

    # A freshly created canonical row starts QUEUED, which the client renders as
    # "still processing". For a legacy save that already carries a real title and
    # thumbnail that would be a lie that never resolves — nothing is enqueued to
    # clear it. PARTIAL is the accurate description: usable metadata, no deep
    # understanding yet.
    if changed and cc.processing_state == ProcessingState.QUEUED and (cc.title or cc.thumbnail_url):
        cc.processing_state = ProcessingState.PARTIAL
        cc.processing_level = max(int(cc.processing_level or 0), 1)
        cc.last_error = None

    return changed


def run_link(db, *, user_id: Optional[int] = None, limit: int = 200) -> Dict[str, Any]:
    """Attach unlinked saves to canonical content. No network, no inference."""
    from ..pipeline.ingest import resolve_or_create_canonical

    linked = 0
    created = 0
    seeded = 0
    skipped = 0

    for bookmark in _unlinked(db, user_id=user_id, limit=limit):
        try:
            cc, was_created = resolve_or_create_canonical(db, bookmark.url, bookmark.platform)
        except Exception as e:
            logger.warning("backfill: identity failed for bookmark %s: %s", bookmark.id, e)
            skipped += 1
            continue

        if cc is None:
            # A URL shape Sava cannot identify (a bare web link, a shortener it
            # cannot expand offline). Leaving it unlinked is correct.
            skipped += 1
            continue

        bookmark.canonical_content_id = cc.id
        linked += 1
        created += 1 if was_created else 0
        if _seed_canonical(cc, bookmark):
            seeded += 1
        bookmark.processing_state = cc.processing_state
        db.commit()

    logger.info("backfill: linked=%d created=%d seeded=%d skipped=%d",
                linked, created, seeded, skipped)
    return {"linked": linked, "canonical_created": created,
            "canonical_seeded": seeded, "skipped": skipped}


# ─── Thumbnail durability ────────────────────────────────────────────────────

_EPHEMERAL_SQL = " OR ".join(
    f"LOWER(thumbnail_url) LIKE '%{host}%'"
    for host in ("tiktokcdn", "fbcdn.net", "cdninstagram.com", "licdn.com",
                 "twimg.com", "redd.it", "pinimg.com")
)


def plan_thumbnails(db, *, user_id: Optional[int] = None, limit: int = 200
                    ) -> Dict[str, Any]:
    """Read-only survey of thumbnails living on expiring CDNs."""
    scope = "AND user_id = :uid" if user_id is not None else ""
    params: Dict[str, Any] = {"lim": limit}
    if user_id is not None:
        params["uid"] = user_id

    rows = db.execute(sql_text(f"""
        SELECT id, platform, thumbnail_url FROM bookmarks
        WHERE thumbnail_url IS NOT NULL AND thumbnail_url != ''
          AND ({_EPHEMERAL_SQL}) {scope}
        ORDER BY id DESC LIMIT :lim
    """), params).mappings().all()

    missing = db.execute(sql_text(f"""
        SELECT COUNT(*) FROM bookmarks
        WHERE (thumbnail_url IS NULL OR thumbnail_url = '') {scope}
    """), {k: v for k, v in params.items() if k != "lim"}).scalar() or 0

    by_platform: Dict[str, int] = {}
    for r in rows:
        by_platform[r["platform"]] = by_platform.get(r["platform"], 0) + 1

    return {
        "ephemeral_batch": len(rows),
        "by_platform": by_platform,
        "no_thumbnail_at_all": int(missing),
        "items": [{"bookmark_id": r["id"], "platform": r["platform"],
                   "url": (r["thumbnail_url"] or "")[:110]} for r in rows[:50]],
    }


def run_thumbnails(db, *, user_id: Optional[int] = None, limit: int = 200
                   ) -> Dict[str, Any]:
    """Mirror expiring thumbnails into local storage.

    A thumbnail that is already dead cannot be recovered here — that needs
    re-extraction from the platform, which is the pipeline's job. Those rows are
    counted as `dead` and left untouched so the client keeps showing its designed
    fallback rather than a broken image.
    """
    from ..services import thumbnails as thumb_svc

    scope = "AND user_id = :uid" if user_id is not None else ""
    params: Dict[str, Any] = {"lim": limit}
    if user_id is not None:
        params["uid"] = user_id

    rows = db.execute(sql_text(f"""
        SELECT id, platform, thumbnail_url, canonical_content_id FROM bookmarks
        WHERE thumbnail_url IS NOT NULL AND thumbnail_url != ''
          AND ({_EPHEMERAL_SQL}) {scope}
        ORDER BY id DESC LIMIT :lim
    """), params).mappings().all()

    mirrored = 0
    dead = 0
    # One remote image can back many saves; fetch it once.
    seen: Dict[str, Optional[str]] = {}

    for r in rows:
        source = r["thumbnail_url"]
        if source not in seen:
            seen[source] = thumb_svc.mirror(source, platform=r["platform"])
        local = seen[source]
        if not local:
            dead += 1
            continue

        db.execute(sql_text("UPDATE bookmarks SET thumbnail_url = :t WHERE id = :i"),
                   {"t": local, "i": r["id"]})
        if r["canonical_content_id"]:
            db.execute(sql_text("""
                UPDATE canonical_content SET thumbnail_url = :t
                WHERE id = :i AND (thumbnail_url IS NULL OR thumbnail_url = :src)
            """), {"t": local, "i": r["canonical_content_id"], "src": source})
        mirrored += 1

    db.commit()
    logger.info("backfill: mirrored=%d dead=%d", mirrored, dead)
    return {"mirrored": mirrored, "dead": dead, "examined": len(rows)}


# ─── Short-form classification ───────────────────────────────────────────────

def run_shortform(db, *, limit: int = 5000) -> Dict[str, Any]:
    """Classify saves that predate the `is_short` column.

    Dimensions were never captured before this pass, so most rows have no
    geometry to reason from. Two recoveries are possible without touching the
    network, and this does both:

      * `metadata_json` from the original yt-dlp extraction sometimes carries
        `width`/`height` even though nothing read them at the time.
      * A bookmark whose saved URL was `youtube.com/shorts/...` is a Short by
        the platform's own declaration, regardless of what we stored.

    Anything still ambiguous stays `False`. A long video wrongly placed in a
    vertical swipe viewer is a much worse failure than a Short that has to be
    opened the ordinary way, so the tie is broken toward not claiming it.
    """
    import json as _json

    from ..models import Bookmark, CanonicalContent
    from .shortform import is_short_form, is_shorts_url

    rows = db.query(CanonicalContent).limit(limit).all()
    stats = {"examined": len(rows), "updated": 0, "dimensions_recovered": 0,
             "shorts_by_url": 0, "short_total": 0}

    for cc in rows:
        try:
            meta = _json.loads(cc.metadata_json or "{}")
        except Exception:
            meta = {}

        if not cc.width and meta.get("width"):
            cc.width, cc.height = meta.get("width"), meta.get("height")
            stats["dimensions_recovered"] += 1

        hint = cc.canonical_url
        if (cc.platform or "").lower() == "youtube" and not meta.get("shorts_url"):
            # The canonical URL was normalized to watch?v=, so the evidence, if
            # it survives anywhere, is on the bookmarks that point here.
            saved = (db.query(Bookmark.url)
                     .filter(Bookmark.canonical_content_id == cc.id).all())
            for (url,) in saved:
                if is_shorts_url(url):
                    hint = url
                    meta["shorts_url"] = True
                    cc.metadata_json = _json.dumps(meta, default=str)[:60000]
                    stats["shorts_by_url"] += 1
                    break

        derived = bool(meta.get("shorts_url")) or is_short_form(
            cc.platform, media_kind=cc.media_kind,
            duration_seconds=cc.duration_seconds,
            width=cc.width, height=cc.height, url_hint=hint)

        if bool(cc.is_short) != derived:
            cc.is_short = derived
            stats["updated"] += 1
        if derived:
            stats["short_total"] += 1

    db.commit()
    return stats


# ─── Instagram legacy repair ─────────────────────────────────────────────────

# Titles the removed instaloader ingestor wrote when it had nothing. They look
# like content and are not, which is worse than an empty title.
_IG_PLACEHOLDER_TITLES = {
    "instagram post", "instagram video", "instagram reel", "instagram photo",
    "untitled", "post", "reel",
}


def run_instagram_repair(db, *, limit: int = 5000) -> Dict[str, Any]:
    """Undo what the old Instagram ingestor asserted but never knew.

    Three specific kinds of damage, all of which look like real data to
    everything downstream:

      * placeholder titles ("Instagram Post") written on every failure,
      * thumbnails stored as bare `/static/thumbnails/...` paths, which no
        object-storage backend can serve and no mirror job can rescue,
      * `media_kind="carousel"` inferred from the URL alone, because the old
        identity rule called every `/p/` a carousel — including plain photos
        and videos.

    Clearing them is what allows the provider chain to repopulate honestly;
    leaving them means the gap-filling writes never fire, because the gaps are
    already full of fiction.
    """
    import json as _json

    from ..models import CanonicalContent, ContentAsset

    rows = (db.query(CanonicalContent)
            .filter(CanonicalContent.platform == "instagram")
            .limit(limit).all())
    stats = {"examined": len(rows), "titles_cleared": 0, "thumbnails_cleared": 0,
             "media_kind_reset": 0, "captures_relabelled": 0}

    for cc in rows:
        # Screenshot captures are not Instagram posts and must never be
        # mistakable for one. The key namespace already keeps them apart; this
        # stops the *record* claiming to be a video it never saw.
        if (cc.content_key or "").startswith("instagram:partial:"):
            if cc.media_kind != "capture":
                cc.media_kind = "capture"
                stats["captures_relabelled"] += 1
            continue

        if cc.title and cc.title.strip().lower() in _IG_PLACEHOLDER_TITLES:
            cc.title = None
            stats["titles_cleared"] += 1

        thumb = cc.thumbnail_url or ""
        if thumb.startswith("/static/thumbnails/") and not cc.thumbnail_stored_key:
            cc.thumbnail_url = None
            stats["thumbnails_cleared"] += 1

        # The same dead path is cached on every bookmark that pointed here, and
        # a per-user copy shadows the canonical one when it is served.
        from ..models import Bookmark as _Bookmark
        stale = (db.query(_Bookmark)
                 .filter(_Bookmark.canonical_content_id == cc.id,
                         _Bookmark.thumbnail_url.like("/static/thumbnails/%"))
                 .all())
        for bm in stale:
            bm.thumbnail_url = None
            stats["bookmark_thumbnails_cleared"] = \
                stats.get("bookmark_thumbnails_cleared", 0) + 1

        if cc.media_kind == "carousel":
            has_children = (db.query(ContentAsset)
                            .filter(ContentAsset.canonical_content_id == cc.id)
                            .count())
            if not has_children:
                # Asserted from the URL, never verified. "unknown" until a
                # provider says otherwise.
                cc.media_kind = "unknown"
                stats["media_kind_reset"] += 1
                try:
                    meta = _json.loads(cc.metadata_json or "{}")
                except Exception:
                    meta = {}
                meta.pop("carousel_count", None)
                cc.metadata_json = _json.dumps(meta, default=str)[:60000]

    db.commit()
    return stats
