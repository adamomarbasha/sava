import SwiftUI

/// Stage 3 — you do not have to remember where you saw it.
///
/// A query types itself, the library *visibly narrows* to the one card that
/// matches, and then Ask answers a question about it. The narrowing is the
/// point: a search box that simply produces a result teaches nothing, whereas
/// watching six cards dim and one survive shows what Sava is doing.
///
/// ── Local, and deliberately so ──────────────────────────────────────────
///
/// Nothing here touches the network. `DemoLibrary.matches` does the filtering
/// and the Ask answer is written down. Calling the real endpoints on this
/// screen would spend the user's Ask allowance before they have agreed to
/// anything, require a signed-in session and a warm backend during the exact
/// minute both are least likely, and risk answering differently — or not at all
/// — on the one screen whose whole job is to show what Ask does.
struct FindDemo: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var typed = ""
    @State private var phase: Phase = .idle
    @State private var runID = 0

    private enum Phase: Int, Comparable {
        case idle, typing, filtered, asked, answered
        static func < (a: Phase, b: Phase) -> Bool { a.rawValue < b.rawValue }
    }

    private let query = DemoLibrary.searchQuery
    private var target: DemoItem { DemoLibrary.searchTarget }

    /// Six of the seven, so the grid stays even and the target is not the last
    /// card in reading order — surviving in the middle is more legible than
    /// surviving at the end.
    private var shelf: [DemoItem] { Array(DemoLibrary.all.prefix(6)) }

    private var survivors: Set<String> {
        guard phase >= .filtered else { return Set(shelf.map(\.id)) }
        return Set(DemoLibrary.matches(query, in: shelf).map(\.id))
    }

    var body: some View {
        // Space.m rather than Space.l: this stage carries a field, six cards
        // and an Ask exchange, and the answer is the payoff — it may not be the
        // thing that falls off the bottom.
        VStack(alignment: .leading, spacing: Space.m) {
            searchField
            shelfGrid
            if phase >= .asked { askExchange }
            replay
        }
        .onAppear { restart() }
        .onDisappear { runID &+= 1 }
    }

    // MARK: Search

    private var searchField: some View {
        HStack(spacing: Space.s) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(SavaColor.tertiary)
            Text(typed.isEmpty ? " " : typed)
                .font(SavaType.body)
                .foregroundStyle(SavaColor.primary)
                .lineLimit(1)
            // A caret, so an empty field reads as "about to be typed in" rather
            // than as a broken control.
            if phase == .typing && !reduceMotion {
                Caret()
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Space.m)
        .frame(height: 46)
        .background(SavaColor.fill, in: RoundedRectangle(cornerRadius: Radius.control,
                                                         style: .continuous))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Search your library for “\(query)”")
    }

    // MARK: The library

    private var shelfGrid: some View {
        GeometryReader { geo in
            let columns = 3
            let gap = Space.s
            let width = (geo.size.width - gap * CGFloat(columns - 1)) / CGFloat(columns)
            LazyVGrid(columns: Array(repeating: GridItem(.fixed(width), spacing: gap),
                                     count: columns),
                      spacing: gap) {
                ForEach(shelf) { item in
                    let alive = survivors.contains(item.id)
                    DemoCard(item: item, width: width, isMuted: !alive)
                        .scaleEffect(alive && phase >= .filtered ? 1 : 0.94)
                        .animation(Motion.respecting(Motion.standard, reduceMotion)
                            .delay(Double(shelf.firstIndex(of: item) ?? 0) * 0.035),
                                   value: phase)
                }
            }
        }
        .frame(height: shelfHeight)
    }

    /// Three columns of 4:5 cards, plus the row gap. Computed rather than
    /// guessed so the grid does not clip at large Dynamic Type sizes, where the
    /// surrounding text grows but the cards do not.
    private var shelfHeight: CGFloat {
        let width = (UIScreen.main.bounds.width - Space.screen * 2 - Space.s * 2) / 3
        return (width / (4.0 / 5.0)) * 2 + Space.s
    }

    // MARK: Ask

    private var askExchange: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            SectionHeader(text: "Then ask about it")
            HStack {
                Spacer(minLength: Space.xl)
                Text(DemoLibrary.askQuestion)
                    .font(SavaType.callout)
                    .foregroundStyle(SavaColor.onAccent)
                    .padding(.horizontal, Space.m)
                    .padding(.vertical, Space.s)
                    .background(SavaColor.accent, in: RoundedRectangle(
                        cornerRadius: Radius.control, style: .continuous))
            }
            if phase >= .answered {
                Text(DemoLibrary.askAnswer)
                    .font(SavaType.prose)
                    .foregroundStyle(SavaColor.primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, Space.m)
                    .padding(.vertical, Space.s)
                    .background(SavaColor.fill, in: RoundedRectangle(
                        cornerRadius: Radius.control, style: .continuous))
                    .transition(.opacity)
            } else {
                TypingDots().padding(.leading, Space.m)
            }
        }
        .transition(.opacity)
        .accessibilityElement(children: .contain)
    }

    private var replay: some View {
        Button {
            Haptics.tap()
            restart()
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "arrow.counterclockwise")
                    .font(.system(size: 11, weight: .semibold))
                Text("Play again").font(SavaType.caption)
            }
            .foregroundStyle(SavaColor.accentBlueText)
            .frame(minHeight: 44)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Play the search demonstration again")
    }

    // MARK: Sequencing

    private func restart() {
        runID &+= 1
        let id = runID
        typed = ""
        phase = .idle

        guard !reduceMotion else {
            // The finished state: query in the field, library filtered, answer
            // present. Everything the animation would have said, at once.
            typed = query
            phase = .answered
            return
        }

        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(350))
            guard id == runID else { return }
            phase = .typing
            for character in query {
                try? await Task.sleep(for: .milliseconds(52))
                guard id == runID else { return }
                typed.append(character)
            }
            try? await Task.sleep(for: .milliseconds(240))
            guard id == runID else { return }
            withAnimation(Motion.standard) { phase = .filtered }

            try? await Task.sleep(for: .milliseconds(900))
            guard id == runID else { return }
            withAnimation(Motion.standard) { phase = .asked }

            try? await Task.sleep(for: .milliseconds(1100))
            guard id == runID else { return }
            withAnimation(Motion.standard) { phase = .answered }
        }
    }
}

// MARK: - Small parts

/// A blinking caret. Driven by a timeline rather than `repeatForever` so it
/// stops with the view instead of outliving it.
private struct Caret: View {
    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.5)) { timeline in
            let on = Int(timeline.date.timeIntervalSinceReferenceDate * 2) % 2 == 0
            RoundedRectangle(cornerRadius: 1)
                .fill(SavaColor.accent)
                .frame(width: 2, height: 19)
                .opacity(on ? 1 : 0)
        }
        .accessibilityHidden(true)
    }
}

/// Three dots while the demo answer is "being written".
private struct TypingDots: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.28)) { timeline in
            let tick = Int(timeline.date.timeIntervalSinceReferenceDate / 0.28)
            HStack(spacing: 4) {
                ForEach(0..<3, id: \.self) { i in
                    Circle()
                        .fill(SavaColor.tertiary)
                        .frame(width: 5, height: 5)
                        .opacity(reduceMotion ? 0.6 : (tick % 3 == i ? 1 : 0.3))
                }
            }
        }
        .accessibilityLabel("Sava is answering")
    }
}
