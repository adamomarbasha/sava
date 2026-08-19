import SwiftUI

/// Drives search against the real `GET /api/bookmarks?q=` endpoint. Debounced,
/// with persisted recent searches. Results are always real bookmarks.
@MainActor
final class SearchViewModel: ObservableObject {
    enum State: Equatable {
        case idle, searching, results([Bookmark]), empty, failed(String)
    }

    @Published var query = ""
    @Published private(set) var state: State = .idle
    @Published private(set) var recents: [String] = []

    private let recentsKey = "sava.recentSearches"
    private var searchTask: Task<Void, Never>?

    init() {
        recents = UserDefaults.standard.stringArray(forKey: recentsKey) ?? []
    }

    /// Debounced live search as the user types.
    func queryChanged(_ service: BookmarkService) {
        searchTask?.cancel()
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            state = .idle
            return
        }
        searchTask = Task {
            try? await Task.sleep(nanoseconds: 320_000_000)
            guard !Task.isCancelled else { return }
            await run(trimmed, service: service)
        }
    }

    /// Immediate search (submit / tap a recent).
    func submit(_ service: BookmarkService) {
        searchTask?.cancel()
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        remember(trimmed)
        Task { await run(trimmed, service: service) }
    }

    func useRecent(_ term: String, service: BookmarkService) {
        query = term
        submit(service)
    }

    func clearRecents() {
        recents = []
        UserDefaults.standard.removeObject(forKey: recentsKey)
    }

    private func run(_ term: String, service: BookmarkService) async {
        state = .searching
        do {
            let items = try await service.list(query: term, limit: 100)
            guard !Task.isCancelled else { return }
            state = items.isEmpty ? .empty : .results(items)
        } catch is CancellationError {
            // superseded
        } catch {
            state = .failed((error as? APIError)?.userMessage ?? "Search failed. Try again.")
        }
    }

    private func remember(_ term: String) {
        var updated = recents.filter { $0.caseInsensitiveCompare(term) != .orderedSame }
        updated.insert(term, at: 0)
        recents = Array(updated.prefix(8))
        UserDefaults.standard.set(recents, forKey: recentsKey)
    }
}
