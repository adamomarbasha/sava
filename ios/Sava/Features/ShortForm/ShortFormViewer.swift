import SwiftUI

/// The full-screen vertical viewer.
///
/// One viewer for both platforms, which is the whole design: TikTok arrives as
/// a proxied MP4 and YouTube as a sanctioned embed, but that difference is
/// resolved server-side into a `PlaybackDescriptor` and lives entirely inside
/// one page type. There is no TikTok viewer and no YouTube viewer to keep in
/// sync — there is a feed of pages, and a page knows how to draw four shapes.
///
/// Paging is the system's (`.scrollTargetBehavior(.paging)`) rather than a
/// hand-rolled drag gesture, so it gets real rubber-banding, real deceleration,
/// interruptible mid-flick swipes and correct behaviour under VoiceOver for
/// free — none of which a custom `DragGesture` reproduces convincingly.
///
/// Laziness is load-bearing. `LazyVStack` builds only the pages near the
/// viewport, and `ShortPlayerPool` keeps at most three players alive across the
/// whole feed, so opening this on a library of five hundred items costs the
/// same as opening it on three.
struct ShortFormViewer: View {
    @StateObject private var feed: ShortFormFeedModel
    @StateObject private var pool = ShortPlayerPool()

    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase

    @State private var askBookmark: Bookmark?
    @State private var moreBookmark: Bookmark?
    @State private var transcriptBookmark: Bookmark?
    @State private var addToBookmark: Bookmark?

    init(bookmarks: [Bookmark], start: Bookmark, source: ShortFormSource,
         service: PlaybackService) {
        _feed = StateObject(wrappedValue: ShortFormFeedModel(
            bookmarks: bookmarks, start: start, source: source, service: service))
    }

    var body: some View {
        // Two readers on purpose. The outer one keeps the scene's safe-area
        // insets, which the chrome needs so the close button does not sit under
        // the clock and the caption does not sit under the home indicator. The
        // inner one ignores them, so a page is the height of the *screen* and
        // video is genuinely full-bleed. Measuring once, inside the safe area,
        // is what letterboxes the video and makes paging snap short.
        GeometryReader { outer in
            let insets = outer.safeAreaInsets

            GeometryReader { geo in
                ScrollView(.vertical) {
                    LazyVStack(spacing: 0) {
                        ForEach(feed.items) { bookmark in
                            ShortFormPage(
                                bookmark: bookmark,
                                descriptor: feed.descriptor(for: bookmark.id),
                                isLoading: feed.isLoading(bookmark.id),
                                isCurrent: feed.currentID == bookmark.id,
                                positionLabel: feed.positionLabel,
                                safeArea: insets,
                                pool: pool,
                                onAsk: { askBookmark = bookmark },
                                onMore: { moreBookmark = bookmark },
                                onClose: { finish() }
                            )
                            .frame(width: geo.size.width, height: geo.size.height)
                            .id(bookmark.id)
                        }
                    }
                    .scrollTargetLayout()
                }
                .scrollTargetBehavior(.paging)
                .scrollPosition(id: $feed.currentID)
                .scrollIndicators(.hidden)
            }
            .ignoresSafeArea()
        }
        .background(Color.black.ignoresSafeArea())
        .statusBarHidden()
        .preferredColorScheme(.dark)
        .persistentSystemOverlays(.hidden)
        .task { syncToCurrent() }
        .task { await devAdvanceIfRequested() }
        .onChange(of: feed.currentID) { _, _ in syncToCurrent() }
        .onChange(of: feed.descriptors) { _, _ in syncToCurrent() }
        // Nothing keeps playing behind another screen. Backgrounding, a sheet,
        // and dismissal all have to stop audio, and forgetting any one of them
        // is the bug users notice first.
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { pool.pauseAll() } else { syncToCurrent() }
        }
        .onDisappear {
            feed.cancelAll()
            pool.teardown()
        }
        .sheet(item: $askBookmark, onDismiss: { syncToCurrent() }) { bookmark in
            AskView(scope: .save(bookmark))
                // Half height on purpose: the requirement is that asking about
                // something never hides the something. The video stays on
                // screen above the sheet, and can be dragged back to full.
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
                .presentationBackgroundInteraction(.enabled(upThrough: .medium))
        }
        .sheet(item: $transcriptBookmark, onDismiss: { syncToCurrent() }) { bookmark in
            TranscriptView(bookmark: bookmark)
        }
        .sheet(item: $addToBookmark, onDismiss: { syncToCurrent() }) { bookmark in
            AddToCollectionSheet(bookmark: bookmark)
        }
        .confirmationDialog(
            moreBookmark?.displayTitle ?? "",
            isPresented: Binding(get: { moreBookmark != nil },
                                 set: { if !$0 { moreBookmark = nil } }),
            titleVisibility: .visible
        ) {
            moreActions
        }
    }

    // MARK: Actions

    @ViewBuilder private var moreActions: some View {
        if let bookmark = moreBookmark {
            Button("View transcript") {
                moreBookmark = nil
                pool.pauseAll()
                transcriptBookmark = bookmark
            }
            Button("Add to collection") {
                moreBookmark = nil
                addToBookmark = bookmark
            }
            Button("Open in \(bookmark.platform.displayName)") {
                moreBookmark = nil
                if let url = URL(string: bookmark.url) { openURL(url) }
            }
            Button("Cancel", role: .cancel) { moreBookmark = nil }
        }
    }

    // MARK: Feed coordination

    /// The single place playback follows paging.
    ///
    /// Three things happen together and must not drift apart: the descriptor for
    /// the current page (and the next) is fetched, the current player starts
    /// while every other one stops, and any player outside the three-page window
    /// is released. Doing these in separate handlers is how feeds end up with
    /// two videos playing at once.
    private func syncToCurrent() {
        guard let index = feed.currentIndex, let current = feed.bookmark(at: index)
        else { return }

        feed.warm(around: index)

        // Keep players for the page either side, so flicking back is instant.
        let retain = feed.window(around: index)

        // But only *fetch* forwards. Creating a player for the previous item
        // when it is not already loaded would spend proxied bandwidth on the
        // less likely gesture; keeping one that already exists costs nothing.
        var ensure: [(id: Int, url: URL)] = []
        for offset in 0...1 {
            guard let item = feed.bookmark(at: index + offset),
                  let descriptor = feed.descriptor(for: item.id),
                  descriptor.kind == .video, let url = descriptor.url
            else { continue }
            ensure.append((id: item.id, url: url))
        }

        // Only a `video` page has an `AVPlayer` to start. An embed drives its
        // own playback through the web view, and a gallery has none — in both
        // cases the correct number of running players is zero.
        let isVideo = feed.descriptor(for: current.id)?.kind == .video
        pool.reconcile(retain: retain, ensure: ensure,
                       active: isVideo ? current.id : nil)
        if !isVideo { pool.markActiveWithoutPlaying(current.id) }

        Haptics.select()
    }

    /// DEBUG-only feed stepper. Compiled to nothing in Release.
    private func devAdvanceIfRequested() async {
        #if DEBUG
        let steps = DevFlags.shortFormAdvance
        guard steps > 0 else { return }
        for _ in 0..<steps {
            try? await Task.sleep(nanoseconds: 2_200_000_000)
            guard let index = feed.currentIndex,
                  let next = feed.bookmark(at: index + 1) else { break }
            feed.currentID = next.id
            let kind = feed.descriptor(for: next.id)?.kind.rawValue ?? "pending"
            NSLog("[sava feed] page=%d/%d id=%d %@ kind=%@ playing=%d live=%d",
                  index + 2, feed.items.count, next.id, next.platform.displayName,
                  kind, pool.debugPlayingCount, pool.debugLiveCount)
        }
        try? await Task.sleep(nanoseconds: 1_500_000_000)
        NSLog("[sava feed] FINAL playing=%d live=%d", pool.debugPlayingCount, pool.debugLiveCount)
        #endif
    }

    private func finish() {
        pool.pauseAll()
        dismiss()
    }
}
