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

    private let service: PlaybackService
    private var tasks: [Int: Task<Void, Never>] = [:]

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
    func load(_ id: Int) {
        guard descriptors[id] == nil, tasks[id] == nil else { return }
        let poster = items.first { $0.id == id }?.imageURL
        loading.insert(id)
        tasks[id] = Task { [weak self] in
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
    }

    /// Resolve the current item and warm the next one.
    ///
    /// Only forward. Preloading in both directions doubles the cost of every
    /// swipe to serve the less likely gesture, and on the proxied TikTok path
    /// that cost is real bandwidth rather than just memory.
    func warm(around index: Int) {
        for offset in 0...1 {
            if let item = bookmark(at: index + offset) { load(item.id) }
        }
    }

    /// Ids worth keeping players for: the current page and its neighbours.
    func window(around index: Int) -> Set<Int> {
        Set((index - 1...index + 1).compactMap { bookmark(at: $0)?.id })
    }

    func cancelAll() {
        tasks.values.forEach { $0.cancel() }
        tasks.removeAll()
    }
}
