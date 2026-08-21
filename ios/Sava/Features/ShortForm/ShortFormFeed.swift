import SwiftUI

/// Where a swipe feed came from.
///
/// The viewer is never "all your short-form videos". It is always the vertical
/// reading of whatever list the user was already looking at, in that list's
/// order, filtered to the items that can actually play. Opening a TikTok from
/// inside a collection and finding yourself swiping through the whole library
/// would be the viewer quietly changing the subject.
enum ShortFormSource: Equatable {
    case library
    case collection(String)
    case search(String)
    /// Everything short-form from one platform: "Scroll TikToks", "Scroll
    /// Shorts". A filtered view of the library rather than a different feed.
    case platform(Platform)

    /// Shown in the viewer's header so the feed says what it is.
    var label: String {
        switch self {
        case .library:              return "Library"
        case .collection(let name): return name
        case .search:               return "Search"
        case .platform(let p):      return p.displayName
        }
    }
}

/// Feed state for the short-form viewer.
///
/// Owns three things the pages must agree on: the ordered items, which one is
/// current, and the playback descriptor for each. Descriptors are fetched
/// lazily and cached for the life of the viewer — resolving a TikTok stream is
/// a live call to the platform, so re-fetching on every swipe back would be
/// both slow and rude.
@MainActor
final class ShortFormFeedModel: ObservableObject {

    let source: ShortFormSource
    let items: [Bookmark]

    @Published var currentID: Int?
    @Published private(set) var descriptors: [Int: PlaybackDescriptor] = [:]
    @Published private(set) var loading: Set<Int> = []

    /// How many items ahead of the user descriptors are resolved for.
    ///
    /// Three covers a fast run of swipes without resolving far into a feed the
    /// user is about to abandon. Each one is a paid platform extraction, so this
    /// is a real trade rather than a free buffer.
    static let prefetchDepth = 3

    /// How many items ahead actually get an `AVPlayer` with buffered video.
    ///
    /// Lower than `prefetchDepth`: a descriptor is a URL, but a player is a
    /// decoder session and live downloading, and iOS grants few of the former.
    static let playerLookahead = 2

    private let service: PlaybackService
    private var tasks: [Int: Task<Void, Never>] = [:]
    private var prefetchTask: Task<Void, Never>?

    /// - Parameter bookmarks: the list as the user sees it, in their order.
    /// - Parameter start: the item they tapped. Kept even if the filter would
    ///   otherwise drop it, so a tap never opens onto a different video.
    init(bookmarks: [Bookmark], start: Bookmark, source: ShortFormSource,
         service: PlaybackService) {
        self.source = source
        self.service = service

        var playable = bookmarks.filter(\.isShortForm)
        if !playable.contains(where: { $0.id == start.id }) {
            playable.insert(start, at: 0)
        }
        self.items = playable
        self.currentID = start.id
    }

    var currentIndex: Int? {
        guard let currentID else { return nil }
        return items.firstIndex { $0.id == currentID }
    }

    var positionLabel: String {
        guard let index = currentIndex else { return source.label }
        return "\(source.label) · \(index + 1) of \(items.count)"
    }

    func bookmark(at index: Int) -> Bookmark? {
        items.indices.contains(index) ? items[index] : nil
    }

    func descriptor(for id: Int) -> PlaybackDescriptor? { descriptors[id] }

    func isLoading(_ id: Int) -> Bool { loading.contains(id) }

    /// Fetch the descriptor for `id` unless it is already known or in flight.
    ///
    /// Returns the in-flight task so a caller can wait for it. `nil` means
    /// there is nothing to wait for — already resolved.
    @discardableResult
    func load(_ id: Int) -> Task<Void, Never>? {
        if descriptors[id] != nil { return nil }
        if let existing = tasks[id] { return existing }

        let poster = items.first { $0.id == id }?.imageURL
        loading.insert(id)
        let task = Task { [weak self] in
            guard let self else { return }
            let result: PlaybackDescriptor
            do {
                result = try await service.descriptor(bookmarkID: id)
            } catch is CancellationError {
                await MainActor.run {
                    self.loading.remove(id)
                    self.tasks[id] = nil
                }
                return
            } catch {
                result = .offline(poster: poster)
            }
            await MainActor.run {
                self.descriptors[id] = result
                self.loading.remove(id)
                self.tasks[id] = nil
            }
        }
        tasks[id] = task
        return task
    }

    /// Resolve the current item, then quietly resolve the next few.
    ///
    /// Resolving a TikTok is not a cache read — it is a live `yt-dlp` extraction
    /// on the server that regularly takes seconds. Warming only one item ahead
    /// meant that any swipe arriving before that call returned sat on a spinner,
    /// which is the wait this is meant to remove. So the window is now
    /// `prefetchDepth` items deep: by the time a swipe lands, the descriptor was
    /// usually fetched while the previous video was still playing.
    ///
    /// The ordering is the important part. These calls are expensive and
    /// serialised behind a small server-side budget, so firing all four at once
    /// would put three speculative extractions in front of the one video the
    /// user is actually staring at. Instead the current item is dispatched
    /// alone, and the speculative ones run **after it resolves, one at a
    /// time** — spending only idle time, and never the user's.
    func warm(around index: Int) {
        if let item = bookmark(at: index) { load(item.id) }

        // Superseded on every swipe: a prefetch aimed at where the user *was*
        // is worth less than one aimed at where they are.
        prefetchTask?.cancel()
        prefetchTask = Task { [weak self] in
            await self?.prefetchForward(from: index)
        }
    }

    /// Resolve ahead of the user, one item at a time, lowest priority.
    private func prefetchForward(from index: Int) async {
        // Never overlap the current item's own resolve.
        if let current = bookmark(at: index), let task = load(current.id) {
            _ = await task.value
        }
        guard !Task.isCancelled else { return }

        for offset in 1...Self.prefetchDepth {
            guard !Task.isCancelled, let item = bookmark(at: index + offset) else { return }
            if let task = load(item.id) {
                _ = await task.value
            }
        }
    }

    /// Ids worth keeping players for: one back, and as far forward as the pool
    /// will hold. Backwards is one only — flicking back is much rarer than
    /// continuing, and a retained player costs a decoder session.
    func window(around index: Int) -> Set<Int> {
        Set((index - 1...index + Self.playerLookahead).compactMap { bookmark(at: $0)?.id })
    }

    func cancelAll() {
        prefetchTask?.cancel()
        prefetchTask = nil
        tasks.values.forEach { $0.cancel() }
        tasks.removeAll()
    }
}
