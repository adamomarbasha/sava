"""Search: a literal match is a result, not a footnote.

Reported from the production iPhone app. Searching

    Speed

showed **"No matches"**, and then underneath, in a section headed "Also
related", listed a saved TikTok titled

    "Speed was convinced that it looked like GTA 5 but GTA 6 💀"

Two independent causes, both fixed here:

  1. **The client ran two different searches.** The primary grid came from
     `GET /api/bookmarks?q=`, whose filter reads only
     `bookmarks.title/author/description/note`. Anything whose text lives on the
     canonical row or in the derived understanding — transcript summary, topics,
     entities — could not appear in the primary results at all. The good hybrid
     endpoint ran second and its hits were demoted into "Also related".

  2. **Lexical matches could be outranked and then filtered out.** Fusion was
     `0.72*semantic + 0.42*keyword`, so a semantic near-miss at 0.8 similarity
     scored 0.576 while a save whose *title contains the query word* scored 0.42.
     The relevance floor, measured against the best overall score, then cut it.
"""
from __future__ import annotations

import pytest

from api.models import Bookmark, CanonicalContent, ContentUnderstanding, ProcessingState
from api.services import retrieval

from conftest import FakeRouter, install_fake_router, make_user


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _save(db, user_id, *, key, title=None, creator=None, description=None,
          bookmark_title=None, note=None, tl_dr=None, topics=None,
          platform="tiktok"):
    """A save whose text lives where the production data actually puts it.

    `bookmark_title` defaults to None on purpose: the whole bug was that the
    primary search only read the bookmark row, so the fixtures must reproduce
    text that exists *only* on the canonical row.
    """
    import json
    cc = CanonicalContent(
        content_key=key, platform=platform, canonical_url=f"https://x/{key}",
        media_kind="video", title=title, description=description,
        creator_name=creator, processing_state=ProcessingState.READY,
        processing_level=4, stage_status="{}", metadata_json="{}")
    db.add(cc)
    db.commit()
    db.refresh(cc)

    if tl_dr or topics:
        db.add(ContentUnderstanding(
            canonical_content_id=cc.id, tl_dr=tl_dr,
            topics=json.dumps(topics or []), key_points="[]", entities="{}",
            typed_data="{}", chapters="[]", sources_used="[]"))

    bm = Bookmark(user_id=user_id, url=f"https://x/{key}", platform=platform,
                  raw="{}", title=bookmark_title, note=note,
                  canonical_content_id=cc.id,
                  processing_state=ProcessingState.READY)
    db.add(bm)
    db.commit()
    db.refresh(bm)
    return cc, bm


@pytest.fixture
def user(clean_db):
    return make_user(clean_db, "search-ranking@example.com")


@pytest.fixture
def library(clean_db, user, monkeypatch):
    """The reported item, plus neighbours for it to be ranked against."""
    install_fake_router(monkeypatch, FakeRouter())
    speed, _ = _save(
        clean_db, user.id, key="tiktok:speed",
        title="Speed was convinced that it looked like GTA 5 but GTA 6 💀",
        creator="clipsdaily", description="reaction to the trailer")
    _save(clean_db, user.id, key="tiktok:pasta",
          title="Three ingredient pasta in ten minutes", creator="cookwithme",
          description="garlic, chilli, olive oil", tl_dr="A very quick pasta recipe.",
          topics=["cooking", "pasta"])
    _save(clean_db, user.id, key="youtube:gpu", platform="youtube",
          title="Building a rendering engine from scratch", creator="handmade",
          description="rasterisation, shaders, performance",
          tl_dr="How a software renderer draws triangles.",
          topics=["graphics", "programming"])
    return {"speed": speed}


def _titles(results):
    return [(r.title or "") for r in results]


# ─── 1. The reported bug ─────────────────────────────────────────────────────

class TestExactTitleMatchIsAPrimaryResult:

    def test_searching_speed_returns_the_speed_tiktok(self, clean_db, user, library):
        """The exact reported query, against the exact reported title."""
        results = retrieval.search_library(clean_db, user.id, "Speed", limit=30)
        assert results, "'Speed' returned nothing at all"
        assert any(t.startswith("Speed was convinced") for t in _titles(results)), \
            f"the literal title match is missing: {_titles(results)}"

    def test_it_is_ranked_first(self, clean_db, user, library):
        """Not merely present — a literal title hit is the answer."""
        results = retrieval.search_library(clean_db, user.id, "Speed", limit=30)
        assert _titles(results)[0].startswith("Speed was convinced")

    def test_the_title_lives_only_on_canonical_content(self, clean_db, user, library):
        """Guards the fixture: if the title were mirrored onto the bookmark row,
        the old broken path would have found it and the test would prove nothing."""
        bm = clean_db.query(Bookmark).filter(
            Bookmark.canonical_content_id == library["speed"].id).first()
        assert bm.title is None
        assert library["speed"].title.startswith("Speed was convinced")

    @pytest.mark.parametrize("query", ["Speed", "speed", "SPEED", "sPeEd"])
    def test_match_is_case_insensitive(self, clean_db, user, library, query):
        results = retrieval.search_library(clean_db, user.id, query, limit=30)
        assert any(t.startswith("Speed was convinced") for t in _titles(results))

    @pytest.mark.parametrize("query", ["convinced", "GTA", "gta 6"])
    def test_other_words_from_the_title_also_match(self, clean_db, user, library, query):
        results = retrieval.search_library(clean_db, user.id, query, limit=30)
        assert any(t.startswith("Speed was convinced") for t in _titles(results)), query

    def test_a_creator_name_matches(self, clean_db, user, library):
        results = retrieval.search_library(clean_db, user.id, "clipsdaily", limit=30)
        assert any(t.startswith("Speed was convinced") for t in _titles(results))

    def test_text_that_lives_only_in_the_understanding_matches(self, clean_db,
                                                               user, library):
        """`tl_dr` and `topics` are derived from the transcript and exist on no
        bookmark column — exactly the content the old primary search could not
        reach."""
        results = retrieval.search_library(clean_db, user.id, "rasterisation", limit=30)
        assert any("rendering engine" in t for t in _titles(results))


# ─── 2. Lexical outranks semantic, and survives the floor ───────────────────

class TestLexicalRanking:

    def test_a_lexical_hit_outranks_a_pure_semantic_one(self, clean_db, user,
                                                        library, monkeypatch):
        """The fusion bug: 0.72*0.8 = 0.576 used to beat a title match at 0.42."""
        results = retrieval.search_library(clean_db, user.id, "Speed", limit=30)
        scores = {(r.title or "")[:24]: r.score for r in results}
        speed = next(v for k, v in scores.items() if k.startswith("Speed was"))
        others = [v for k, v in scores.items() if not k.startswith("Speed was")]
        assert all(speed > o for o in others), scores

    def test_a_lexical_hit_is_never_cut_by_the_relevance_floor(self, clean_db,
                                                               user, library):
        """The floor is measured against the best *semantic* score now, and
        never applied to lexical hits at all."""
        results = retrieval.search_library(clean_db, user.id, "clipsdaily", limit=30)
        assert any(t.startswith("Speed was convinced") for t in _titles(results))

    def test_lexical_band_sits_above_the_semantic_ceiling(self):
        """The invariant the ranking depends on, asserted directly."""
        assert retrieval.LEXICAL_BAND > 0.72


# ─── 3. Dedup ────────────────────────────────────────────────────────────────

class TestNoDuplicates:

    def test_an_item_found_lexically_and_semantically_appears_once(
            self, clean_db, user, library):
        results = retrieval.search_library(clean_db, user.id, "pasta", limit=30)
        ids = [r.canonical_id for r in results]
        assert len(ids) == len(set(ids)), f"duplicate canonical ids: {ids}"

    @pytest.mark.parametrize("query", ["Speed", "pasta", "GTA", "rendering"])
    def test_no_query_ever_returns_the_same_save_twice(self, clean_db, user,
                                                       library, query):
        results = retrieval.search_library(clean_db, user.id, query, limit=30)
        for field in ("canonical_id", "bookmark_id"):
            values = [getattr(r, field) for r in results]
            assert len(values) == len(set(values)), f"{query}: duplicate {field}"


# ─── 4. Semantic still works ─────────────────────────────────────────────────

class TestSemanticStillWorks:

    def test_a_query_with_no_literal_match_can_still_return_results(
            self, clean_db, user, library):
        """`FakeRouter` embeds a hashed bag of words, so shared vocabulary
        produces real similarity without a substring match anywhere."""
        results = retrieval.search_library(
            clean_db, user.id, "garlic chilli olive oil", limit=30)
        assert results, "semantic retrieval returned nothing"

    def test_semantic_results_are_primary_results(self, clean_db, user, library):
        """There is no second bucket any more — whatever comes back *is* the
        result list."""
        results = retrieval.search_library(clean_db, user.id, "cooking", limit=30)
        assert all(hasattr(r, "score") for r in results)


# ─── 5. Genuinely nothing ────────────────────────────────────────────────────

class TestTrueNoMatch:

    @pytest.mark.parametrize("query", ["zzzqqxwv", "kkkkkkkkkkkk"])
    def test_a_nonsense_query_returns_no_lexical_hits(self, clean_db, user,
                                                      library, query, monkeypatch):
        """With embeddings unavailable there is nothing to fall back on, so the
        result must be empty rather than a page of coincidences."""
        monkeypatch.setattr(retrieval, "_embed_query", lambda q: None)
        assert retrieval.search_library(clean_db, user.id, query, limit=30) == []

    def test_an_empty_library_returns_nothing(self, clean_db, monkeypatch):
        install_fake_router(monkeypatch, FakeRouter())
        monkeypatch.setattr(retrieval, "_embed_query", lambda q: None)
        empty = make_user(clean_db, "nothing-saved@example.com")
        assert retrieval.search_library(clean_db, empty.id, "anything", limit=30) == []

    def test_results_never_cross_users(self, clean_db, user, library, monkeypatch):
        other = make_user(clean_db, "someone-else@example.com")
        monkeypatch.setattr(retrieval, "_embed_query", lambda q: None)
        assert retrieval.search_library(clean_db, other.id, "Speed", limit=30) == []


# ─── 6. The iOS UI no longer has an "Also related" bucket ───────────────────

class TestSearchUIHasNoSecondBucket:
    """Source-level, like `test_ios_shortcut.py`. Cheap, and it catches the
    exact regression: someone re-adding a secondary strip that hides matches."""

    @staticmethod
    def _ios(*parts):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent / "ios"
        return (root.joinpath(*parts)).read_text(encoding="utf-8")

    def test_the_search_view_renders_no_also_related_section(self):
        source = self._ios("Sava", "Features", "Search", "SearchView.swift")
        assert "Also related" not in source
        assert "alsoRelated" not in source

    def test_the_view_model_has_no_also_related_state(self):
        source = self._ios("Sava", "Features", "Search", "SearchViewModel.swift")
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("///"))
        assert "alsoRelated" not in code
        assert "semanticTask" not in code

    def test_search_runs_one_pass_against_the_hybrid_endpoint(self):
        """Two passes were the bug. The keyword-only bookmark listing must not
        be what populates the results grid."""
        source = self._ios("Sava", "Features", "Search", "SearchViewModel.swift")
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("///"))
        assert "searchLibrary(" in code, "search must use the hybrid endpoint"
        assert "bookmarks.list(" not in code, \
            "the keyword-only listing must not drive search results"
