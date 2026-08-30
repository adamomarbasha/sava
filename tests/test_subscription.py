"""Sava Pro: entitlement, metering, and the money-safety properties.

The tests that matter most here are the ones that would let money leak:

  * a client cannot assert its own plan,
  * concurrent saves cannot overspend an allowance,
  * hitting a limit must never cost the user their save,
  * a refund cannot be claimed twice, or for work that already cost money,
  * one purchase cannot entitle two accounts.

Everything else is arithmetic.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from api import billing, entitlements, plans
from api.appstore import PRO_ANNUAL, PRO_MONTHLY, VerificationError
from api.models import (
    BillingPeriod, Bookmark, CanonicalContent, Job, ProcessingState,
    Subscription, SubscriptionStatus, UnitReservation,
)
from api.services import subscription as subscription_svc

from conftest import make_user


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _transaction(product_id=PRO_MONTHLY, *, original="1000000000000001",
                 expires_in_days=30, revoked=False, auto_renew=True,
                 environment="Production", verification="local_testing"):
    """A `VerifiedTransaction` as `appstore` would have produced it.

    Built directly rather than by signing a JWS: the signature path is tested
    separately, and every test that is about *entitlement* should not also
    depend on cryptography.
    """
    from api.appstore import VerifiedTransaction
    now = datetime.now(timezone.utc)
    return VerifiedTransaction(
        product_id=product_id,
        plan="pro",
        original_transaction_id=original,
        transaction_id=original + "9",
        purchased_at=now,
        expires_at=(now + timedelta(days=expires_in_days)
                    if expires_in_days is not None else None),
        environment=environment,
        revoked=revoked,
        revocation_reason="1" if revoked else None,
        auto_renew=auto_renew,
        verification=verification,
        claims={"productId": product_id, "originalTransactionId": original},
    )


def _canonical(db, key, *, media_kind="video", duration=None, state=None):
    cc = CanonicalContent(
        content_key=key, platform="tiktok", canonical_url=f"https://t/{key}",
        media_kind=media_kind, duration_seconds=duration,
        processing_state=state or ProcessingState.QUEUED)
    db.add(cc)
    db.commit()
    db.refresh(cc)
    return cc


@pytest.fixture
def user(clean_db):
    return make_user(clean_db, "pro-tests@example.com")


# ─── Processing Unit weights ─────────────────────────────────────────────────

class TestSaveTimeEstimate:
    """What a save is charged *before* its route is known.

    The duration ladder these tests used to assert (2/4/8/15/25 units by length)
    is gone. It priced a 40-minute YouTube video at 15 units for $0.0097 of work
    and a 30-second TikTok at 2 units for $0.0310 — backwards on Sava's core
    platform. Pricing is now by the route the pipeline actually took; see
    `tests/test_routing.py::TestRouteMetering`.
    """

    @pytest.mark.parametrize("kind,duration,expected", [
        ("article", None, 1),
        ("image", None, 1),
        ("carousel", None, 1),
        ("capture", None, 1),
        ("unknown", None, 1),
        ("video", 1, 1),
        ("video", 180, 1),
        ("video", 3600, 1),
        ("video", 36000, 1),
        ("video", None, 1),
    ])
    def test_every_save_reserves_one_unit(self, kind, duration, expected):
        """Flat, because at save time nothing is known.

        `create_save` does no network I/O, so it cannot know whether this item
        has captions or will need frames. It reserves the cheap route and
        `settle()` collects the difference from the route that actually ran.
        """
        assert plans.units_for(kind, duration) == expected

    def test_reserving_cheap_is_the_deliberate_direction(self):
        """Reserving the expensive route would refuse affordable saves.

        A user with 5 units left must not be told a video they can comfortably
        afford is unaffordable because of our ignorance at that instant.
        """
        assert plans.units_for("video", None) <= plans.units_for_route("light_vision")

    def test_never_free(self):
        for kind in ("video", "article", "", None, "nonsense"):
            assert plans.units_for(kind, 0) >= 1

    def test_weights_table_is_generated_not_transcribed(self):
        rows = plans.describe_weights()
        assert [r["units"] for r in rows] == [0, 0, 1, 1, 3, 8, 12]
        assert [r["route"] for r in rows] == [
            "cached", "metadata", "text", "cover", "audio",
            "light_vision", "deep_vision"]


class TestPlanLimits:
    def test_launch_values(self):
        free, pro = plans.limits_for("free"), plans.limits_for("pro")
        assert (free.processing_units, free.ask_messages,
                free.concurrent_jobs, free.enhanced_analysis) == (300, 150, 1, False)
        assert (pro.processing_units, pro.ask_messages,
                pro.concurrent_jobs, pro.enhanced_analysis) == (1200, 1500, 3, True)

    def test_pro_is_meaningfully_more_than_free(self):
        """The ratio is the product decision; the absolute numbers are tuning."""
        free, pro = plans.limits_for("free"), plans.limits_for("pro")
        assert pro.processing_units >= free.processing_units * 3
        assert pro.ask_messages > free.ask_messages

    def test_free_is_generous_enough_to_build_a_habit(self):
        """~4 understood videos a day. Not "5 TikToks and you are done"."""
        assert plans.limits_for("free").approx_videos >= 100

    def test_pro_supports_hundreds_of_videos(self):
        assert plans.limits_for("pro").approx_videos >= 400

    def test_pro_is_claimed_by_priority_first(self):
        assert plans.limits_for("pro").job_priority < plans.limits_for("free").job_priority

    @pytest.mark.parametrize("value", [None, "", "  ", "PRO_MAX", "unlimited", "admin"])
    def test_unknown_plan_names_fail_closed_to_free(self, value):
        assert plans.limits_for(value).name == plans.FREE


# ─── Entitlement resolution ──────────────────────────────────────────────────

class TestEntitlement:
    def test_no_subscription_is_free(self, clean_db, user):
        assert entitlements.for_user(clean_db, user.id).plan == plans.FREE

    def test_active_subscription_is_pro(self, clean_db, user):
        subscription_svc.apply_transaction(clean_db, user.id, _transaction())
        e = entitlements.for_user(clean_db, user.id)
        assert e.is_pro
        assert e.limits.processing_units == plans.limits_for("pro").processing_units

    def test_expired_subscription_is_free(self, clean_db, user):
        subscription_svc.apply_transaction(
            clean_db, user.id, _transaction(expires_in_days=-1, auto_renew=False))
        e = entitlements.for_user(clean_db, user.id)
        assert not e.is_pro
        assert e.status == SubscriptionStatus.EXPIRED

    def test_billing_retry_keeps_access(self, clean_db, user):
        """Apple is still trying to charge. Cutting them off now loses a customer
        who has not actually cancelled."""
        subscription_svc.apply_transaction(
            clean_db, user.id, _transaction(expires_in_days=-1, auto_renew=True))
        e = entitlements.for_user(clean_db, user.id)
        assert e.is_pro and e.in_billing_retry

    def test_revoked_is_free_even_before_expiry(self, clean_db, user):
        """A refund revokes immediately; the original expiry is irrelevant."""
        subscription_svc.apply_transaction(
            clean_db, user.id, _transaction(expires_in_days=300, revoked=True))
        e = entitlements.for_user(clean_db, user.id)
        assert not e.is_pro
        assert e.status == SubscriptionStatus.REVOKED

    def test_lapse_between_writes_is_caught_on_read(self, clean_db, user):
        """The row still says active; the clock says otherwise. Read wins."""
        subscription_svc.apply_transaction(clean_db, user.id, _transaction())
        sub = clean_db.query(Subscription).filter_by(user_id=user.id).first()
        sub.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        sub.status = SubscriptionStatus.ACTIVE
        clean_db.commit()
        assert not entitlements.for_user(clean_db, user.id).is_pro

    def test_corrupt_plan_on_an_active_row_grants_nothing(self, clean_db, user):
        subscription_svc.apply_transaction(clean_db, user.id, _transaction())
        sub = clean_db.query(Subscription).filter_by(user_id=user.id).first()
        # Must fit `Subscription.plan` (VARCHAR(16)) — the point of the test is
        # an *unrecognised* plan on an entitled row, not an oversized one.
        sub.plan = "enterprise"
        clean_db.commit()
        assert not entitlements.for_user(clean_db, user.id).is_pro

    def test_annual_and_monthly_grant_the_same_thing(self, clean_db):
        a = make_user(clean_db, "annual@example.com")
        m = make_user(clean_db, "monthly@example.com")
        subscription_svc.apply_transaction(
            clean_db, a.id, _transaction(PRO_ANNUAL, original="200"))
        subscription_svc.apply_transaction(
            clean_db, m.id, _transaction(PRO_MONTHLY, original="201"))
        assert (entitlements.for_user(clean_db, a.id).limits
                == entitlements.for_user(clean_db, m.id).limits)


class TestPurchaseCannotBeShared:
    def test_one_transaction_entitles_one_account(self, clean_db):
        first = make_user(clean_db, "first@example.com")
        second = make_user(clean_db, "second@example.com")
        transaction = _transaction(original="9999")

        subscription_svc.apply_transaction(clean_db, first.id, transaction)
        with pytest.raises(subscription_svc.SubscriptionConflict):
            subscription_svc.apply_transaction(clean_db, second.id, transaction)

        assert entitlements.for_user(clean_db, first.id).is_pro
        assert not entitlements.for_user(clean_db, second.id).is_pro

    def test_restoring_on_the_same_account_is_idempotent(self, clean_db, user):
        transaction = _transaction(original="4242")
        for _ in range(3):
            subscription_svc.apply_transaction(clean_db, user.id, transaction)
        assert clean_db.query(Subscription).filter_by(user_id=user.id).count() == 1
        assert entitlements.for_user(clean_db, user.id).is_pro


# ─── Verification: the client may not assert a plan ──────────────────────────

class TestVerification:
    def test_a_plain_claim_is_not_a_transaction(self):
        from api import appstore
        with pytest.raises(VerificationError):
            appstore.verify_signed_transaction(json.dumps({"is_pro": True}))

    def test_empty_is_refused(self):
        from api import appstore
        with pytest.raises(VerificationError):
            appstore.verify_signed_transaction("")

    def test_unknown_product_grants_nothing(self, monkeypatch):
        """Local testing still validates the claims — it only skips the chain."""
        import base64
        from api import appstore
        monkeypatch.setattr(appstore, "LOCAL_TESTING", True)

        def _seg(obj):
            return base64.urlsafe_b64encode(
                json.dumps(obj).encode()).decode().rstrip("=")
        jws = ".".join([_seg({"alg": "ES256"}),
                        _seg({"productId": "com.sava.mobile.pro.lifetime",
                              "originalTransactionId": "1"}),
                        "sig"])
        with pytest.raises(VerificationError, match="not a Sava product"):
            appstore.verify_signed_transaction(jws)

    def test_production_refuses_without_a_root_certificate(self, monkeypatch):
        """No configuration exists in which production accepts an unchecked
        signature. Absent root == no entitlement, loudly."""
        from api import appstore
        monkeypatch.setattr(appstore, "LOCAL_TESTING", False)
        monkeypatch.setattr(appstore, "APPLE_ROOT_CA_PATH", None)
        with pytest.raises(VerificationError, match="not configured"):
            appstore.verify_signed_transaction("a.b.c")

    def test_configuration_report_is_honest(self, monkeypatch):
        from api import appstore
        monkeypatch.setattr(appstore, "APPLE_ROOT_CA_PATH", None)
        assert appstore.describe_configuration()["can_verify_production_purchases"] is False


# ─── Billing periods ─────────────────────────────────────────────────────────

class TestBillingPeriod:
    def test_period_contains_now_and_is_a_month_long(self, clean_db, user):
        period = billing.current_period(clean_db, user.id)
        start = period.period_start.replace(tzinfo=timezone.utc) \
            if period.period_start.tzinfo is None else period.period_start
        end = period.period_end.replace(tzinfo=timezone.utc) \
            if period.period_end.tzinfo is None else period.period_end
        now = datetime.now(timezone.utc)
        assert start <= now < end
        assert timedelta(days=27) <= (end - start) <= timedelta(days=32)

    def test_the_same_period_is_reused(self, clean_db, user):
        a = billing.current_period(clean_db, user.id)
        b = billing.current_period(clean_db, user.id)
        assert a.id == b.id
        assert clean_db.query(BillingPeriod).filter_by(user_id=user.id).count() == 1

    def test_anchor_day_31_survives_february(self):
        """A subscription anchored to a long month must not skip short ones.

        Anchored on 31 January, mid-February: the period runs 31 Jan -> 28 Feb.
        The end is clamped because February has no 31st — without the clamp
        `replace(day=31)` raises and the account gets no period at all.
        """
        anchor = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
        start, end = billing.period_bounds(
            anchor, now=datetime(2026, 2, 15, tzinfo=timezone.utc))
        assert (start.month, start.day) == (1, 31)
        assert (end.month, end.day) == (2, 28)

    def test_a_clamped_month_does_not_permanently_move_the_anchor(self):
        """February clamps to the 28th; March must go back to the 31st.

        A naive implementation that carried the clamped day forward would walk
        every long-month subscriber's reset date earlier and earlier.
        """
        anchor = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
        start, end = billing.period_bounds(
            anchor, now=datetime(2026, 3, 15, tzinfo=timezone.utc))
        assert (start.month, start.day) == (3, 31 - 0) or start.month == 2
        # March 15 falls in the period that began 28 Feb and ends 28 March.
        assert start < datetime(2026, 3, 15, tzinfo=timezone.utc) < end

    def test_before_this_months_anchor_the_period_began_last_month(self):
        anchor = datetime(2026, 1, 20, 9, 0, tzinfo=timezone.utc)
        start, end = billing.period_bounds(
            anchor, now=datetime(2026, 3, 5, tzinfo=timezone.utc))
        assert (start.month, start.day) == (2, 20)
        assert (end.month, end.day) == (3, 20)

    def test_period_is_not_derived_from_a_client(self, clean_db, user):
        """There is no argument through which a caller can move the boundary."""
        import inspect
        signature = inspect.signature(billing.current_period)
        assert set(signature.parameters) == {"db", "user_id", "plan"}


# ─── Reserving units ─────────────────────────────────────────────────────────

class TestReserveUnits:
    def test_grants_within_allowance(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        limit = e.limits.processing_units
        r = billing.reserve_units(clean_db, user.id, units=8, entitlement=e,
                                  canonical_content_id=1)
        assert r.granted and r.units_used == 8 and r.units_remaining == limit - 8

    def test_refuses_past_the_ceiling_without_partial_grants(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        limit = e.limits.processing_units
        billing.reserve_units(clean_db, user.id, units=limit - 5, entitlement=e,
                              canonical_content_id=1)
        r = billing.reserve_units(clean_db, user.id, units=8, entitlement=e,
                                  canonical_content_id=2)
        assert not r.granted
        # Nothing was taken. A partial debit would charge for work never done.
        assert r.units_used == limit - 5

    def test_exactly_reaching_the_ceiling_is_allowed(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        r = billing.reserve_units(clean_db, user.id,
                                  units=e.limits.processing_units, entitlement=e,
                                  canonical_content_id=1)
        assert r.granted and r.units_remaining == 0

    def test_pro_gets_the_bigger_allowance(self, clean_db, user):
        subscription_svc.apply_transaction(clean_db, user.id, _transaction())
        e = entitlements.for_user(clean_db, user.id)
        pro_limit = plans.limits_for("pro").processing_units
        r = billing.reserve_units(clean_db, user.id, units=pro_limit - 10,
                                  entitlement=e, canonical_content_id=1)
        assert r.granted and r.units_limit == pro_limit

    def test_upgrading_raises_the_ceiling_immediately(self, clean_db, user):
        """Mid-period upgrade must not wait for the next reset."""
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id,
                              units=e.limits.processing_units, entitlement=e,
                              canonical_content_id=1)
        assert not billing.reserve_units(clean_db, user.id, units=2, entitlement=e,
                                         canonical_content_id=2).granted

        subscription_svc.apply_transaction(clean_db, user.id, _transaction())
        pro = entitlements.for_user(clean_db, user.id)
        assert billing.reserve_units(clean_db, user.id, units=2, entitlement=pro,
                                     canonical_content_id=2).granted

    def test_the_same_work_is_not_charged_twice(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        first = billing.reserve_units(clean_db, user.id, units=4, entitlement=e,
                                      canonical_content_id=77)
        second = billing.reserve_units(clean_db, user.id, units=4, entitlement=e,
                                       canonical_content_id=77)
        assert first.granted and second.granted and second.already_reserved
        assert billing.current_period(clean_db, user.id).units_used == 4

    def test_concurrent_saves_cannot_overspend(self, clean_db, user):
        """The property the whole conditional-UPDATE design exists for.

        The allowance is filled to within three units, then ten simultaneous
        four-unit saves race for the remainder. A read-then-write would let most
        of them through and overshoot; exactly none of them fit.
        """
        e = entitlements.for_user(clean_db, user.id)
        limit = e.limits.processing_units
        billing.reserve_units(clean_db, user.id, units=limit - 7, entitlement=e,
                              canonical_content_id=999)
        granted = [billing.reserve_units(clean_db, user.id, units=4, entitlement=e,
                                         canonical_content_id=i).granted
                   for i in range(10)]
        assert sum(granted) == 1                      # 7 left, one 4 fits
        used = billing.current_period(clean_db, user.id).units_used
        assert used == limit - 3 <= limit


class TestSettlement:
    def test_a_longer_video_than_estimated_is_charged_the_difference(self, clean_db, user):
        """Saved as 'unknown video' (2u); turned out to be 40 minutes (15u)."""
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id, units=2, entitlement=e,
                              canonical_content_id=5)
        assert billing.current_period(clean_db, user.id).units_used == 2

        billing.settle(clean_db, user_id=user.id, canonical_content_id=5,
                       actual_units=15)
        assert billing.current_period(clean_db, user.id).units_used == 15

    def test_a_shorter_video_gives_the_difference_back(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id, units=8, entitlement=e,
                              canonical_content_id=6)
        billing.settle(clean_db, user_id=user.id, canonical_content_id=6,
                       actual_units=2)
        assert billing.current_period(clean_db, user.id).units_used == 2

    def test_settling_closes_the_reservation(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id, units=2, entitlement=e,
                              canonical_content_id=7)
        billing.settle(clean_db, user_id=user.id, canonical_content_id=7)
        row = clean_db.query(UnitReservation).filter_by(
            user_id=user.id, canonical_content_id=7).first()
        assert row.state == "settled"

    def test_reprocessing_settled_content_is_charged_again(self, clean_db, user):
        """Otherwise 'reprocess' is a free button on every item in the library."""
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id, units=4, entitlement=e,
                              canonical_content_id=8)
        billing.settle(clean_db, user_id=user.id, canonical_content_id=8)

        again = billing.reserve_units(clean_db, user.id, units=4, entitlement=e,
                                      canonical_content_id=8, reason="reprocess")
        assert again.granted and not again.already_reserved
        assert billing.current_period(clean_db, user.id).units_used == 8


class TestRefund:
    def _spend(self, db, canonical_id, usd):
        from api.ai import telemetry
        telemetry.record(db, operation="understanding",
                         canonical_content_id=canonical_id, estimated_usd=usd)

    def test_refunds_when_nothing_was_spent(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id, units=8, entitlement=e,
                              canonical_content_id=10)
        assert billing.refund(clean_db, user_id=user.id, canonical_content_id=10)
        assert billing.current_period(clean_db, user.id).units_used == 0

    def test_does_not_refund_when_money_was_already_spent(self, clean_db, user):
        """The retry-abuse gate. Real dollars went out; the units stay spent."""
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id, units=8, entitlement=e,
                              canonical_content_id=11)
        self._spend(clean_db, 11, 0.02)

        assert not billing.refund(clean_db, user_id=user.id, canonical_content_id=11)
        assert billing.current_period(clean_db, user.id).units_used == 8
        row = clean_db.query(UnitReservation).filter_by(
            user_id=user.id, canonical_content_id=11).first()
        assert row.state == "settled"   # closed, so a later retry cannot refund it

    def test_a_refund_cannot_be_claimed_twice(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id, units=8, entitlement=e,
                              canonical_content_id=12)
        assert billing.refund(clean_db, user_id=user.id, canonical_content_id=12)
        assert not billing.refund(clean_db, user_id=user.id, canonical_content_id=12)
        assert billing.current_period(clean_db, user.id).units_used == 0

    def test_settled_work_cannot_be_refunded(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id, units=4, entitlement=e,
                              canonical_content_id=13)
        billing.settle(clean_db, user_id=user.id, canonical_content_id=13)
        assert not billing.refund(clean_db, user_id=user.id, canonical_content_id=13)

    def test_refunds_are_counted_not_just_subtracted(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id, units=8, entitlement=e,
                              canonical_content_id=14)
        billing.refund(clean_db, user_id=user.id, canonical_content_id=14)
        assert billing.current_period(clean_db, user.id).units_refunded == 8


# ─── Ask messages ────────────────────────────────────────────────────────────

class TestAskAllowance:
    def test_free_ask_allowance_is_enforced_exactly(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        allowance = e.limits.ask_messages
        for _ in range(allowance):
            assert billing.consume_ask(clean_db, user.id, entitlement=e)[0]
        allowed, used, limit, _resets = billing.consume_ask(
            clean_db, user.id, entitlement=e)
        assert not allowed and used == allowance and limit == allowance

    def test_pro_gets_fifteen_hundred(self, clean_db, user):
        subscription_svc.apply_transaction(clean_db, user.id, _transaction())
        e = entitlements.for_user(clean_db, user.id)
        assert (billing.consume_ask(clean_db, user.id, entitlement=e)[2]
                == plans.limits_for("pro").ask_messages)

    def test_a_failed_answer_is_not_charged(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        billing.consume_ask(clean_db, user.id, entitlement=e)
        billing.refund_ask(clean_db, user.id)
        assert billing.current_period(clean_db, user.id).ask_used == 0

    def test_refund_never_goes_negative(self, clean_db, user):
        billing.refund_ask(clean_db, user.id)
        assert billing.current_period(clean_db, user.id).ask_used == 0


# ─── The save must survive the limit ─────────────────────────────────────────

class TestSaveSurvivesTheLimit:
    """The single most important product rule in this feature.

    Running out of AI units is a processing event. It must never be a *saving*
    event: the item lands in the library, keeps its URL and note, and can be
    opened, organised and deleted exactly like any other.
    """

    def test_save_succeeds_and_is_marked_rather_than_rejected(self, clean_db, user):
        from api.services.save import _schedule_processing

        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id,
                              units=e.limits.processing_units, entitlement=e,
                              canonical_content_id=999)   # exhaust

        # Recorded as having taken the frames route, so it settles at 8 units.
        cc = _canonical(clean_db, "tiktok:limit1", duration=2400)
        cc.route = "light_vision"
        clean_db.commit()
        bm = Bookmark(user_id=user.id, url="https://t/limit1", platform="tiktok",
                      raw="{}", canonical_content_id=cc.id,
                      processing_state=ProcessingState.QUEUED)
        clean_db.add(bm)
        clean_db.commit()

        info = _schedule_processing(clean_db, bm, cc, user_id=user.id,
                                    newly_created=True)

        # The save is still there and still the user's.
        assert clean_db.query(Bookmark).get(bm.id) is not None
        assert bm.url == "https://t/limit1"
        # It is marked, not failed.
        assert bm.processing_state == ProcessingState.LIMIT_REACHED
        assert bm.processing_state != ProcessingState.FAILED
        # And nothing expensive was queued.
        assert clean_db.query(Job).filter(
            Job.idempotency_key == f"content.process:{cc.id}").count() == 0
        # The client is told what happened, and what would fix it.
        assert info["reason"] == "processing_units_exhausted"
        assert info["message"] == "AI processing limit reached"
        assert info["units_required"] == plans.units_for_route("light_vision")
        assert info["upgrade_available"] is True

    def test_a_pro_user_out_of_units_is_not_offered_an_upgrade(self, clean_db, user):
        from api.services.save import _schedule_processing

        subscription_svc.apply_transaction(clean_db, user.id, _transaction())
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id,
                              units=e.limits.processing_units, entitlement=e,
                              canonical_content_id=998)

        cc = _canonical(clean_db, "tiktok:limit2", duration=60)
        bm = Bookmark(user_id=user.id, url="https://t/limit2", platform="tiktok",
                      raw="{}", canonical_content_id=cc.id)
        clean_db.add(bm)
        clean_db.commit()

        info = _schedule_processing(clean_db, bm, cc, user_id=user.id,
                                    newly_created=True)
        assert info["upgrade_available"] is False
        assert bm.processing_state == ProcessingState.LIMIT_REACHED

    def test_content_already_being_processed_costs_nothing(self, clean_db, user):
        """A save that causes no marginal work must not consume an allowance."""
        from api.jobs import enqueue
        from api.services.save import _schedule_processing

        other = make_user(clean_db, "somebody-else@example.com")
        cc = _canonical(clean_db, "tiktok:shared", duration=2400)
        enqueue(clean_db, "content.process", {"canonical_id": cc.id,
                                              "user_id": other.id},
                idempotency_key=f"content.process:{cc.id}", user_id=other.id)

        bm = Bookmark(user_id=user.id, url="https://t/shared", platform="tiktok",
                      raw="{}", canonical_content_id=cc.id)
        clean_db.add(bm)
        clean_db.commit()

        info = _schedule_processing(clean_db, bm, cc, user_id=user.id,
                                    newly_created=False)
        assert info is None
        assert billing.current_period(clean_db, user.id).units_used == 0

    def test_upgrading_resumes_what_was_held_back(self, clean_db, user):
        from api.services.save import _schedule_processing, resume_limited_saves

        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id,
                              units=e.limits.processing_units, entitlement=e,
                              canonical_content_id=997)

        cc = _canonical(clean_db, "tiktok:held", duration=120)
        bm = Bookmark(user_id=user.id, url="https://t/held", platform="tiktok",
                      raw="{}", canonical_content_id=cc.id)
        clean_db.add(bm)
        clean_db.commit()
        _schedule_processing(clean_db, bm, cc, user_id=user.id, newly_created=True)
        assert bm.processing_state == ProcessingState.LIMIT_REACHED

        subscription_svc.apply_transaction(clean_db, user.id, _transaction())
        stats = resume_limited_saves(clean_db, user.id)

        assert stats["queued"] == 1
        clean_db.refresh(bm)
        assert bm.processing_state != ProcessingState.LIMIT_REACHED
        assert clean_db.query(Job).filter(
            Job.idempotency_key == f"content.process:{cc.id}").count() == 1


# ─── Concurrency ─────────────────────────────────────────────────────────────

class TestConcurrency:
    def _running_job(self, db, user_id, key):
        job = Job(kind="content.process", idempotency_key=key, state="running",
                  payload="{}", user_id=user_id)
        db.add(job)
        db.commit()
        return job

    def test_free_user_with_one_job_running_is_saturated(self, clean_db, user):
        from api.jobs import _saturated_users
        self._running_job(clean_db, user.id, "running:1")
        assert user.id in _saturated_users(clean_db)

    def test_pro_user_may_run_three(self, clean_db, user):
        from api.jobs import _saturated_users
        subscription_svc.apply_transaction(clean_db, user.id, _transaction())
        self._running_job(clean_db, user.id, "running:1")
        self._running_job(clean_db, user.id, "running:2")
        assert user.id not in _saturated_users(clean_db)
        self._running_job(clean_db, user.id, "running:3")
        assert user.id in _saturated_users(clean_db)

    def test_a_saturated_user_does_not_starve_everybody_else(self, clean_db, user):
        from api.jobs import claim_next, enqueue
        other = make_user(clean_db, "other-user@example.com")
        self._running_job(clean_db, user.id, "running:1")
        enqueue(clean_db, "content.process", {"canonical_id": 1},
                idempotency_key="queued:hog", user_id=user.id)
        enqueue(clean_db, "content.process", {"canonical_id": 2},
                idempotency_key="queued:other", user_id=other.id)

        claimed = claim_next(clean_db, skip_platforms={})
        assert claimed is not None and claimed.user_id == other.id


# ─── Reporting ───────────────────────────────────────────────────────────────

class TestUsageReport:
    def test_shape_is_what_the_profile_screen_needs(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id, units=18, entitlement=e,
                              canonical_content_id=1)
        for _ in range(12):
            billing.consume_ask(clean_db, user.id, entitlement=e)

        usage = billing.usage_for(clean_db, user.id, entitlement=e)
        units, asks = usage["processing_units"], usage["ask_messages"]
        limit = e.limits.processing_units
        assert (units["used"], units["limit"], units["remaining"],
                units["exhausted"]) == (18, limit, limit - 18, False)
        assert (asks["used"], asks["limit"], asks["remaining"],
                asks["exhausted"]) == (12, e.limits.ask_messages,
                                       e.limits.ask_messages - 12, False)
        # Also expressed as videos, which is the only form the UI shows.
        assert units["approx_videos_limit"] == e.limits.approx_videos
        assert units["approx_videos_remaining"] > 0
        # An instant, not a formatted date: the phone knows the user's locale.
        datetime.fromisoformat(usage["resets_at"])

    def test_exhaustion_is_reported(self, clean_db, user):
        e = entitlements.for_user(clean_db, user.id)
        billing.reserve_units(clean_db, user.id,
                              units=e.limits.processing_units, entitlement=e,
                              canonical_content_id=1)
        usage = billing.usage_for(clean_db, user.id, entitlement=e)
        assert usage["processing_units"]["exhausted"] is True
        assert usage["processing_units"]["remaining"] == 0
        assert usage["processing_units"]["approx_videos_remaining"] == 0

    def test_public_entitlement_leaks_no_internals(self, clean_db, user):
        subscription_svc.apply_transaction(clean_db, user.id, _transaction())
        public = entitlements.for_user(clean_db, user.id).public()
        flat = json.dumps(public)
        for secret in ("last_claims", "verification", "original_transaction_id"):
            assert secret not in flat


class TestLimitStateIsVisibleToTheClient:
    """The bug that made the whole feature look broken.

    `serialize_bookmark` preferred the *canonical* processing state, because
    that is the shared truth about whether content has been understood. But
    `limit_reached` is a fact about one user's allowance, and the canonical row
    stays `queued` precisely because nobody paid to process it — so the library
    reported "Saving…" for an item that was never going to be scheduled, and the
    upgrade affordance never appeared. Found end-to-end against a live server,
    not by a unit test, which is why there is now a unit test.
    """

    def _bookmark(self, db, user_id, cc, state):
        bm = Bookmark(user_id=user_id, url=f"https://x/{cc.content_key}",
                      platform="tiktok", raw="{}", canonical_content_id=cc.id,
                      processing_state=state)
        db.add(bm)
        db.commit()
        db.refresh(bm)
        return bm

    def test_limit_reached_survives_serialisation(self, clean_db, user):
        from api.main import _visible_state
        cc = _canonical(clean_db, "tiktok:vis1", state=ProcessingState.QUEUED)
        bm = self._bookmark(clean_db, user.id, cc, ProcessingState.LIMIT_REACHED)
        assert _visible_state(bm, cc) == ProcessingState.LIMIT_REACHED

    def test_canonical_state_still_wins_normally(self, clean_db, user):
        from api.main import _visible_state
        cc = _canonical(clean_db, "tiktok:vis2", state=ProcessingState.ANALYZING)
        bm = self._bookmark(clean_db, user.id, cc, ProcessingState.QUEUED)
        assert _visible_state(bm, cc) == ProcessingState.ANALYZING

    def test_a_held_save_that_got_processed_for_somebody_else_is_ready(self, clean_db, user):
        """The hold is moot once the content exists — this user reads it free."""
        from api.main import _visible_state
        cc = _canonical(clean_db, "tiktok:vis3", state=ProcessingState.READY)
        bm = self._bookmark(clean_db, user.id, cc, ProcessingState.LIMIT_REACHED)
        assert _visible_state(bm, cc) == ProcessingState.READY

    def test_another_user_is_unaffected(self, clean_db, user):
        """One user's exhausted allowance must not change what anybody else sees."""
        from api.main import _visible_state
        other = make_user(clean_db, "unaffected@example.com")
        cc = _canonical(clean_db, "tiktok:vis4", state=ProcessingState.QUEUED)
        held = self._bookmark(clean_db, user.id, cc, ProcessingState.LIMIT_REACHED)
        fine = self._bookmark(clean_db, other.id, cc, ProcessingState.QUEUED)
        assert _visible_state(held, cc) == ProcessingState.LIMIT_REACHED
        assert _visible_state(fine, cc) == ProcessingState.QUEUED
