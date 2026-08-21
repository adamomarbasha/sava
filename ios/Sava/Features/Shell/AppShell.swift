import SwiftUI

/// The signed-in container.
///
/// Four destinations, because the product has four ideas: your saves, the way
/// they group, watching them full screen, and asking across all of them.
///
/// Search is deliberately *not* one of them. A tab is for a place you go; search
/// is something you do to the place you are already in, and it was the only tab
/// whose screen was empty until you typed. It now lives as a button in the
/// Library's top bar and opens over whatever you were looking at. Scroll took
/// the slot it left, which is the better fit for a tab: it is a genuine
/// destination with its own contents.
///
/// The tab bar is the system's, so every screen inside gets the right bottom
/// safe area, the right scroll insets and the right scroll-edge behaviour for
/// free. That is why no screen in this app carries a magic bottom padding
/// constant.
struct AppShell: View {
    let user: User

    @EnvironmentObject private var session: SessionStore
    @StateObject private var library = LibraryViewModel()
    /// Presented from here rather than from each list, so there is only ever
    /// one viewer and only ever one thing playing.
    @StateObject private var shortForm = ShortFormContext()

    @State private var tab: Destination = .library
    @State private var libraryPath = NavigationPath()
    @State private var collectionsPath = NavigationPath()
    @State private var scrollPath = NavigationPath()
    @State private var askPath = NavigationPath()
    @State private var devShowProfile = false
    @State private var devShowSave = false
    @State private var devAskBookmark: Bookmark?
    @State private var devAddToBookmark: Bookmark?
    @State private var devTranscriptBookmark: Bookmark?
    @State private var devShowHistory = false
    @State private var devShowSearch = false

    enum Destination: String, CaseIterable, Hashable {
        case library, collections, scroll, ask

        var title: String {
            switch self {
            case .library:     return "Library"
            case .collections: return "Collections"
            case .scroll:      return "Scroll"
            case .ask:         return "Ask"
            }
        }

        var icon: String {
            switch self {
            case .library:     return "square.grid.2x2"
            case .collections: return "rectangle.stack"
            case .scroll:      return "play.square.stack"
            case .ask:         return "text.bubble"
            }
        }

    }

    var body: some View {
        // A real `TabView`, not a hand-built bar.
        //
        // The bar is the least interesting part of this app and the most
        // load-bearing: it owns the bottom safe area that every screen inside it
        // depends on. A custom bar drawn as an overlay does not publish that
        // inset, so Ask's composer sat underneath it and scroll views ended in
        // the wrong place. The system bar publishes the inset, matches the
        // platform, handles Dynamic Type and VoiceOver, and needs no styling to
        // look right — which is exactly what "native to iOS" is supposed to mean.
        TabView(selection: tabSelection) {
            NavigationStack(path: $libraryPath) {
                LibraryView(user: user).savaDestinations()
            }
            .tabItem { Label(Destination.library.title, systemImage: Destination.library.icon) }
            .tag(Destination.library)

            NavigationStack(path: $collectionsPath) {
                CollectionsView().savaDestinations()
            }
            .tabItem { Label(Destination.collections.title, systemImage: Destination.collections.icon) }
            .tag(Destination.collections)

            NavigationStack(path: $scrollPath) {
                ScrollHomeView().savaDestinations()
            }
            .tabItem { Label(Destination.scroll.title, systemImage: Destination.scroll.icon) }
            .tag(Destination.scroll)

            NavigationStack(path: $askPath) {
                AskView(scope: .library).savaDestinations()
            }
            .tabItem { Label(Destination.ask.title, systemImage: Destination.ask.icon) }
            .tag(Destination.ask)
        }
        .environmentObject(library)
        .environmentObject(shortForm)
        .tint(SavaColor.primary)
        .fullScreenCover(item: $shortForm.presenting) { bookmark in
            ShortFormViewer(bookmarks: shortForm.feed(for: bookmark),
                            start: bookmark,
                            source: shortForm.source,
                            service: PlaybackService(client: session.api))
        }
        .onAppear(perform: applyDevFlags)
        .task { await openDevScreenIfRequested() }
        .sheet(isPresented: $devShowProfile) {
            NavigationStack { ProfileView(user: user).environmentObject(library) }
        }
        .sheet(isPresented: $devShowSave) {
            QuickSaveSheet { library.insertSaved($0, service: BookmarkService(client: session.api)) }
        }
        .sheet(item: $devAskBookmark) { bookmark in
            AskView(scope: .save(bookmark))
        }
        .sheet(item: $devAddToBookmark) { bookmark in
            AddToCollectionSheet(bookmark: bookmark)
        }
        .sheet(item: $devTranscriptBookmark) { bookmark in
            TranscriptView(bookmark: bookmark)
        }
        .sheet(isPresented: $devShowHistory) {
            ChatHistorySheet(scope: "library", bookmarkID: nil) { _ in }
        }
        .sheet(isPresented: $devShowSearch) {
            NavigationStack { SearchView() }
                .environmentObject(library)
                .environmentObject(shortForm)
        }
    }

    /// Selecting the tab you are already on returns it to its root, the way
    /// every system app behaves. Without it a deep stack is a trap.
    private var tabSelection: Binding<Destination> {
        Binding(
            get: { tab },
            set: { next in
                if next == tab {
                    popToRoot()
                    Haptics.tap()
                } else {
                    Haptics.select()
                    tab = next
                }
            }
        )
    }

    /// Tapping the active tab returns to its root, the way every system app
    /// behaves. Without it a deep stack is a trap.
    private func popToRoot() {
        switch tab {
        case .library:     libraryPath = NavigationPath()
        case .collections: collectionsPath = NavigationPath()
        case .scroll:      scrollPath = NavigationPath()
        case .ask:         askPath = NavigationPath()
        }
    }

    private func applyDevFlags() {
        guard let requested = DevFlags.initialTab,
              let match = Destination(rawValue: requested) else { return }
        tab = match
    }

    /// DEBUG-only deep link so any screen can be captured directly. Compiled to
    /// nothing in Release because `DevFlags.screen` is always nil there.
    private func openDevScreenIfRequested() async {
        guard let screen = DevFlags.screen else { return }
        let service = BookmarkService(client: session.api)
        let intelligence = IntelligenceService(client: session.api)

        switch screen {
        case .detail(let id):
            guard let bookmark = await firstBookmark(id: id, using: service) else { return }
            libraryPath.append(bookmark)

        case .askThis:
            guard let bookmark = await firstBookmark(id: nil, using: service) else { return }
            devAskBookmark = bookmark

        case .collection(let id):
            guard let list = try? await intelligence.collections(),
                  let match = list.first(where: { $0.id == id }) else { return }
            tab = .collections
            collectionsPath.append(match)

        case .profile:
            devShowProfile = true

        case .save:
            devShowSave = true

        case .addTo:
            guard let bookmark = await firstBookmark(id: nil, using: service) else { return }
            devAddToBookmark = bookmark

        case .transcript(let id):
            guard let bookmark = await firstBookmark(id: id, using: service) else { return }
            devTranscriptBookmark = bookmark

        case .history:
            tab = .ask
            devShowHistory = true

        case .search:
            devShowSearch = true

        case .shortForm(let id):
            // Publish a real feed first, so the viewer under test is the same
            // multi-item feed the library produces rather than a feed of one.
            let all = (try? await service.list(limit: 200)) ?? []
            let playable = all.filter(\.isShortForm)
            guard let start = id.flatMap({ wanted in playable.first { $0.id == wanted } })
                    ?? playable.first else { return }
            shortForm.publish(playable, source: .library)
            shortForm.open(start)

        case .shortFormInCollection(let id):
            // Exercises the real path: a collection publishes its own items as
            // the feed, so the viewer swipes through the collection and names
            // it, rather than falling back to the library.
            guard let detail = try? await intelligence.collection(id: id) else { return }
            let playable = detail.items.filter(\.isShortForm)
            guard let start = playable.first else { return }
            let name = (try? await intelligence.collections())?
                .first { $0.id == id }?.name ?? "Collection"
            shortForm.publish(playable, source: .collection(name))
            shortForm.open(start)
        }
    }

    private func firstBookmark(id: Int?, using service: BookmarkService) async -> Bookmark? {
        if let id { return try? await service.bookmark(id: id) }
        return try? await service.list(limit: 1).first
    }
}

// MARK: - Shared navigation

/// Any screen can push a save or a collection, so the destinations are declared
/// once and attached to every stack.
extension View {
    func savaDestinations() -> some View {
        self
            .navigationDestination(for: Bookmark.self) { SaveDetailView(bookmark: $0) }
            .navigationDestination(for: SavaCollection.self) { CollectionDetailView(collection: $0) }
            .navigationDestination(for: RelatedSave.self) { SaveReferenceView(reference: $0) }
    }
}

// MARK: - Opening a referenced save

/// Opens a save that was referenced by an answer or a search result.
///
/// Those references carry only enough metadata to be rendered inline, so the
/// full record is fetched before the detail screen is shown. Showing a thinner
/// detail screen for a save reached from Ask than for the same save reached from
/// the library would be a visible inconsistency.
struct SaveReferenceView: View {
    let reference: RelatedSave

    @EnvironmentObject private var session: SessionStore
    @State private var bookmark: Bookmark?
    @State private var failed = false

    var body: some View {
        Group {
            if let bookmark {
                SaveDetailView(bookmark: bookmark)
            } else if failed {
                SavaEmptyState(title: "Can't open this item",
                               message: "It may have been removed from your library.")
                    .background(SavaColor.ground)
            } else {
                ZStack {
                    SavaColor.ground.ignoresSafeArea()
                    ProgressView().tint(SavaColor.tertiary)
                }
                .navigationBarTitleDisplayMode(.inline)
            }
        }
        .task {
            let service = BookmarkService(client: session.api)
            do {
                bookmark = try await service.bookmark(id: reference.id)
            } catch {
                failed = true
            }
        }
    }
}
