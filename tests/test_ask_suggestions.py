"""Ask's opening questions.

The property that matters is not "returns four strings" — it is that a
suggestion is never offered unless the library can answer it. A suggestion is a
promise, and the failure mode being guarded against is a confident question that
retrieves nothing.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from api.models import (
    Bookmark, CanonicalContent, Collection, CollectionItem, ContentUnderstanding, User,
)
from api.services import ask_suggestions as svc


@pytest.fixture
def db(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from api.models import Base

    engine = create_engine(f"sqlite:///{tmp_path/'sugg.db'}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def user(db):
    u = User(email="s@example.com", password_hash="x")
    db.add(u)
    db.commit()
    return u


_seq = itertools.count()


def _save(db, user, *, creator=None, content_type=None, topics=None,
          days_ago=0, opens=1, typed=None, key_points=None):
    import json

    # A process-wide counter. Both `content_key` and `(user_id, url)` are
    # unique, so every fixture save needs its own identity.
    n = next(_seq)
    url = f"https://example.com/{n}"
    cc = CanonicalContent(
        content_key=f"youtube:{n}",
        platform="youtube", canonical_url=url, media_kind="video",
        creator_name=creator, content_type=content_type)
    db.add(cc)
    db.flush()

    if topics or content_type or typed or key_points:
        db.add(ContentUnderstanding(
            canonical_content_id=cc.id, content_type=content_type,
            topics=json.dumps(topics or []), typed_data=json.dumps(typed or {}),
            key_points=json.dumps(key_points or [])))

    bm = Bookmark(user_id=user.id, platform="youtube", url=url,
                  title="t", author=creator, canonical_content_id=cc.id,
                  open_count=opens,
                  created_at=datetime.now(timezone.utc) - timedelta(days=days_ago))
    db.add(bm)
    db.commit()
    return bm


def _texts(result):
    return [s["text"] for s in result["suggestions"]]


class TestEvidenceIsRequired:
    def test_empty_library_suggests_nothing(self, db, user):
        """An empty library cannot answer anything, so it promises nothing."""
        assert svc.suggest(db, user_id=user.id, seed=1)["suggestions"] == []

    def test_a_creator_with_one_save_is_not_suggested(self, db, user):
        _save(db, user, creator="Solo Creator")
        result = svc.suggest(db, user_id=user.id, limit=6, seed=1)
        assert not any("Solo Creator" in t for t in _texts(result))

    def test_a_creator_with_enough_saves_is_suggested(self, db, user):
        for _ in range(svc.MIN_CREATOR_SAVES):
            _save(db, user, creator="Repeat Creator")
        result = svc.suggest(db, user_id=user.id, limit=6, seed=1)
        assert any("Repeat Creator" in t for t in _texts(result))

    def test_topics_come_from_understanding_not_a_fixed_list(self, db, user):
        for _ in range(svc.MIN_TOPIC_SAVES):
            _save(db, user, topics=["formula 1"])
        result = svc.suggest(db, user_id=user.id, limit=6, seed=1)
        assert any("formula 1" in t.lower() for t in _texts(result))

    def test_recipe_questions_need_recipes(self, db, user):
        """The old fixed list asked about restaurants regardless of the library."""
        for _ in range(4):
            _save(db, user, content_type="fitness", topics=["training"])
        texts = " ".join(_texts(svc.suggest(db, user_id=user.id, limit=6, seed=1))).lower()
        assert "recipe" not in texts
        assert "restaurant" not in texts

    def test_this_week_only_when_something_landed_this_week(self, db, user):
        for _ in range(6):
            _save(db, user, days_ago=90)
        texts = " ".join(_texts(svc.suggest(db, user_id=user.id, limit=6, seed=3)))
        assert "this week" not in texts.lower()

        for _ in range(3):
            _save(db, user, days_ago=1)
        texts = " ".join(_texts(svc.suggest(db, user_id=user.id, limit=6, seed=3)))
        assert "this week" in texts.lower() or "week in saves" in texts.lower()

    def test_unwatched_needs_unopened_saves(self, db, user):
        for _ in range(8):
            _save(db, user, opens=3)
        texts = " ".join(_texts(svc.suggest(db, user_id=user.id, limit=6, seed=2))).lower()
        assert "never watched" not in texts and "unopened" not in texts


class TestVariety:
    def test_repeated_opens_do_not_repeat_the_same_set(self, db, user):
        """The specific complaint: the same three questions on every open."""
        for i in range(12):
            _save(db, user, creator=f"Creator {i % 4}", topics=[f"topic{i % 5}"],
                  content_type="tutorial", days_ago=i % 3, opens=0)

        sets = {tuple(_texts(svc.suggest(db, user_id=user.id, seed=s))) for s in range(12)}
        assert len(sets) > 6, f"only {len(sets)} distinct openings across 12 seeds"

    def test_one_kind_never_fills_the_whole_list(self, db, user):
        """Ten creators must not produce four creator questions."""
        for i in range(10):
            for _ in range(2):
                _save(db, user, creator=f"Creator {i}", opens=0)

        for seed in range(10):
            result = svc.suggest(db, user_id=user.id, limit=4, seed=seed)
            kinds = [s["kind"] for s in result["suggestions"]]
            assert kinds.count("creator") <= 2, kinds

    def test_no_duplicate_text_within_one_response(self, db, user):
        for i in range(20):
            _save(db, user, creator=f"C{i % 3}", topics=[f"t{i % 3}"],
                  content_type="recipe", opens=0)
        for seed in range(15):
            texts = _texts(svc.suggest(db, user_id=user.id, limit=6, seed=seed))
            assert len(texts) == len(set(texts))

    def test_a_seed_is_reproducible(self, db, user):
        for i in range(10):
            _save(db, user, creator=f"C{i % 3}", topics=["x"], opens=0)
        assert (_texts(svc.suggest(db, user_id=user.id, seed=7))
                == _texts(svc.suggest(db, user_id=user.id, seed=7)))


class TestScopes:
    def test_collection_scope_names_the_collection(self, db, user):
        coll = Collection(user_id=user.id, name="Formula 1")
        db.add(coll)
        db.flush()
        for _ in range(3):
            bm = _save(db, user, creator="Aston", topics=["racing"])
            db.add(CollectionItem(collection_id=coll.id, bookmark_id=bm.id))
        db.commit()

        result = svc.suggest(db, user_id=user.id, scope="collection",
                             collection_id=coll.id, limit=6, seed=1)
        assert any("Formula 1" in t for t in _texts(result))

    def test_collection_scope_draws_only_on_that_collection(self, db, user):
        coll = Collection(user_id=user.id, name="Cooking")
        db.add(coll)
        db.flush()
        for _ in range(3):
            bm = _save(db, user, creator="In Collection", topics=["baking"])
            db.add(CollectionItem(collection_id=coll.id, bookmark_id=bm.id))
        db.commit()
        # Outside the collection, and more numerous.
        for _ in range(6):
            _save(db, user, creator="Outside Creator", topics=["unrelated"])

        texts = " ".join(_texts(svc.suggest(db, user_id=user.id, scope="collection",
                                           collection_id=coll.id, limit=6, seed=1)))
        assert "Outside Creator" not in texts
        assert "unrelated" not in texts

    def test_save_scope_uses_what_was_extracted(self, db, user):
        bm = _save(db, user, creator="Chef", content_type="recipe",
                   typed={"ingredients": ["flour"], "steps": ["mix"]},
                   key_points=["a point"])
        texts = " ".join(_texts(svc.suggest(db, user_id=user.id, scope="save",
                                            bookmark_id=bm.id, limit=6, seed=1))).lower()
        assert "ingredients" in texts

    def test_save_scope_omits_fields_that_were_not_extracted(self, db, user):
        """No ingredients parsed means no question about ingredients."""
        bm = _save(db, user, content_type="recipe", typed={})
        texts = " ".join(_texts(svc.suggest(db, user_id=user.id, scope="save",
                                            bookmark_id=bm.id, limit=6, seed=1))).lower()
        assert "ingredients" not in texts

    def test_save_scope_always_offers_something(self, db, user):
        """Summarise is answerable from a description alone, so it is the floor."""
        bm = _save(db, user)
        assert svc.suggest(db, user_id=user.id, scope="save",
                           bookmark_id=bm.id, seed=1)["suggestions"]


class TestOwnership:
    def test_another_users_collection_yields_nothing(self, db, user):
        other = User(email="other@example.com", password_hash="x")
        db.add(other)
        db.flush()
        coll = Collection(user_id=other.id, name="Private")
        db.add(coll)
        db.commit()

        result = svc.suggest(db, user_id=user.id, scope="collection",
                             collection_id=coll.id, seed=1)
        assert result["suggestions"] == []

    def test_another_users_bookmark_yields_nothing(self, db, user):
        other = User(email="other2@example.com", password_hash="x")
        db.add(other)
        db.flush()
        theirs = _save(db, other, creator="Theirs", content_type="recipe")

        result = svc.suggest(db, user_id=user.id, scope="save",
                             bookmark_id=theirs.id, seed=1)
        assert result["suggestions"] == []

    def test_library_scope_never_crosses_users(self, db, user):
        other = User(email="other3@example.com", password_hash="x")
        db.add(other)
        db.flush()
        for _ in range(5):
            _save(db, other, creator="Their Creator", topics=["theirs"])
        for _ in range(3):
            _save(db, user, creator="My Creator", topics=["mine"])

        texts = " ".join(_texts(svc.suggest(db, user_id=user.id, limit=6, seed=1)))
        assert "Their Creator" not in texts and "theirs" not in texts


class TestRobustness:
    def test_malformed_understanding_json_does_not_raise(self, db, user):
        bm = _save(db, user, creator="C", topics=["x"])
        und = (db.query(ContentUnderstanding)
               .filter(ContentUnderstanding.canonical_content_id
                       == bm.canonical_content_id).first())
        und.topics = "{not json"
        und.typed_data = "]["
        db.commit()
        svc.suggest(db, user_id=user.id, seed=1)  # must not raise

    def test_limit_is_clamped(self, db, user):
        for i in range(20):
            _save(db, user, creator=f"C{i % 4}", topics=[f"t{i % 4}"], opens=0)
        assert len(svc.suggest(db, user_id=user.id, limit=999, seed=1)["suggestions"]) <= 6
        assert len(svc.suggest(db, user_id=user.id, limit=0, seed=1)["suggestions"]) >= 1

    def test_every_suggestion_carries_a_kind_and_icon(self, db, user):
        for i in range(12):
            _save(db, user, creator=f"C{i % 3}", topics=[f"t{i % 3}"],
                  content_type="tutorial", opens=0)
        for s in svc.suggest(db, user_id=user.id, limit=6, seed=1)["suggestions"]:
            assert s["text"] and s["kind"] and s["icon"]
