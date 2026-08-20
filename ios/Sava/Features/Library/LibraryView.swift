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
            LazyVStack(alignment: .leading, spacing: Space.l) {
                filterRow
                // The animation lives on the switching content, not on the
                // scroll view's whole subtree. Animating the container also
                // animates the geometry `refreshable` is manipulating, which is
                // what left a band of empty space above the grid that never
                // closed after a pull-to-refresh.
                content
                    .transition(.opacity)
                    .animation(Motion.gentle, value: model.state)
                    .animation(Motion.gentle, value: model.selectedPlatform)
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
                .accessibilityLabel("Save a link")
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
            .animation(Motion.gentle, value: model.availablePlatforms)
        }
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
                message: "Save something from TikTok, Instagram or YouTube and it will appear here.",
                actionTitle: "Save a link") { showSave = true }

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
