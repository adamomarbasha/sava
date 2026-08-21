import SwiftUI

/// The chrome over a playing item.
///
/// What is deliberately absent is most of it. There are no hearts, no comment
/// counts, no share rail, no follow button, no "AI" badge — none of that is
/// Sava's to show, because none of it is Sava's data. This is a private library,
/// so the only things worth saying over the video are whose it is, what it is,
/// where you are in your own feed, and the one action that is genuinely Sava's:
/// asking about it.
///
/// The result reads as a reader's viewer rather than a social one, which is the
/// whole point of it not being a TikTok clone.
struct ShortFormOverlay: View {
    let bookmark: Bookmark
    let positionLabel: String
    let progress: Double
    let isMuted: Bool
    let showsMute: Bool
    /// The scene's insets. The page deliberately ignores the safe area so video
    /// can be full-bleed, which means the chrome has to put it back by hand.
    let safeArea: EdgeInsets

    var onAsk: () -> Void
    var onMore: () -> Void
    /// Nil when the feed is a tab rather than a presented viewer — there is
    /// nothing to close, and a chevron that dismissed a tab would be a lie.
    var onClose: (() -> Void)?
    var onToggleMute: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            topBar
            Spacer(minLength: 0)
            bottomRow
        }
        .padding(.top, safeArea.top)
        .padding(.bottom, max(safeArea.bottom, Space.m))
        .background(alignment: .bottom) {
            // Legibility, not decoration: white text on an arbitrary video frame
            // is unreadable without it. Confined to the bottom third and capped
            // well below opaque so it never reads as a panel.
            LinearGradient(
                colors: [.clear, .black.opacity(0.35), .black.opacity(0.72)],
                startPoint: .top, endPoint: .bottom)
                .frame(height: 320)
                .allowsHitTesting(false)
        }
        .background(alignment: .top) {
            LinearGradient(colors: [.black.opacity(0.45), .clear],
                           startPoint: .top, endPoint: .bottom)
                .frame(height: 140)
                .allowsHitTesting(false)
        }
    }

    // MARK: Top

    /// Close only.
    ///
    /// Mute and More used to live up here, which put three controls and — in the
    /// Scroll tab — the lane switcher all in the same strip. They have moved to
    /// the bottom right, where the thumb already is on a phone this size and
    /// where they no longer compete with the row that says which feed you are
    /// looking at.
    private var topBar: some View {
        HStack(alignment: .center, spacing: Space.l) {
            if let onClose {
                glyphButton("chevron.down", label: "Close", action: onClose)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Space.screen)
        .padding(.top, safeArea.top > 0 ? Space.xs : Space.m)
    }

    /// Mute and More, stacked at the bottom right against the caption.
    private var sideControls: some View {
        VStack(spacing: Space.xs) {
            if showsMute {
                glyphButton(isMuted ? "speaker.slash.fill" : "speaker.wave.2.fill",
                            label: isMuted ? "Unmute" : "Mute", action: onToggleMute)
            }
            glyphButton("ellipsis", label: "More", action: onMore)
        }
    }

    /// No pill, no circle, no blurred capsule. A white glyph with a shadow is
    /// legible on any frame and adds no furniture to the screen.
    private func glyphButton(_ symbol: String, label: String,
                             action: @escaping () -> Void) -> some View {
        Button {
            Haptics.tap()
            action()
        } label: {
            Image(systemName: symbol)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(.white.opacity(0.92))
                .shadow(color: .black.opacity(0.45), radius: 6, y: 1)
                .frame(width: 44, height: 44)      // full touch target, no visible box
                .contentShape(Rectangle())
        }
        .accessibilityLabel(label)
    }

    // MARK: Bottom

    private var bottomBlock: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            // Where you are in *your* feed. A social app has no reason to show
            // this; a library does — it is the difference between an endless
            // scroll and a finite set of things you chose to keep. It sits here
            // rather than centred at the top because that is where the Dynamic
            // Island is, and the Island wins.
            Text(positionLabel)
                .font(SavaType.numeric)
                .foregroundStyle(.white.opacity(0.6))
                .lineLimit(1)

            if let creator = bookmark.displayCreator {
                Text(creator)
                    .font(SavaType.mediaTitle)
                    .foregroundStyle(.white)
            }

            if !bookmark.hasGenericTitle, let title = bookmark.title {
                Text(title)
                    .font(SavaType.callout)
                    .foregroundStyle(.white.opacity(0.86))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(spacing: Space.m) {
                Text(bookmark.platform.displayName)
                    .font(SavaType.meta)
                    .foregroundStyle(.white.opacity(0.55))

                Button {
                    Haptics.tap()
                    onAsk()
                } label: {
                    HStack(spacing: 5) {
                        Image(systemName: "text.bubble")
                            .font(.system(size: 11, weight: .semibold))
                        Text("Ask this")
                            .font(SavaType.caption)
                    }
                    .foregroundStyle(.white)
                    .padding(.horizontal, Space.m)
                    .padding(.vertical, 7)
                    .overlay(Capsule().stroke(.white.opacity(0.4), lineWidth: 1))
                }
                .accessibilityHint("Ask a question about this item")
            }
            .padding(.top, Space.xs)

            progressLine
                .padding(.top, Space.m)
        }
        .shadow(color: .black.opacity(0.5), radius: 8, y: 1)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Space.screen)
        .padding(.bottom, Space.l)
    }

    /// The caption and the controls share the bottom, aligned on their baseline
    /// rather than stacked: the caption is variable height, and anchoring the
    /// buttons to the bottom keeps them in the same place no matter how long the
    /// title runs.
    private var bottomRow: some View {
        HStack(alignment: .bottom, spacing: Space.m) {
            bottomBlock
            sideControls
                .padding(.trailing, Space.screen)
                // Clear of the progress line, which spans the full width.
                .padding(.bottom, Space.xl)
        }
    }

    /// A hairline, not a scrubber. The app draws separators at this weight
    /// everywhere else, so position reads as part of the page rather than as a
    /// media control bolted onto it.
    private var progressLine: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(.white.opacity(0.22))
                Capsule().fill(.white.opacity(0.9))
                    .frame(width: max(0, geo.size.width * progress))
            }
        }
        .frame(height: 2)
        .animation(.linear(duration: 0.25), value: progress)
        .accessibilityHidden(true)
    }
}
