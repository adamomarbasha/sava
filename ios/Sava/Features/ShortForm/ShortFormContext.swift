import SwiftUI

/// The list the user is currently looking at, and the one place the viewer is
/// presented from.
///
/// The viewer has to be able to open from anywhere — a grid card, a detail
/// hero, a search result — while always swiping through *that* screen's list in
/// *that* screen's order. Passing the surrounding list down through every
/// navigation destination would mean threading it through `SaveDetailView`,
/// `savaDestinations()` and every future entry point; instead each list screen
/// publishes what it is showing, and whoever opens the viewer just names the
/// item.
///
/// It also guarantees there is exactly one viewer. Presenting a full-screen
/// player from several places independently is how two videos end up playing at
/// once.
@MainActor
final class ShortFormContext: ObservableObject {

    /// The list currently on screen, in its own order.
    @Published private(set) var items: [Bookmark] = []
    @Published private(set) var source: ShortFormSource = .library

    /// Non-nil while the viewer is up.
    @Published var presenting: Bookmark?

    /// Called by a list screen whenever what it is showing changes.
    func publish(_ items: [Bookmark], source: ShortFormSource) {
        self.items = items
        self.source = source
    }

    /// Open the viewer on `bookmark`, swiping through whatever was last published.
    func open(_ bookmark: Bookmark) {
        presenting = bookmark
    }

    /// The feed for a given item, falling back to the item alone when the
    /// surrounding list is unknown — reached from a deep link, say. A feed of
    /// one still plays; it simply has nothing to swipe to.
    func feed(for bookmark: Bookmark) -> [Bookmark] {
        items.contains { $0.id == bookmark.id } ? items : [bookmark]
    }
}

/// The tap target that opens the viewer from a thumbnail.
///
/// Small, low-contrast, bottom-left — deliberately not the giant centred
/// translucent circle every video grid uses. On a page of mixed content the
/// grid's job is to show the media; this only has to say "this one moves".
struct PlayAffordance: View {
    var action: () -> Void

    var body: some View {
        Button {
            Haptics.tap()
            action()
        } label: {
            Image(systemName: "play.fill")
                .font(.system(size: 10, weight: .black))
                .foregroundStyle(.white)
                .frame(width: 26, height: 26)
                .background(.black.opacity(0.5), in: Circle())
                .overlay(Circle().stroke(.white.opacity(0.22), lineWidth: 0.5))
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Play")
        .accessibilityHint("Opens the full-screen viewer")
    }
}
