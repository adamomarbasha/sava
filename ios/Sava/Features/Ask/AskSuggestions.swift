import SwiftUI

/// An opening question, and the surface that offers it.
///
/// The old opening was three fixed strings in a hairline list. It failed twice
/// over: the questions were the same on every open, and a plain list of text
/// rows gives the eye nothing to land on, so it read as a menu rather than an
/// invitation. Both are fixed here — the questions now come from the server
/// grounded in the user's own library, and each one is a card with a kind glyph
/// so the set can be scanned before it is read.

struct AskSuggestion: Decodable, Identifiable, Equatable {
    let text: String
    /// The category — creator, topic, collection, recent, and so on. Drives the
    /// glyph and its tint, so the list groups visually before it is read.
    let kind: String
    /// An SF Symbol name chosen by the server alongside the kind.
    let icon: String

    var id: String { text }

    /// A tint per kind, so the set groups by colour before a word is read.
    ///
    /// Every kind that can plausibly appear alongside another gets a different
    /// hue — two grey glyphs in one list is the same as no glyphs at all. Note
    /// that `SavaColor.accent` is deliberately *not* used: it is the primary
    /// action's fill and inverts to ink in light mode, which would silently
    /// turn a citron glyph black. `accentTint` is the mark-safe citron.
    var tint: Color {
        switch kind {
        case "recent":     return SavaColor.accentBlueText
        case "creator":    return SavaColor.coral
        case "topic":      return SavaColor.success
        case "collection": return SavaColor.accentTint
        case "synthesis":  return SavaColor.violet
        case "practical":  return SavaColor.success
        case "type":       return SavaColor.accentBlueText
        default:           return SavaColor.secondary
        }
    }
}


/// The opening state of a conversation: a lede, a set of cards, and a way to be
/// offered different ones.
struct AskOpeningView: View {
    let lede: String
    let suggestions: [AskSuggestion]
    let isLoading: Bool
    let onPick: (String) -> Void
    let onShuffle: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Bumped whenever a new set arrives, which re-keys the cards and replays
    /// their entrance. This is what makes the section "pop up" on every open
    /// rather than only the first.
    @State private var appearance = 0
    @State private var shown = false
    @State private var breathing = false

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            Text(lede)
                .font(SavaType.lede)
                .foregroundStyle(SavaColor.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if isLoading && suggestions.isEmpty {
                placeholders
            } else if !suggestions.isEmpty {
                cards
                shuffleButton
            }
        }
        .padding(.top, Space.s)
        .onChange(of: suggestions) { _, _ in replay() }
        .onAppear { replay() }
    }

    // MARK: Cards

    private var cards: some View {
        VStack(spacing: Space.s) {
            ForEach(Array(suggestions.enumerated()), id: \.element.id) { index, suggestion in
                Button {
                    Haptics.tap()
                    onPick(suggestion.text)
                } label: {
                    card(suggestion)
                }
                .buttonStyle(PressableStyle())
                // Staggered, so the set assembles rather than snapping in as a
                // block. 55ms is enough to read as sequence and short enough
                // that the last card is not a wait.
                .opacity(shown ? 1 : 0)
                .offset(y: shown ? 0 : 12)
                .animation(reduceMotion ? nil
                           : .spring(response: 0.42, dampingFraction: 0.86)
                               .delay(Double(index) * 0.055),
                           value: shown)
            }
        }
        .id(appearance)
    }

    private func card(_ suggestion: AskSuggestion) -> some View {
        HStack(spacing: Space.m) {
            Image(systemName: suggestion.icon)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(suggestion.tint)
                .frame(width: 30, height: 30)
                .background(suggestion.tint.opacity(0.12),
                            in: RoundedRectangle(cornerRadius: 9, style: .continuous))

            Text(suggestion.text)
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.primary)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)

            Spacer(minLength: Space.s)

            Image(systemName: "arrow.up.right")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(SavaColor.tertiary)
        }
        .padding(.horizontal, Space.m)
        .padding(.vertical, Space.m)
        .frame(minHeight: 60)
        .background(SavaColor.surface,
                    in: RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                .strokeBorder(SavaColor.hairline, lineWidth: 0.5))
        .contentShape(RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
    }

    // MARK: Shuffle

    /// Deliberately worded as an offer rather than a control. "Shuffle" would
    /// describe the mechanism; what the user wants is a different way in.
    private var shuffleButton: some View {
        Button {
            Haptics.tap()
            onShuffle()
        } label: {
            HStack(spacing: Space.xs) {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.system(size: 11, weight: .semibold))
                Text("Something else")
                    .font(SavaType.caption)
            }
            .foregroundStyle(SavaColor.tertiary)
            .padding(.vertical, Space.xs)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .opacity(shown ? 1 : 0)
        .animation(reduceMotion ? nil : Motion.standard.delay(0.3), value: shown)
        .accessibilityLabel("Suggest different questions")
    }

    // MARK: Loading

    /// Card-shaped, so nothing shifts when the real suggestions land.
    ///
    /// A slow breath rather than a sweeping shimmer: this is a sub-second wait
    /// on a surface that is about to fill with text, and a travelling highlight
    /// would draw more attention than the content it is standing in for.
    private var placeholders: some View {
        VStack(spacing: Space.s) {
            ForEach(0..<3, id: \.self) { _ in
                RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                    .fill(SavaColor.surface)
                    .frame(height: 60)
            }
        }
        .opacity(breathing ? 0.55 : 1)
        .animation(reduceMotion ? nil
                   : .easeInOut(duration: 0.9).repeatForever(autoreverses: true),
                   value: breathing)
        .onAppear { breathing = true }
        .accessibilityHidden(true)
    }

    private func replay() {
        shown = false
        appearance &+= 1
        guard !reduceMotion else { shown = true; return }
        // One runloop hop, so the reset is committed before the entrance runs.
        DispatchQueue.main.async { shown = true }
    }
}
