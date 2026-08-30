"""What a user is entitled to, right now.

One question — "is this account Pro?" — answered in one place, from the
database, from data Apple signed. Every expensive path asks here rather than
carrying its own notion of who is paying.

The rule that makes this safe: **entitlement is derived, never asserted.** There
is no code path anywhere that turns a client-supplied boolean into Pro. The only
writer of `subscriptions` is `api/appstore.py`, and it writes only after
verifying Apple's signature over the transaction.

Expiry is evaluated on read rather than by a sweep. A subscription that lapsed
overnight is Free the next time anything asks, without waiting for a cron job —
and there is no window in which a stale row grants access it should not.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status

from . import plans
from .auth import get_current_user
from .db import get_db
from .models import Subscription, SubscriptionStatus

logger = logging.getLogger(__name__)

#: How long past expiry a billing-retry subscription keeps its entitlement.
#: Apple's maximum grace period is 16 days; this matches it rather than guessing.
GRACE_PERIOD_DAYS = 16


@dataclass(frozen=True)
class Entitlement:
    """The resolved answer for one user."""

    user_id: int
    plan: str
    limits: plans.PlanLimits
    status: str
    expires_at: Optional[datetime]
    product_id: Optional[str]
    auto_renew: bool
    environment: Optional[str]

    @property
    def is_pro(self) -> bool:
        return self.plan == plans.PRO

    @property
    def in_billing_retry(self) -> bool:
        """Apple is still trying to charge. Entitled, but worth telling them."""
        return self.status == SubscriptionStatus.GRACE

    def public(self) -> dict:
        """The shape the iOS client reads. No operator internals."""
        return {
            "plan": self.plan,
            "display_name": self.limits.display_name,
            "is_pro": self.is_pro,
            "status": self.status,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "auto_renew": self.auto_renew,
            "in_billing_retry": self.in_billing_retry,
            "product_id": self.product_id,
            "limits": {
                "processing_units": self.limits.processing_units,
                # What the client actually shows. Units are an internal
                # accounting detail; nobody should have to read "1,200
                # processing units" and work out what it buys them.
                "approx_videos": self.limits.approx_videos,
                "ask_messages": self.limits.ask_messages,
                "concurrent_jobs": self.limits.concurrent_jobs,
                "enhanced_analysis": self.limits.enhanced_analysis,
                "priority_processing": self.limits.is_pro,
            },
        }


def _free(user_id: int, *, status_value: str = SubscriptionStatus.NONE,
          expires_at: Optional[datetime] = None,
          product_id: Optional[str] = None,
          environment: Optional[str] = None) -> Entitlement:
    return Entitlement(
        user_id=user_id, plan=plans.FREE, limits=plans.limits_for(plans.FREE),
        status=status_value, expires_at=expires_at, product_id=product_id,
        auto_renew=False, environment=environment)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """Naive datetimes are UTC.

    SQLite hands back naive values for `DateTime(timezone=True)` columns while
    Postgres hands back aware ones. Comparing a naive value to an aware `now()`
    raises, and the shape of that failure would be "expiry check crashes on
    SQLite only" — which is exactly the class of bug that survives a test suite
    that runs on one engine.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def for_user(db, user_id: int) -> Entitlement:
    """Resolve this account's entitlement. Never raises; fails to Free."""
    try:
        sub = (db.query(Subscription)
               .filter(Subscription.user_id == user_id).first())
    except Exception as e:
        # A metering table being unreachable must not take down saving. Free is
        # the safe answer: it under-grants rather than over-grants.
        logger.warning("entitlement lookup failed for user %s: %s", user_id, e)
        return _free(user_id)

    if sub is None:
        return _free(user_id)

    expires_at = _aware(sub.expires_at)
    status_value = sub.status or SubscriptionStatus.NONE

    # Revocation wins over everything, including an expiry date still in the
    # future: a refunded or family-sharing-withdrawn purchase is not entitled
    # even though Apple's original expiry has not passed yet.
    if status_value == SubscriptionStatus.REVOKED:
        return _free(user_id, status_value=SubscriptionStatus.REVOKED,
                     expires_at=expires_at, product_id=sub.product_id,
                     environment=sub.environment)

    if status_value in SubscriptionStatus.ENTITLED:
        # How long past `expires_at` this row still grants access.
        #
        # Zero for ACTIVE: the date has passed, so it is over.
        #
        # For GRACE it is the whole point of the state. Apple keeps retrying a
        # failed charge for up to 16 days and the customer keeps their service
        # throughout — applying the plain expiry check here would cut off
        # everybody whose card bounced, which is both wrong and the most
        # expensive kind of wrong. The window is bounded rather than open-ended
        # so a row that stops being refreshed cannot grant Pro forever.
        allowance = (timedelta(days=GRACE_PERIOD_DAYS)
                     if status_value == SubscriptionStatus.GRACE
                     else timedelta(0))

        if expires_at is not None and (expires_at + allowance) <= datetime.now(timezone.utc):
            # Lapsed since the row was last written. Report expired without
            # writing — this is a read path, and a GET that mutates is how a
            # concurrent verification gets clobbered.
            return _free(user_id, status_value=SubscriptionStatus.EXPIRED,
                         expires_at=expires_at, product_id=sub.product_id,
                         environment=sub.environment)

        limits = plans.limits_for(sub.plan)
        # An entitled row whose plan does not resolve to Pro is a corrupt row.
        # Grant Free and say so loudly rather than silently honouring it.
        if not limits.is_pro:
            logger.warning("subscription %s is %s but plan=%r — treating as Free",
                           sub.id, status_value, sub.plan)
            return _free(user_id, status_value=status_value, expires_at=expires_at,
                         product_id=sub.product_id, environment=sub.environment)

        return Entitlement(
            user_id=user_id, plan=limits.name, limits=limits,
            status=status_value, expires_at=expires_at,
            product_id=sub.product_id, auto_renew=bool(sub.auto_renew),
            environment=sub.environment)

    return _free(user_id, status_value=status_value, expires_at=expires_at,
                 product_id=sub.product_id, environment=sub.environment)


def is_pro(db, user_id: int) -> bool:
    return for_user(db, user_id).is_pro


# ─── FastAPI dependencies ────────────────────────────────────────────────────

def current_entitlement(current_user: dict = Depends(get_current_user),
                        db=Depends(get_db)) -> Entitlement:
    return for_user(db, current_user["id"])


class UpgradeRequired(HTTPException):
    """402 — this capability is part of Sava Pro.

    402 rather than 403 so the client can tell "you need to pay" apart from "you
    may not touch this", and present a paywall for one and an error for the
    other. The body carries `upgrade: true` so an older client that does not
    special-case the status still has something to branch on.
    """

    def __init__(self, message: str, *, capability: Optional[str] = None):
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"message": message, "upgrade": True, "plan": plans.PRO,
                    "capability": capability},
        )


def require_pro(entitlement: Entitlement = Depends(current_entitlement)) -> Entitlement:
    """Gate a Pro-only capability.

    Used as a dependency: `Depends(require_pro)`. Future premium capabilities
    check this same function rather than inspecting a subscription themselves.
    """
    if not entitlement.is_pro:
        raise UpgradeRequired(
            "This is part of Sava Pro.", capability="pro")
    return entitlement
