"""Recording a verified purchase.

The write half of subscriptions. `api/appstore.py` decides whether a transaction
is real; this decides what it means for the account and persists it.

One rule shapes everything here: **`original_transaction_id` is unique across
the whole installation.** Apple gives every subscription a stable id that
survives renewals, upgrades and restores, so it is the natural identity of "this
purchase". Making it unique means one paid subscription can entitle exactly one
Sava account — restoring on a second account finds the row already belongs to
somebody else and is refused, rather than quietly turning one payment into two
Pro users.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from .. import plans
from ..appstore import VerifiedTransaction
from ..models import Subscription, SubscriptionStatus

logger = logging.getLogger(__name__)


class SubscriptionConflict(ValueError):
    """This purchase already entitles a different Sava account."""


def _status_for(transaction: VerifiedTransaction) -> str:
    if transaction.revoked:
        return SubscriptionStatus.REVOKED
    if transaction.expires_at is None:
        # No expiry on a subscription product means Apple has not told us when
        # it ends. Treat as active; the next refresh will carry a date.
        return SubscriptionStatus.ACTIVE
    if transaction.expires_at > datetime.now(timezone.utc):
        return SubscriptionStatus.ACTIVE
    # Lapsed, but auto-renew still on means Apple is retrying the charge. That
    # is a billing retry, not a cancellation, and the customer keeps access.
    return (SubscriptionStatus.GRACE if transaction.auto_renew
            else SubscriptionStatus.EXPIRED)


def apply_transaction(db, user_id: int,
                      transaction: VerifiedTransaction) -> Subscription:
    """Record a verified transaction against this user and return the row.

    Idempotent: re-posting the same transaction (a restore, a relaunch, a
    retried request) converges on the same state rather than stacking.
    """
    owner = (db.query(Subscription)
             .filter(Subscription.original_transaction_id ==
                     transaction.original_transaction_id).first())

    if owner is not None and owner.user_id != user_id:
        logger.warning(
            "subscription %s (original_transaction_id=%s) is already held by "
            "user %s; user %s tried to claim it",
            owner.id, transaction.original_transaction_id, owner.user_id, user_id)
        raise SubscriptionConflict(
            "This subscription is already linked to another Sava account. "
            "Sign in with that account, or contact support.")

    sub = owner or (db.query(Subscription)
                    .filter(Subscription.user_id == user_id).first())
    if sub is None:
        sub = Subscription(user_id=user_id)
        db.add(sub)

    status = _status_for(transaction)

    sub.plan = transaction.plan if status in SubscriptionStatus.ENTITLED else plans.FREE
    sub.status = status
    sub.product_id = transaction.product_id
    sub.original_transaction_id = transaction.original_transaction_id
    sub.latest_transaction_id = transaction.transaction_id
    sub.purchased_at = transaction.purchased_at
    sub.expires_at = transaction.expires_at
    sub.auto_renew = bool(transaction.auto_renew)
    sub.environment = transaction.environment
    sub.verification = transaction.verification
    sub.last_verified_at = datetime.now(timezone.utc)
    # Trimmed: the claim set is small, but a runaway field should not be able to
    # turn a subscription row into a blob.
    sub.last_claims = json.dumps(transaction.claims, default=str)[:4000]

    db.commit()
    db.refresh(sub)
    logger.info("subscription: user %s -> %s (%s, %s, expires %s)",
                user_id, sub.plan, sub.status, sub.product_id, sub.expires_at)
    return sub


def clear(db, user_id: int, *, reason: str = "expired") -> Optional[Subscription]:
    """Drop an account to Free without deleting its history.

    Used when the client reports that StoreKit no longer has any entitlement —
    a cancellation that ran its course, or a revocation. The row is kept because
    the original transaction id must stay claimed: releasing it would let the
    same purchase be re-attached to a second account.
    """
    sub = (db.query(Subscription)
           .filter(Subscription.user_id == user_id).first())
    if sub is None:
        return None
    if sub.status in (SubscriptionStatus.REVOKED,):
        return sub
    sub.plan = plans.FREE
    sub.status = (SubscriptionStatus.REVOKED if reason == "revoked"
                  else SubscriptionStatus.EXPIRED)
    sub.auto_renew = False
    sub.last_verified_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(sub)
    logger.info("subscription: user %s cleared (%s)", user_id, reason)
    return sub
