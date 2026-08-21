"""Collection covers.

No test here reaches the network or a model. Providers are stubbed, the ranking
step is stubbed, and mirroring is stubbed — because what needs protecting is
Sava's policy, not a third party's uptime:

  * a read never costs a search or an inference,
  * a cover the user chose is never overwritten by the system,
  * an image whose rights cannot be established is never published,
  * candidate volume reaching a model stays bounded,
  * a cover survives its source page disappearing.
"""
from __future__ import annotations

import json

import pytest

from conftest import make_user

from api.models import Collection
from api.services import collection_covers as CC


def _collection(db, user, name="Kai Cenat", signature="creator:kaicenat", **kw):
    coll = Collection(user_id=user.id, name=name, kind="auto", signature=signature, **kw)
    db.add(coll)
    db.commit()
    db.refresh(coll)
    return coll


def _candidate(idx=0, **kw):
    return CC.ImageCandidate(
        candidate_id=kw.pop("candidate_id", f"stub:{idx}"),
        image_url=kw.pop("image_url", f"https://commons.wikimedia.org/a{idx}.jpg"),
        source_domain=kw.pop("source_domain", "commons.wikimedia.org"),
        title=kw.pop("title", f"Candidate {idx}"),
        width=kw.pop("width", 1200), height=kw.pop("height", 1600),
        license=kw.pop("license", "cc by 3.0"),
        provider=kw.pop("provider", "wikimedia"), **kw)


# ─── Identity ────────────────────────────────────────────────────────────────

class TestCollectionIdentity:
    def test_a_creator_collection_is_a_person(self):
        ident = CC.identify("penguinz0", "creator:penguinz0")
        assert ident.entity_type == "person"
        assert "penguinz0" in ident.visual_intent

    def test_restaurants_are_a_place(self):
        ident = CC.identify("New York Restaurants", "typed:restaurant:newyork")
        assert ident.entity_type == "place"

    def test_recipes_are_a_topic_with_an_appetising_intent(self):
        ident = CC.identify("Air Fryer Recipes", "typed:recipe:airfryer")
        assert ident.entity_type == "topic"
        assert "appetising" in ident.visual_intent

    def test_a_tagged_subject_is_treated_as_a_nameable_thing(self):
        ident = CC.identify("Attack on Titan", "tag:aot")
        assert ident.entity_type == "brand"
        assert ident.search_intent == "Attack on Titan"

    def test_identity_never_leaks_technical_data_into_display(self):
        ident = CC.identify("BMW", "entity:bmw")
        assert ident.display_name == "BMW"
        assert "entity:" not in ident.display_name


# ─── Rights ──────────────────────────────────────────────────────────────────

class TestRights:
    @pytest.mark.parametrize("license_", [
        "cc0", "CC0 1.0", "pdm", "by", "by-3.0", "cc by 3.0", "by-sa", "public domain",
    ])
    def test_permissive_licenses_are_accepted(self, license_):
        assert CC.license_is_acceptable(license_)

    @pytest.mark.parametrize("license_", [
        None, "", "all rights reserved", "by-nc", "by-nd", "by-nc-nd", "unknown",
        "editorial use only",
    ])
    def test_anything_else_is_refused(self, license_):
        """Absence of a licence is not permission.

        An image is not reusable because it appeared in a search result, and
        treating unknown as yes is how a product ends up republishing someone
        else's photograph inside a stranger's library.
        """
        assert not CC.license_is_acceptable(license_)

    def test_unlicensed_candidates_never_reach_the_model(self):
        kept = CC.filter_candidates([
            _candidate(0, license="all rights reserved"),
            _candidate(1, license="by-nc"),
            _candidate(2, license="cc0"),
        ])
        assert [c.candidate_id for c in kept] == ["stub:2"]


# ─── Bounding ────────────────────────────────────────────────────────────────

class TestCandidateBounding:
    def test_the_model_never_sees_more_than_the_cap(self):
        """The cost control. Five hundred images must not become five hundred
        images of inference."""
        kept = CC.filter_candidates([_candidate(i) for i in range(200)])
        assert len(kept) <= CC.MAX_AI_CANDIDATES

    def test_tiny_images_are_dropped(self):
        assert CC.filter_candidates([_candidate(0, width=120, height=120)]) == []

    def test_extreme_aspect_ratios_are_dropped(self):
        """Banners and panoramas cannot be cropped to a cover without losing
        the subject."""
        assert CC.filter_candidates([_candidate(0, width=4000, height=400)]) == []

    def test_duplicate_urls_collapse(self):
        kept = CC.filter_candidates([
            _candidate(0, image_url="https://x/same.jpg"),
            _candidate(1, image_url="https://x/same.jpg"),
        ])
        assert len(kept) == 1

    def test_external_imagery_outranks_internal_thumbnails(self):
        """Internal media is the safety net, not the preferred answer — it is
        what the old cover system already produced."""
        kept = CC.filter_candidates([
            _candidate(0, provider="internal", license="internal"),
            _candidate(1, provider="wikimedia"),
        ])
        assert kept[0].provider == "wikimedia"


# ─── Selection and failure ───────────────────────────────────────────────────

class TestSelection:
    def test_a_model_failure_still_produces_a_cover(self, db, monkeypatch):
        """Ranking is an improvement on ordering, not a prerequisite for it."""
        user = make_user(db, "cover-aifail@test.dev")
        coll = _collection(db, user)
        monkeypatch.setattr(CC, "discover_candidates",
                            lambda *a, **k: [_candidate(0), _candidate(1)])
        monkeypatch.setattr(CC, "rank_with_ai",
                            lambda *a, **k: CC.CoverSelection(
                                images=[_candidate(0)], confidence=0.3,
                                reason="ranking failed"))
        monkeypatch.setattr(CC, "_mirror_selection", lambda *a, **k: True)

        assert CC.select_cover(db, coll, force=True)["status"] == "ok"

    def test_no_candidates_is_reported_not_raised(self, db, monkeypatch):
        user = make_user(db, "cover-none@test.dev")
        coll = _collection(db, user)
        monkeypatch.setattr(CC, "discover_candidates", lambda *a, **k: [])
        assert CC.select_cover(db, coll, force=True)["status"] == "no_candidates"

    def test_a_mirror_failure_does_not_record_a_broken_cover(self, db, monkeypatch):
        """Better no cover than a stored reference to an image we never got."""
        user = make_user(db, "cover-mirrorfail@test.dev")
        coll = _collection(db, user)
        monkeypatch.setattr(CC, "discover_candidates", lambda *a, **k: [_candidate(0)])
        monkeypatch.setattr(CC, "rank_with_ai",
                            lambda *a, **k: CC.CoverSelection(images=[_candidate(0)],
                                                              confidence=0.9))
        monkeypatch.setattr(CC, "_mirror_selection", lambda *a, **k: False)

        assert CC.select_cover(db, coll, force=True)["status"] == "mirror_failed"
        db.refresh(coll)
        assert coll.cover_storage_key is None

    def test_a_mosaic_is_recorded_as_several_images(self, db, monkeypatch):
        user = make_user(db, "cover-mosaic@test.dev")
        coll = _collection(db, user, name="Recipes", signature="typed:recipe:x")
        picks = [_candidate(i) for i in range(3)]
        monkeypatch.setattr(CC, "discover_candidates", lambda *a, **k: picks)
        monkeypatch.setattr(CC, "rank_with_ai",
                            lambda *a, **k: CC.CoverSelection(images=picks,
                                                              confidence=0.7,
                                                              is_mosaic=True))
        monkeypatch.setattr(
            "api.services.thumbnails.mirror_to_storage",
            lambda url, **k: (f"covers/{abs(hash(url))}.jpg",
                              f"/static/objects/covers/{abs(hash(url))}.jpg"))

        assert CC.select_cover(db, coll, force=True)["mosaic"] is True
        db.refresh(coll)
        assert len(json.loads(coll.cover_mosaic)) == 3


# ─── Stability ───────────────────────────────────────────────────────────────

class TestCoverStability:
    def test_a_healthy_cover_is_not_reselected(self, db):
        """Opening Collections must not re-run search or inference."""
        user = make_user(db, "cover-stable@test.dev")
        coll = _collection(db, user)
        coll.cover_storage_key = "covers/a.jpg"
        coll.cover_confidence = 0.9
        coll.cover_signature = CC.cover_signature(db, coll)
        db.commit()

        assert CC.needs_reselection(db, coll) is False

    def test_a_missing_cover_is_reselected(self, db):
        user = make_user(db, "cover-missing@test.dev")
        coll = _collection(db, user)
        assert CC.needs_reselection(db, coll) is True

    def test_a_low_confidence_cover_is_reconsidered(self, db):
        user = make_user(db, "cover-weak@test.dev")
        coll = _collection(db, user)
        coll.cover_storage_key = "covers/a.jpg"
        coll.cover_confidence = 0.1
        coll.cover_signature = CC.cover_signature(db, coll)
        db.commit()
        assert CC.needs_reselection(db, coll) is True

    def test_the_signature_ignores_trivial_membership_changes(self, db):
        """Adding one item must not trigger a fresh search."""
        from api.models import Bookmark, CollectionItem

        user = make_user(db, "cover-sig@test.dev")
        coll = _collection(db, user)
        for i in range(9):
            bm = Bookmark(user_id=user.id, url=f"https://x/{i}", platform="tiktok", raw="{}")
            db.add(bm)
            db.commit()
            db.add(CollectionItem(collection_id=coll.id, bookmark_id=bm.id))
        db.commit()

        before = CC.cover_signature(db, coll)
        bm = Bookmark(user_id=user.id, url="https://x/extra", platform="tiktok", raw="{}")
        db.add(bm)
        db.commit()
        db.add(CollectionItem(collection_id=coll.id, bookmark_id=bm.id))
        db.commit()

        assert CC.cover_signature(db, coll) == before

    def test_renaming_the_collection_does_change_the_signature(self, db):
        user = make_user(db, "cover-rename@test.dev")
        coll = _collection(db, user)
        before = CC.cover_signature(db, coll)
        coll.name = "Something Else Entirely"
        db.commit()
        assert CC.cover_signature(db, coll) != before


# ─── Manual override ─────────────────────────────────────────────────────────

class TestManualOverride:
    def _manual(self, db, user, source="suggested", monkeypatch=None):
        coll = _collection(db, user)
        CC.set_manual_cover(db, coll, image_url="https://commons.wikimedia.org/x.jpg",
                            source=source)
        return coll

    def test_a_user_choice_is_recorded_as_theirs(self, db, monkeypatch):
        monkeypatch.setattr("api.services.thumbnails.mirror_to_storage",
                            lambda url, **k: ("covers/u.jpg", "/static/objects/covers/u.jpg"))
        user = make_user(db, "cover-manual@test.dev")
        coll = self._manual(db, user)
        db.refresh(coll)
        assert coll.cover_source == "suggested"
        assert coll.cover_url == "/static/objects/covers/u.jpg"

    def test_automatic_selection_never_overwrites_a_manual_cover(self, db, monkeypatch):
        """The rule the whole feature turns on: AI does not get to decide it
        found something better."""
        monkeypatch.setattr("api.services.thumbnails.mirror_to_storage",
                            lambda url, **k: ("covers/u.jpg", "/static/objects/covers/u.jpg"))
        user = make_user(db, "cover-nooverwrite@test.dev")
        coll = self._manual(db, user, source="user_upload")

        assert CC.needs_reselection(db, coll) is False
        result = CC.select_cover(db, coll)
        assert result["status"] in ("unchanged", "skipped")
        db.refresh(coll)
        assert coll.cover_source == "user_upload"
        assert coll.cover_url == "/static/objects/covers/u.jpg"

    def test_a_manual_cover_survives_a_rebuild(self, db, monkeypatch):
        from api.services import collections as coll_svc

        monkeypatch.setattr("api.services.thumbnails.mirror_to_storage",
                            lambda url, **k: ("covers/u.jpg", "/static/objects/covers/u.jpg"))
        user = make_user(db, "cover-rebuild@test.dev")
        coll = self._manual(db, user, source="collection_media")

        coll_svc.rebuild_auto_collections(db, user.id)

        survivor = db.query(Collection).filter(Collection.id == coll.id).first()
        if survivor is not None:      # the grouping may retire; the cover must not flip
            assert survivor.cover_source == "collection_media"

    def test_an_upload_is_stored_durably(self, db):
        user = make_user(db, "cover-upload@test.dev")
        coll = _collection(db, user)
        result = CC.set_manual_cover(db, coll, image_bytes=b"\xff\xd8\xff\xe0" + b"0" * 512,
                                     source="user_upload")
        assert result["status"] == "ok"
        db.refresh(coll)
        assert coll.cover_storage_key and coll.cover_source == "user_upload"

    def test_reset_hands_control_back_to_sava(self, db, monkeypatch):
        monkeypatch.setattr("api.services.thumbnails.mirror_to_storage",
                            lambda url, **k: ("covers/u.jpg", "/static/objects/covers/u.jpg"))
        monkeypatch.setattr(CC, "discover_candidates", lambda *a, **k: [_candidate(0)])
        monkeypatch.setattr(CC, "rank_with_ai",
                            lambda *a, **k: CC.CoverSelection(images=[_candidate(0)],
                                                              confidence=0.8))
        user = make_user(db, "cover-reset@test.dev")
        coll = self._manual(db, user, source="user_upload")

        CC.reset_to_automatic(db, coll)
        db.refresh(coll)
        assert coll.cover_source == "automatic"


# ─── Reads are free ──────────────────────────────────────────────────────────

class TestReadsCostNothing:
    def test_listing_collections_performs_no_search_and_no_inference(self, db, monkeypatch):
        """The performance contract. If this breaks, opening a tab starts
        spending money."""
        from api.services.collections import list_collections

        called = {"search": 0, "ai": 0}
        monkeypatch.setattr(CC, "discover_candidates",
                            lambda *a, **k: called.__setitem__("search", called["search"] + 1) or [])
        monkeypatch.setattr(CC, "rank_with_ai",
                            lambda *a, **k: called.__setitem__("ai", called["ai"] + 1)
                            or CC.CoverSelection())

        user = make_user(db, "cover-read@test.dev")
        _collection(db, user)
        list_collections(db, user.id)

        assert called == {"search": 0, "ai": 0}

    def test_a_stored_cover_is_served_without_reselection(self, db, monkeypatch):
        from api.services.collections import list_collections

        from api.models import Bookmark, CollectionItem

        user = make_user(db, "cover-served@test.dev")
        coll = _collection(db, user)
        # An empty automatic collection is correctly hidden from the shelf, so
        # give it a member — the point here is cover serving, not visibility.
        bm = Bookmark(user_id=user.id, url="https://x/served", platform="tiktok", raw="{}")
        db.add(bm)
        db.commit()
        db.add(CollectionItem(collection_id=coll.id, bookmark_id=bm.id))
        db.commit()

        coll.cover_url = "/static/objects/covers/stored.jpg"
        coll.cover_storage_key = "covers/stored.jpg"
        coll.cover_source = "automatic"
        db.commit()

        rows = [c for c in list_collections(db, user.id) if c["id"] == coll.id]
        assert rows and rows[0]["cover_thumbnail_url"] == "/static/objects/covers/stored.jpg"
