import SwiftUI

/// The Scroll tab — the feed itself, not a menu that leads to one.
///
/// The first version of this screen was a list of lanes you tapped to open a
/// viewer. That is a table of contents in front of the content: two taps and a
/// full-screen transition before anything plays, on a tab whose entire purpose
/// is that something is already playing. A tab called Scroll that does not
/// scroll anything is a menu wearing the wrong label.
///
/// So the tab *is* the feed. It opens on everything, plays immediately, and the
/// choice that used to be the whole screen is now a row of labels across the
/// top — the same shape every short-form app uses, because it is the right one:
/// it keeps the video full-bleed, costs one tap, and never hides what you are
/// watching behind a decision.
struct ScrollHomeView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var model: LibraryViewModel

    /// Nil is everything. Reset per launch rather than persisted: "all of it"
    /// is the right thing to open on, and a filter silently remembered from
    /// last week is how a feed appears to have lost most of its videos.
    @State private var lane: Platform?

    private var service: BookmarkService { BookmarkService(client: session.api) }

    private var playable: [Bookmark] { model.all.filter(\.isShortForm) }

    private func items(for platform: Platform?) -> [Bookmark] {
        guard let platform else { return playable }
        return playable.filter { $0.platform == platform }
    }

    /// The platforms with something in them, in a fixed order.
    ///
    /// Fixed rather than sorted by count, so the row does not rearrange itself
    /// between launches. Empty ones are dropped — a lane with nothing behind it
    /// is a tab that opens onto a blank screen.
    private var lanes: [Platform] {
        [.youtube, .tiktok, .instagram].filter { !items(for: $0).isEmpty }
    }

    private var current: [Bookmark] { items(for: lane) }

    var body: some View {
        ZStack(alignment: .top) {
            Color.black.ignoresSafeArea()

            if let first = current.first {
                ShortFormViewer(bookmarks: current,
                                start: first,
                                source: lane.map { .platform($0) } ?? .library,
                                service: PlaybackService(client: session.api),
                                chrome: .embedded)
                    // Switching lane is a different feed, so it gets a different
                    // viewer: the identity change tears the old pool down and
                    // builds the new one, which is exactly what has to happen.
                    // Trying to mutate one feed's items in place would leave
                    // players alive for videos no longer in it.
                    .id(lane)
                    .transition(.opacity)
            } else {
                empty
            }

            // Above the viewer in the ZStack so it stays put while pages move
            // underneath, and outside its `.id` so it does not get rebuilt on
            // every lane change.
            if lanes.count > 1 {
                LaneSwitcher(lanes: lanes, selection: $lane)
            }
        }
        .animation(Motion.gentle, value: lane)
        .toolbar(.hidden, for: .navigationBar)
        .task { await model.loadIfNeeded(service) }
    }

    private var empty: some View {
        VStack(spacing: Space.m) {
            Image(systemName: "play.square.stack")
                .font(.system(size: 30, weight: .light))
                .foregroundStyle(.white.opacity(0.35))
            Text("Nothing to scroll yet")
                .font(SavaType.title)
                .foregroundStyle(.white)
            Text("Save a short, a Reel or a TikTok and it will play here.")
                .font(SavaType.callout)
                .foregroundStyle(.white.opacity(0.6))
                .multilineTextAlignment(.center)
        }
        .padding(.horizontal, Space.xxl)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}


/// The lane selector that sits over the feed.
///
/// Text on the video rather than a control on a bar. Anything with a fill — a
/// segmented control, a row of chips — puts an opaque panel across the top of
/// a full-bleed video, which is the one thing this screen is trying to avoid.
/// Labels with a shadow stay legible on any frame and take up no space when the
/// eye is not looking for them.
private struct LaneSwitcher: View {
    let lanes: [Platform]
    @Binding var selection: Platform?

    @Namespace private var underline
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: Space.l) {
            label(nil, title: "All")
            ForEach(lanes, id: \.self) { label($0, title: Self.laneTitle($0)) }
        }
        // Hard against the top of the safe area, the way every short-form app
        // puts its feed selector. It can sit here now because mute and More
        // moved to the bottom right — previously they occupied this strip and
        // the switcher had to be pushed down a full touch target to clear them.
        .padding(.horizontal, Space.screen)
        .padding(.top, Space.xs)
        .frame(maxWidth: .infinity)
        .background(alignment: .top) {
            // Just enough to keep white type off a white frame.
            LinearGradient(colors: [.black.opacity(0.5), .clear],
                           startPoint: .top, endPoint: .bottom)
                .frame(height: 120)
                .allowsHitTesting(false)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Feed")
    }

    /// Named for the format, not the platform.
    ///
    /// "Reels" and "Shorts" are what these are actually called, they are what
    /// the user searched for when they saved them, and they are shorter — which
    /// matters in a row that has to fit between two toolbar buttons.
    static func laneTitle(_ platform: Platform) -> String {
        switch platform {
        case .youtube:   return "Shorts"
        case .tiktok:    return "TikTok"
        case .instagram: return "Reels"
        default:         return platform.displayName
        }
    }

    private func label(_ platform: Platform?, title: String) -> some View {
        let selected = selection == platform
        return Button {
            guard !selected else { return }
            Haptics.select()
            selection = platform
        } label: {
            VStack(spacing: 5) {
                Text(title)
                    .font(.system(size: 15, weight: selected ? .bold : .medium))
                    .foregroundStyle(.white.opacity(selected ? 1 : 0.6))

                // The one piece of colour: citron, so the active lane is Sava's
                // rather than the platform's. A brand-coloured underline would
                // make the app look like it belongs to whoever made the video.
                Group {
                    if selected {
                        Capsule()
                            .fill(SavaColor.accentTint)
                            .frame(height: 2.5)
                            .matchedGeometryEffect(id: "lane", in: underline)
                    } else {
                        Color.clear.frame(height: 2.5)
                    }
                }
            }
            .shadow(color: .black.opacity(0.5), radius: 5, y: 1)
            .contentShape(Rectangle())
            .padding(.vertical, Space.xs)
        }
        .buttonStyle(.plain)
        .animation(reduceMotion ? nil : Motion.tap, value: selection)
        .accessibilityLabel(title)
        .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
    }
}
