"""Controlled cache-version upgrades.

Cached understanding must not be permanent-and-unimprovable, but it must also
never trigger an automatic reprocess of the entire library — that is the most
expensive operation Sava can perform, and doing it implicitly on deploy would
be a self-inflicted cost incident.

The model here is *lazy and opt-in*:
  * every canonical row records the `pipeline_version` that produced it;
  * an operator asks for a batch (`limit`) to be upgraded;
  * jobs go through the same idempotent queue and the same platform budget,
    so an upgrade sweep is throttled exactly like normal ingestion.

Content that is already current is never touched.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text as sql_text

from ..config import PIPELINE_VERSION

logger = logging.getLogger(__name__)


def plan_upgrade(db, *, limit: int = 50,
                 target_version: Optional[int] = None) -> Dict[str, Any]:
    """Identify content behind the target pipeline version. Read-only."""
    target = int(target_version or PIPELINE_VERSION)

    rows = db.execute(sql_text("""
        SELECT cc.id, cc.content_key, cc.platform, cc.pipeline_version, cc.title,
               (SELECT COUNT(*) FROM bookmarks b WHERE b.canonical_content_id = cc.id)
                 AS save_count
        FROM canonical_content cc
        WHERE COALESCE(cc.pipeline_version, 0) < :target
          AND cc.processing_state IN ('ready','partial')
        ORDER BY save_count DESC, cc.id ASC
        LIMIT :lim
    """), {"target": target, "lim": limit}).mappings().all()

    total = db.execute(sql_text("""
        SELECT COUNT(*) FROM canonical_content
        WHERE COALESCE(pipeline_version, 0) < :target
          AND processing_state IN ('ready','partial')
    """), {"target": target}).scalar() or 0

    by_platform: Dict[str, int] = {}
    for r in rows:
        by_platform[r["platform"]] = by_platform.get(r["platform"], 0) + 1

    return {
        "target_version": target,
        "current_version": PIPELINE_VERSION,
        "eligible_total": int(total),
        "batch_size": len(rows),
        "by_platform": by_platform,
        # Most-saved content first: upgrading a viral item improves the
        # experience for every user who saved it, for one processing run.
        "items": [
            {"canonical_id": r["id"], "content_key": r["content_key"],
             "platform": r["platform"], "from_version": r["pipeline_version"],
             "save_count": int(r["save_count"] or 0),
             "title": (r["title"] or "")[:80]}
            for r in rows
        ],
    }


def queue_upgrade(db, plan: Dict[str, Any], *, user_id: Optional[int] = None
                  ) -> Dict[str, Any]:
    """Enqueue the planned batch. Idempotent per (content, target version)."""
    from ..jobs import enqueue

    target = plan["target_version"]
    queued: List[int] = []
    for item in plan.get("items", []):
        cid = item["canonical_id"]
        job = enqueue(
            db, "content.process",
            {"canonical_id": cid, "user_id": user_id, "force": True},
            # Version in the key so an upgrade to v3 is a distinct unit of work
            # from the original v1 processing, but re-requesting the same
            # upgrade twice is still a no-op.
            idempotency_key=f"content.upgrade:{cid}:v{target}",
            platform=item.get("platform"),
            priority=300,          # never competes with a user's fresh save
        )
        if job is not None:
            queued.append(cid)

    logger.info("queued %d canonical items for upgrade to v%s", len(queued), target)
    return {"queued": len(queued), "canonical_ids": queued,
            "target_version": target,
            "remaining": max(0, plan["eligible_total"] - len(queued))}
