import SwiftUI

/// Owns the library feed: fetches the user's real bookmarks, filters
/// client-side for instant platform switching, and applies optimistic updates
/// for quick-save and delete.
@MainActor
final class LibraryViewModel: ObservableObject {
    enum LoadState: Equatable {
        case idle, loading, loaded, empty, failed(String)
    }

    @Published private(set) var state: LoadState = .idle
    @Published private(set) var all: [Bookmark] = []
    @Published private(set) var selectedPlatform: Platform?

    private var hasLoaded = false
    private var watchers: [Int: Task<Void, Never>] = [:]

    var visible: [Bookmark] {
        guard let selectedPlatform else { return all }
        return all.filter { $0.platform == selectedPlatform }
    }

    /// Platforms offered in the filter row: the ones Sava can actually extract
    /// from, and only those the person has something from. A chip that filters
    /// down to a handful of bare links is worse than no chip.
    var availablePlatforms: [Platform] {
        let counts = Dictionary(grouping: all, by: \.platform).mapValues(\.count)
        return Platform.supported
            .filter { (counts[$0] ?? 0) > 0 }
            .sorted { (counts[$0] ?? 0) > (counts[$1] ?? 0) }
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
            // Superseded by a newer load.
        } catch {
            let message = (error as? APIError)?.userMessage ?? "Couldn't load your library."
            // A failed refresh must not throw away a library that is already on
            // screen — the user would watch their saves vanish over a blip.
            state = all.isEmpty ? .failed(message) : .loaded
        }
    }

    /// Pull-to-refresh, with a floor on how fast it may finish.
    ///
    /// A local API answers in ~40ms, and a refresh control that is dismissed
    /// that quickly does not get a chance to animate closed — it snaps, and on
    /// a `ScrollView` it can leave its own height behind as a band of empty
    /// space above the content. Holding it open briefly is both the fix and the
    /// better feel: the gesture reads as having done something.
    func refresh(_ service: BookmarkService) async {
        async let work: Void = load(service)
        async let floor: Void = Task.sleep(for: .milliseconds(650))
        _ = await (work, try? floor)
    }

    func setFilter(_ platform: Platform?) {
        guard selectedPlatform != platform else { return }
        Haptics.select()
        withAnimation(Motion.gentle) { selectedPlatform = platform }
    }

    /// Insert a freshly created bookmark at the top, then watch it finish.
    ///
    /// A save arrives with whatever metadata the server already had — often a
    /// bare URL. Enrichment happens in a background worker seconds later, and
    /// without this the card would sit unfinished until the user thought to pull
    /// to refresh. The card's geometry is fixed by platform, so the row does not
    /// move when the picture and title arrive; it just fills in.
    func insertSaved(_ bookmark: Bookmark, service: BookmarkService) {
        withAnimation(Motion.standard) {
            all.removeAll { $0.id == bookmark.id }
            all.insert(bookmark, at: 0)
            state = .loaded
        }
        guard bookmark.isProcessing else { return }
        watch(bookmark.id, using: service)
    }

    /// Poll one save until it stops processing. Bounded on purpose: a stuck
    /// pipeline must cost a fixed number of requests, not an open-ended loop for
    /// as long as the app is open.
    private func watch(_ id: Int, using service: BookmarkService) {
        watchers[id]?.cancel()
        watchers[id] = Task { [weak self] in
            for attempt in 0..<20 {
                // Back off gently: quick at first, then every few seconds.
                try? await Task.sleep(for: .seconds(attempt < 3 ? 2 : 5))
                guard !Task.isCancelled, let self else { return }
                guard let updated = try? await service.bookmark(id: id) else { continue }
                self.replace(updated)
                if !updated.isProcessing { break }
            }
            self?.watchers[id] = nil
        }
    }

    private func replace(_ bookmark: Bookmark) {
        guard let index = all.firstIndex(where: { $0.id == bookmark.id }),
              all[index] != bookmark else { return }
        withAnimation(Motion.gentle) { all[index] = bookmark }
    }

    deinit {
        watchers.values.forEach { $0.cancel() }
    }

    func delete(_ bookmark: Bookmark, using service: BookmarkService) async {
        let previous = all
        let previousState = state
        withAnimation(Motion.standard) {
            all.removeAll { $0.id == bookmark.id }
            if all.isEmpty { state = .empty }
        }
        do {
            try await service.delete(id: bookmark.id)
            Haptics.success()
        } catch {
            withAnimation(Motion.standard) {
                all = previous
                state = previousState
            }
            Haptics.error()
        }
    }
}
