"""Health checks.

The question these exist to answer: *if saves stopped processing at 2 AM, would
anything know?* Previously the answer was no — `GET /` returned a hardcoded
string and would have stayed green through a total outage.

The load-bearing case is `test_a_stalled_queue_is_unhealthy`. A dead worker does
not make the API unhealthy in any obvious way: requests still succeed and rows
are still written. Only the age of the oldest queued job moves, and it moves in
every version of that failure.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api import health as health_module
from api.db import SessionLocal
from api.main import app
from api.models import Job

_seq = itertools.count()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.query(Job).filter(Job.kind == "health.probe").delete()
    session.commit()
    session.close()


def _queued_job(db, *, age_seconds: int) -> Job:
    # `idempotency_key` is NOT NULL and uniquely constrained — it is what stops
    # the same save being enqueued twice. Each probe needs its own.
    job = Job(kind="health.probe", state="queued",
              idempotency_key=f"health-probe-{next(_seq)}",
              created_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds))
    db.add(job)
    db.commit()
    return job


class TestLiveness:
    def test_livez_is_shallow_and_always_cheap(self, client):
        """It must not consult the database — see the note in api/health.py."""
        r = client.get("/livez")
        assert r.status_code == 200
        assert r.json() == {"ok": True}


class TestDeepHealth:
    def test_a_healthy_system_reports_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["checks"]["database"]["ok"] is True

    def test_it_reports_the_three_things_that_break(self, client):
        checks = client.get("/health").json()["checks"]
        assert set(checks) == {"database", "storage", "queue"}

    def test_a_stalled_queue_is_unhealthy(self, client, db):
        """The 2 AM case: API fine, worker dead, nothing draining."""
        _queued_job(db, age_seconds=health_module.QUEUE_STALL_SECONDS + 120)
        r = client.get("/health")
        assert r.status_code == 503, "a stalled queue must not report healthy"
        queue = r.json()["checks"]["queue"]
        assert queue["stalled"] is True
        assert queue["oldest_queued_age_seconds"] > health_module.QUEUE_STALL_SECONDS

    def test_a_busy_but_moving_queue_is_healthy(self, client, db):
        """Depth alone is not a fault — a burst of saves is normal."""
        for _ in range(5):
            _queued_job(db, age_seconds=10)
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["checks"]["queue"]["queued"] >= 5
        assert r.json()["checks"]["queue"]["stalled"] is False

    def test_a_database_failure_is_reported_not_raised(self, client, monkeypatch):
        def _broken(_db):
            raise RuntimeError("connection refused to postgres://user:pw@host/db")
        monkeypatch.setattr(health_module, "_check_database",
                            lambda db: {"ok": False, "error": "OperationalError"})
        r = client.get("/health")
        assert r.status_code == 503
        assert r.json()["checks"]["database"]["ok"] is False


class TestHealthLeaksNothing:
    def test_no_secret_or_connection_string_in_the_body(self, client):
        body = client.get("/health").text.lower()
        for forbidden in ("secret", "password", "postgresql://", "sqlite:///",
                          "api_key", "aiza", "bearer"):
            assert forbidden not in body, f"/health leaked {forbidden!r}"

    def test_database_errors_report_a_type_not_a_message(self):
        """Connection error strings carry host, user and sometimes password."""
        class _Boom:
            def execute(self, *a, **k):
                raise RuntimeError("could not connect to postgres://u:hunter2@h/db")
        result = health_module._check_database(_Boom())
        assert result["ok"] is False
        assert "hunter2" not in str(result)
        assert result["error"] == "RuntimeError"


class TestQueueAgeParsing:
    def test_handles_naive_datetimes(self):
        naive = datetime.utcnow() - timedelta(seconds=60)
        age = health_module._age_seconds(naive)
        assert age is not None and 30 < age < 120

    def test_handles_iso_strings(self):
        iso = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
        age = health_module._age_seconds(iso)
        assert age is not None and 20 < age < 90

    def test_handles_none(self):
        assert health_module._age_seconds(None) is None
