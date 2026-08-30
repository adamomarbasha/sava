"""Ask streams, and time-to-first-token stops equalling time-to-answer.

── The old architecture ────────────────────────────────────────────────────

`POST /api/ask` was a synchronous handler: retrieval, then the model, then the
database writes, then one JSON body. Nothing reached the client until the whole
answer existed, so **time-to-first-token was identical to time-to-full-answer**
— several seconds of frozen screen, then a timeout when the model ran long.
Raising the timeout would have made the freeze longer, not shorter.

── The new one ─────────────────────────────────────────────────────────────

Server-Sent Events. `sources` first (retrieval is tens of milliseconds, the
model is seconds), then `token` deltas as Gemini produces them, then `done`.

These tests use a fake provider that yields real chunks, so what is under test
is the plumbing — ordering, delta semantics, persistence, billing and
cancellation — rather than the model.
"""
from __future__ import annotations

import itertools
import json

import pytest
from fastapi.testclient import TestClient

from api import auth_guard
from api.ai.base import CompletionChunk
from api.auth import create_access_token
from api.db import SessionLocal
from api.main import app
from api.models import (Bookmark, CanonicalContent, ChatMessage, ChatThread,
                        ProcessingState)
from api.services import intelligence

from conftest import make_user

_seq = itertools.count()


@pytest.fixture(autouse=True)
def _reset_limiters():
    auth_guard.reset_all()
    yield
    auth_guard.reset_all()


@pytest.fixture
def client():
    return TestClient(app)


def _auth(user):
    # The token subject is the *email*, matching `api.auth`. Using the id here
    # produced a 401 on every request and looked like a routing failure.
    return {"Authorization": f"Bearer {create_access_token({'sub': user.email})}"}


@pytest.fixture
def user(clean_db):
    return make_user(clean_db, f"stream-{next(_seq)}@example.com")


@pytest.fixture
def saved(clean_db, user):
    cc = CanonicalContent(
        content_key=f"tiktok:s{next(_seq)}", platform="tiktok",
        canonical_url="https://x/1", media_kind="video",
        title="Three ingredient pasta", creator_name="cookwithme",
        processing_state=ProcessingState.READY, processing_level=4,
        stage_status="{}", metadata_json="{}")
    clean_db.add(cc); clean_db.commit(); clean_db.refresh(cc)
    bm = Bookmark(user_id=user.id, url="https://x/1", platform="tiktok", raw="{}",
                  canonical_content_id=cc.id, processing_state=ProcessingState.READY)
    clean_db.add(bm); clean_db.commit(); clean_db.refresh(bm)
    return bm


# ─── A provider that really streams ──────────────────────────────────────────

class FakeStreamRouter:
    """Yields deltas, like the real one. Never returns the running total."""

    PIECES = ["Three ", "ingredient ", "pasta, ", "ten minutes."]

    def __init__(self, *, fail=False, pieces=None):
        self.fail = fail
        self.pieces = pieces if pieces is not None else self.PIECES
        self.stream_calls = 0

    def is_available(self):
        return True

    def complete_stream(self, task, **kw):
        self.stream_calls += 1
        if self.fail:
            from api.ai.base import ProviderError
            raise ProviderError("model is down", provider="fake")
        for piece in self.pieces:
            yield CompletionChunk(text=piece)
        yield CompletionChunk(done=True, input_tokens=10, output_tokens=4)

    def complete(self, task, **kw):
        from api.ai.base import Completion
        return Completion(text="".join(self.pieces), provider="fake",
                          model="fake", input_tokens=10, output_tokens=4)

    def embed(self, texts, **kw):
        raise AssertionError("streaming must not re-embed the corpus")


def _install(monkeypatch, router):
    monkeypatch.setattr(intelligence, "get_router", lambda: router)
    monkeypatch.setattr("api.ai.router.get_router", lambda: router)
    return router


def _frames(response) -> list:
    """Parse an SSE body into event dicts."""
    out = []
    for block in response.text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: "):]))
    return out


# ─── The protocol ────────────────────────────────────────────────────────────

class TestLibraryAskStreams:

    def test_it_returns_an_event_stream(self, client, user, saved, monkeypatch):
        _install(monkeypatch, FakeStreamRouter())
        r = client.post("/api/ask/stream", json={"question": "what pasta?"},
                        headers=_auth(user))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")

    def test_proxies_are_told_not_to_buffer(self, client, user, saved, monkeypatch):
        """Render and most reverse proxies buffer by default, which would hold
        every token until the stream closed — reproducing the exact freeze this
        replaces."""
        _install(monkeypatch, FakeStreamRouter())
        r = client.post("/api/ask/stream", json={"question": "hi"}, headers=_auth(user))
        assert r.headers.get("x-accel-buffering") == "no"
        assert "no-cache" in r.headers.get("cache-control", "")

    def test_events_arrive_in_the_useful_order(self, client, user, saved, monkeypatch):
        """Sources before tokens: retrieval is fast and the model is slow, so
        the client can show what it is reading from while the answer is written."""
        _install(monkeypatch, FakeStreamRouter())
        r = client.post("/api/ask/stream", json={"question": "what pasta?"},
                        headers=_auth(user))
        names = [f.get("event") for f in _frames(r)]
        assert names[0] == "meta"
        assert "sources" in names
        assert names.index("sources") < names.index("token")
        assert names[-1] == "done"

    def test_tokens_are_deltas_not_running_totals(self, client, user, saved,
                                                  monkeypatch):
        """The classic streaming bug: yield the accumulated string each time and
        the answer repeats itself on screen."""
        _install(monkeypatch, FakeStreamRouter())
        r = client.post("/api/ask/stream", json={"question": "what pasta?"},
                        headers=_auth(user))
        frames = _frames(r)
        tokens = [f["text"] for f in frames if f.get("event") == "token"]
        done = next(f for f in frames if f.get("event") == "done")
        assert tokens == FakeStreamRouter.PIECES
        assert "".join(tokens) == done["answer"]

    def test_nothing_is_revealed_from_a_finished_string(self, monkeypatch, clean_db,
                                                        user, saved):
        """Faking streaming would mean calling `complete()` and slicing it up.

        Asserted at the service layer: the streaming path must call
        `complete_stream` and must never call `complete`.
        """
        router = _install(monkeypatch, FakeStreamRouter())
        called = []
        router.complete = lambda *a, **k: called.append(1)
        list(intelligence.ask_sava_stream(clean_db, user.id, "what pasta?"))
        assert router.stream_calls == 1
        assert called == [], "the stream must not go through complete()"

    def test_the_thread_id_arrives_first_so_a_retry_can_reuse_it(
            self, client, user, saved, monkeypatch):
        _install(monkeypatch, FakeStreamRouter())
        r = client.post("/api/ask/stream", json={"question": "what pasta?"},
                        headers=_auth(user))
        first = _frames(r)[0]
        assert first["event"] == "meta"
        assert isinstance(first["thread_id"], int)

    def test_timings_come_back_for_measurement(self, client, user, saved, monkeypatch):
        _install(monkeypatch, FakeStreamRouter())
        r = client.post("/api/ask/stream", json={"question": "what pasta?"},
                        headers=_auth(user))
        done = next(f for f in _frames(r) if f.get("event") == "done")
        timings = done["timings_ms"]
        assert "retrieval" in timings and "total" in timings
        assert "first_token" in timings, "time-to-first-token must be measurable"


# ─── Conversation state ──────────────────────────────────────────────────────

class TestPersistence:

    def test_a_completed_stream_is_saved_to_the_thread(self, client, user, saved,
                                                       clean_db, monkeypatch):
        _install(monkeypatch, FakeStreamRouter())
        r = client.post("/api/ask/stream", json={"question": "what pasta?"},
                        headers=_auth(user))
        thread_id = _frames(r)[0]["thread_id"]
        db = SessionLocal()
        try:
            roles = [m.role for m in db.query(ChatMessage)
                     .filter(ChatMessage.thread_id == thread_id)
                     .order_by(ChatMessage.created_at).all()]
        finally:
            db.close()
        assert roles == ["user", "assistant"]

    def test_a_follow_up_reuses_the_thread(self, client, user, saved, monkeypatch):
        _install(monkeypatch, FakeStreamRouter())
        first = client.post("/api/ask/stream", json={"question": "what pasta?"},
                            headers=_auth(user))
        thread_id = _frames(first)[0]["thread_id"]
        second = client.post("/api/ask/stream",
                             json={"question": "how long?", "thread_id": thread_id},
                             headers=_auth(user))
        assert _frames(second)[0]["thread_id"] == thread_id


# ─── Failure, billing and cancellation ───────────────────────────────────────

class TestFailureHandling:

    def test_a_provider_failure_is_an_error_event_not_a_500(
            self, client, user, saved, monkeypatch):
        """The user's message must survive on screen so Retry has something to
        retry — an HTTP error would drop the whole exchange."""
        _install(monkeypatch, FakeStreamRouter(fail=True))
        r = client.post("/api/ask/stream", json={"question": "what pasta?"},
                        headers=_auth(user))
        assert r.status_code == 200
        names = [f.get("event") for f in _frames(r)]
        assert "error" in names
        assert "done" not in names

    def test_an_answer_that_never_started_is_refunded(self, client, user, saved,
                                                      clean_db, monkeypatch):
        """Charging for an outage is how it comes to feel like a scam."""
        from api import billing
        refunds = []
        monkeypatch.setattr(billing, "refund_ask",
                            lambda db, uid, **kw: refunds.append(uid))
        monkeypatch.setattr("api.routes_intelligence.billing.refund_ask",
                            lambda db, uid, **kw: refunds.append(uid))
        _install(monkeypatch, FakeStreamRouter(fail=True))
        client.post("/api/ask/stream", json={"question": "what pasta?"},
                    headers=_auth(user))
        assert refunds == [user.id]

    def test_a_successful_answer_is_not_refunded(self, client, user, saved,
                                                 monkeypatch):
        refunds = []
        monkeypatch.setattr("api.routes_intelligence.billing.refund_ask",
                            lambda db, uid, **kw: refunds.append(uid))
        _install(monkeypatch, FakeStreamRouter())
        client.post("/api/ask/stream", json={"question": "what pasta?"},
                    headers=_auth(user))
        assert refunds == []

    def test_an_empty_question_is_still_rejected(self, client, user, monkeypatch):
        _install(monkeypatch, FakeStreamRouter())
        r = client.post("/api/ask/stream", json={"question": "   "},
                        headers=_auth(user))
        assert r.status_code == 422

    def test_the_stream_requires_auth(self, client):
        r = client.post("/api/ask/stream", json={"question": "hi"})
        assert r.status_code in (401, 403)


# ─── Per-item Ask, same architecture ────────────────────────────────────────

class TestItemAskStreams:

    def test_it_streams_too(self, client, user, saved, monkeypatch):
        _install(monkeypatch, FakeStreamRouter())
        r = client.post(f"/api/bookmarks/{saved.id}/ask/stream",
                        json={"question": "what is this about?"}, headers=_auth(user))
        assert r.status_code == 200
        names = [f.get("event") for f in _frames(r)]
        assert "token" in names and names[-1] == "done"

    def test_tokens_are_deltas_here_as_well(self, client, user, saved, monkeypatch):
        _install(monkeypatch, FakeStreamRouter())
        r = client.post(f"/api/bookmarks/{saved.id}/ask/stream",
                        json={"question": "what is this about?"}, headers=_auth(user))
        frames = _frames(r)
        tokens = [f["text"] for f in frames if f.get("event") == "token"]
        done = next(f for f in frames if f.get("event") == "done")
        assert "".join(tokens) == done["answer"]

    def test_it_cannot_read_somebody_elses_save(self, client, clean_db, saved,
                                                monkeypatch):
        _install(monkeypatch, FakeStreamRouter())
        attacker = make_user(clean_db, f"attacker-{next(_seq)}@example.com")
        r = client.post(f"/api/bookmarks/{saved.id}/ask/stream",
                        json={"question": "what is this?"}, headers=_auth(attacker))
        assert r.status_code == 404


# ─── Visual escalation ───────────────────────────────────────────────────────

class TestVisualEscalation:

    def test_a_transcript_question_never_looks_at_the_video(
            self, clean_db, user, saved, monkeypatch):
        """"What is this about?" is answerable from the summary. Escalating
        would spend money and minutes for nothing."""
        _install(monkeypatch, FakeStreamRouter())
        from api.services import visual_ask
        escalations = []
        monkeypatch.setattr(visual_ask, "_escalate",
                            lambda *a, **k: escalations.append(1))
        bm = clean_db.query(Bookmark).get(saved.id)
        list(intelligence.ask_this_stream(clean_db, bm, "what is this about?",
                                          user_id=user.id))
        assert escalations == []

    def test_a_queued_look_is_announced_before_the_answer(self, clean_db, user,
                                                          saved, monkeypatch):
        """The chat must say "Looking through the video…" rather than freeze."""
        _install(monkeypatch, FakeStreamRouter())
        from api.services import visual_ask

        class Ctx:
            required, available, queued, blocked = True, False, True, None
            upgrade_available = False
            def public(self): return {"visual_required": True, "visual_queued": True}
        monkeypatch.setattr(visual_ask, "prepare", lambda *a, **k: Ctx())
        monkeypatch.setattr(visual_ask, "context_note", lambda v: None)

        bm = clean_db.query(Bookmark).get(saved.id)
        events = list(intelligence.ask_this_stream(
            clean_db, bm, "what shirt is he wearing?", user_id=user.id))
        status = [e for e in events if e.get("event") == "status"]
        assert status, "a queued visual look must be announced"
        assert "Looking through the video" in status[0]["message"]
        assert events.index(status[0]) < next(
            i for i, e in enumerate(events) if e.get("event") == "token")

    def test_it_still_answers_rather_than_stalling(self, clean_db, user, saved,
                                                   monkeypatch):
        """A queued frames job does not licence guessing, but it also must not
        stop the assistant from answering from what it knows now."""
        _install(monkeypatch, FakeStreamRouter())
        from api.services import visual_ask

        class Ctx:
            required, available, queued, blocked = True, False, True, None
            upgrade_available = False
            def public(self): return {"visual_required": True, "visual_queued": True}
        monkeypatch.setattr(visual_ask, "prepare", lambda *a, **k: Ctx())
        monkeypatch.setattr(visual_ask, "context_note", lambda v: None)

        bm = clean_db.query(Bookmark).get(saved.id)
        events = list(intelligence.ask_this_stream(
            clean_db, bm, "what shirt is he wearing?", user_id=user.id))
        assert events[-1]["event"] == "done"
        assert events[-1]["answer"]


# ─── Reuse, not reacquisition ────────────────────────────────────────────────

class TestReusesStoredIntelligence:

    def test_streaming_an_answer_does_not_re_embed_the_library(
            self, clean_db, user, saved, monkeypatch):
        """`FakeStreamRouter.embed` raises. Reaching it would mean the Ask path
        is rebuilding embeddings that were computed at save time."""
        _install(monkeypatch, FakeStreamRouter())
        events = list(intelligence.ask_sava_stream(clean_db, user.id, "pasta"))
        assert events[-1]["event"] == "done"

    def test_it_does_not_reprocess_media(self, clean_db, user, saved, monkeypatch):
        _install(monkeypatch, FakeStreamRouter())
        from api.pipeline import ingest
        monkeypatch.setattr(ingest, "process_content",
                            lambda *a, **k: pytest.fail("Ask must not reprocess media"))
        list(intelligence.ask_sava_stream(clean_db, user.id, "pasta"))
