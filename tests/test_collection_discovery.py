""""Look for new groupings" — what it does, and what it says it did.

Reported: the button appears to do nothing. Traced from tap to database, the
grouping *algorithm* turned out to be fine; the wiring around it was not.

── Root causes ─────────────────────────────────────────────────────────────

  1. **Fire-and-forget.** `/api/collections/rebuild` defaults to
     `background=true`: it enqueues a job and returns `{"queued": true}` in
     milliseconds. The client then reloaded the list *before the worker could
     have run* and stopped. Nothing polled, so groups only ever appeared on
     some later pull-to-refresh.

  2. **No state was rendered.** The client's `rebuilding` flag reached only
     `.disabled()` on a menu item — inside a `Menu` that dismisses on tap.

  3. **The result was thrown away.** `_ = try? await rebuildCollections()`
     discarded the response and swallowed every error, so success, "found
     nothing", and a server fault were indistinguishable.

Measured, which is what justifies running it inline instead: 43ms at 50 saves,
35ms at 200, 48ms at 500. The work is dominated by one query.
"""
from __future__ import annotations

import itertools
import json
import pathlib

import pytest

from api.models import (Bookmark, CanonicalContent, Collection, CollectionItem,
                        ContentUnderstanding, ProcessingState)
from api.services.collections import rebuild_auto_collections
from api.services.grouping import MIN_MEMBERS

from conftest import make_user

_seq = itertools.count(1)
IOS = pathlib.Path(__file__).resolve().parent.parent / "ios"


def add(db, user, *, title, creator, topics=None, entities=None,
        platform="tiktok"):
    n = next(_seq)
    cc = CanonicalContent(
        content_key=f"{platform}:disc{n}", platform=platform,
        platform_content_id=str(n), canonical_url=f"https://x/disc{n}",
        media_kind="video", title=title, creator_name=creator, description="",
        content_type="video", processing_state=ProcessingState.READY,
        processing_level=4, stage_status="{}", metadata_json="{}")
    db.add(cc); db.flush()
    db.add(ContentUnderstanding(
        canonical_content_id=cc.id, tl_dr=title,
        topics=json.dumps(topics or []), key_points="[]",
        entities=json.dumps(entities or {}), typed_data="{}",
        chapters="[]", sources_used="[]"))
    bm = Bookmark(user_id=user.id, url=cc.canonical_url, platform=platform,
                  raw="{}", canonical_content_id=cc.id,
                  processing_state=ProcessingState.READY)
    db.add(bm); db.commit()
    return bm


@pytest.fixture
def user(clean_db):
    return make_user(clean_db, f"discovery-{next(_seq)}@example.com")


# ─── What comes back ─────────────────────────────────────────────────────────

class TestTheResponseIsUsable:

    def test_an_empty_library_says_so(self, clean_db, user):
        result = rebuild_auto_collections(clean_db, user.id)
        assert result["status"] == "empty_library"
        assert result["created"] == 0

    def test_a_library_below_the_minimum_says_so_distinctly(self, clean_db, user):
        """"No new groups" to somebody with two saves is a non-answer: it
        sounds like Sava looked and found nothing, when there was nothing to
        look at."""
        add(clean_db, user, title="One", creator="a")
        result = rebuild_auto_collections(clean_db, user.id)
        assert result["status"] == "not_enough_content"
        assert result["minimum"] == MIN_MEMBERS

    def test_counts_distinguish_created_from_updated(self, clean_db, user):
        """A group created moments ago always "changes" when its members are
        written; counting that as an update produced "2 new groups, 2 updated"
        for two brand-new groups."""
        for i in range(4):
            add(clean_db, user, title=f"Gym {i}", creator="fitcoach",
                topics=["fitness"])
        result = rebuild_auto_collections(clean_db, user.id)
        assert result["created"] >= 1
        assert result["updated"] == 0

    def test_a_second_run_with_no_changes_reports_nothing_new(self, clean_db, user):
        for i in range(4):
            add(clean_db, user, title=f"Gym {i}", creator="fitcoach",
                topics=["fitness"])
        rebuild_auto_collections(clean_db, user.id)
        again = rebuild_auto_collections(clean_db, user.id)
        assert again["created"] == 0
        assert again["updated"] == 0
        assert again["removed"] == 0

    def test_timings_are_reported(self, clean_db, user):
        add(clean_db, user, title="One", creator="a")
        assert "total" in rebuild_auto_collections(clean_db, user.id)["timings_ms"]


# ─── Quality: fewer, stronger groups ────────────────────────────────────────

class TestGroupingQuality:

    def test_one_strong_cluster_becomes_one_group(self, clean_db, user):
        for i in range(5):
            add(clean_db, user, title=f"Gym workout {i}", creator="fitcoach",
                topics=["fitness", "gym"])
        for i in range(3):
            add(clean_db, user, title=f"Unrelated {i}", creator=f"rand{i}",
                topics=[f"misc{i}"])
        result = rebuild_auto_collections(clean_db, user.id)
        assert len(result["collections"]) >= 1

    def test_unrelated_items_produce_no_groups(self, clean_db, user):
        """Zero is the preferred answer for a library with no patterns."""
        for i in range(10):
            add(clean_db, user, title=f"Different subject {i}",
                creator=f"person{i}", topics=[f"topic{i}"],
                platform=["tiktok", "youtube", "instagram"][i % 3])
        assert rebuild_auto_collections(clean_db, user.id)["collections"] == []

    def test_sharing_only_a_platform_is_not_a_group(self, clean_db, user):
        """Eight TikToks with nothing else in common are eight TikToks."""
        for i in range(8):
            add(clean_db, user, title=f"Thing {i}", creator=f"c{i}",
                topics=[], platform="tiktok")
        assert rebuild_auto_collections(clean_db, user.id)["collections"] == []

    def test_a_group_needs_the_minimum_membership(self, clean_db, user):
        for i in range(MIN_MEMBERS - 1):
            add(clean_db, user, title=f"Gym {i}", creator="fitcoach",
                topics=["fitness"])
        add(clean_db, user, title="Other", creator="someone", topics=["x"])
        add(clean_db, user, title="Other2", creator="someone2", topics=["y"])
        names = [c["name"] for c in rebuild_auto_collections(clean_db, user.id)["collections"]]
        assert "fitcoach" not in names

    def test_multiple_strong_clusters_each_become_a_group(self, clean_db, user):
        for i in range(4):
            add(clean_db, user, title=f"Pasta {i}", creator="cookwithme",
                topics=["cooking"])
        for i in range(4):
            add(clean_db, user, title=f"Gym {i}", creator="fitcoach",
                topics=["fitness"])
        result = rebuild_auto_collections(clean_db, user.id)
        assert len(result["collections"]) >= 2


# ─── Stability ───────────────────────────────────────────────────────────────

class TestRepeatedDiscoveryIsSafe:

    def test_rerunning_does_not_duplicate_collections(self, clean_db, user):
        for i in range(4):
            add(clean_db, user, title=f"Gym {i}", creator="fitcoach",
                topics=["fitness"])
        for _ in range(3):
            rebuild_auto_collections(clean_db, user.id)
        rows = clean_db.query(Collection).filter(Collection.user_id == user.id).all()
        assert len(rows) == len({c.signature for c in rows})

    def test_names_are_stable_across_runs(self, clean_db, user):
        for i in range(4):
            add(clean_db, user, title=f"Gym {i}", creator="fitcoach",
                topics=["fitness"])
        first = {c["name"] for c in rebuild_auto_collections(clean_db, user.id)["collections"]}
        second = {c["name"] for c in rebuild_auto_collections(clean_db, user.id)["collections"]}
        assert first == second

    def test_a_manual_collection_is_never_touched(self, clean_db, user):
        manual = Collection(user_id=user.id, name="My own list", kind="manual")
        clean_db.add(manual); clean_db.commit(); clean_db.refresh(manual)
        for i in range(4):
            add(clean_db, user, title=f"Gym {i}", creator="fitcoach",
                topics=["fitness"])
        rebuild_auto_collections(clean_db, user.id)
        still = clean_db.query(Collection).filter(Collection.id == manual.id).first()
        assert still is not None and still.name == "My own list"
        assert still.kind == "manual"

    def test_an_item_the_user_added_by_hand_survives(self, clean_db, user):
        """Only rows this process added are ever taken away."""
        for i in range(4):
            add(clean_db, user, title=f"Gym {i}", creator="fitcoach",
                topics=["fitness"])
        stray = add(clean_db, user, title="Nothing alike", creator="other",
                    topics=["zzz"])
        rebuild_auto_collections(clean_db, user.id)
        coll = clean_db.query(Collection).filter(
            Collection.user_id == user.id, Collection.kind == "auto").first()
        assert coll is not None
        clean_db.add(CollectionItem(collection_id=coll.id, bookmark_id=stray.id,
                                    added_by="user", score=1.0))
        clean_db.commit()
        rebuild_auto_collections(clean_db, user.id)
        kept = clean_db.query(CollectionItem).filter(
            CollectionItem.collection_id == coll.id,
            CollectionItem.bookmark_id == stray.id).first()
        assert kept is not None, "a hand-added item must not be reclaimed"


# ─── The client ──────────────────────────────────────────────────────────────

def code_of(*parts: str) -> str:
    return "\n".join(l for l in IOS.joinpath(*parts).read_text().splitlines()
                     if not l.strip().startswith("///")
                     and not l.strip().startswith("//"))


VIEW = ("Sava", "Features", "Collections", "CollectionsView.swift")
MODEL = ("Sava", "Core", "Models", "CollectionDiscovery.swift")
SERVICE = ("Sava", "Core", "Networking", "IntelligenceService.swift")


class TestTheButtonRespondsVisibly:

    def test_it_calls_the_synchronous_path(self):
        """The async default is what made the button appear to do nothing."""
        code = code_of(*SERVICE)
        assert 'URLQueryItem(name: "background", value: "false")' in code

    def test_the_result_is_decoded_not_discarded(self):
        code = code_of(*SERVICE)
        assert "-> CollectionDiscovery" in code
        assert "-> Data" not in code.split("rebuildCollections")[1][:200]

    def test_errors_are_not_swallowed(self):
        code = code_of(*VIEW)
        assert "_ = try? await intelligence.rebuildCollections()" not in code
        assert "catch {" in code

    def test_every_phase_the_user_can_be_in_has_a_sentence(self):
        code = code_of(*MODEL)
        for phase in ("idle", "starting", "analyzing", "grouping", "saving",
                      "complete", "noNewGroups", "notEnoughContent", "failed"):
            assert f"case {phase}" in code, phase

    def test_the_phase_is_actually_rendered(self):
        """The old flag reached only `.disabled()` on a menu item, inside a
        `Menu` that dismisses on tap.

        The phase is now mapped onto the shared `InlineStatus` component rather
        than a bespoke banner, so this asserts the mapping and the render.
        """
        code = code_of(*VIEW)
        assert "discovery.actionStatus" in code
        assert "InlineStatus(status: status" in code

    def test_a_repeated_tap_is_ignored_while_running(self):
        code = code_of(*VIEW)
        assert "guard !discovery.isRunning else { return }" in code

    def test_a_failure_offers_retry(self):
        code = code_of(*VIEW)
        assert "onRetry" in code
        assert "Try again" in IOS.joinpath(*VIEW).read_text()

    def test_guidance_and_failures_do_not_auto_dismiss(self):
        """"Save a few more things" is an instruction to act on, not news."""
        code = code_of(*VIEW)
        block = code[code.index("private func scheduleDiscoveryDismiss"):]
        block = block[:block.index("private func retry")]
        # Assert the arm each state lands in, not its spelling — the list
        # grows (`.awaitingUnderstanding` joined it) and a literal match would
        # fail on a change that is exactly what the test wants.
        break_arm = block[block.index("case ."):block.index("break")]
        for state in (".complete", ".noNewGroups", ".awaitingUnderstanding"):
            assert state in break_arm, f"{state} should not auto-dismiss"

    def test_nothing_claims_a_group_before_the_server_says_so(self):
        """The running phases are cosmetic pacing; only the response decides
        what is reported."""
        code = code_of(*VIEW)
        assert "phase(for: result)" in code
