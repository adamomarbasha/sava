import SwiftUI

/// Search is retrieval, not reasoning. It runs in two passes, and the order
/// matters:
///
///   1. **Keyword**, against `GET /api/bookmarks?q=`. Pure SQL over title,
///      creator and caption. It is instant, it returns the full save record, and
///      it works on every save in the library including ones the understanding
///      pipeline has never touched.
///   2. **Semantic**, against `GET /api/search`. This embeds the query, so it
///      costs a round trip through a model and only covers content that has been
///      processed. It runs *after* the keyword results are already on screen and
///      only contributes matches the first pass missed.
///
/// Doing it the other way round — semantic first — would make every keystroke
/// wait on an embedding call, which is the opposite of what search should feel
/// like.
@MainActor
final class SearchViewModel: ObservableObject {
    enum State: Equatable {
        case idle, searching, results([Bookmark]), empty, failed(String)
    }

    @Published var query = ""
    @Published var platform: Platform?
    @Published private(set) var state: State = .idle
    /// Semantic matches the keyword pass did not already return.
    @Published private(set) var recents: [String] = []

    private let recentsKey = "sava.recentSearches"
    private var searchTask: Task<Void, Never>?

    init() {
        recents = UserDefaults.standard.stringArray(forKey: recentsKey) ?? []
    }

    var trimmedQuery: String {
        query.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Debounced live search as the user types.
    func queryChanged(bookmarks: BookmarkService, intelligence: IntelligenceService) {
        searchTask?.cancel()

        guard !trimmedQuery.isEmpty else {
            state = .idle
            return
        }
        let term = trimmedQuery
        searchTask = Task {
            try? await Task.sleep(for: .milliseconds(280))
            guard !Task.isCancelled else { return }
            await run(term, bookmarks: bookmarks, intelligence: intelligence)
        }
    }

    /// Immediate search (submit, tap a recent, change a filter).
    func submit(bookmarks: BookmarkService, intelligence: IntelligenceService) {
        searchTask?.cancel()
        guard !trimmedQuery.isEmpty else { return }
        let term = trimmedQuery
        remember(term)
        searchTask = Task { await run(term, bookmarks: bookmarks, intelligence: intelligence) }
    }

    func useRecent(_ term: String, bookmarks: BookmarkService,
                   intelligence: IntelligenceService) {
        query = term
        submit(bookmarks: bookmarks, intelligence: intelligence)
    }

    func setPlatform(_ value: Platform?, bookmarks: BookmarkService,
                     intelligence: IntelligenceService) {
        guard platform != value else { return }
        Haptics.select()
        platform = value
        guard !trimmedQuery.isEmpty else { return }
        submit(bookmarks: bookmarks, intelligence: intelligence)
    }

    func clearRecents() {
        Haptics.tap()
        recents = []
        UserDefaults.standard.removeObject(forKey: recentsKey)
    }

    // MARK: - Running

    /// One search, one ranked list.
    ///
    /// This used to run two passes — a keyword query against the bookmark rows
    /// for the grid, then a second semantic query whose extra hits went into an
    /// "Also related" strip underneath. That split was the search bug: the
    /// first pass only reads `bookmarks.title/author/description/note`, so a
    /// save whose text lives on the canonical row or in the derived
    /// understanding could never reach the primary results. Searching "Speed"
    /// reported "No matches" and then listed a TikTok whose title starts with
    /// the word.
    ///
    /// `GET /api/search` already does lexical *and* semantic retrieval, fuses
    /// them into one ranking, and dedupes by canonical id. Splitting its output
    /// back into two buckets discarded that ranking and demoted real matches.
    private func run(_ term: String, bookmarks: BookmarkService,
                     intelligence: IntelligenceService) async {
        state = .searching
        do {
            let items = try await intelligence.searchLibrary(
                query: term, platform: platform, limit: 60)
            guard !Task.isCancelled else { return }
            state = items.isEmpty ? .empty : .results(items)
        } catch is CancellationError {
            // Superseded.
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
