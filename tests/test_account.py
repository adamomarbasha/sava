"""Account deletion and export.

Required by App Store Guideline 5.1.1(v), and genuinely subtle here because Sava
deduplicates. Two users who save the same TikTok share one `canonical_content`
row and one expensive set of derived rows — understanding, transcript,
embeddings. A naive deletion reaches through that shared row and destroys the
other user's library.

`test_deleting_one_user_leaves_another_users_shared_content_intact` is the test
this whole module exists for.
"""
from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

from api.auth import create_access_token, get_password_hash
from api.db import SessionLocal
from api.main import app
from api.models import (
    Bookmark, CanonicalContent, ChatMessage, ChatThread, Collection,
    CollectionItem, ContentEmbedding, ContentUnderstanding, UsageEvent, User,
)
from api.services import account as account_svc

_seq = itertools.count()
PASSWORD = "Correct-Horse-9!"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _user(db) -> User:
    u = User(email=f"acct{next(_seq)}@example.com",
             password_hash=get_password_hash(PASSWORD))
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _shared_content(db, key: str) -> CanonicalContent:
    """One canonical row plus the derived rows two users would share."""
    cc = CanonicalContent(content_key=key, platform="tiktok",
                          canonical_url=f"https://tiktok.com/{key}",
                          media_kind="video")
    db.add(cc)
    db.flush()
    db.add(ContentUnderstanding(canonical_content_id=cc.id, tl_dr="shared summary"))
    db.add(ContentEmbedding(canonical_content_id=cc.id))
    db.commit()
    db.refresh(cc)
    return cc


def _save(db, user: User, cc: CanonicalContent | None = None) -> Bookmark:
    bm = Bookmark(user_id=user.id, platform="tiktok",
                  url=f"https://tiktok.com/v/{next(_seq)}", title="t",
                  canonical_content_id=cc.id if cc else None, raw="{}")
    db.add(bm)
    db.commit()
    db.refresh(bm)
    return bm


class TestSharedContentBoundary:
    def test_deleting_one_user_leaves_another_users_shared_content_intact(self, db):
        """The test this module exists for."""
        alice, bob = _user(db), _user(db)
        shared = _shared_content(db, f"tiktok:shared-{next(_seq)}")
        _save(db, alice, shared)
        bob_save = _save(db, bob, shared)

        account_svc.delete_account(db, alice.id)

        assert db.query(CanonicalContent).filter(
            CanonicalContent.id == shared.id).first() is not None, \
            "shared content was destroyed while another user still had it saved"
        assert db.query(ContentUnderstanding).filter(
            ContentUnderstanding.canonical_content_id == shared.id).first() is not None
        assert db.query(ContentEmbedding).filter(
            ContentEmbedding.canonical_content_id == shared.id).first() is not None
        assert db.query(Bookmark).filter(Bookmark.id == bob_save.id).first() is not None

    def test_content_nobody_else_holds_is_removed(self, db):
        """The other half: deletion must not leave orphaned strangers' content."""
        solo = _user(db)
        only = _shared_content(db, f"tiktok:solo-{next(_seq)}")
        _save(db, solo, only)
        # Ids captured before deletion: touching an attribute on a deleted ORM
        # instance makes the session try to refresh a row that is gone.
        solo_id, only_id = solo.id, only.id

        report = account_svc.delete_account(db, solo_id)

        assert db.query(CanonicalContent).filter(
            CanonicalContent.id == only_id).first() is None
        assert db.query(ContentUnderstanding).filter(
            ContentUnderstanding.canonical_content_id == only_id).first() is None
        assert report.canonical_deleted == 1

    def test_the_report_distinguishes_removed_from_retained(self, db):
        alice, bob = _user(db), _user(db)
        shared = _shared_content(db, f"tiktok:mix-a-{next(_seq)}")
        solo = _shared_content(db, f"tiktok:mix-b-{next(_seq)}")
        _save(db, alice, shared)
        _save(db, bob, shared)
        _save(db, alice, solo)

        report = account_svc.delete_account(db, alice.id)
        assert report.canonical_retained == 1
        assert report.canonical_deleted == 1


class TestPrivateDataIsFullyRemoved:
    def test_every_user_owned_row_goes(self, db):
        user = _user(db)
        bm = _save(db, user)

        collection = Collection(user_id=user.id, name="Mine")
        db.add(collection)
        db.flush()
        db.add(CollectionItem(collection_id=collection.id, bookmark_id=bm.id))

        thread = ChatThread(user_id=user.id, scope="library")
        db.add(thread)
        db.flush()
        db.add(ChatMessage(thread_id=thread.id, role="user", content="private question"))
        db.add(UsageEvent(user_id=user.id, operation="ask", estimated_usd=0.01))
        db.commit()
        user_id, thread_id, collection_id = user.id, thread.id, collection.id

        account_svc.delete_account(db, user_id)

        assert db.query(User).filter(User.id == user_id).first() is None
        assert db.query(Bookmark).filter(Bookmark.user_id == user_id).count() == 0
        assert db.query(Collection).filter(Collection.user_id == user_id).count() == 0
        assert db.query(ChatThread).filter(ChatThread.user_id == user_id).count() == 0
        assert db.query(ChatMessage).filter(
            ChatMessage.thread_id == thread_id).count() == 0
        assert db.query(CollectionItem).filter(
            CollectionItem.collection_id == collection_id).count() == 0

    def test_usage_attribution_is_removed(self, db):
        """`usage_events` has no foreign key, so nothing else would clear it."""
        user = _user(db)
        db.add(UsageEvent(user_id=user.id, operation="ask", estimated_usd=0.5))
        db.commit()
        user_id = user.id
        account_svc.delete_account(db, user_id)
        assert db.query(UsageEvent).filter(UsageEvent.user_id == user_id).count() == 0

    def test_another_users_collection_link_to_a_deleted_bookmark_is_cleared(self, db):
        """A dangling collection_items row would break the other user's list."""
        alice, bob = _user(db), _user(db)
        alice_save = _save(db, alice)
        bob_collection = Collection(user_id=bob.id, name="Bob's")
        db.add(bob_collection)
        db.flush()
        db.add(CollectionItem(collection_id=bob_collection.id,
                              bookmark_id=alice_save.id))
        db.commit()
        alice_id, save_id, bob_coll_id = alice.id, alice_save.id, bob_collection.id

        account_svc.delete_account(db, alice_id)

        assert db.query(CollectionItem).filter(
            CollectionItem.bookmark_id == save_id).count() == 0
        assert db.query(Collection).filter(
            Collection.id == bob_coll_id).first() is not None


class TestDeletionEndpoint:
    def _auth(self, user):
        return {"Authorization": f"Bearer {create_access_token({'sub': user.email})}"}

    def test_deleting_requires_authentication(self, client):
        assert client.request("DELETE", "/api/account",
                              json={"password": PASSWORD}).status_code in (401, 403)

    def test_the_wrong_password_is_refused(self, client, db):
        user = _user(db)
        r = client.request("DELETE", "/api/account", headers=self._auth(user),
                           json={"password": "not-my-password"})
        assert r.status_code == 401
        assert db.query(User).filter(User.id == user.id).first() is not None

    def test_the_correct_password_deletes(self, client, db):
        user = _user(db)
        user_id = user.id
        r = client.request("DELETE", "/api/account", headers=self._auth(user),
                           json={"password": PASSWORD})
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] is True
        db.expire_all()
        assert db.query(User).filter(User.id == user_id).first() is None

    def test_the_token_stops_working_afterwards(self, client, db):
        user = _user(db)
        headers = self._auth(user)
        client.request("DELETE", "/api/account", headers=headers,
                       json={"password": PASSWORD})
        assert client.get("/api/bookmarks", headers=headers).status_code in (401, 403)


class TestExport:
    def _auth(self, user):
        return {"Authorization": f"Bearer {create_access_token({'sub': user.email})}"}

    def test_export_requires_authentication(self, client):
        assert client.get("/api/account/export").status_code in (401, 403)

    def test_export_contains_the_users_own_content(self, client, db):
        user = _user(db)
        _save(db, user)
        r = client.get("/api/account/export", headers=self._auth(user))
        assert r.status_code == 200
        body = r.json()
        assert body["account"]["email"] == user.email
        assert len(body["saves"]) >= 1

    def test_export_never_contains_a_password_hash(self, client, db):
        user = _user(db)
        r = client.get("/api/account/export", headers=self._auth(user))
        assert "password" not in r.text.lower()
        assert "$2b$" not in r.text

    def test_export_contains_no_other_users_data(self, client, db):
        alice, bob = _user(db), _user(db)
        _save(db, bob)
        r = client.get("/api/account/export", headers=self._auth(alice))
        assert bob.email not in r.text
