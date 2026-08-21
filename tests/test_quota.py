"""Per-user cost limits.

Sava metered spend but never read the meter. `api/platform_budget.py` protects
TikTok and YouTube from Sava; nothing protected Sava's bill from one account in a
loop. Every save runs extraction, understanding and embeddings, so an unbounded
save loop is an unbounded invoice — a denial-of-wallet, not a denial-of-service.

These tests hold two lines at once: a runaway user is stopped, and an ordinary
user never notices the ceiling exists.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api import quota
from api.auth import create_access_token, get_password_hash
from api.db import SessionLocal
from api.main import app
from api.models import UsageEvent, User

_seq = itertools.count()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def user(db):
    u = User(email=f"quota{next(_seq)}@example.com",
             password_hash=get_password_hash("Correct-Horse-9!"))
    db.add(u)
    db.commit()
    db.refresh(u)
    return {"id": u.id, "email": u.email,
            "token": create_access_token({"sub": u.email})}


def _spend(db, user_id: int, operation: str, *, n: int = 1,
           usd: float = 0.0, age_hours: float = 0.0) -> None:
    for _ in range(n):
        db.add(UsageEvent(
            user_id=user_id, operation=operation, estimated_usd=usd,
            created_at=datetime.now(timezone.utc) - timedelta(hours=age_hours)))
    db.commit()


class TestRunawayUsageIsStopped:
    def test_a_save_loop_is_capped(self, db, user):
        _spend(db, user["id"], "understanding", n=quota.SAVES_PER_DAY)
        with pytest.raises(quota.QuotaExceeded):
            quota.check(db, user["id"], "save")

    def test_ask_spam_is_capped(self, db, user):
        _spend(db, user["id"], "ask", n=quota.ASKS_PER_DAY)
        with pytest.raises(quota.QuotaExceeded):
            quota.check(db, user["id"], "ask")

    def test_forced_reprocessing_is_capped_hardest(self, db, user):
        """It re-runs the whole pipeline on already-understood content."""
        assert quota.REPROCESS_PER_DAY < quota.SAVES_PER_DAY
        _spend(db, user["id"], "reprocess", n=quota.REPROCESS_PER_DAY)
        with pytest.raises(quota.QuotaExceeded):
            quota.check(db, user["id"], "reprocess")

    def test_monthly_spend_is_a_backstop_across_all_operations(self, db, user):
        """Catches an expensive pattern that stays under every count limit."""
        _spend(db, user["id"], "vision", n=3, usd=quota.MONTHLY_USD / 2)
        with pytest.raises(quota.QuotaExceeded):
            quota.check(db, user["id"], "save")

    def test_the_error_is_a_429_with_a_retry_after(self, db, user):
        _spend(db, user["id"], "ask", n=quota.ASKS_PER_DAY)
        with pytest.raises(quota.QuotaExceeded) as e:
            quota.check(db, user["id"], "ask")
        assert e.value.status_code == 429
        assert int(e.value.headers["Retry-After"]) > 0

    def test_the_message_is_for_a_person_not_an_operator(self, db, user):
        _spend(db, user["id"], "understanding", n=quota.SAVES_PER_DAY)
        with pytest.raises(quota.QuotaExceeded) as e:
            quota.check(db, user["id"], "save")
        detail = e.value.detail.lower()
        assert "limit" in detail
        # No internals: no cost, no model name, no operator budget.
        for leak in ("usd", "$", "gemini", "token", "quota exceeded for user"):
            assert leak not in detail


class TestNormalUsageIsUnaffected:
    def test_a_fresh_user_is_never_blocked(self, db, user):
        for kind in ("save", "ask", "reprocess"):
            quota.check(db, user["id"], kind)

    def test_a_heavy_but_reasonable_day_passes(self, db, user):
        """Twenty saves and twenty questions is a very active day."""
        _spend(db, user["id"], "understanding", n=20)
        _spend(db, user["id"], "ask", n=20)
        quota.check(db, user["id"], "save")
        quota.check(db, user["id"], "ask")

    def test_the_window_rolls_so_yesterday_does_not_count(self, db, user):
        _spend(db, user["id"], "understanding",
               n=quota.SAVES_PER_DAY + 10, age_hours=30)
        quota.check(db, user["id"], "save")  # must not raise

    def test_one_users_spending_never_limits_another(self, db, user):
        other = User(email=f"quota-other{next(_seq)}@example.com",
                     password_hash=get_password_hash("x"))
        db.add(other)
        db.commit()
        db.refresh(other)
        _spend(db, other.id, "understanding", n=quota.SAVES_PER_DAY * 2)
        quota.check(db, user["id"], "save")  # must not raise


class TestOperationMapping:
    def test_every_limit_has_operations_behind_it(self):
        assert set(quota.LIMITS) == set(quota._OPERATIONS)

    def test_an_unknown_operation_kind_is_ignored_not_crashed(self, db, user):
        quota.check(db, user["id"], "not-a-real-kind")

    def test_limits_can_be_disabled_for_a_deployment(self, db, user, monkeypatch):
        _spend(db, user["id"], "ask", n=quota.ASKS_PER_DAY * 2)
        monkeypatch.setattr(quota, "ENABLED", False)
        quota.check(db, user["id"], "ask")  # must not raise


class TestVisibility:
    def test_a_user_can_see_their_own_usage(self, client, db, user):
        _spend(db, user["id"], "ask", n=3)
        r = client.get("/api/me/usage",
                       headers={"Authorization": f"Bearer {user['token']}"})
        assert r.status_code == 200
        body = r.json()
        assert body["limits"]["ask"]["used"] >= 3
        assert body["limits"]["ask"]["limit"] == quota.ASKS_PER_DAY

    def test_usage_requires_authentication(self, client):
        assert client.get("/api/me/usage").status_code in (401, 403)

    def test_usage_reports_only_the_callers_own_numbers(self, client, db, user):
        other = User(email=f"quota-vis{next(_seq)}@example.com",
                     password_hash=get_password_hash("x"))
        db.add(other)
        db.commit()
        db.refresh(other)
        _spend(db, other.id, "ask", n=50)
        r = client.get("/api/me/usage",
                       headers={"Authorization": f"Bearer {user['token']}"})
        assert r.json()["limits"]["ask"]["used"] == 0


class TestDedupStillSavesMoney:
    def test_canonical_content_is_keyed_for_sharing(self):
        """Two users saving one video must not be understood twice.

        Enforced at the schema level by the unique `content_key`; this asserts
        the property has not been dropped, because quota ceilings only work if
        shared work stays shared.
        """
        from api.models import CanonicalContent
        constraint_cols = {c.name for c in CanonicalContent.__table__.columns
                           if c.unique}
        assert "content_key" in constraint_cols
