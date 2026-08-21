"""Security regression tests.

Every test here corresponds to a vulnerability that was live in this repository.
They exist so that a future refactor that quietly drops an ownership check or an
auth dependency fails the build instead of shipping.

Grouped by the class of bug rather than by route, because the classes are what
recur: unauthenticated data exposure, missing ownership checks (IDOR), account
enumeration, and unthrottled authentication.
"""
from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

from api import auth_guard
from api.auth import create_access_token, get_password_hash
from api.db import SessionLocal
from api.main import app
from api.models import Bookmark, User

_seq = itertools.count()


@pytest.fixture(autouse=True)
def _reset_limiters():
    """The limiters are module-level singletons shared across tests."""
    auth_guard.reset_all()
    yield
    auth_guard.reset_all()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def _user(db, password="Correct-Horse-9!") -> dict:
    email = f"sec{next(_seq)}@example.com"
    u = User(email=email, password_hash=get_password_hash(password))
    db.add(u)
    db.commit()
    db.refresh(u)
    return {"id": u.id, "email": email, "password": password,
            "token": create_access_token({"sub": email})}


def _bookmark(db, user_id: int) -> Bookmark:
    n = next(_seq)
    bm = Bookmark(user_id=user_id, platform="youtube",
                  url=f"https://example.com/sec/{n}", title="t")
    db.add(bm)
    db.commit()
    db.refresh(bm)
    return bm


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


# ─── Unauthenticated data exposure ───────────────────────────────────────────

class TestNoPublicUserList:
    def test_users_endpoint_is_gone(self, client):
        """`GET /users` returned every account's email to anonymous callers."""
        assert client.get("/users").status_code == 404

    def test_no_route_advertises_a_user_listing(self):
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        for path in paths:
            assert path.rstrip("/") != "/users"
            assert not path.startswith("/admin/users")

    def test_debug_test_endpoint_is_gone(self, client):
        assert client.get("/test/instagram-thumbnail?url=x").status_code == 404

    def test_openapi_exposes_no_unauthenticated_user_data_route(self, client):
        """A cheap net for the whole class, not just the one route we removed."""
        spec = client.get("/openapi.json")
        if spec.status_code != 200:
            pytest.skip("openapi disabled in this environment")
        for path, methods in spec.json().get("paths", {}).items():
            if path.rstrip("/") in ("/users", "/api/users"):
                pytest.fail(f"{path} is exposed")


# ─── Ownership / IDOR ────────────────────────────────────────────────────────

class TestCommentsOwnership:
    """All three comments routes were unauthenticated; two took a raw id."""

    def test_reading_comments_requires_authentication(self, client, db):
        owner = _user(db)
        bm = _bookmark(db, owner["id"])
        assert client.get(f"/api/comments/{bm.id}").status_code in (401, 403)

    def test_reading_another_users_comments_is_refused(self, client, db):
        owner, attacker = _user(db), _user(db)
        bm = _bookmark(db, owner["id"])
        r = client.get(f"/api/comments/{bm.id}", headers=_auth(attacker))
        assert r.status_code == 404, r.text

    def test_the_owner_can_still_read_their_own_comments(self, client, db):
        owner = _user(db)
        bm = _bookmark(db, owner["id"])
        r = client.get(f"/api/comments/{bm.id}", headers=_auth(owner))
        assert r.status_code == 200, r.text

    def test_saving_comments_requires_authentication(self, client, db):
        owner = _user(db)
        bm = _bookmark(db, owner["id"])
        r = client.post(f"/api/comments/save/{bm.id}?video_url_or_id=abc")
        assert r.status_code in (401, 403)

    def test_writing_to_another_users_bookmark_is_refused(self, client, db):
        """The worst of the three: an unauthenticated cross-user write."""
        owner, attacker = _user(db), _user(db)
        bm = _bookmark(db, owner["id"])
        r = client.post(f"/api/comments/save/{bm.id}?video_url_or_id=abc",
                        headers=_auth(attacker))
        assert r.status_code == 404, r.text

    def test_fetching_comments_by_url_requires_authentication(self, client):
        """Spends Sava's YouTube quota — never anonymous."""
        r = client.post("/api/comments", json={"video_url_or_id": "dQw4w9WgXcQ"})
        assert r.status_code in (401, 403)


class TestTranscriptEndpointsRequireAuth:
    """Five routes that ran a real extraction for anonymous callers."""

    @pytest.mark.parametrize("method,path", [
        ("get", "/api/transcript?video_url_or_id=abc"),
        ("post", "/api/transcript"),
        ("get", "/api/transcript/languages?video_url_or_id=abc"),
        ("get", "/api/transcript/status"),
        ("get", "/api/transcript/abc123"),
    ])
    def test_anonymous_is_refused(self, client, method, path):
        r = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
        assert r.status_code in (401, 403, 422), f"{path} -> {r.status_code}"


class TestOpsEndpointsAreAdminOnly:
    """Installation-wide telemetry and platform-wide backfills."""

    @pytest.mark.parametrize("method,path", [
        ("get", "/api/ops/usage?mine=false"),
        ("get", "/api/ops/queue"),
        ("get", "/api/ops/economics"),
        ("get", "/api/ops/latency"),
        ("get", "/api/ops/platforms"),
        ("get", "/api/ops/extraction"),
        ("post", "/api/ops/upgrade-pipeline"),
        ("post", "/api/ops/backfill-canonical"),
        ("post", "/api/ops/backfill-thumbnails"),
    ])
    def test_an_ordinary_user_cannot_reach_them(self, client, db, method, path):
        user = _user(db)
        r = getattr(client, method)(path, headers=_auth(user))
        assert r.status_code == 404, f"{path} -> {r.status_code}"

    def test_anonymous_cannot_reach_them(self, client):
        assert client.get("/api/ops/economics").status_code in (401, 403, 404)

    def test_admin_allowlist_is_closed_by_default(self, monkeypatch):
        from api import authz
        monkeypatch.delenv("SAVA_ADMIN_EMAILS", raising=False)
        assert authz.admin_emails() == set()
        assert authz.is_admin("anyone@example.com") is False

    def test_admin_allowlist_grants_only_listed_addresses(self, monkeypatch):
        from api import authz
        monkeypatch.setenv("SAVA_ADMIN_EMAILS", "boss@example.com, Ops@Example.com")
        assert authz.is_admin("boss@example.com")
        assert authz.is_admin("OPS@example.com"), "must be case-insensitive"
        assert not authz.is_admin("someone@example.com")


class TestBookmarkOwnership:
    def test_cross_user_bookmark_read_is_refused(self, client, db):
        owner, attacker = _user(db), _user(db)
        bm = _bookmark(db, owner["id"])
        r = client.get(f"/api/bookmarks/{bm.id}", headers=_auth(attacker))
        assert r.status_code == 404

    def test_cross_user_bookmark_delete_is_refused(self, client, db):
        owner, attacker = _user(db), _user(db)
        bm = _bookmark(db, owner["id"])
        r = client.delete(f"/api/bookmarks/{bm.id}", headers=_auth(attacker))
        assert r.status_code == 404
        assert db.query(Bookmark).filter(Bookmark.id == bm.id).first() is not None

    def test_owner_access_still_works(self, client, db):
        owner = _user(db)
        bm = _bookmark(db, owner["id"])
        assert client.get(f"/api/bookmarks/{bm.id}",
                          headers=_auth(owner)).status_code == 200

    def test_protected_routes_reject_a_missing_token(self, client):
        assert client.get("/api/bookmarks").status_code in (401, 403)

    def test_protected_routes_reject_a_forged_token(self, client):
        r = client.get("/api/bookmarks",
                       headers={"Authorization": "Bearer not.a.real.token"})
        assert r.status_code in (401, 403)

    def test_ownership_helper_refuses_another_users_row(self, db):
        from fastapi import HTTPException
        from api.authz import owned_bookmark
        owner, attacker = _user(db), _user(db)
        bm = _bookmark(db, owner["id"])
        with pytest.raises(HTTPException) as e:
            owned_bookmark(db, bm.id, attacker["id"])
        assert e.value.status_code == 404, "403 would confirm the row exists"


# ─── Authentication hardening ────────────────────────────────────────────────

class TestNoAccountEnumeration:
    def test_unknown_and_wrong_password_are_indistinguishable(self, client, db):
        user = _user(db)
        unknown = client.post("/auth/login", json={
            "email": "definitely-not-registered@example.com", "password": "whatever"})
        wrong = client.post("/auth/login", json={
            "email": user["email"], "password": "wrong-password"})

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["detail"] == wrong.json()["detail"]

    def test_the_failure_message_names_neither_field(self, client):
        r = client.post("/auth/login",
                        json={"email": "nobody@example.com", "password": "x"})
        detail = r.json()["detail"].lower()
        assert "not found" not in detail
        assert "incorrect password" not in detail

    def test_a_correct_login_still_succeeds(self, client, db):
        user = _user(db)
        r = client.post("/auth/login",
                        json={"email": user["email"], "password": user["password"]})
        assert r.status_code == 200, r.text
        assert r.json()["access_token"]


class TestAuthRateLimiting:
    def test_repeated_failures_from_one_address_are_throttled(self, client, db):
        user = _user(db)
        codes = []
        for _ in range(auth_guard.IP_MAX_FAILURES + 3):
            codes.append(client.post("/auth/login", json={
                "email": user["email"], "password": "wrong"}).status_code)
        assert 429 in codes, f"never throttled: {codes}"

    def test_a_throttled_response_says_when_to_retry(self, client, db):
        user = _user(db)
        last = None
        for _ in range(auth_guard.IP_MAX_FAILURES + 3):
            last = client.post("/auth/login",
                               json={"email": user["email"], "password": "wrong"})
        assert last.status_code == 429
        assert int(last.headers["Retry-After"]) > 0

    def test_throttling_does_not_reveal_which_limit_tripped(self, client, db):
        """A distinct 'account locked' message would confirm the account exists."""
        user = _user(db)
        for _ in range(auth_guard.IP_MAX_FAILURES + 2):
            r = client.post("/auth/login",
                            json={"email": user["email"], "password": "wrong"})
        assert "account" not in r.json()["detail"].lower()

    def test_a_success_clears_the_account_bucket(self, db):
        """The property that stops this being a lockout weapon."""
        email = "victim@example.com"
        for _ in range(auth_guard.ACCOUNT_MAX_FAILURES):
            auth_guard.record_login_failure(None, email)
        allowed, _ = auth_guard.login_account_limiter.check(
            f"acct:{auth_guard.account_key(email)}")
        assert not allowed

        auth_guard.record_login_success(None, email)
        allowed, _ = auth_guard.login_account_limiter.check(
            f"acct:{auth_guard.account_key(email)}")
        assert allowed, "a correct password must clear a bucket an attacker filled"

    def test_account_limit_is_looser_than_address_limit(self):
        """So an attacker must burn many addresses before affecting a victim."""
        assert auth_guard.ACCOUNT_MAX_FAILURES > auth_guard.IP_MAX_FAILURES

    def test_account_throttling_always_expires(self):
        """No permanent lockout: the window slides and there is no admin unlock."""
        assert auth_guard.ACCOUNT_WINDOW_SECONDS > 0
        assert not hasattr(auth_guard, "lock_account")

    def test_registration_is_throttled_per_address(self, client):
        codes = []
        for i in range(auth_guard.REGISTER_MAX + 3):
            codes.append(client.post("/auth/register", json={
                "email": f"flood{next(_seq)}@example.com",
                "password": "Correct-Horse-9!"}).status_code)
        assert 429 in codes, f"registration never throttled: {codes}"

    def test_a_spoofed_forwarded_header_is_ignored_by_default(self, client, db):
        """Otherwise the per-address limit is defeated by one header."""
        assert auth_guard.TRUST_PROXY is False
        user = _user(db)
        codes = []
        for i in range(auth_guard.IP_MAX_FAILURES + 3):
            codes.append(client.post(
                "/auth/login",
                json={"email": user["email"], "password": "wrong"},
                headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code)
        assert 429 in codes, "rotating X-Forwarded-For bypassed the limit"


class TestPasswordHandling:
    def test_passwords_are_bcrypt_hashed_not_stored(self, db):
        user = _user(db, password="Correct-Horse-9!")
        row = db.query(User).filter(User.id == user["id"]).first()
        assert row.password_hash != "Correct-Horse-9!"
        assert row.password_hash.startswith("$2")

    def test_no_endpoint_returns_a_password_hash(self, client, db):
        user = _user(db)
        r = client.get("/auth/me", headers=_auth(user))
        assert r.status_code == 200
        assert "password" not in str(r.json()).lower()

    def test_registration_response_carries_no_credential(self, client):
        r = client.post("/auth/register", json={
            "email": f"reg{next(_seq)}@example.com", "password": "Correct-Horse-9!"})
        if r.status_code == 200:
            assert "password" not in str(r.json()).lower()
