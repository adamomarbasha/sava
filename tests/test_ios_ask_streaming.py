"""The iOS Ask client streams, and does not fake it.

Source-level assertions, in the style of `test_ios_shortcut.py`. The failure
modes here are silent and specific — an answer that repeats itself because
deltas were assigned instead of appended, a buffered response that undoes the
whole point, a timer-based reveal left switched on so real tokens get animated
twice — and none of them stop the app compiling.
"""
from __future__ import annotations

import pathlib
import re

import pytest

IOS = pathlib.Path(__file__).resolve().parent.parent / "ios"


def read(*parts: str) -> str:
    return IOS.joinpath(*parts).read_text(encoding="utf-8")


def code_of(*parts: str) -> str:
    return "\n".join(line for line in read(*parts).splitlines()
                     if not line.strip().startswith("///")
                     and not line.strip().startswith("//"))


ASK_VIEW = ("Sava", "Features", "Ask", "AskView.swift")
ASK_STREAM = ("Sava", "Core", "Networking", "AskStream.swift")
CLIENT = ("Sava", "Core", "Networking", "APIClient.swift")
STREAM_TEXT = ("Sava", "DesignSystem", "Components", "StreamingText.swift")


# ─── The transport ───────────────────────────────────────────────────────────

class TestTransport:

    def test_it_reads_bytes_not_a_buffered_body(self):
        """`data(for:)` collects the whole body before returning, which would
        deliver every token at once at the end — indistinguishable from the
        synchronous endpoint this replaces."""
        code = code_of(*CLIENT)
        assert "session.bytes(for: request)" in code
        stream = code[code.index("func stream("):]
        stream = stream[:stream.index("private func buildRequest")]
        assert "session.data(for:" not in stream

    def test_it_asks_for_an_event_stream(self):
        code = code_of(*CLIENT)
        assert 'setValue("text/event-stream", forHTTPHeaderField: "Accept")' in code

    def test_it_parses_sse_data_lines(self):
        code = code_of(*CLIENT)
        assert 'hasPrefix("data:")' in code

    def test_cancelling_the_task_tears_down_the_connection(self):
        """Cancellation must reach the server as a closed connection, which is
        what stops generation — there is no "stop" message to send."""
        code = code_of(*CLIENT)
        assert "continuation.onTermination" in code
        assert "task.cancel()" in code

    def test_a_streamed_error_status_maps_like_any_other(self):
        """A 402 mid-Ask should raise the upgrade prompt, not a generic error."""
        code = code_of(*CLIENT)
        assert "static func error(status: Int, data: Data) -> APIError" in code
        assert "Self.error(status: http.statusCode, data: body)" in code
        assert "upgradeRequired" in code

    def test_the_stream_timeout_is_longer_than_a_json_call(self):
        code = code_of(*CLIENT)
        assert "endpoint.timeout ?? 120" in code


# ─── The protocol ────────────────────────────────────────────────────────────

class TestEventModel:

    def test_every_server_event_is_handled(self):
        code = code_of(*ASK_STREAM)
        for event in ("meta", "sources", "status", "token", "done", "error"):
            assert f'case "{event}"' in code, event

    def test_done_decodes_with_the_existing_answer_model(self):
        """A parallel model would drift from the non-streaming response and
        silently drop sources, citations or the visual flags."""
        assert "try AskAnswer(from: decoder)" in code_of(*ASK_STREAM)

    def test_an_unknown_event_does_not_kill_the_answer(self):
        """A future server may add a progress event this build has never heard
        of; dropping the answer half-way through would be the worse failure."""
        code = code_of(*ASK_STREAM)
        assert "try? decoder.decode(AskEvent.self" in code
        assert "continue" in code

    def test_both_scopes_stream_through_one_architecture(self):
        code = code_of(*ASK_STREAM)
        assert "api/ask/stream" in code
        assert "ask/stream" in code and "bookmarks/" in code
        assert "func askSavaStream" in code and "func askThisStream" in code


# ─── Rendering ───────────────────────────────────────────────────────────────

class TestRendering:

    def test_tokens_are_appended_not_assigned(self):
        """The classic streaming bug: assigning each delta shows a growing echo
        of the answer instead of the answer."""
        code = code_of(*ASK_VIEW)
        assert "turns[i].answer += delta" in code
        assert "turns[i].answer = delta" not in code

    def test_the_answer_is_mutable_so_it_can_grow(self):
        code = code_of(*ASK_VIEW)
        turn = code[code.index("struct Turn: Identifiable"):]
        turn = turn[:turn.index("}")]
        assert "var answer: String" in turn
        assert "let answer: String" not in turn

    def test_the_timer_based_reveal_is_off_for_streamed_answers(self):
        """`StreamingText` reveals a *finished* string word by word. With real
        deltas arriving it would animate an animation, holding the last words
        behind a timer unrelated to the model."""
        code = code_of(*ASK_VIEW)
        assert "animates: false" in code
        assert "animates: turn.isNew" not in code

    def test_nothing_reveals_a_finished_string_in_the_ask_transcript(self):
        code = code_of(*ASK_VIEW)
        assert "StreamingText(text: turn.answer" in code
        block = code[code.index("StreamingText(text: turn.answer"):]
        assert "animates: false" in block[:200]

    def test_a_caret_marks_an_answer_still_arriving(self):
        assert "StreamingCaret" in code_of(*ASK_VIEW)
        assert "struct StreamingCaret" in code_of(*STREAM_TEXT)

    def test_the_caret_does_not_outlive_its_view(self):
        """`repeatForever` keeps a render loop alive behind a finished chat."""
        code = code_of(*STREAM_TEXT)
        caret = code[code.index("struct StreamingCaret"):]
        assert "TimelineView" in caret
        assert "repeatForever" not in caret

    def test_the_scroll_follows_a_growing_answer(self):
        """`turns.count` fires once, when the empty turn is appended. Without
        watching the text the answer writes itself off the bottom of the
        screen."""
        code = code_of(*ASK_VIEW)
        assert "onChange(of: turns.last?.answer)" in code


# ─── Conversation behaviour ──────────────────────────────────────────────────

class TestBehaviour:

    def test_the_question_appears_before_any_request(self):
        code = code_of(*ASK_VIEW)
        ask = code[code.index("private func ask(_ text: String)"):]
        ask = ask[:ask.index("private func stream(")]
        assert "pending = trimmed" in ask
        assert ask.index("pending = trimmed") < ask.index("askTask = Task")

    def test_a_thinking_state_is_shown_before_the_first_token(self):
        code = code_of(*ASK_VIEW)
        assert "workingLine" in code
        assert "ThinkingDots()" in code

    def test_the_thread_is_captured_from_the_first_frame(self):
        """So a retry after a failure continues the conversation rather than
        orphaning it."""
        code = code_of(*ASK_VIEW)
        assert "case .meta(let id)" in code
        assert "threadID = id" in code

    def test_cancelling_keeps_what_was_already_written(self):
        """The user watched those words appear; deleting them looks like a bug,
        not like a cancellation."""
        code = code_of(*ASK_VIEW)
        assert "func finishCancelled" in code
        assert "isStreaming = false" in code

    def test_a_failure_keeps_a_partial_answer_and_offers_retry(self):
        code = code_of(*ASK_VIEW)
        assert "lastQuestion" in code
        assert "errorMessage" in code

    def test_a_server_error_frame_and_a_transport_error_land_together(self):
        code = code_of(*ASK_VIEW)
        assert "struct AskStreamFailure" in code
        assert "case .failed(let message" in code

    def test_an_empty_answer_is_not_left_as_an_empty_bubble(self):
        code = code_of(*ASK_VIEW)
        assert "func discard" in code

    def test_a_visual_status_is_surfaced_rather_than_freezing(self):
        code = code_of(*ASK_VIEW)
        assert "case .status(let message" in code
        assert "statusMessage" in code

    def test_the_old_blocking_calls_are_no_longer_used_by_the_view(self):
        """`askSava`/`askThis` remain on the service for non-streaming callers,
        but the transcript must not go through them."""
        code = code_of(*ASK_VIEW)
        assert "intelligence.askSava(" not in code
        assert "intelligence.askThis(" not in code
        assert "askSavaStream(" in code and "askThisStream(" in code
