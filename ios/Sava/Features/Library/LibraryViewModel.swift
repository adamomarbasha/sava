import SwiftUI

/// Owns the library feed: fetches the user's real bookmarks once, filters
/// client-side for instant platform switching, and tracks transient
/// "just saved" state for optimistic quick-save inserts.
@MainActor
final class LibraryViewModel: ObservableObject {
    enum LoadState: Equatable {
        case idle, loading, loaded, empty, failed(String)
    }

    @Published private(set) var state: LoadState = .idle
    @Published private(set) var all: [Bookmark] = []
    @Published var selectedPlatform: Platform? = nil

    /// Ids saved in this session that are still being ingested/analyzed server-
    /// side. Drives an honest "Analyzing" badge only for items we just created.
    @Published private(set) var processingIDs: Set<Int> = []

    private var hasLoaded = false

    var visible: [Bookmark] {
        guard let selectedPlatform else { return all }
        return all.filter { $0.platform == selectedPlatform }
    }

    /// Platforms present in the library, ordered by frequency, for filter pills.
    var availablePlatforms: [Platform] {
        let counts = Dictionary(grouping: all, by: { $0.platform }).mapValues(\.count)
        return counts.keys.sorted { (counts[$0] ?? 0) > (counts[$1] ?? 0) }
    }

    func count(for platform: Platform?) -> Int {
        guard let platform else { return all.count }
        return all.filter { $0.platform == platform }.count
    }

    func loadIfNeeded(_ service: BookmarkService) async {
        guard !hasLoaded else { return }
        await load(service)
    }

    func load(_ service: BookmarkService) async {
        if all.isEmpty { state = .loading }
        do {
            let items = try await service.list(limit: 500)
            all = items
            state = items.isEmpty ? .empty : .loaded
            hasLoaded = true
        } catch is CancellationError {
            // ignore
        } catch {
            let message = (error as? APIError)?.userMessage ?? "Couldn't load your library."
            state = all.isEmpty ? .failed(message) : .loaded
        }
    }

    func refresh(_ service: BookmarkService) async {
        await load(service)
    }

    func setFilter(_ platform: Platform?) {
        guard selectedPlatform != platform else { return }
        withAnimation(SavaMotion.tap) { selectedPlatform = platform }
        Haptics.selection()
    }

    /// Insert a freshly created bookmark at the top and mark it as processing.
    func insertSaved(_ bookmark: Bookmark) {
        withAnimation(SavaMotion.standard) {
            all.removeAll { $0.id == bookmark.id }
            all.insert(bookmark, at: 0)
            processingIDs.insert(bookmark.id)
            state = .loaded
        }
    }

    func delete(_ bookmark: Bookmark, using service: BookmarkService) async {
        let previous = all
        withAnimation(SavaMotion.standard) {
            all.removeAll { $0.id == bookmark.id }
            if all.isEmpty { state = .empty }
        }
        do {
            try await service.delete(id: bookmark.id)
            Haptics.success()
        } catch {
            // Roll back on failure.
            withAnimation(SavaMotion.standard) { all = previous; state = .loaded }
            Haptics.error()
        }
    }
}
