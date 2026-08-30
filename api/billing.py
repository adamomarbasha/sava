"""Metering: billing periods, and the atomic spending of Processing Units.

This is the money-safety layer. Its whole job is to make sure that between
"user asks for expensive work" and "worker starts spending", the allowance was
checked and debited in a way that concurrent requests cannot beat.

── Why the debit is a conditional UPDATE ───────────────────────────────────

The obvious implementation is read-check-write:

    used = SELECT units_used ...
    if used + n <= limit:
        UPDATE ... SET units_used = used + n

Two saves arriving together both read `used`, both decide there is room, and
both write. The account overspends by exactly the amount that mattered. It is
the classic lost update, and at 30 units a Free account could be walked past its
ceiling by anyone with two devices.

Instead every debit is a single statement whose WHERE clause contains the
limit:

    UPDATE billing_periods SET units_used = units_used + :n
     WHERE id = :period AND units_used + :n <= :limit

The database evaluates and applies in one atomic step. `rowcount` is the answer:
1 means granted, 0 means there was not room. No lock is held across a network
call, and it behaves identically on Postgres and SQLite.

── Why reservations exist ──────────────────────────────────────────────────

A bare counter can be decremented but not *un*-decremented safely — nothing
stops the same failure refunding twice, or a refund arriving for work that
actually completed. `UnitReservation` gives each debit an identity and a state
machine (queued -> settled | refunded), and the unique key
(user_id, canonical_content_id, attempt) makes the whole thing idempotent: a
retried save, a re-enqueued job, or two devices saving the same link cannot debit
twice. The `attempt` component is what keeps a *second* genuine run — a
reprocess — chargeable rather than free.

── The refund rule ─────────────────────────────────────────────────────────

Units are returned when, and only when:

  1. the reservation is still `queued` (never refunded before), AND
  2. the failure is attributable to Sava or its upstreams — the job died, a
     platform was unavailable, acquisition or the model provider failed — and
     not to the content being unprocessable, AND
  3. **no billable AI work was recorded** against that content since the
     reservation opened (`SUM(usage_events.estimated_usd) == 0`).

Condition 3 is what stops retry abuse. If a video was downloaded, transcribed
and half-analysed before the failure, the money is gone; refunding the units
would let somebody spend real dollars repeatedly at no cost to their allowance.
In that case the reservation is *settled* instead, and the user is told the item
failed. Refunds are counted in `units_refunded` rather than silently subtracted,
so a pattern of them is visible.
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import text

from . import plans
from .models import BillingPeriod, UnitReservation, UsageEvent, User

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes; Postgres returns aware ones."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# ─── Period boundaries ───────────────────────────────────────────────────────
#
# Anchored to the day of the month the account was created, not to the 1st.
#
# Two reasons. Operationally it spreads every account's reset across the month
# instead of stacking the entire user base's renewed allowance onto midnight on
# the 1st. Commercially it is the honest boundary: somebody who signs up on the
# 28th should not get four days of Pro before their first reset.
#
# It is deliberately independent of Apple's billing date. Those are different
# clocks — one is when money moves, the other is when the allowance refills —
# and coupling them would mean re-cutting the period every time a renewal
# arrived, which is exactly when you least want to be moving counters around.


def _clamp_day(year: int, month: int, day: int) -> int:
    """Day-of-month that exists in this month. The 31st becomes the 30th, or the
    28th in February — a subscription anchored to a long month must not skip."""
    return min(day, calendar.monthrange(year, month)[1])


def _add_month(moment: datetime, anchor_day: int) -> datetime:
    month = moment.month + 1
    year = moment.year + (1 if month > 12 else 0)
    month = 1 if month > 12 else month
    return moment.replace(year=year, month=month,
                          day=_clamp_day(year, month, anchor_day))


def period_bounds(anchor: datetime, now: Optional[datetime] = None
                  ) -> Tuple[datetime, datetime]:
    """The [start, end) of the billing month containing `now`."""
    now = now or _now()
    anchor = _aware(anchor) or now
    anchor_day = anchor.day

    start = now.replace(
        day=_clamp_day(now.year, now.month, anchor_day),
        hour=anchor.hour, minute=anchor.minute, second=anchor.second,
        microsecond=0)
    if start > now:
        # We are before this month's anchor, so the period began last month.
        month = start.month - 1 or 12
        year = start.year - (1 if start.month == 1 else 0)
        start = start.replace(year=year, month=month,
                              day=_clamp_day(year, month, anchor_day))

    return start, _add_month(start, anchor_day)


def _anchor_for(db, user_id: int) -> datetime:
    """When this account's month turns over. Account creation, or now."""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        created = _aware(getattr(user, "created_at", None)) if user else None
        if created is not None:
            return created
    except Exception as e:
        logger.debug("anchor lookup failed for user %s: %s", user_id, e)
    return _now()


def current_period(db, user_id: int, *, plan: str = plans.FREE) -> BillingPeriod:
    """This user's open billing period, creating it if the month has turned.

    Idempotent under concurrency: the unique key (user_id, period_start) means
    two simultaneous first-saves of the month cannot create two periods — the
    loser of the race re-reads the winner's row.
    """
    start, end = period_bounds(_anchor_for(db, user_id))

    period = (db.query(BillingPeriod)
              .filter(BillingPeriod.user_id == user_id,
                      BillingPeriod.period_start == start).first())
    if period is not None:
        return period

    period = BillingPeriod(user_id=user_id, period_start=start, period_end=end,
                           plan=plan, units_used=0, ask_used=0, units_refunded=0)
    db.add(period)
    try:
        db.commit()
        db.refresh(period)
        return period
    except Exception:
        db.rollback()
        existing = (db.query(BillingPeriod)
                    .filter(BillingPeriod.user_id == user_id,
                            BillingPeriod.period_start == start).first())
        if existing is None:
            raise
        return existing


# ─── Reservation outcomes ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Reservation:
    """The result of asking to spend units."""

    granted: bool
    units: int
    reservation_id: Optional[int]
    units_used: int
    units_limit: int
    period_end: datetime
    #: True when this exact work was already paid for — a retry, not a new debit.
    already_reserved: bool = False

    @property
    def units_remaining(self) -> int:
        return max(0, self.units_limit - self.units_used)


def _counters(db, period_id: int) -> Tuple[int, int]:
    row = db.execute(text(
        "SELECT units_used, ask_used FROM billing_periods WHERE id = :pid"
    ), {"pid": period_id}).first()
    return (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)


def reserve_units(db, user_id: int, *, units: int, entitlement,
                  canonical_content_id: Optional[int] = None,
                  bookmark_id: Optional[int] = None,
                  reason: str = "content.process",
                  baseline_units: int = 0) -> Reservation:
    """Debit `units` if the allowance covers them. Never partially grants.

    Returns a `Reservation` whose `granted` says what happened. Callers must not
    start expensive work on `granted=False` — but they must also not reject the
    user's save, which stays in the library either way.
    """
    limits = entitlement.limits
    limit = int(limits.processing_units)
    units = max(1, int(units))
    period = current_period(db, user_id, plan=entitlement.plan)

    # Idempotency: is there already a *live* debit for this content?
    #
    # Only a `queued` reservation counts. A settled one means an earlier run
    # finished and was paid for; asking again is new work and is charged again,
    # which is what stops "reprocess" being a free button.
    attempt = 0
    if canonical_content_id is not None:
        live = (db.query(UnitReservation)
                .filter(UnitReservation.user_id == user_id,
                        UnitReservation.canonical_content_id == canonical_content_id,
                        UnitReservation.state == "queued").first())
        if live is not None:
            used, _ = _counters(db, period.id)
            return Reservation(
                granted=True, units=int(live.units or 0),
                reservation_id=live.id, units_used=used, units_limit=limit,
                period_end=_aware(period.period_end), already_reserved=True)

        highest = db.execute(text(
            "SELECT MAX(attempt) FROM unit_reservations "
            " WHERE user_id = :uid AND canonical_content_id = :cid"
        ), {"uid": user_id, "cid": canonical_content_id}).scalar()
        attempt = (int(highest) + 1) if highest is not None else 0

    # The atomic debit. Everything that makes this safe is in the WHERE clause.
    result = db.execute(text("""
        UPDATE billing_periods
           SET units_used = units_used + :n
         WHERE id = :pid
           AND units_used + :n <= :limit
    """), {"n": units, "pid": period.id, "limit": limit})

    if (result.rowcount or 0) != 1:
        db.rollback()
        used, _ = _counters(db, period.id)
        logger.info("units: user %s denied %s unit(s) (%s/%s used)",
                    user_id, units, used, limit)
        return Reservation(granted=False, units=units, reservation_id=None,
                           units_used=used, units_limit=limit,
                           period_end=_aware(period.period_end))

    reservation = UnitReservation(
        user_id=user_id, period_id=period.id,
        canonical_content_id=canonical_content_id, bookmark_id=bookmark_id,
        units=units, state="queued", reason=reason, attempt=attempt,
        baseline_units=max(0, int(baseline_units or 0)))
    db.add(reservation)
    try:
        db.commit()
    except Exception as e:
        # Lost the race to insert the reservation. The rollback takes the debit
        # with it, so the winner's reservation is the only one that paid — and
        # the work it covers is the same work, so this caller may proceed.
        db.rollback()
        logger.info("units: reservation race for user %s content %s (%s)",
                    user_id, canonical_content_id, e)
        used, _ = _counters(db, period.id)
        return Reservation(granted=True, units=units, reservation_id=None,
                           units_used=used, units_limit=limit,
                           period_end=_aware(period.period_end),
                           already_reserved=True)

    used, _ = _counters(db, period.id)
    return Reservation(granted=True, units=units, reservation_id=reservation.id,
                       units_used=used, units_limit=limit,
                       period_end=_aware(period.period_end))


def settle(db, *, user_id: int, canonical_content_id: int,
           actual_units: Optional[int] = None) -> None:
    """Close a reservation once the work is genuinely done.

    Reconciles what the account has paid toward this run to what the run
    actually cost. The comparison is against **`baseline_units + this
    reservation`**, not against this reservation alone — that was a real
    over-charge:

        save routes to text   reserve 1, settle to 1              paid 1
        Ask needs the picture reserve 7 (the 8-unit route minus 1) paid 8
        worker finishes       settle sees actual 8 vs reserved 7   paid 9

    The account paid 9 units for an 8-unit item, every time an Ask escalated.
    `_escalate` records the 1 already paid as `baseline_units`, so settlement
    sees 1 + 7 == 8 and charges nothing further.

    Deliberately not a period-wide sum over the item. That fixes the same +1 and
    opens a worse hole: a reprocess is a genuinely new run of the same route, and
    reconciling it against the earlier run's charge makes every reprocess after
    the first free while still downloading the video — about $14 a month per
    user of real spend, uncharged. Runs are distinguished by intent, and only
    the caller knows its intent, so the caller states it.

    Settlement stays idempotent: it only ever acts on a `queued` reservation, so
    a retried job or a repeated call finds nothing to do.
    """
    reservation = (db.query(UnitReservation)
                   .filter(UnitReservation.user_id == user_id,
                           UnitReservation.canonical_content_id == canonical_content_id,
                           UnitReservation.state == "queued").first())
    if reservation is None:
        return

    reserved = int(reservation.units or 0)
    baseline = max(0, int(reservation.baseline_units or 0))
    if actual_units is not None:
        # What this run still owes, after what an earlier run already paid.
        # Floored at 1 so a run can never end up costing nothing at all.
        outstanding = max(1, int(actual_units) - baseline)
        delta = outstanding - reserved
        if delta > 0:
            applied = db.execute(text("""
                UPDATE billing_periods SET units_used = units_used + :n
                 WHERE id = :pid
            """), {"n": delta, "pid": reservation.period_id})
            if applied.rowcount:
                reservation.units = reserved + delta
                logger.info("units: settled +%s for user %s content %s",
                            delta, user_id, canonical_content_id)
        elif delta < 0:
            db.execute(text("""
                UPDATE billing_periods
                   SET units_used = CASE WHEN units_used + :d < 0 THEN 0
                                         ELSE units_used + :d END
                 WHERE id = :pid
            """), {"d": delta, "pid": reservation.period_id})
            reservation.units = max(1, reserved + delta)

    reservation.state = "settled"
    reservation.settled_at = _now()
    db.commit()


def _billable_spend_since(db, canonical_content_id: int,
                          since: Optional[datetime]) -> float:
    """Dollars actually spent understanding this content since `since`.

    The refund gate. If this is above zero, real money was spent and the units
    are not coming back.
    """
    if since is None:
        return 0.0
    try:
        # The ORM rather than `text()`, so SQLAlchemy applies the column's own
        # type on both engines.
        #
        # The one-second back-off is not slop, it is a precision correction.
        # SQLite's `CURRENT_TIMESTAMP` — which is what `func.now()` compiles to,
        # and what wrote both rows — truncates to whole seconds and stores
        # "2026-08-30 04:38:19". SQLAlchemy binds a Python datetime as
        # "2026-08-30 04:38:19.000000". Those are compared as *strings*, and the
        # stored value sorts BEFORE the bound one, so an event written in the
        # same second as the reservation was invisible and the units were handed
        # straight back. Postgres compares real timestamps and never had the
        # problem, which is exactly why it survived a green test run.
        #
        # Widening the window can only make the gate find *more* spend, i.e.
        # refund less. It fails closed, which is the correct direction for
        # something whose failure mode is giving away paid-for work.
        from sqlalchemy import func as sa_func
        floor = since - timedelta(seconds=1)
        value = (db.query(sa_func.coalesce(sa_func.sum(UsageEvent.estimated_usd), 0.0))
                 .filter(UsageEvent.canonical_content_id == canonical_content_id,
                         UsageEvent.created_at >= floor)
                 .scalar())
        return float(value or 0.0)
    except Exception as e:
        # Cannot prove nothing was spent, so assume something was. Failing this
        # check closed costs the user units they might deserve back; failing it
        # open is a free retry loop.
        logger.warning("refund gate could not read spend for content %s: %s",
                       canonical_content_id, e)
        return 1.0


def refund(db, *, user_id: int, canonical_content_id: int,
           reason: str = "infrastructure_failure") -> bool:
    """Give units back for work that failed before costing anything.

    Returns whether a refund was actually issued. See the module docstring for
    the rule; the three conditions are enforced here in order.
    """
    reservation = (db.query(UnitReservation)
                   .filter(UnitReservation.user_id == user_id,
                           UnitReservation.canonical_content_id == canonical_content_id,
                           UnitReservation.state == "queued").first())
    if reservation is None:
        return False   # already settled or refunded — condition 1

    # Passed exactly as SQLAlchemy loaded it — deliberately NOT normalised.
    #
    # The two engines store `DateTime(timezone=True)` differently (SQLite: naive
    # text, Postgres: timestamptz) and hand it back the same way they store it.
    # Converting to aware made the comparison right on Postgres and wrong on
    # SQLite; converting to naive did the reverse. Round-tripping the value
    # untouched is correct on both, because it goes back in the shape it came
    # out in.
    spend = _billable_spend_since(db, canonical_content_id,
                                  reservation.created_at)
    if spend > 0:
        # Condition 3 failed: money was spent. Settle instead, so the
        # reservation closes and cannot be refunded by a later retry.
        reservation.state = "settled"
        reservation.settled_at = _now()
        reservation.reason = f"{reason}:spent_${spend:.4f}"
        db.commit()
        logger.info("units: NOT refunding user %s content %s — $%.4f already spent",
                    user_id, canonical_content_id, spend)
        return False

    units = max(0, int(reservation.units or 0))
    db.execute(text("""
        UPDATE billing_periods
           SET units_used = CASE WHEN units_used - :n < 0 THEN 0
                                 ELSE units_used - :n END,
               units_refunded = units_refunded + :n
         WHERE id = :pid
    """), {"n": units, "pid": reservation.period_id})
    reservation.state = "refunded"
    reservation.settled_at = _now()
    reservation.reason = reason
    db.commit()
    logger.info("units: refunded %s to user %s for content %s (%s)",
                units, user_id, canonical_content_id, reason)
    return True


# ─── Ask messages ────────────────────────────────────────────────────────────

def consume_ask(db, user_id: int, *, entitlement) -> Tuple[bool, int, int, datetime]:
    """Debit one Ask message. Returns (allowed, used, limit, resets_at).

    Same conditional-UPDATE shape as units, for the same reason: Ask is cheap
    per call but trivially parallelised, and a read-then-write here would let a
    handful of concurrent questions walk straight past the ceiling.
    """
    limit = int(entitlement.limits.ask_messages)
    period = current_period(db, user_id, plan=entitlement.plan)

    result = db.execute(text("""
        UPDATE billing_periods SET ask_used = ask_used + 1
         WHERE id = :pid AND ask_used + 1 <= :limit
    """), {"pid": period.id, "limit": limit})

    if (result.rowcount or 0) != 1:
        db.rollback()
        _, used = _counters(db, period.id)
        return False, used, limit, _aware(period.period_end)

    db.commit()
    _, used = _counters(db, period.id)
    return True, used, limit, _aware(period.period_end)


def refund_ask(db, user_id: int) -> None:
    """Give an Ask message back when the answer never happened.

    Called when Ask fails before producing anything — no retrieval, no model
    call, nothing the user could read. Charging for that would be charging for
    our own outage.
    """
    try:
        period = current_period(db, user_id)
        db.execute(text("""
            UPDATE billing_periods
               SET ask_used = CASE WHEN ask_used - 1 < 0 THEN 0 ELSE ask_used - 1 END
             WHERE id = :pid
        """), {"pid": period.id})
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("ask refund failed for user %s: %s", user_id, e)


# ─── Concurrency ─────────────────────────────────────────────────────────────

def running_jobs_for(db, user_id: int) -> int:
    """How many expensive jobs this user currently has in flight."""
    try:
        return int(db.execute(text(
            "SELECT COUNT(*) FROM jobs WHERE state = 'running' AND user_id = :uid"
        ), {"uid": user_id}).scalar() or 0)
    except Exception as e:
        logger.debug("concurrency count failed for user %s: %s", user_id, e)
        return 0


# ─── Reporting ───────────────────────────────────────────────────────────────

def usage_for(db, user_id: int, *, entitlement) -> dict:
    """The numbers the Profile screen shows.

    `resets_at` is an ISO instant, not a formatted string: the phone formats it
    for the user's locale and calendar, which is not something a server should
    be guessing at.
    """
    limits = entitlement.limits
    period = current_period(db, user_id, plan=entitlement.plan)
    units_used, ask_used = _counters(db, period.id)

    unit_limit = int(limits.processing_units)
    ask_limit = int(limits.ask_messages)

    return {
        "period_start": _aware(period.period_start).isoformat(),
        "resets_at": _aware(period.period_end).isoformat(),
        "processing_units": {
            "used": units_used,
            "limit": unit_limit,
            "remaining": max(0, unit_limit - units_used),
            "exhausted": units_used >= unit_limit,
            # The same allowance expressed the way the product talks about it.
            # Units are the internal accounting; videos are what the user has.
            "approx_videos_remaining": int(
                max(0, unit_limit - units_used) / plans.TYPICAL_UNITS_PER_VIDEO),
            "approx_videos_limit": limits.approx_videos,
        },
        "ask_messages": {
            "used": ask_used,
            "limit": ask_limit,
            "remaining": max(0, ask_limit - ask_used),
            "exhausted": ask_used >= ask_limit,
        },
        "concurrent_jobs": {
            "running": running_jobs_for(db, user_id),
            "limit": int(limits.concurrent_jobs),
        },
    }
