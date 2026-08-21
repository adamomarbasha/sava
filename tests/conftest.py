"""Test harness.

Every test runs against a throwaway SQLite file, never the developer's real
database. The env var is set before any `api.*` import so `api.config` picks it
up at module load.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# The whole suite can run against either database.
#
# SQLite by default, because it needs no service and keeps `pytest` a one-word
# command. But "the tests pass" previously meant "the tests pass on SQLite",
# while production is Postgres — a suite that is green about the wrong database
# is green about the wrong thing. Setting SAVA_TEST_DATABASE_URL runs every test,
# not just tests/test_postgres.py, against a real server. CI does exactly that.
_OVERRIDE = os.getenv("SAVA_TEST_DATABASE_URL")
if _OVERRIDE:
    os.environ["DATABASE_URL"] = _OVERRIDE
else:
    _TMP_DB = Path(tempfile.mkdtemp(prefix="sava_test_")) / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"

# Tests are a test environment, stated explicitly. `ENVIRONMENT` now defaults to
# production, so leaving it unset would make every test run assert itself into
# the production configuration gate.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ["SAVA_INLINE_JOBS"] = "0"
os.environ.setdefault("SAVA_TIKTOK_VISION_MODE", "conditional")

import pytest  # noqa: E402

from api.db import SessionLocal, engine, ensure_extensions  # noqa: E402
from api.migrations import run_migrations  # noqa: E402
from api.models import Base, Bookmark, User  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    # Same ordering the application uses: the embedding column is of type
    # `vector`, so the extension has to exist before any table is created.
    ensure_extensions(engine)
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def clean_db(db):
    """Truncate everything between tests that assert on global counts."""
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    yield db


def make_user(db, email: str) -> User:
    u = db.query(User).filter(User.email == email).first()
    if u:
        return u
    u = User(email=email, password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def make_bookmark(db, user_id: int, url: str, **kw) -> Bookmark:
    bm = Bookmark(
        user_id=user_id, url=url,
        platform=kw.pop("platform", "youtube"),
        title=kw.pop("title", None), author=kw.pop("author", None),
        description=kw.pop("description", None), note=kw.pop("note", None),
        raw="{}", **kw,
    )
    db.add(bm)
    db.commit()
    db.refresh(bm)
    return bm


class FakeCompletion:
    def __init__(self, text, model="fake-model"):
        self.text = text
        self.provider = "fake"
        self.model = model
        self.input_tokens = 100
        self.output_tokens = 50
        self.wall_ms = 5
        self.estimated_usd = 0.0001


class FakeEmbedResult:
    def __init__(self, vectors, dim):
        self.vectors = vectors
        self.provider = "fake"
        self.model = "fake-embed"
        self.input_tokens = 10
        self.wall_ms = 1
        self.dim = dim
        self._usd = 0.0


class FakeRouter:
    """Deterministic stand-in for the model router.

    Embeddings are hashed bag-of-words projections: same text -> same vector,
    similar text -> similar vector. Enough to exercise real retrieval maths
    without spending tokens or depending on the network.
    """

    def __init__(self, dim=1536, completion_text='{"tl_dr":"t","key_points":[],"topics":[]}'):
        self.dim = dim
        self.completion_text = completion_text
        self.embed_calls = 0
        self.complete_calls = 0

    def is_available(self):
        return True

    def embed(self, texts, task_type="retrieval_document", dim=None):
        import numpy as np
        self.embed_calls += 1
        vecs = []
        for t in texts:
            v = np.zeros(self.dim, dtype=np.float32)
            for word in str(t).lower().split():
                h = hash(word) % self.dim
                v[h] += 1.0
            n = np.linalg.norm(v)
            vecs.append((v / n if n else v).tolist())
        return FakeEmbedResult(vecs, self.dim)

    def complete(self, task, **kw):
        self.complete_calls += 1
        return FakeCompletion(self.completion_text)

    def spec_for(self, task, mode=None):
        from api.ai.router import CHEAP
        return CHEAP


def install_fake_router(monkeypatch, fake):
    """Patch EVERY reference to the router.

    `api.services.intelligence` imports `get_router` at module load, so patching
    only `api.ai.router.get_router` leaves that binding pointing at the real
    provider — tests would silently hit the live API and any "did we call a
    model?" assertion would be vacuously true.
    """
    from api.ai import router as router_mod
    from api.services import collections as coll_mod
    from api.services import intelligence as intel_mod
    from api.services import retrieval as retr_mod

    monkeypatch.setattr(router_mod, "get_router", lambda: fake)
    monkeypatch.setattr(intel_mod, "get_router", lambda: fake)
    for mod in (coll_mod, retr_mod):
        if hasattr(mod, "get_router"):
            monkeypatch.setattr(mod, "get_router", lambda: fake)
    monkeypatch.setattr(retr_mod, "_embed_query",
                        lambda q: fake.embed([q]).vectors[0])
    return fake
