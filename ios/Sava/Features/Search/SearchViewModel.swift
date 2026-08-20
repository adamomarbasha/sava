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
    @Published private(set) var alsoRelated: [RelatedSave] = []
    @Published private(set) var recents: [String] = []

    private let recentsKey = "sava.recentSearches"
    private var searchTask: Task<Void, Never>?
    private var semanticTask: Task<Void, Never>?

    init() {
        recents = UserDefaults.standard.stringArray(forKey: recentsKey) ?? []
    }

    var trimmedQuery: String {
        query.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Debounced live search as the user types.
    func queryChanged(bookmarks: BookmarkService, intelligence: IntelligenceService) {
        searchTask?.cancel()
        semanticTask?.cancel()
        alsoRelated = []

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
        semanticTask?.cancel()
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

    private func run(_ term: String, bookmarks: BookmarkService,
                     intelligence: IntelligenceService) async {
        state = .searching
        do {
            let items = try await bookmarks.list(platform: platform, query: term, limit: 100)
            guard !Task.isCancelled else { return }
            state = items.isEmpty ? .empty : .results(items)
            runSemanticPass(term, found: Set(items.map(\.id)), intelligence: intelligence)
        } catch is CancellationError {
            // Superseded.
        } catch {
            state = .failed((error as? APIError)?.userMessage ?? "Search failed. Try again.")
        }
    }

    /// The second pass. Failure here is silent on purpose: the keyword results
    /// are already a complete, useful answer, and a semantic outage should not
    /// turn a working search into an error screen.
    private func runSemanticPass(_ term: String, found: Set<Int>,
                                 intelligence: IntelligenceService) {
        semanticTask = Task {
            guard let response = try? await intelligence.search(query: term,
                                                                platform: platform,
                                                                limit: 12),
                  !Task.isCancelled else { return }
            let extras = response.results.filter { !found.contains($0.id) }
            guard !extras.isEmpty else { return }
            withAnimation(Motion.gentle) { alsoRelated = extras }
        }
    }

    private func remember(_ term: String) {
        var updated = recents.filter { $0.caseInsensitiveCompare(term) != .orderedSame }
        updated.insert(term, at: 0)
        recents = Array(updated.prefix(8))
        UserDefaults.standard.set(recents, forKey: recentsKey)
    }
}
