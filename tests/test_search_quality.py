"""Search precision: an empty result beats eleven bad ones.

Reported from a physical iPhone. Searching

    shirt

returned a page of apparently unrelated videos. Nothing in the library was
about clothing; the results were embedding coincidences presented as answers.

── Root cause ──────────────────────────────────────────────────────────────

The only relevance filter was *relative*: keep everything within
`RELEVANCE_RATIO` of the best semantic score. `retrieval.py` already recorded
the measurement that makes that fatal — "an unrelated video still lands around
0.36 against any query". So for a query the library genuinely does not contain,
the distribution is **flat**: the best score is itself noise, `best * 0.55`
lands below the noise, and every coincidence clears the bar. The filter could
rank noise but never reject it. A trailing `or ranked[:1]` then guaranteed at
least one result even when the list was empty.

A relative floor answers "which of these is best". Only an absolute one can
answer "is any of this a match at all".

── The fix, in three gates ─────────────────────────────────────────────────

    1. absolute floor       raw similarity >= SEMANTIC_FLOOR
    2. confidence gate      if the best survivor < SEMANTIC_CONFIDENCE, drop
                            the whole semantic tail
    3. relative floor       the original rule, among what is left

Lexical hits pass through all three untouched: a save whose title contains the
query word is a result however lonely its score looks.
"""
from __future__ import annotations

import json

import pytest

from api.models import Bookmark, CanonicalContent, ContentUnderstanding, ProcessingState
from api.services import retrieval

from conftest import FakeRouter, install_fake_router, make_user


# ─── A library with, and without, clothing in it ─────────────────────────────

def _save(db, user_id, *, key, title, creator, description=None,
          tl_dr=None, topics=None, platform="tiktok"):
    cc = CanonicalContent(
        content_key=key, platform=platform, canonical_url=f"https://x/{key}",
        media_kind="video", title=title, description=description,
        creator_name=creator, processing_state=ProcessingState.READY,
        processing_level=4, stage_status="{}", metadata_json="{}")
    db.add(cc); db.commit(); db.refresh(cc)
    if tl_dr or topics:
        db.add(ContentUnderstanding(
            canonical_content_id=cc.id, tl_dr=tl_dr,
            topics=json.dumps(topics or []), key_points="[]", entities="{}",
            typed_data="{}", chapters="[]", sources_used="[]"))
    bm = Bookmark(user_id=user_id, url=f"https://x/{key}", platform=platform,
                  raw="{}", title=None, canonical_content_id=cc.id,
                  processing_state=ProcessingState.READY)
    db.add(bm); db.commit(); db.refresh(bm)
    return cc


@pytest.fixture
def user(clean_db):
    return make_user(clean_db, "search-quality@example.com")


@pytest.fixture
def no_clothing(clean_db, user, monkeypatch):
    """Seven saves, none of which is about what anybody wears."""
    install_fake_router(monkeypatch, FakeRouter())
    _save(clean_db, user.id, key="q:pasta", title="Three ingredient pasta",
          creator="cookwithme", description="garlic chilli olive oil",
          tl_dr="A very quick pasta recipe.", topics=["cooking"])
    _save(clean_db, user.id, key="q:gpu", platform="youtube",
          title="Building a rendering engine", creator="handmade",
          description="rasterisation shaders", tl_dr="How a renderer draws.",
          topics=["graphics"])
    _save(clean_db, user.id, key="q:berlin", platform="instagram",
          title="Berlin in October", creator="faye",
          description="autumn canals", tl_dr="A walking tour of Berlin.",
          topics=["travel"])
    _save(clean_db, user.id, key="q:espresso", platform="youtube",
          title="Why your espresso tastes sour", creator="thebean",
          description="extraction temperature", tl_dr="Espresso troubleshooting.",
          topics=["coffee"])
    _save(clean_db, user.id, key="q:compilers", platform="youtube",
          title="How compilers work", creator="handmade",
          description="parsing codegen", tl_dr="Compiler pipeline explained.",
          topics=["programming"])
    return user


def _titles(results):
    return [(r.title or "") for r in results]


# ─── The reported bug ────────────────────────────────────────────────────────

class TestUnrelatedQueryReturnsNothing:

    def test_shirt_against_a_library_with_no_clothing_returns_nothing(
            self, clean_db, no_clothing, monkeypatch):
        """The exact reported query. Every candidate is a coincidence, so the
        honest answer is an empty list."""
        _flat_similarity(monkeypatch, 0.37)
        results = retrieval.search_library(clean_db, no_clothing.id, "shirt", limit=30)
        assert results == [], f"expected nothing, got {_titles(results)}"

    @pytest.mark.parametrize("query", ["shirt", "shoes", "car insurance",
                                       "tax return", "wedding venue"])
    def test_no_query_the_library_cannot_answer_returns_coincidences(
            self, clean_db, no_clothing, query, monkeypatch):
        _flat_similarity(monkeypatch, 0.40)
        assert retrieval.search_library(clean_db, no_clothing.id, query, limit=30) == []

    def test_there_is_no_guaranteed_single_result_fallback(self, clean_db,
                                                           no_clothing, monkeypatch):
        """`or ranked[:1]` used to guarantee one result no matter how bad."""
        _flat_similarity(monkeypatch, 0.20)
        assert retrieval.search_library(clean_db, no_clothing.id, "zzz", limit=30) == []

    def test_the_fallback_clause_is_gone_from_the_source(self):
        """Checked against code, not comments — the removal is explained in a
        comment that necessarily quotes the clause it removed."""
        import pathlib
        source = (pathlib.Path(__file__).resolve().parent.parent
                  / "api" / "services" / "retrieval.py").read_text()
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("#"))
        assert "or ranked[:1]" not in code


# ─── But real matches still come back ────────────────────────────────────────

class TestPrecisionDoesNotCostRecall:

    def test_an_exact_title_match_is_returned(self, clean_db, no_clothing, monkeypatch):
        _flat_similarity(monkeypatch, 0.10)
        results = retrieval.search_library(clean_db, no_clothing.id, "pasta", limit=30)
        assert any("pasta" in t.lower() for t in _titles(results))

    def test_a_creator_match_is_returned(self, clean_db, no_clothing, monkeypatch):
        _flat_similarity(monkeypatch, 0.10)
        results = retrieval.search_library(clean_db, no_clothing.id, "handmade", limit=30)
        assert len(results) >= 1

    def test_a_word_from_the_summary_is_returned(self, clean_db, no_clothing,
                                                 monkeypatch):
        """Text that exists only on the derived understanding, not on any
        bookmark column."""
        _flat_similarity(monkeypatch, 0.10)
        results = retrieval.search_library(clean_db, no_clothing.id,
                                           "troubleshooting", limit=30)
        assert any("espresso" in t.lower() for t in _titles(results))

    def test_lexical_hits_survive_a_hostile_similarity_distribution(
            self, clean_db, no_clothing, monkeypatch):
        """A literal match must never be filtered by a semantic threshold."""
        _flat_similarity(monkeypatch, 0.0)
        results = retrieval.search_library(clean_db, no_clothing.id, "espresso", limit=30)
        assert any("espresso" in t.lower() for t in _titles(results))

    def test_a_confident_semantic_match_is_returned_without_any_shared_word(
            self, clean_db, no_clothing, monkeypatch):
        """A paraphrase with no literal overlap still resolves — the gate is
        about confidence, not about requiring a keyword."""
        target = (clean_db.query(CanonicalContent)
                  .filter(CanonicalContent.content_key == "q:berlin").first())
        _fixed_similarity(monkeypatch, {target.id: 0.81}, default=0.34)
        results = retrieval.search_library(clean_db, no_clothing.id,
                                           "somewhere to wander in the autumn",
                                           limit=30)
        assert any("Berlin" in t for t in _titles(results))


# ─── The thresholds themselves ───────────────────────────────────────────────

class TestThresholds:

    def test_the_floor_sits_above_the_documented_noise_level(self):
        """`retrieval.py` records unrelated content landing around 0.36."""
        assert retrieval.SEMANTIC_FLOOR > 0.36

    def test_confidence_is_at_least_the_floor(self):
        assert retrieval.SEMANTIC_CONFIDENCE >= retrieval.SEMANTIC_FLOOR

    def test_both_are_tunable_without_a_release(self, monkeypatch):
        monkeypatch.setenv("SAVA_SEARCH_SEMANTIC_FLOOR", "0.9")
        assert retrieval._float_env("SAVA_SEARCH_SEMANTIC_FLOOR", 0.5) == 0.9

    def test_a_malformed_override_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("SAVA_SEARCH_SEMANTIC_FLOOR", "not-a-number")
        assert retrieval._float_env("SAVA_SEARCH_SEMANTIC_FLOOR", 0.5) == 0.5

    def test_one_borderline_match_is_kept_and_its_tail_is_not(
            self, clean_db, no_clothing, monkeypatch):
        """The boundary, asserted directly: one clearly-good score survives and
        the near-noise around it does not."""
        rows = clean_db.query(CanonicalContent).all()
        good = next(c for c in rows if c.content_key == "q:pasta")
        scores = {c.id: 0.42 for c in rows}
        scores[good.id] = 0.77
        _fixed_similarity(monkeypatch, scores)
        # No word here appears in any title, creator or summary — otherwise a
        # lexical hit would arrive alongside the semantic one and the boundary
        # under test would not be the thing being measured. ("cook" would have
        # matched the creator `cookwithme`.)
        results = retrieval.search_library(clean_db, no_clothing.id,
                                           "dinner ideas for tonight", limit=30)
        assert len(results) == 1, _titles(results)
        assert "pasta" in (results[0].title or "").lower()


# ─── Dedupe still holds ──────────────────────────────────────────────────────

class TestNoDuplicates:

    @pytest.mark.parametrize("query", ["pasta", "espresso", "handmade"])
    def test_an_item_matched_twice_appears_once(self, clean_db, no_clothing,
                                                query, monkeypatch):
        _flat_similarity(monkeypatch, 0.65)
        results = retrieval.search_library(clean_db, no_clothing.id, query, limit=30)
        ids = [r.canonical_id for r in results]
        assert len(ids) == len(set(ids)), ids


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _flat_similarity(monkeypatch, value: float):
    """Every candidate scores the same — the distribution that defeated the
    relative-only filter."""
    monkeypatch.setattr(retrieval, "_embed_query", lambda q: [0.0] * 8)

    def knn(db, **kw):
        rows = db.execute(__import__("sqlalchemy").text(
            "SELECT DISTINCT canonical_content_id FROM bookmarks "
            "WHERE user_id = :uid AND canonical_content_id IS NOT NULL"),
            {"uid": kw["params"]["uid"]}).fetchall()
        return [(r[0], value) for r in rows]
    monkeypatch.setattr(retrieval, "knn", knn)


def _fixed_similarity(monkeypatch, scores: dict, default: float = 0.0):
    """Exact per-item similarity, so a boundary can be asserted."""
    monkeypatch.setattr(retrieval, "_embed_query", lambda q: [0.0] * 8)

    def knn(db, **kw):
        rows = db.execute(__import__("sqlalchemy").text(
            "SELECT DISTINCT canonical_content_id FROM bookmarks "
            "WHERE user_id = :uid AND canonical_content_id IS NOT NULL"),
            {"uid": kw["params"]["uid"]}).fetchall()
        out = [(r[0], scores.get(r[0], default)) for r in rows]
        return sorted(out, key=lambda t: t[1], reverse=True)
    monkeypatch.setattr(retrieval, "knn", knn)
