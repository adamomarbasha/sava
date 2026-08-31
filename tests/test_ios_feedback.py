"""Actions must not lie about what happened.

Source-level assertions, in the style of `test_ios_shortcut.py`.

── The pattern this file exists to prevent ─────────────────────────────────

Several actions were written as

    Task {
        _ = try? await service.doTheThing()
        await reload()
        Haptics.success()
    }

which has three faults and no visible symptom:

  * no acknowledgement while it runs,
  * the error is swallowed by `try?`,
  * and `Haptics.success()` fires *whether or not it worked* — a failed action
    buzzed as though it had succeeded.

The third is the worst of them. Silence is a gap; a success haptic on a failed
write is the app asserting something untrue, and the user walks away believing
a collection was created or a save was filed when it was not.
"""
from __future__ import annotations

import pathlib
import re

import pytest

IOS = pathlib.Path(__file__).resolve().parent.parent / "ios"


def read(*parts: str) -> str:
    return IOS.joinpath(*parts).read_text(encoding="utf-8")


def code_of(*parts: str) -> str:
    return "\n".join(l for l in read(*parts).splitlines()
                     if not l.strip().startswith("///")
                     and not l.strip().startswith("//"))


STATUS = ("Sava", "DesignSystem", "Components", "InlineStatus.swift")
COLLECTIONS = ("Sava", "Features", "Collections", "CollectionsView.swift")
ADD_TO = ("Sava", "Features", "Collections", "AddToCollectionSheet.swift")
DETAIL = ("Sava", "Features", "Detail", "SaveDetailView.swift")
ERROR = ("Sava", "Core", "Networking", "APIError.swift")
SEARCH = ("Sava", "Features", "Search", "SearchViewModel.swift")
LIBRARY = ("Sava", "Features", "Library", "LibraryViewModel.swift")


# ─── The shared primitive ────────────────────────────────────────────────────

class TestInlineStatus:

    def test_it_covers_the_four_shapes_an_action_can_end_in(self):
        code = code_of(*STATUS)
        for case in ("working", "success", "info", "failure"):
            assert f"case {case}" in code, case

    def test_results_fade_and_failures_do_not(self):
        """A failure has something to do about it, so it waits to be acted on."""
        code = code_of(*STATUS)
        block = code[code.index("var autoDismisses"):]
        block = block[:block.index("}\n}")]
        assert "case .success, .info: return true" in block
        assert "case .working, .failure: return false" in block

    def test_the_success_haptic_only_fires_on_the_success_path(self):
        """The whole point. `Haptics.success()` must be unreachable from the
        catch branch."""
        code = code_of(*STATUS)
        run = code[code.index("func run(working:"):]
        success_at = run.index("Haptics.success()")
        catch_at = run.index("} catch {")
        assert success_at < catch_at, "success haptic must precede the catch"
        assert "Haptics.error()" in run[catch_at:]

    def test_a_failure_reports_the_typed_message_not_a_raw_error(self):
        code = code_of(*STATUS)
        assert "(error as? APIError)?.userMessage" in code

    def test_status_changes_are_announced(self):
        code = code_of(*STATUS)
        assert "accessibilityLabel(status.message)" in code
        assert "updatesFrequently" in code

    def test_there_is_one_status_component_not_several(self):
        """`DiscoveryBanner` was a second implementation of the same idea; it is
        a mapping onto this one now."""
        code = code_of(*COLLECTIONS)
        assert "struct DiscoveryBanner" not in code
        assert "var actionStatus: ActionStatus?" in code


# ─── The actions that used to lie ────────────────────────────────────────────

class TestCollectionMutationsReport:

    @pytest.mark.parametrize("action", ["create", "commitRename"])
    def test_it_no_longer_swallows_the_error(self, action):
        code = code_of(*COLLECTIONS)
        body = code[code.index(f"private func {action}()"):]
        body = body[:body.index("\n    private func")]
        assert "try?" not in body, f"{action} still swallows its error"

    @pytest.mark.parametrize("action", ["create", "commitRename"])
    def test_it_reports_all_three_states(self, action):
        code = code_of(*COLLECTIONS)
        body = code[code.index(f"private func {action}()"):]
        body = body[:body.index("\n    private func")]
        assert "reporter.run(working:" in body
        assert "success:" in body and "failure:" in body

    def test_a_failed_delete_puts_the_collection_back_and_says_why(self):
        """Optimistic removal is right; undoing it silently is not. Previously
        `load()` simply restored the row — it vanished, reappeared, and nothing
        explained it."""
        code = code_of(*COLLECTIONS)
        body = code[code.index("private func commitDelete()"):]
        # Anchored on a line that survives comment-stripping.
        body = body[:body.index("private func rebuild()")]
        assert "let restore = collections" in body
        assert "collections = restore" in body
        assert "reporter.report(.failure(" in body

    def test_create_cannot_be_double_tapped(self):
        code = code_of(*COLLECTIONS)
        assert "guard !name.isEmpty, !creating else { return }" in code

    def test_the_status_is_actually_rendered(self):
        code = code_of(*COLLECTIONS)
        assert "InlineStatus(status: status)" in code


class TestAddToCollection:

    def test_both_calls_are_checked(self):
        """Create and add are two requests. The second was swallowed, so a
        collection could be created, the save silently not added, and the sheet
        still ticked the row and reported success."""
        code = code_of(*ADD_TO)
        body = code[code.index("private func create()"):]
        assert "try await intelligence.createCollection" in body
        assert "try await intelligence.addToCollection" in body
        assert "_ = try? await intelligence.addToCollection" not in body

    def test_it_reports_a_failure(self):
        code = code_of(*ADD_TO)
        body = code[code.index("private func create()"):]
        assert "errorMessage =" in body
        assert "Haptics.error()" in body

    def test_it_cannot_be_double_tapped(self):
        code = code_of(*ADD_TO)
        assert "!creating else { return }" in code


class TestDetailRetry:

    def test_the_button_recovers_after_a_failure(self):
        """`retrying` was set and never cleared, so one tap disabled Retry
        permanently — including when retrying was exactly what was needed."""
        code = code_of(*DETAIL)
        body = code[code.index("private func retryProcessing()"):]
        body = body[:body.index("\n    private func")]
        assert "retrying = false" in body

    def test_queued_is_shown_only_after_the_server_accepts(self):
        code = code_of(*DETAIL)
        body = code[code.index("private func retryProcessing()"):]
        body = body[:body.index("\n    private func")]
        assert "try await intelligence.reprocess" in body
        assert "queued = true" in body
        assert body.index("try await intelligence.reprocess") < body.index("queued = true")

    def test_a_refused_reprocess_is_visible(self):
        code = code_of(*DETAIL)
        assert "retryError" in code


# ─── The taxonomy the whole app leans on ────────────────────────────────────

class TestErrorTaxonomy:

    @pytest.mark.parametrize("case", ["offline", "timedOut", "unauthorized",
                                      "notFound", "conflict", "upgradeRequired",
                                      "badRequest", "server"])
    def test_the_distinctions_that_matter_exist(self, case):
        assert f"case {case}" in code_of(*ERROR), case

    def test_every_case_has_human_copy(self):
        code = code_of(*ERROR)
        block = code[code.index("var userMessage: String"):]
        block = block[:block.index("\n    var needsUpgrade")]
        # No raw interpolation of a backend error object into user-facing text.
        assert "\\(error)" not in block
        assert "localizedDescription" not in block

    def test_offline_and_server_failure_read_differently(self):
        code = read(*ERROR)
        assert "You appear to be offline" in code
        assert "Sava is having a moment" in code


# ─── Screens that were already right, and must stay right ───────────────────

class TestExistingGoodBehaviourIsGuarded:

    def test_search_has_a_real_state_machine(self):
        code = code_of(*SEARCH)
        assert "case idle, searching, results([Bookmark]), empty, failed(String)" in code

    def test_search_reports_a_typed_message(self):
        assert "(error as? APIError)?.userMessage" in code_of(*SEARCH)

    def test_a_failed_refresh_does_not_empty_the_library(self):
        """The user would watch their saves vanish over a blip."""
        code = code_of(*LIBRARY)
        assert "state = all.isEmpty ? .failed(message) : .loaded" in code

    def test_a_failed_delete_restores_the_library(self):
        code = code_of(*LIBRARY)
        assert "all = previous" in code
