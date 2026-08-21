import SwiftUI

/// Inside a collection.
///
/// The media dominates. Above it sit exactly three things: the count, an
/// optional descriptor, and the one action that belongs here — asking the
/// collection a question. Automatic and manual collections use the same screen,
/// because to the person looking at it there is no difference worth designing.
struct CollectionDetailView: View {
    let collection: SavaCollection

    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var shortForm: ShortFormContext
    @State private var items: [Bookmark] = []
    @State private var loading = true
    @State private var loadFailed: String?
    @State private var showAsk = false
    @State private var covers: [URL?] = []
    @State private var showCoverPicker = false

    private var intelligence: IntelligenceService {
        IntelligenceService(client: session.api)
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.xl) {
                header
                if !items.isEmpty || loading {
                    askButton
                }
                content
            }
            .screenPadding()
            .padding(.top, Space.s)
            .padding(.bottom, Space.xl)
        }
        .devScrollAnchor()
        .background(SavaColor.ground)
        .navigationTitle(collection.name)
        .navigationBarTitleDisplayMode(.inline)
        .tint(SavaColor.primary)
        .task { await load() }
        .refreshable { await load() }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button {
                        Haptics.tap()
                        showCoverPicker = true
                    } label: { Label("Change cover", systemImage: "photo") }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 15, weight: .semibold))
                }
                .accessibilityLabel("Collection actions")
            }
        }
        .sheet(isPresented: $showCoverPicker) {
            CoverPickerSheet(collection: collection) {
                Task { await load() }
            }
            .environmentObject(session)
        }
        .sheet(isPresented: $showAsk) {
            AskView(scope: .collection(collection))
                .environmentObject(session)
        }
    }

    /// Cover, name, count. The cover is built from the collection's own members
    /// and updates as they change, so a collection is fronted by what is
    /// actually inside it rather than by a frozen first pick.
    private var header: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            // The chosen cover wins. It was selected to represent the whole
            // collection; member thumbnails are only the fallback for one that
            // has not been chosen yet.
            CollectionCover(name: collection.name,
                            thumbnails: collection.coverURLs.isEmpty ? covers
                                                                     : collection.coverURLs,
                            aspect: 16.0 / 9.0)

            VStack(alignment: .leading, spacing: Space.xs) {
                Text(collection.name)
                    .font(SavaType.title)
                    .foregroundStyle(SavaColor.primary)
                    .fixedSize(horizontal: false, vertical: true)

                Text(loading ? collection.countLabel
                             : "\(items.count) item\(items.count == 1 ? "" : "s")")
                    .font(SavaType.meta)
                    .foregroundStyle(SavaColor.tertiary)
                    .monospacedDigit()

                if let description = collection.description, !description.isEmpty {
                    Text(description)
                        .font(SavaType.callout)
                        .foregroundStyle(SavaColor.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, Space.xs)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var askButton: some View {
        SavaInlineAction(title: "Ask this collection", symbol: "text.bubble") {
            showAsk = true
        }
    }

    @ViewBuilder private var content: some View {
        if loading {
            MediaGridSkeleton(rows: 2)
        } else if let loadFailed {
            SavaEmptyState(title: "Can't load this collection", message: loadFailed,
                           actionTitle: "Try again") { Task { await load() } }
        } else if items.isEmpty {
            SavaEmptyState(
                title: "Nothing here yet",
                message: collection.isAutomatic
                    ? "Items that belong in \(collection.name) will appear here as you add them."
                    : "Add items to \(collection.name) and they'll appear here.")
        } else {
            MediaGrid(bookmarks: items, onRemove: remove)
        }
    }

    /// Take a save out of this collection without deleting it.
    ///
    /// Removed from the list first, then persisted. For an automatic collection
    /// the server also records the correction, so the next rebuild does not put
    /// it straight back — which is the whole reason this is a distinct action
    /// rather than a plain delete.
    private func remove(_ bookmark: Bookmark) {
        items.removeAll { $0.id == bookmark.id }
        shortForm.publish(items, source: .collection(collection.name))
        covers = Self.coverURLs(from: items)
        Haptics.tap()
        Task {
            try? await intelligence.removeFromCollection(
                collectionID: collection.id, bookmarkID: bookmark.id)
        }
    }

    /// Up to four *distinct* images.
    ///
    /// Two saves can legitimately share cover art — a creator reusing a
    /// thumbnail across episodes — and a mosaic showing the same picture twice
    /// reads as a rendering bug rather than as a coincidence.
    private static func coverURLs(from items: [Bookmark]) -> [URL?] {
        var seen = Set<String>()
        var out: [URL?] = []
        for url in items.compactMap(\.imageURL) where seen.insert(url.absoluteString).inserted {
            out.append(url)
            if out.count == 4 { break }
        }
        return out
    }

    private func load() async {
        do {
            items = try await intelligence.collection(id: collection.id).items
            // Swiping from inside a collection stays inside that collection.
            shortForm.publish(items, source: .collection(collection.name))
            covers = Self.coverURLs(from: items)
            loadFailed = nil
        } catch {
            if items.isEmpty {
                loadFailed = (error as? APIError)?.userMessage
                    ?? "Couldn't load this collection."
            }
        }
        loading = false
    }
}
