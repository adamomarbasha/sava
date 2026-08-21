import SwiftUI

/// The library — the user's saved world.
///
/// Opening the app should feel like opening a visual memory, so the first thing
/// on screen is the media itself. There is no dashboard, no stat row, no "welcome
/// back", and no hero card: a large title, a filter row that only appears when it
/// has something to filter, and then the grid.
struct LibraryView: View {
    let user: User

    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var model: LibraryViewModel
    @EnvironmentObject private var shortForm: ShortFormContext
    @State private var showProfile = false
    @State private var showSave = false

    private var service: BookmarkService { BookmarkService(client: session.api) }

    var body: some View {
        ScrollView {
            // Nothing in here may animate its own height.
            //
            // This is the root of the pull-to-refresh gap. `refreshable` owns
            // the scroll view's content offset while the refresh control
            // retracts; if the content's height *animates* during that
            // retraction — which is exactly when it did, because the state
            // switch, the filter row and the grid all changed at the moment the
            // refresh finished — the scroll view is settling against a target
            // that is still moving, and it stops short, leaving a band of empty
            // space at the top.
            //
            // The fix is to make the content height a pure function of the
            // data: it still changes when items arrive, but instantaneously, so
            // retraction always has a stable target to settle against. An
            // earlier attempt moved the filter row out into a `safeAreaInset`,
            // which fixed the height but displaced the large navigation title —
            // the row belongs in the scroll content, it just must not animate.
            LazyVStack(alignment: .leading, spacing: Space.l) {
                filterRow
                content
            }
            .padding(.top, Space.s)
            .padding(.bottom, Space.xl)
        }
        .devScrollAnchor()
        .background(SavaColor.ground)
        .navigationTitle("Library")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Haptics.tap()
                    showSave = true
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 16, weight: .semibold))
                }
                .accessibilityLabel("Add a link")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Haptics.tap()
                    showProfile = true
                } label: {
                    Image(systemName: "person.crop.circle")
                        .font(.system(size: 17, weight: .regular))
                }
                .accessibilityLabel("Profile")
            }
        }
        .tint(SavaColor.primary)
        .refreshable { await model.refresh(service) }
        .task { await model.loadIfNeeded(service) }
        // The swipe feed is whatever the library is showing right now, filter
        // included — so opening a video from a filtered view swipes through
        // that filter, not through everything.
        .onAppear { shortForm.publish(model.visible, source: .library) }
        .onChange(of: model.visible) { _, items in
            shortForm.publish(items, source: .library)
        }
        .sheet(isPresented: $showProfile) {
            NavigationStack { ProfileView(user: user) }
                .environmentObject(session)
                .environmentObject(model)
        }
        .sheet(isPresented: $showSave) {
            QuickSaveSheet { bookmark in model.insertSaved(bookmark, service: service) }
                .environmentObject(session)
        }
    }

    // MARK: Filters

    /// Only shown when the library actually spans more than one platform. A
    /// filter row with one filter in it is chrome.
    @ViewBuilder private var filterRow: some View {
        if model.availablePlatforms.count > 1 {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Space.s) {
                    // The short-form entry point lives here rather than as its
                    // own button or a new section, because the filter row is
                    // already the place where "which part of my library am I
                    // looking at" is decided — and that is exactly what choosing
                    // a feed is. It names what it will open, so there is no
                    // separate "Scroll TikToks" and "Scroll Shorts" control to
                    // find, and it is simply absent when the current selection
                    // has nothing that plays.
                    if let label = scrollEntryLabel {
                        ScrollEntryChip(title: label) { openScrollFeed() }
                    }

                    SavaChip(title: "All", count: model.all.count,
                             selected: model.selectedPlatform == nil) {
                        model.setFilter(nil)
                    }
                    ForEach(model.availablePlatforms) { platform in
                        SavaChip(title: platform.displayName,
                                 count: model.count(for: platform),
                                 selected: model.selectedPlatform == platform) {
                            model.setFilter(platform)
                        }
                    }
                }
                .screenPadding()
            }
            .scrollClipDisabled()
        }
    }

    // MARK: Short-form entry

    /// Items in the current selection that can actually play.
    private var scrollable: [Bookmark] { model.visible.filter(\.isShortForm) }

    /// What the entry point opens, named for what the user is looking at.
    /// Nil when there is nothing to scroll, so the control is never a dead end.
    private var scrollEntryLabel: String? {
        guard scrollable.count >= 2 else { return nil }
        switch model.selectedPlatform {
        case .tiktok:  return "Scroll TikToks"
        case .youtube: return "Scroll Shorts"
        case .some:    return "Scroll"
        case nil:      return "Scroll"
        }
    }

    private func openScrollFeed() {
        let source: ShortFormSource = model.selectedPlatform.map { .platform($0) } ?? .library
        shortForm.openFeed(scrollable, source: source)
    }

    // MARK: States

    @ViewBuilder private var content: some View {
        switch model.state {
        case .idle, .loading:
            MediaGridSkeleton().screenPadding()

        case .failed(let message):
            SavaEmptyState(title: "Can't reach Sava",
                           message: message,
                           actionTitle: "Try again") {
                Task { await model.load(service) }
            }

        case .empty:
            SavaEmptyState(
                title: "Your library is empty",
                message: "Add something from TikTok, Instagram or YouTube and it will appear here.",
                actionTitle: "Add a link") { showSave = true }

        case .loaded:
            if model.visible.isEmpty {
                SavaEmptyState(
                    title: "Nothing from \(model.selectedPlatform?.displayName ?? "there")",
                    message: "Clear the filter to see your whole library.",
                    actionTitle: "Show all") { model.setFilter(nil) }
            } else {
                MediaGrid(bookmarks: model.visible) { bookmark in
                    Task { await model.delete(bookmark, using: service) }
                }
                .screenPadding()
            }
        }
    }
}
