"""Automatic and manual collections.

The grouping engine calls no model, so almost all of this runs on plain data
and asserts on decisions rather than on a provider's mood.

What these protect, in rough order of how much it would hurt to lose:

  * the *quality* bar — "Entertainment", "Other", "fyp" and "Gaming" are not
    collections, and a feature that produces them is worse than no feature,
  * corrections survive rebuilds, because silently undoing a user's edit is the
    fastest way to make automatic organisation feel hostile,
  * one grouping is one collection — no duplicate spam, including the subtle
    case where renaming an automatic collection clones it,
  * manual collections are never touched by regrouping,
  * resurfacing states facts about content and never scores about the person.
"""
from __future__ import annotations

import pytest

from conftest import make_bookmark, make_user

from api.models import (
    CanonicalContent, Collection, CollectionFeedback, CollectionItem,
)
from api.services import collections as coll_svc
from api.services import grouping as G


# ─── Quality gates ───────────────────────────────────────────────────────────

class TestJunkIsNotACollection:
    @pytest.mark.parametrize("label", [
        "Entertainment", "Other", "General", "Videos", "Content", "Saved",
        "Miscellaneous", "Random", "Stuff", "Inspiration", "Vibes",
    ])
    def test_generic_labels_are_rejected(self, label):
        """The exact names the brief calls out as failures."""
        assert G.is_junk_label(label)

    @pytest.mark.parametrize("tag", [
        "fyp", "foryou", "viral", "trending", "shorts", "tiktok", "explorepage",
    ])
    def test_platform_furniture_is_rejected(self, tag):
        """`#fyp` describes where something was found, not what it is."""
        assert G.is_junk_label(tag)

    @pytest.mark.parametrize("label", [
        "Kai Cenat", "Attack on Titan", "Air Fryer Recipes", "BMW M Cars",
        "New York Restaurants", "penguinz0", "Japan",
    ])
    def test_good_labels_survive(self, label):
        assert not G.is_junk_label(label)

    def test_broad_genres_do_not_become_topics(self):
        """"Gaming" is "Entertainment" wearing a different coat."""
        items = [
            G.LibraryItem(bookmark_id=i, canonical_id=i, platform="youtube",
                          creator=f"creator {i}", title="t", caption="",
                          content_type=None, topics=["gaming", "comedy", "music"])
            for i in range(6)
        ]
        assert G.topic_candidates(items, G.PhraseIndex(items)) == []

    def test_placeholder_creators_are_not_people(self):
        items = [
            G.LibraryItem(bookmark_id=i, canonical_id=i, platform="instagram",
                          creator="Instagram User", title="t", caption="",
                          content_type=None)
            for i in range(5)
        ]
        assert G.creator_candidates(items) == []


# ─── Signal tiers ────────────────────────────────────────────────────────────

def _item(bid, **kw):
    title = kw.pop("title", "")
    caption = kw.pop("caption", "")
    return G.LibraryItem(
        bookmark_id=bid, canonical_id=bid,
        platform=kw.pop("platform", "tiktok"),
        creator=kw.pop("creator", None), title=title, caption=caption,
        content_type=kw.pop("content_type", None),
        topics=kw.pop("topics", []), entities=kw.pop("entities", {}),
        typed=kw.pop("typed", {}),
        hashtags=G.extract_hashtags(f"{title} {caption}"),
    )


class TestSignalTiers:
    def test_a_repeated_creator_becomes_a_collection(self):
        items = [_item(i, creator="penguinz0") for i in range(4)]
        [cand] = G.creator_candidates(items)
        assert cand.label == "penguinz0"
        assert cand.signature == "creator:penguinz0"
        assert cand.size == 4

    def test_two_saves_from_one_creator_is_a_coincidence(self):
        assert G.creator_candidates([_item(i, creator="Someone") for i in range(2)]) == []

    def test_hashtags_group_and_expand_from_the_library_itself(self):
        """The readable phrase is recovered from the user's own text, free.

        `#attackontitan` has had its spacing destroyed, but the properly written
        phrase is sitting in another title in the same library — which is where
        the hashtag came from in the first place.
        """
        items = [_item(i, caption="clip #attackontitan #fyp") for i in range(3)]
        items.append(_item(99, title="Attack on Titan final season"))
        cands = G.hashtag_candidates(items, G.PhraseIndex(items))
        labels = {c.label for c in cands}
        assert "Attack on Titan" in labels
        assert not any(c.label.lower() == "fyp" for c in cands)

    def test_typed_data_names_specifically(self):
        """"Japanese Recipes", never the bare category."""
        items = [_item(i, content_type="recipe", typed={"cuisine": "Japanese"})
                 for i in range(3)]
        [cand] = G.typed_candidates(items)
        assert cand.label == "Japanese Recipes"

    def test_restaurants_are_grouped_by_city(self):
        items = [_item(i, content_type="restaurant", typed={"city": "New York"})
                 for i in range(3)]
        [cand] = G.typed_candidates(items)
        assert cand.label == "New York Restaurants"


# ─── Overlap and naming ──────────────────────────────────────────────────────

class TestNoOverlappingCollections:
    def test_the_same_saves_do_not_become_two_collections(self):
        members = {1, 2, 3, 4}
        merged = G.merge_candidates([
            G.Candidate("creator:x", "Kai Cenat Live", set(members), "creator", 1.0),
            G.Candidate("tag:kaicenat", "Kaicenat", set(members), "tag", 0.8),
        ])
        assert len(merged) == 1

    def test_the_more_readable_name_wins_a_merge(self):
        """An acronym must not become the permanent title of a collection whose
        other candidate name was the readable one."""
        merged = G.merge_candidates([
            G.Candidate("tag:aot", "Aot", {1, 2, 3}, "tag", 0.85),
            G.Candidate("tag:attackontitan", "Attack on Titan", {1, 2, 3}, "tag", 0.85),
        ])
        assert [c.label for c in merged] == ["Attack on Titan"]

    def test_distinct_groupings_are_kept_apart(self):
        merged = G.merge_candidates([
            G.Candidate("creator:a", "A", {1, 2, 3}, "creator", 1.0),
            G.Candidate("creator:b", "B", {4, 5, 6}, "creator", 1.0),
        ])
        assert len(merged) == 2


# ─── The feedback loop ───────────────────────────────────────────────────────

class TestFeedbackSurvivesRebuilds:
    def test_a_removed_item_is_not_re_added(self):
        cands = G.apply_feedback(
            [G.Candidate("tag:aot", "Attack on Titan", {1, 2, 3, 4}, "tag", 1.0)],
            rejected=set(), removed={"tag:aot": {2}})
        assert cands[0].members == {1, 3, 4}

    def test_a_rejected_grouping_does_not_return(self):
        assert G.apply_feedback(
            [G.Candidate("creator:x", "X", {1, 2, 3}, "creator", 1.0)],
            rejected={"creator:x"}, removed={}) == []

    def test_removing_below_the_minimum_retires_the_collection(self):
        """Three members is the floor. Take one away and it stops being a
        grouping rather than becoming a two-item one."""
        assert G.apply_feedback(
            [G.Candidate("tag:x", "X", {1, 2, 3}, "tag", 1.0)],
            rejected=set(), removed={"tag:x": {3}}) == []


class TestRebuildIntegration:
    def _library(self, db, user, creator="RepeatCreator", n=4):
        made = []
        for i in range(n):
            cc = CanonicalContent(
                content_key=f"youtube:coll{user.id}x{i}", platform="youtube",
                platform_content_id=f"coll{user.id}x{i}",
                canonical_url=f"https://youtube.com/watch?v=coll{user.id}x{i}",
                media_kind="video", creator_name=creator, title=f"Episode {i}")
            db.add(cc)
            db.commit()
            db.refresh(cc)
            bm = make_bookmark(db, user.id, cc.canonical_url, platform="youtube")
            bm.canonical_content_id = cc.id
            db.commit()
            made.append(bm)
        return made

    def test_ids_are_stable_across_rebuilds(self, db):
        """A collection must not become a different row every time the library
        grows — it would lose its cover and its place on the screen."""
        user = make_user(db, "stable@test.dev")
        self._library(db, user)

        first = coll_svc.rebuild_auto_collections(db, user.id)
        second = coll_svc.rebuild_auto_collections(db, user.id)
        by_sig = lambda r: {c["signature"]: c["id"] for c in r["collections"]}
        assert by_sig(first) == by_sig(second)
        assert by_sig(first)

    def test_manual_collections_are_never_touched(self, db):
        user = make_user(db, "manual-safe@test.dev")
        self._library(db, user)
        mine = coll_svc.create_collection(db, user.id, "My Own Thing")

        coll_svc.rebuild_auto_collections(db, user.id)

        still = db.query(Collection).filter(Collection.id == mine.id).first()
        assert still is not None and still.kind == "manual"
        assert still.name == "My Own Thing"

    def test_renaming_an_auto_collection_does_not_clone_it(self, db):
        """The regression that produced two "Kai Cenat Live" collections.

        Renaming converts the row to manual but leaves its signature behind. A
        rebuild that only looks at automatic rows sees the signature as missing
        and creates a second collection over the same saves.
        """
        user = make_user(db, "rename-clone@test.dev")
        self._library(db, user, creator="CloneCheck")

        report = coll_svc.rebuild_auto_collections(db, user.id)
        assert report["collections"]
        target = db.query(Collection).filter(
            Collection.id == report["collections"][0]["id"]).first()
        signature = target.signature

        target.name = "Renamed By Me"
        target.kind = "manual"
        db.commit()

        coll_svc.rebuild_auto_collections(db, user.id)

        holders = db.query(Collection).filter(
            Collection.user_id == user.id, Collection.signature == signature).all()
        assert len(holders) == 1, [c.name for c in holders]

    def test_a_rejected_collection_stays_gone_after_rebuild(self, db):
        user = make_user(db, "reject-persist@test.dev")
        self._library(db, user, creator="RejectMe")

        report = coll_svc.rebuild_auto_collections(db, user.id)
        signature = report["collections"][0]["signature"]

        coll_svc.record_feedback(db, user.id, signature, "reject_collection")
        for c in db.query(Collection).filter(Collection.user_id == user.id,
                                             Collection.signature == signature).all():
            db.query(CollectionItem).filter(
                CollectionItem.collection_id == c.id).delete()
            db.delete(c)
        db.commit()

        after = coll_svc.rebuild_auto_collections(db, user.id)
        assert signature not in {c["signature"] for c in after["collections"]}

    def test_feedback_is_recorded_once(self, db):
        user = make_user(db, "feedback-dedup@test.dev")
        # A real bookmark, because `CollectionFeedback.bookmark_id` is a foreign
        # key. SQLite does not enforce those by default and Postgres always
        # does, so a made-up id passed here on SQLite and silently recorded
        # nothing on Postgres — which is how the swallowed-exception bug in
        # `record_feedback` stayed hidden.
        bookmark = make_bookmark(db, user.id, url="https://example.com/feedback-dedup")
        for _ in range(3):
            coll_svc.record_feedback(db, user.id, "tag:x", "remove_item", bookmark.id)
        assert db.query(CollectionFeedback).filter(
            CollectionFeedback.user_id == user.id).count() == 1

    def test_one_user_grouping_never_sees_another_users_saves(self, db):
        alice = make_user(db, "coll-alice@test.dev")
        bob = make_user(db, "coll-bob@test.dev")
        self._library(db, alice, creator="AliceOnly")
        self._library(db, bob, creator="BobOnly")

        report = coll_svc.rebuild_auto_collections(db, alice.id)
        names = {c["name"] for c in report["collections"]}
        assert "BobOnly" not in names


# ─── Resurfacing ─────────────────────────────────────────────────────────────

class TestResurfacing:
    def test_reasons_are_facts_about_content_not_the_person(self):
        """No streaks, no counts of what the user did. The section states
        properties of the save, which the reader can check."""
        from api.services.resurfacing import _age_phrase

        assert _age_phrase(9) == "Saved 9 days ago"
        assert _age_phrase(70) == "Saved 2 months ago"
        assert _age_phrase(800) == "Saved 2 years ago"

    def test_recent_saves_are_not_resurfaced(self, db):
        """Something saved yesterday is not being forgotten."""
        from api.services.resurfacing import worth_revisiting

        user = make_user(db, "resurface-fresh@test.dev")
        make_bookmark(db, user.id, "https://youtube.com/watch?v=freshfresh1",
                      platform="youtube")
        assert worth_revisiting(db, user.id) == []

    def test_a_common_word_cannot_justify_a_claim(self):
        """"You asked about gaming" on a baseball clip is a word match and
        obvious nonsense to a reader, which makes the feature look careless."""
        from api.services.resurfacing import _distinctive_terms
        from collections import Counter

        blobs = ["gaming stream highlights"] * 9 + ["baseball catch"]
        kept = _distinctive_terms(Counter({"gaming": 3}), blobs)
        assert "gaming" not in kept

    def test_a_proper_noun_is_nameable_but_a_common_word_is_not(self):
        from api.services.resurfacing import _appears_as_proper_noun

        assert _appears_as_proper_noun("titan", "Watch Attack on Titan now")
        assert not _appears_as_proper_noun("there", "is there anything good")
        # Sentence-initial capitals are grammar, not names.
        assert not _appears_as_proper_noun("there", "There is a video")
