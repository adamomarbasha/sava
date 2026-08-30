"""The Scroll viewer's embed state machine.

Source-level assertions, in the style of `test_ios_shortcut.py`. The failure
these guard is the reported one and it is completely silent in code review:
nothing throws, nothing logs, and the screen is simply black.

The `AVPlayer` path already held the item's poster under the video until the
first frame arrived. The embed path had no states at all — an opaque black
`WKWebView` went on screen the moment a descriptor said `.embed`, and the only
delegate callback implemented was `didFinish`.
"""
from __future__ import annotations

import pathlib

import pytest

IOS = pathlib.Path(__file__).resolve().parent.parent / "ios"


def read(*parts: str) -> str:
    return IOS.joinpath(*parts).read_text(encoding="utf-8")


def code_of(*parts: str) -> str:
    return "\n".join(line for line in read(*parts).splitlines()
                     if not line.strip().startswith("///")
                     and not line.strip().startswith("//"))


EMBED = ("Sava", "Features", "ShortForm", "EmbedPlayer.swift")
PAGE = ("Sava", "Features", "ShortForm", "ShortFormPage.swift")
FEED = ("Sava", "Features", "ShortForm", "ShortFormFeed.swift")
VIEWER = ("Sava", "Features", "ShortForm", "ShortFormViewer.swift")


# ─── The state machine ───────────────────────────────────────────────────────

class TestPhases:

    @pytest.mark.parametrize("phase", ["preparing", "loadingPlayback", "ready",
                                       "unavailable", "failed", "retrying"])
    def test_every_phase_exists(self, phase):
        assert f"case {phase}" in code_of(*EMBED), phase

    def test_only_ready_takes_the_poster_down(self):
        """Any other phase — including a failure — keeps the item's own frame
        on screen. That is the whole difference from a black rectangle."""
        code = code_of(*EMBED)
        block = code[code.index("var showsPoster"):]
        block = block[:block.index("}") + 1]
        assert "case .ready: return false" in block

    def test_a_failure_is_distinguished_from_an_unavailability(self):
        """Retry on "the creator disabled embedding" is a button that cannot
        work; withholding it on "you're offline" strands somebody in a tunnel."""
        code = code_of(*EMBED)
        assert "case unavailable(String)" in code
        assert "case failed(String)" in code
        assert "var isTerminalFailure" in code


# ─── Failure detection ───────────────────────────────────────────────────────

class TestFailuresAreNoLongerSwallowed:

    def test_provisional_navigation_failures_are_handled(self):
        """The original coordinator implemented `didFinish` and nothing else,
        so a failed load left a black view and no signal."""
        assert "didFailProvisionalNavigation" in code_of(*EMBED)

    def test_navigation_failures_are_handled(self):
        code = code_of(*EMBED)
        assert "func webView(_ webView: WKWebView, didFail navigation" in code

    def test_a_dead_web_content_process_is_handled(self):
        """Memory pressure after a long session kills the content process. It
        renders nothing and reports nothing through any navigation callback."""
        assert "webViewWebContentProcessDidTerminate" in code_of(*EMBED)

    def test_a_cancelled_load_is_not_reported_as_a_failure(self):
        """Swiping away cancels the load. Drawing an error over an item the
        user has already left is worse than drawing nothing."""
        code = code_of(*EMBED)
        assert "NSURLErrorCancelled" in code

    def test_being_offline_says_so(self):
        assert "NSURLErrorNotConnectedToInternet" in code_of(*EMBED)

    def test_page_load_alone_does_not_mean_ready(self):
        """`didFinish` fires for the host page, which loads fine, while the
        iframe inside it can stay empty forever."""
        code = code_of(*EMBED)
        finish = code[code.index("didFinish navigation"):]
        finish = finish[:finish.index("\n\n")]
        assert "isReady = true" not in finish

    def test_a_watchdog_covers_a_page_that_never_reports(self):
        code = code_of(*EMBED)
        assert "armWatchdog" in code and "cancelWatchdog" in code

    def test_the_page_can_report_through_a_message_handler(self):
        code = code_of(*EMBED)
        assert "WKScriptMessageHandler" in code
        assert 'userContentController.add(context.coordinator, name: "sava")' in code

    def test_the_handler_is_removed_on_teardown(self):
        """The content controller retains its handler, and with it the whole
        page graph — a leak that only shows after enough swipes to matter."""
        assert "removeScriptMessageHandler" in code_of(*EMBED)

    def test_an_error_can_supersede_a_ready(self):
        """YouTube fires `onReady` before it discovers the video cannot play."""
        code = code_of(*EMBED)
        report = code[code.index("func report(state:"):]
        report = report[:report.index("func fail")]
        assert "isTerminalFailure" in report


# ─── Composition ─────────────────────────────────────────────────────────────

class TestTheStageHoldsThePoster:

    def test_the_web_view_is_transparent(self):
        """An opaque web view hides the poster drawn behind it."""
        code = code_of(*EMBED)
        assert "webView.isOpaque = false" in code
        assert "backgroundColor = .clear" in code
        assert "backgroundColor = .black" not in code

    def test_the_poster_is_held_under_the_player(self):
        code = code_of(*PAGE)
        stage = code[code.index("private struct EmbedStage"):]
        assert "state.phase.showsPoster" in stage
        assert "ShortFormPoster(" in stage

    def test_the_player_is_hidden_until_ready(self):
        code = code_of(*PAGE)
        stage = code[code.index("private struct EmbedStage"):]
        assert "opacity(state.phase == .ready ? 1 : 0)" in stage

    def test_a_retryable_failure_offers_retry(self):
        code = code_of(*PAGE)
        assert "struct ShortFormRetry" in code
        assert "Try again" in read(*PAGE)

    def test_an_unavailable_item_does_not_offer_retry(self):
        """It would be a button that cannot work."""
        code = code_of(*PAGE)
        stage = code[code.index("private struct EmbedStage"):]
        unavailable = stage[stage.index("case .unavailable(let why)"):]
        unavailable = unavailable[:unavailable.index("case .failed")]
        assert "ShortFormRetry" not in unavailable

    def test_the_state_survives_a_parent_redraw(self):
        """SwiftUI recreates the representable struct constantly; state living
        inside it would reset and flash the poster back over a playing video."""
        assert "@StateObject private var embedState = EmbedState()" in code_of(*PAGE)

    def test_retry_reloads_in_place(self):
        code = code_of(*EMBED)
        assert "coordinator.attempt != state.attempt" in code


# ─── Work is scoped to what is on screen ────────────────────────────────────

class TestOffScreenWorkIsBounded:

    def test_descriptors_are_fetched_in_a_window(self):
        assert "func window(around index: Int)" in code_of(*FEED)

    def test_in_flight_work_is_cancellable(self):
        code = code_of(*FEED)
        assert "tasks" in code and "cancelAll" in code

    def test_leaving_the_viewer_cancels_everything(self):
        assert "feed.cancelAll()" in code_of(*VIEWER)

    def test_the_web_view_is_torn_down_when_its_page_leaves(self):
        """A `WKWebView` is far heavier than an `AVPlayer`; a feed that
        accumulated them is evicted by the system within a dozen swipes."""
        code = code_of(*EMBED)
        assert "static func dismantleUIView" in code
        assert "webView.stopLoading()" in code


# ─── Instrumentation ─────────────────────────────────────────────────────────

class TestInstrumentation:

    def test_time_to_ready_is_measured(self):
        code = code_of(*EMBED)
        assert "readyMilliseconds" in code

    def test_retries_are_counted(self):
        assert "attempt" in code_of(*EMBED)

    def test_the_log_is_debug_only_and_carries_no_url(self):
        """Playback URLs carry a signed token."""
        code = read(*EMBED)
        log = code[code.index("private func log()"):]
        log = log[:log.index("}\n}")]
        assert "#if DEBUG" in log
        assert "url" not in log.lower().replace("readymilliseconds", "")
