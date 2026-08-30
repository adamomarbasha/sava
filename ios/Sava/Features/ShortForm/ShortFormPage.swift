import AVFoundation
import SwiftUI

/// One full-screen item in the swipe feed: the media, then Sava's chrome over it.
struct ShortFormPage: View {
    let bookmark: Bookmark
    let descriptor: PlaybackDescriptor?
    let isLoading: Bool
    let isCurrent: Bool
    let positionLabel: String
    /// The scene's insets, measured before the page opted out of them.
    let safeArea: EdgeInsets

    @ObservedObject var pool: ShortPlayerPool

    var onAsk: () -> Void
    var onMore: () -> Void
    var onClose: (() -> Void)?

    @State private var progress: Double = 0
    @State private var showPlayGlyph = false
    /// One per page, and owned here rather than inside `EmbedPlayer`: SwiftUI
    /// recreates the representable struct on every parent redraw, so state
    /// living in it would reset and flash the poster back over a playing video.
    @StateObject private var embedState = EmbedState()

    var body: some View {
        ZStack {
            Color.black

            stage
                .allowsHitTesting(descriptor?.kind == .gallery)

            ShortFormOverlay(
                bookmark: bookmark,
                positionLabel: positionLabel,
                progress: progress,
                isMuted: pool.isMuted,
                showsMute: descriptor?.kind != .gallery,
                safeArea: safeArea,
                onAsk: onAsk,
                onMore: onMore,
                onClose: onClose,
                onToggleMute: { pool.toggleMute() }
            )

            if showPlayGlyph {
                Image(systemName: pool.isPlaying(bookmark.id) ? "play.fill" : "pause.fill")
                    .font(.system(size: 44, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.9))
                    .shadow(color: .black.opacity(0.4), radius: 12)
                    .transition(.opacity.combined(with: .scale(scale: 0.85)))
                    .allowsHitTesting(false)
            }
        }
        .contentShape(Rectangle())
        .onTapGesture { tapToToggle() }
        .onChange(of: isCurrent) { _, current in if !current { progress = 0 } }
    }

    // MARK: Stage

    @ViewBuilder private var stage: some View {
        switch descriptor?.kind {
        case .video:
            if let url = descriptor?.url {
                VideoStage(bookmark: bookmark, url: url, isCurrent: isCurrent,
                           pool: pool, progress: $progress)
            } else {
                ShortFormUnavailable(bookmark: bookmark, reason: nil)
            }

        case .embed:
            if let url = descriptor?.url {
                EmbedStage(bookmark: bookmark, url: url,
                           aspect: descriptor?.aspect ?? bookmark.mediaAspect,
                           isCurrent: isCurrent, pool: pool, state: embedState)
            } else {
                ShortFormUnavailable(bookmark: bookmark, reason: nil)
            }

        case .gallery:
            ShortFormGallery(images: descriptor?.images ?? [], isCurrent: isCurrent)

        case .unavailable:
            ShortFormUnavailable(bookmark: bookmark, reason: descriptor?.reason)

        case nil:
            // The poster is already in the image cache from the grid, so this
            // is not a black screen with a spinner on it — it is the frame the
            // user tapped, holding still until the video is ready behind it.
            ShortFormPoster(bookmark: bookmark, showsSpinner: isLoading)
        }
    }

    private func tapToToggle() {
        guard descriptor?.kind == .video else { return }
        pool.togglePlayback(bookmark.id)
        Haptics.tap()
        withAnimation(Motion.gentle) { showPlayGlyph = true }
        Task {
            try? await Task.sleep(nanoseconds: 550_000_000)
            withAnimation(Motion.gentle) { showPlayGlyph = false }
        }
    }
}

// MARK: - Embed

/// The web-view stage: poster underneath, player over it, failure in front.
///
/// Mirrors `VideoStage` deliberately. Both hold the item's poster until their
/// decoder has something real to show, and both keep the poster *under* the
/// player rather than instead of it, so the handover has nothing to flicker
/// through. The embed path simply had none of this — it put an opaque black web
/// view on screen and hoped.
private struct EmbedStage: View {
    let bookmark: Bookmark
    let url: URL
    let aspect: CGFloat
    let isCurrent: Bool
    @ObservedObject var pool: ShortPlayerPool
    @ObservedObject var state: EmbedState

    var body: some View {
        ZStack {
            // Held under the player, not instead of it, so the transition from
            // the grid is continuous and a slow embed shows the frame the user
            // tapped rather than a hole.
            if state.phase.showsPoster {
                ShortFormPoster(bookmark: bookmark,
                                showsSpinner: !state.phase.isTerminalFailure)
            }

            // Sized to the item's own ratio rather than filled, so a Short is
            // not cropped and a mis-tagged landscape video is not blown up past
            // the edges of the screen.
            //
            // Kept mounted through a retryable failure: tearing the web view
            // down would discard a page that may still be mid-recovery, and
            // remounting is what makes a retry feel like a reload rather than a
            // navigation.
            if !isUnavailable {
                EmbedPlayer(url: url, isActive: isCurrent,
                            isMuted: pool.isMuted, state: state)
                    .aspectRatio(aspect, contentMode: .fit)
                    .opacity(state.phase == .ready ? 1 : 0)
                    .allowsHitTesting(state.phase == .ready)
            }

            switch state.phase {
            case .unavailable(let why):
                ShortFormUnavailable(bookmark: bookmark, reason: why)
            case .failed(let why):
                ShortFormRetry(bookmark: bookmark, reason: why) { state.retry() }
            default:
                EmptyView()
            }
        }
    }

    private var isUnavailable: Bool {
        if case .unavailable = state.phase { return true }
        return false
    }
}

/// A failure that might not recur, with the one control that helps.
///
/// Distinct from `ShortFormUnavailable`, which is for permanent facts about the
/// content ("the creator doesn't allow this to play outside YouTube"). Offering
/// Retry there would be a button that cannot work; withholding it here would
/// strand somebody whose train went into a tunnel.
private struct ShortFormRetry: View {
    let bookmark: Bookmark
    let reason: String
    let onRetry: () -> Void

    var body: some View {
        ZStack {
            MediaImage(url: bookmark.imageURL, fallback: .transparent,
                       fit: .fitOnBackdrop, cornerRadius: 0)
                .overlay(Color.black.opacity(0.68))

            VStack(spacing: Space.l) {
                Image(systemName: "arrow.trianglehead.clockwise")
                    .font(.system(size: 28, weight: .regular))
                    .foregroundStyle(.white.opacity(0.85))
                Text(reason)
                    .font(SavaType.callout)
                    .foregroundStyle(.white.opacity(0.9))
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                Button(action: onRetry) {
                    Text("Try again")
                        .font(SavaType.caption)
                        .foregroundStyle(SavaColor.onAccent)
                        .padding(.horizontal, Space.xl)
                        .frame(minHeight: 44)
                        .background(SavaColor.accent, in: Capsule())
                }
                .buttonStyle(.plain)
            }
            .screenPadding()
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(reason) Double tap Try again to reload.")
    }
}

// MARK: - Video

/// The `AVPlayer` stage. Owns nothing — the player belongs to the pool — and is
/// responsible only for attaching the layer and reporting progress.
private struct VideoStage: View {
    let bookmark: Bookmark
    let url: URL
    let isCurrent: Bool
    @ObservedObject var pool: ShortPlayerPool
    @Binding var progress: Double

    @State private var observer: Any?
    @State private var observed: AVPlayer?
    @State private var hasFirstFrame = false

    /// Looked up, never created. The pool decides which items have players; a
    /// page only draws whichever one it is given, and shows the poster until
    /// then.
    private var player: AVPlayer? { pool.existingPlayer(for: bookmark.id) }

    /// Always fit. Never fill.
    ///
    /// This was `.resizeAspectFill` for anything vertical, on the reasoning that
    /// vertical video "should fill a vertical screen". It does not: a 9:16 clip
    /// is 0.5625 wide-over-tall and a modern iPhone is about 0.46, so filling
    /// scales the video until its height matches and then throws away roughly a
    /// fifth of its width — which is the "slightly zoomed in" look, and it cuts
    /// off captions and faces near the edges.
    ///
    /// Fitting shows the frame the creator actually composed. The leftover space
    /// is absorbed by the stage's own black canvas, which is what every video
    /// player does and the least distracting treatment available here — a blur
    /// behind moving video would be both expensive and noisy.
    private var gravity: AVLayerVideoGravity { .resizeAspect }

    var body: some View {
        ZStack {
            if !hasFirstFrame {
                // Held under the video, not instead of it: the poster is what
                // makes the transition from the grid feel continuous.
                ShortFormPoster(bookmark: bookmark, showsSpinner: true)
            }
            if let player {
                PlayerSurface(player: player, gravity: gravity)
                    .opacity(hasFirstFrame ? 1 : 0)
            }
        }
        .onAppear { attach() }
        .onDisappear { detach() }
        .onChange(of: isCurrent) { _, _ in attach() }
        // The player arrives after the pool reconciles, which is usually after
        // this page first drew. Without this the time observer would never be
        // attached and the page would sit on its poster for ever.
        .onChange(of: pool.existingPlayer(for: bookmark.id) != nil) { _, _ in attach() }
    }

    private func attach() {
        detach()
        guard let target = player else { return }
        observed = target
        observer = target.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 0.25, preferredTimescale: 600), queue: .main
        ) { time in
            if !hasFirstFrame, time.seconds > 0 {
                withAnimation(Motion.gentle) { hasFirstFrame = true }
            }
            guard let duration = target.currentItem?.duration.seconds,
                  duration.isFinite, duration > 0 else { return }
            progress = min(1, max(0, time.seconds / duration))
        }
    }

    /// Removed from the player it was added to, which is not necessarily the
    /// one the pool would hand out now — the pool recycles instances, and
    /// removing an observer from the wrong player traps.
    private func detach() {
        if let observer, let observed { observed.removeTimeObserver(observer) }
        observer = nil
        observed = nil
    }
}

// MARK: - Gallery

/// A TikTok photo post.
///
/// The point of this branch is that it exists. A carousel routed through the
/// video path is a page that buffers forever and never plays — so instead of a
/// broken video the user gets the thing they actually saved: the creator's
/// images, in the creator's order, starting on the cover they chose.
private struct ShortFormGallery: View {
    let images: [PlaybackDescriptor.GalleryImage]
    let isCurrent: Bool

    @State private var index = 0

    var body: some View {
        // The pager fills the whole stage rather than sitting in a stack with
        // the indicator. Every slide therefore gets an identical box, so paging
        // between a 4:5 photo and a 9:16 one does not resize the viewport
        // underneath the user's thumb — the picture changes shape inside a
        // container that does not.
        TabView(selection: $index) {
            ForEach(Array(images.enumerated()), id: \.offset) { offset, image in
                MediaImage(url: image.url, fallback: .transparent,
                           fit: .fitOnBackdrop, cornerRadius: 0)
                    .tag(offset)
            }
        }
        .tabViewStyle(.page(indexDisplayMode: .never))
        // The horizontal pager and the feed's vertical pager are different
        // axes, so they coexist without a custom gesture recogniser: a
        // sideways drag pages the gallery, a vertical one moves to the next
        // item. Overlaying the indicator instead of stacking it is what keeps
        // that true — a `VStack` would shrink the pager and leave a strip at
        // the bottom where a vertical drag lands on nothing.
        .overlay(alignment: .bottom) {
            if images.count > 1 {
                pageIndicator
                    .padding(.bottom, 150)
                    .allowsHitTesting(false)
            }
        }
        .onChange(of: isCurrent) { _, current in if !current { index = 0 } }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Gallery, \(images.count) images")
    }

    /// Sava's own marker rather than the system dots: a row of hairlines reads
    /// as pages of a set, and stays legible at twelve slides where dots stop
    /// being countable.
    private var pageIndicator: some View {
        HStack(spacing: 4) {
            ForEach(images.indices, id: \.self) { slide in
                Capsule()
                    .fill(.white.opacity(slide == index ? 0.95 : 0.3))
                    .frame(width: slide == index ? 18 : 10, height: 2)
            }
        }
        .animation(Motion.gentle, value: index)
        .shadow(color: .black.opacity(0.4), radius: 4)
    }
}

// MARK: - Fallback states

/// The saved thumbnail, filling the stage. Used before playback resolves and
/// whenever it cannot.
private struct ShortFormPoster: View {
    let bookmark: Bookmark
    var showsSpinner: Bool = false

    var body: some View {
        ZStack {
            MediaImage(url: bookmark.imageURL, fallback: .transparent,
                       fit: .fitOnBackdrop, cornerRadius: 0)
            if showsSpinner {
                ProgressView()
                    .tint(.white.opacity(0.7))
                    .scaleEffect(0.9)
            }
        }
    }
}

/// Said plainly, over the thumbnail, with a way out. A viewer that spins
/// forever is worse than one that admits it cannot play something.
private struct ShortFormUnavailable: View {
    let bookmark: Bookmark
    let reason: String?

    @Environment(\.openURL) private var openURL

    var body: some View {
        ZStack {
            MediaImage(url: bookmark.imageURL, fallback: .transparent,
                       fit: .fitOnBackdrop, cornerRadius: 0)
                .overlay(Color.black.opacity(0.68))

            VStack(spacing: Space.m) {
                Text("Can't play this here")
                    .font(SavaType.title)
                    .foregroundStyle(.white)
                Text(reason ?? "The platform didn't return a playable video.")
                    .font(SavaType.callout)
                    .foregroundStyle(.white.opacity(0.7))
                    .multilineTextAlignment(.center)

                if let url = URL(string: bookmark.url) {
                    Button {
                        Haptics.tap()
                        openURL(url)
                    } label: {
                        Text("Open in \(bookmark.platform.displayName)")
                            .font(SavaType.button)
                            .foregroundStyle(.white)
                            .padding(.horizontal, Space.l)
                            .padding(.vertical, 10)
                            .overlay(Capsule().stroke(.white.opacity(0.35), lineWidth: 1))
                    }
                    .padding(.top, Space.xs)
                }
            }
            .padding(.horizontal, Space.xxl)
        }
    }
}
