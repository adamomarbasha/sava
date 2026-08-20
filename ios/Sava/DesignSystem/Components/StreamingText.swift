import SwiftUI

/// Text that arrives the way it was written.
///
/// When Sava has just generated something — a summary being read for the first
/// time, an answer that has only this second come back — it appears
/// progressively instead of snapping in fully formed. That reads as thinking
/// finishing rather than as a page loading, and it is the difference between an
/// app that feels alive and one that feels like a database view.
///
/// Three rules keep it from becoming a gimmick:
///
///   * It reveals by word, not by letter. A terminal-style typewriter is a
///     costume; word-level reveal is what generation actually looks like.
///   * It happens **once**. Content that already existed renders instantly, with
///     no artificial delay — nobody should wait for an animation to re-watch
///     text they read yesterday.
///   * Reduce Motion turns it off entirely.
///
/// Rendering goes through `ProseView` throughout, so markdown, bullets and
/// citations look identical mid-reveal and after it.
struct StreamingText: View {
    let text: String
    var font: Font = SavaType.body
    /// True only for content generated in this moment.
    var animates: Bool = false
    var onFinish: (() -> Void)? = nil

    @State private var revealed = ""
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ProseView(text: revealed, font: font)
            .task(id: text) { await reveal() }
            // Mid-reveal the text is partial, which is meaningless to read
            // aloud; VoiceOver gets the finished text and nothing else.
            .accessibilityLabel(text)
    }

    private func reveal() async {
        let words = text.split(separator: " ", omittingEmptySubsequences: false)

        // Short answers have nothing to reveal — "Yeah, pretty common" streaming
        // in would be theatre. Below the threshold, and whenever the content is
        // not new, it simply appears.
        guard animates, !reduceMotion, words.count > 14 else {
            revealed = text
            onFinish?()
            return
        }

        // Longer answers take bigger steps so a paragraph and an essay both
        // finish in roughly the same couple of seconds.
        let steps = 55
        let stride = max(1, Int((Double(words.count) / Double(steps)).rounded(.up)))

        revealed = ""
        var index = 0
        while index < words.count {
            index = min(words.count, index + stride)
            revealed = words[0..<index].joined(separator: " ")
            try? await Task.sleep(for: .milliseconds(32))
            if Task.isCancelled {
                revealed = text
                return
            }
        }
        revealed = text
        onFinish?()
    }
}

/// Remembers which summaries have already been watched appearing.
///
/// The server tells us whether it generated a summary just now or served a
/// cached one, which is the right signal nearly all the time. This covers the
/// gap: reopening an item in the same session, before the client has re-fetched
/// and seen `cached: true`, must not replay the animation.
enum SummaryRevealLog {
    private static let key = "sava.revealedSummaries"

    static func hasSeen(_ id: Int) -> Bool {
        Set(UserDefaults.standard.array(forKey: key) as? [Int] ?? []).contains(id)
    }

    static func markSeen(_ id: Int) {
        var ids = UserDefaults.standard.array(forKey: key) as? [Int] ?? []
        guard !ids.contains(id) else { return }
        ids.append(id)
        // Bounded: this is a hint, not a record worth growing for ever.
        if ids.count > 500 { ids.removeFirst(ids.count - 500) }
        UserDefaults.standard.set(ids, forKey: key)
    }
}

/// The "working on it" state for a conversation.
///
/// Three dots breathing in sequence. It replaces a spinner and a sentence of
/// narration, because during the second or two an answer takes, the only thing
/// worth communicating is that something is happening.
struct ThinkingDots: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase = 0

    private let timer = Timer.publish(every: 0.28, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(SavaColor.tertiary)
                    .frame(width: 6, height: 6)
                    .opacity(reduceMotion ? 0.6 : (phase == index ? 1 : 0.28))
                    .scaleEffect(reduceMotion ? 1 : (phase == index ? 1.15 : 0.9))
            }
        }
        .animation(.easeInOut(duration: 0.28), value: phase)
        .onReceive(timer) { _ in
            guard !reduceMotion else { return }
            phase = (phase + 1) % 3
        }
        .accessibilityHidden(true)
    }
}
