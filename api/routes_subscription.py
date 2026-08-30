"""Subscription, usage and pricing endpoints.

Mounted alongside the existing routers. Nothing here changes an existing
endpoint's contract.

The client's side of the bargain is narrow on purpose: it may present evidence
(a signed transaction) and it may report that StoreKit has no entitlement. It
may not state a plan, a limit, or a unit count. Everything a user sees about
what they are allowed to do is computed here and sent down.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import appstore, billing, entitlements, plans
from .pipeline import route as route_mod
from .ai import telemetry
from .auth import get_current_user
from .authz import require_admin
from .db import get_db
from .services import subscription as subscription_svc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["subscription"])


# ─── What the user is on, and what they have spent ───────────────────────────

@router.get("/api/me/subscription")
def my_subscription(current_user: dict = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Plan, entitlement and this period's usage, in one call.

    One endpoint rather than two because the client needs both to draw a single
    row, and two round-trips would let the Profile screen render a plan and a
    usage bar that disagree with each other for a frame.
    """
    entitlement = entitlements.for_user(db, current_user["id"])
    return {
        "subscription": entitlement.public(),
        "usage": billing.usage_for(db, current_user["id"], entitlement=entitlement),
    }


class VerifyIn(BaseModel):
    """A StoreKit 2 signed transaction.

    `Transaction.jsonRepresentation` on the client, which despite the name is a
    JWS string. Sent as evidence, not as an assertion.
    """
    signed_transaction: str = Field(..., min_length=16, max_length=32768)


@router.post("/api/subscription/verify")
def verify_subscription(body: VerifyIn,
                        current_user: dict = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Verify a purchase with Apple and grant the entitlement it proves.

    Called after a purchase, after a restore, and on every launch where StoreKit
    reports a current entitlement. Idempotent, so calling it more than necessary
    is cheap and safe.
    """
    user_id = current_user["id"]
    try:
        transaction = appstore.verify_signed_transaction(body.signed_transaction)
    except appstore.VerificationError as e:
        telemetry.record(db, operation="subscription.verify_failed",
                         user_id=user_id, success=False, error=str(e)[:500])
        logger.warning("subscription verification failed for user %s: %s", user_id, e)
        # 422, not 400: the request was well-formed and the *evidence* was not
        # acceptable. The message is Apple's reason or ours, and is safe to show.
        raise HTTPException(status_code=422, detail=str(e))

    try:
        sub = subscription_svc.apply_transaction(db, user_id, transaction)
    except subscription_svc.SubscriptionConflict as e:
        telemetry.record(db, operation="subscription.conflict", user_id=user_id,
                         success=False, error=str(e)[:500])
        raise HTTPException(status_code=409, detail=str(e))

    entitlement = entitlements.for_user(db, user_id)
    telemetry.record(
        db, operation="subscription.verified", user_id=user_id,
        model=sub.product_id, provider=sub.environment,
        success=entitlement.is_pro)

    # Upgrading should feel like it did something. Anything this user saved
    # while out of units is queued now, oldest first, against the new allowance
    # — rather than sitting at "AI processing limit reached" until they think to
    # go and find each one.
    resumed = {}
    if entitlement.is_pro:
        try:
            from .services.save import resume_limited_saves
            resumed = resume_limited_saves(db, user_id)
        except Exception as e:
            logger.warning("could not resume held saves for user %s: %s", user_id, e)

    return {
        "subscription": entitlement.public(),
        "usage": billing.usage_for(db, user_id, entitlement=entitlement),
        "resumed": resumed,
    }


class ClearIn(BaseModel):
    #: "expired" when StoreKit simply has nothing current; "revoked" for a
    #: refund or a withdrawn family share.
    reason: str = Field("expired", max_length=32)


@router.post("/api/subscription/clear")
def clear_subscription(body: ClearIn,
                       current_user: dict = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """The client reports StoreKit no longer shows an entitlement.

    Safe to accept from a client precisely because it can only ever *remove*
    access. The dangerous direction — a client claiming it has Pro — is the one
    that requires Apple's signature.
    """
    subscription_svc.clear(db, current_user["id"], reason=body.reason)
    entitlement = entitlements.for_user(db, current_user["id"])
    telemetry.record(db, operation="subscription.cleared",
                     user_id=current_user["id"], error=body.reason[:64])
    return {
        "subscription": entitlement.public(),
        "usage": billing.usage_for(db, current_user["id"], entitlement=entitlement),
    }


# ─── The plan catalogue ──────────────────────────────────────────────────────

@router.get("/api/pricing")
def pricing(current_user: dict = Depends(get_current_user)):
    """What each plan includes, and what things cost in Processing Units.

    Served from `api/plans.py` rather than duplicated in the app, so changing a
    limit by environment variable updates the paywall without a client release.

    Notably absent: prices. Money is StoreKit's to state — it knows the user's
    storefront, currency, tax treatment and any offer they are eligible for, and
    a price shipped from here would be wrong for most of the world.
    """
    catalogue = []
    for name in plans.PLAN_NAMES:
        limits = plans.limits_for(name)
        catalogue.append({
            "plan": limits.name,
            "display_name": limits.display_name,
            "processing_units": limits.processing_units,
            "approx_videos": limits.approx_videos,
            "ask_messages": limits.ask_messages,
            "concurrent_jobs": limits.concurrent_jobs,
            "enhanced_analysis": limits.enhanced_analysis,
            "priority_processing": limits.is_pro,
        })
    return {
        "plans": catalogue,
        "product_ids": {
            "monthly": appstore.PRO_MONTHLY,
            "annual": appstore.PRO_ANNUAL,
        },
        "processing_unit_weights": plans.describe_weights(),
        "typical_units_per_video": plans.TYPICAL_UNITS_PER_VIDEO,
    }


# ─── Business telemetry ──────────────────────────────────────────────────────
#
# Recorded into the `usage_events` ledger that already exists. No new vendor, no
# SDK, no device identifier, no IDFA — one row carrying an operation name and
# the user id we already know because the request is authenticated.

#: The only event names accepted. An allowlist rather than free text so the
#: endpoint cannot become an arbitrary write channel into the ledger, and so a
#: typo in the client shows up as a 422 instead of a metric nobody can find.
_PAYWALL_EVENTS = {
    "paywall_viewed",
    "pro_monthly_selected",
    "pro_annual_selected",
    "purchase_started",
    "purchase_completed",
    "purchase_failed",
    "purchase_cancelled",
    "purchase_restored",
    "quota_reached_processing",
    "quota_reached_ask",
    "manage_subscription_opened",
}


class EventIn(BaseModel):
    event: str = Field(..., max_length=48)
    #: Which product the event was about, when it was about one. Never anything
    #: that identifies a person — the user id comes from the session.
    product_id: Optional[str] = Field(None, max_length=120)
    #: Where the paywall was opened from: "profile", "quota_processing", ...
    context: Optional[str] = Field(None, max_length=48)


@router.post("/api/telemetry/subscription")
def record_event(body: EventIn,
                 current_user: dict = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Record one paywall/purchase event."""
    if body.event not in _PAYWALL_EVENTS:
        raise HTTPException(status_code=422, detail=f"Unknown event {body.event!r}")

    telemetry.record(
        db, operation=f"paywall.{body.event}", user_id=current_user["id"],
        model=body.product_id, platform=None,
        # `provider` is reused as the placement. Adding a column for a string
        # that only this one path writes would be a schema change for nothing.
        provider=(body.context or None),
        success=not body.event.endswith("_failed"))
    return {"ok": True}


@router.get("/api/ops/subscription-economics")
def subscription_economics(days: int = Query(30, ge=1, le=365),
                           current_user: dict = Depends(require_admin),
                           db: Session = Depends(get_db)):
    """Aggregate consumption per plan — the numbers that decide whether the
    launch limits are priced correctly. Admin-only, and aggregate-only.

    This is the feedback loop the launch defaults were guessed without. It
    answers, in one call: what does an active user of each plan actually consume,
    how many of them reach a ceiling, and what does that cost us per head.
    """
    from sqlalchemy import text as sql_text
    from datetime import datetime, timedelta, timezone as _tz

    since = datetime.now(_tz.utc) - timedelta(days=days)

    rows = db.execute(sql_text("""
        SELECT COALESCE(s.plan, 'free') AS plan,
               COUNT(DISTINCT p.user_id)                       AS users,
               COALESCE(AVG(p.units_used), 0)                  AS avg_units,
               COALESCE(MAX(p.units_used), 0)                  AS max_units,
               COALESCE(AVG(p.ask_used), 0)                    AS avg_ask,
               COALESCE(MAX(p.ask_used), 0)                    AS max_ask
          FROM billing_periods p
          LEFT JOIN subscriptions s ON s.user_id = p.user_id
                                   AND s.status IN ('active', 'grace')
         WHERE p.period_end >= :since
         GROUP BY COALESCE(s.plan, 'free')
    """), {"since": since}).mappings().all()

    out = {}
    for row in rows:
        plan_name = row["plan"] or "free"
        limits = plans.limits_for(plan_name)
        users = int(row["users"] or 0)

        # How many of these users are pressed against a ceiling. The single most
        # important number for tuning: high means the limit is too low (or the
        # plan is underpriced), zero means it is not doing any work.
        at_units = db.execute(sql_text("""
            SELECT COUNT(DISTINCT p.user_id) FROM billing_periods p
              LEFT JOIN subscriptions s ON s.user_id = p.user_id
                                       AND s.status IN ('active','grace')
             WHERE p.period_end >= :since
               AND COALESCE(s.plan,'free') = :plan
               AND p.units_used >= :limit
        """), {"since": since, "plan": plan_name,
               "limit": limits.processing_units}).scalar() or 0

        spend = db.execute(sql_text("""
            SELECT COALESCE(SUM(u.estimated_usd), 0) FROM usage_events u
             WHERE u.created_at >= :since
               AND u.operation NOT LIKE 'platform.%'
               AND u.user_id IN (
                   SELECT p.user_id FROM billing_periods p
                     LEFT JOIN subscriptions s ON s.user_id = p.user_id
                                              AND s.status IN ('active','grace')
                    WHERE p.period_end >= :since
                      AND COALESCE(s.plan,'free') = :plan)
        """), {"since": since, "plan": plan_name}).scalar() or 0.0

        out[plan_name] = {
            "active_users": users,
            "avg_processing_units": round(float(row["avg_units"] or 0), 2),
            "max_processing_units": int(row["max_units"] or 0),
            "unit_limit": limits.processing_units,
            "avg_utilisation": (round(float(row["avg_units"] or 0)
                                      / limits.processing_units, 4)
                                if limits.processing_units else None),
            "avg_ask_messages": round(float(row["avg_ask"] or 0), 2),
            "max_ask_messages": int(row["max_ask"] or 0),
            "ask_limit": limits.ask_messages,
            "users_at_unit_ceiling": int(at_units),
            "pct_at_unit_ceiling": round(int(at_units) / users, 4) if users else None,
            "variable_cost_usd": round(float(spend), 4),
            "variable_cost_per_user_usd": (round(float(spend) / users, 4)
                                           if users else None),
        }

    return {
        "window_days": days,
        "note": ("`platform.*` operations are excluded: they duplicate the "
                 "`acquire.*` events for the same bytes and would double-count."),
        "by_plan": out,
        "unit_weights": plans.describe_weights(),
        "verification": appstore.describe_configuration(),
    }


@router.get("/api/ops/routes")
def route_distribution(days: int = Query(30, ge=1, le=365),
                       current_user: dict = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """Which pipeline route each item actually took, and what it cost.

    The single most important operational number in Sava's economics, and the
    one the launch weights had to be *estimated* without. The whole cost model
    turns on what fraction of short-form saves are served by text/cover versus
    by a video download — the difference between $0.002 and $0.016 per item.

    Re-run this after a few weeks of real traffic and re-derive `ROUTE_UNITS`
    and the plan allowances from it instead of from the 129-item sample the
    defaults were chosen against.
    """
    from sqlalchemy import text as sql_text
    from datetime import datetime, timedelta, timezone as _tz

    since = datetime.now(_tz.utc) - timedelta(days=days)

    rows = db.execute(sql_text("""
        SELECT COALESCE(route, 'unrouted') AS route,
               COALESCE(platform, 'unknown') AS platform,
               COUNT(*) AS n
          FROM canonical_content
         WHERE processing_state IN ('ready', 'partial')
         GROUP BY COALESCE(route, 'unrouted'), COALESCE(platform, 'unknown')
    """)).mappings().all()

    by_route: dict = {}
    by_platform: dict = {}
    total = 0
    for r in rows:
        route_name, platform, n = r["route"], r["platform"], int(r["n"])
        total += n
        entry = by_route.setdefault(route_name, {"items": 0, "units": 0,
                                                 "approx_usd": 0.0})
        entry["items"] += n
        entry["units"] += plans.units_for_route(route_name) * n
        entry["approx_usd"] = round(
            entry["approx_usd"] + plans.ROUTE_USD.get(route_name, 0.0) * n, 4)
        by_platform.setdefault(platform, {}).setdefault(route_name, 0)
        by_platform[platform][route_name] += n

    for entry in by_route.values():
        entry["share"] = round(entry["items"] / total, 4) if total else None

    # What was actually spent, so the estimate can be checked against reality.
    measured = db.execute(sql_text("""
        SELECT COALESCE(SUM(estimated_usd), 0) FROM usage_events
         WHERE created_at >= :since AND operation NOT LIKE 'platform.%'
    """), {"since": since}).scalar() or 0.0

    estimated = round(sum(e["approx_usd"] for e in by_route.values()), 4)
    return {
        "window_days": days,
        "items": total,
        "by_route": by_route,
        "by_platform": by_platform,
        "estimated_usd_from_routes": estimated,
        "measured_usd_in_window": round(float(measured), 4),
        "route_ladder": route_mod.describe(),
        "unit_weights": plans.describe_weights(),
        "note": ("`unrouted` items predate route recording. Compare "
                 "estimated_usd_from_routes against measured_usd_in_window to "
                 "check whether ROUTE_USD still reflects reality."),
    }
