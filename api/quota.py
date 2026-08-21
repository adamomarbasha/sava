"""Per-user limits on the operations that cost money.

Sava already meters spend: every model call and media fetch writes a
`UsageEvent` carrying `estimated_usd`, tokens, audio seconds, frames and proxied
bytes. What was missing was anything that *reads* that meter and says no.

`api/platform_budget.py` protects the platforms — it rate-limits and circuit-
breaks calls to TikTok and YouTube. It does not protect the operator. One account
in a save loop could enqueue unbounded extraction and Gemini work, and the only
symptom would be the bill.

This is abuse and cost protection, not billing. There are no plans and no
payment; there is a daily ceiling per user on each expensive operation, and a
monthly ceiling on money. Legitimate use should never reach them: the defaults
are far above what the product's own usage looks like, and the point is to make
a runaway loop stop rather than to ration normal behaviour.

Deliberately counted from `UsageEvent` rather than a separate counter. A second
source of truth would drift, and the events are already written on the paths that
matter — which also means a restart cannot reset someone's usage.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func

from .models import UsageEvent

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Limit:
    """One ceiling: how many of `operation` per rolling window."""
    key: str
    max_events: int
    window_hours: int
    human: str


# Saves are the expensive one — each triggers extraction plus understanding plus
# embeddings. 200/day is roughly ten times a heavy real day.
SAVES_PER_DAY = _int_env("SAVA_MAX_SAVES_PER_DAY", 200)

# Ask is cheap per call and easy to hammer.
ASKS_PER_DAY = _int_env("SAVA_MAX_ASKS_PER_DAY", 300)

# Reprocessing is user-triggered and re-runs the whole expensive pipeline on
# content that has already been understood, so it is capped hardest.
REPROCESS_PER_DAY = _int_env("SAVA_MAX_REPROCESS_PER_DAY", 30)

# The backstop that does not care which operation spent the money.
MONTHLY_USD = _float_env("SAVA_MAX_MONTHLY_USD_PER_USER", 15.0)

LIMITS = {
    "save": Limit("save", SAVES_PER_DAY, 24, "saves"),
    "ask": Limit("ask", ASKS_PER_DAY, 24, "questions"),
    "reprocess": Limit("reprocess", REPROCESS_PER_DAY, 24, "reprocessing requests"),
}

# `UsageEvent.operation` values that belong to each limit. Kept explicit rather
# than prefix-matched so a new operation name cannot silently escape a ceiling.
_OPERATIONS = {
    "save": ("understanding", "transcript", "vision", "acquire", "embed"),
    "ask": ("ask", "ask_this", "chat"),
    "reprocess": ("reprocess",),
}

ENABLED = os.getenv("SAVA_QUOTAS_ENABLED", "1").lower() not in ("0", "false", "no")


class QuotaExceeded(HTTPException):
    """429 with a plain explanation and when to come back."""

    def __init__(self, message: str, retry_after_seconds: int):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=message,
            headers={"Retry-After": str(max(1, int(retry_after_seconds)))},
        )


def _window_start(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def count_operations(db, user_id: int, kind: str) -> int:
    """How many billable events of `kind` this user caused in the window."""
    limit = LIMITS[kind]
    operations = _OPERATIONS[kind]
    return int(
        db.query(func.count(UsageEvent.id))
        .filter(UsageEvent.user_id == user_id,
                UsageEvent.operation.in_(operations),
                UsageEvent.created_at >= _window_start(limit.window_hours))
        .scalar() or 0)


def month_spend_usd(db, user_id: int) -> float:
    return float(
        db.query(func.coalesce(func.sum(UsageEvent.estimated_usd), 0.0))
        .filter(UsageEvent.user_id == user_id,
                UsageEvent.created_at >= _window_start(24 * 30))
        .scalar() or 0.0)


def check(db, user_id: int, kind: str) -> None:
    """Raise `QuotaExceeded` if this user should not do `kind` right now.

    Called *before* the expensive work starts, so a blocked request costs a
    single indexed count rather than a model call.
    """
    if not ENABLED or kind not in LIMITS:
        return

    limit = LIMITS[kind]
    used = count_operations(db, user_id, kind)
    if used >= limit.max_events:
        logger.warning("quota: user %s hit the %s ceiling (%s/%s)",
                       user_id, kind, used, limit.max_events)
        raise QuotaExceeded(
            f"You've reached today's limit of {limit.max_events} {limit.human}. "
            "This resets on a rolling 24-hour basis.",
            retry_after_seconds=limit.window_hours * 3600)

    spend = month_spend_usd(db, user_id)
    if spend >= MONTHLY_USD:
        logger.warning("quota: user %s hit the monthly spend ceiling ($%.2f)",
                       user_id, spend)
        raise QuotaExceeded(
            "You've reached this month's processing limit. New saves are still "
            "stored — they just won't be analysed until the limit resets.",
            retry_after_seconds=24 * 3600)


def status_for(db, user_id: int) -> dict:
    """What the user has used. Safe to show; contains no operator internals."""
    return {
        "enabled": ENABLED,
        "limits": {
            kind: {
                "used": count_operations(db, user_id, kind),
                "limit": limit.max_events,
                "window_hours": limit.window_hours,
            }
            for kind, limit in LIMITS.items()
        },
        "month_spend_usd": round(month_spend_usd(db, user_id), 4),
        "month_spend_limit_usd": MONTHLY_USD,
    }
