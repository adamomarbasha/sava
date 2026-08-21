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
            VStack(alignment: .leading, spacing: Space.l) {
                masthead
                filterRow
                content
            }
            .padding(.top, Space.s)
            .padding(.bottom, Space.xl)
        }
        .devScrollAnchor()
        .background(SavaColor.ground)
        // ── The pull-to-refresh gap, fixed at the mechanism ──────────────
        //
        // The title is drawn in the scroll content, and the navigation bar is
        // given nothing to lay out. That is the fix, and it is deliberately
        // blunt.
        //
        // Every earlier attempt treated the *content*: dropping implicit
        // animations, swapping LazyVStack for VStack, removing an artificial
        // delay. Each helped and none of them held, because the gap was never
        // in the content. It was in the large title. `refreshable` owns the
        // scroll view's top inset while the control retracts, and a large title
        // derives its own height from that same offset; when the two disagree
        // mid-retraction the bar keeps the extra height and nothing ever takes
        // it back. That is the band of empty space above "Library".
        //
        // A large title cannot desync from a scroll offset it is not reading.
        // With `.inline` and an empty title the bar has no height of its own to
        // get wrong, and the masthead is just text in a VStack — it scrolls
        // because the content scrolls, which is the only behaviour there is.
        //
        // Cost: the system's title-collapse animation is gone. Worth it. It was
        // the source of a bug that came back three times.
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            // Scroll lives in the bar's leading slot: it is a mode change for
            // the whole screen, which is what a leading bar item is for, and it
            // no longer competes with the filters for the same row.
            ToolbarItem(placement: .topBarLeading) {
                if let label = scrollEntryLabel {
                    Button {
                        Haptics.tap()
                        openScrollFeed()
                    } label: {
                        HStack(spacing: 5) {
                            Image(systemName: "play.fill")
                                .font(.system(size: 10, weight: .black))
                            Text("Scroll")
                                .font(.system(size: 14, weight: .semibold))
                        }
                        .foregroundStyle(SavaColor.onAccent)
                        .padding(.horizontal, 11)
                        .padding(.vertical, 6)
                        .background(SavaColor.accent, in: Capsule())
                    }
                    .buttonStyle(.pressable)
                    .accessibilityLabel(label)
                }
            }
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
        .task { await devHammerRefresh() }
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

    /// The screen's title, drawn as content rather than as a navigation bar
    /// large title. See the note on `navigationBarTitleDisplayMode` above.
    private var masthead: some View {
        Text("Library")
            .font(.system(size: 34, weight: .heavy))
            .tracking(Tracking.tight)
            .foregroundStyle(SavaColor.primary)
            .screenPadding()
            .padding(.top, Space.xs)
            .accessibilityAddTraits(.isHeader)
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
                                 selected: model.selectedPlatform == platform,
                                 platform: platform) {
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

    /// DEBUG-only. Compiled to nothing in Release.
    private func devHammerRefresh() async {
        #if DEBUG
        let runs = DevFlags.refreshRuns
        guard runs > 0 else { return }
        for i in 1...runs {
            await model.refresh(service)
            NSLog("[sava refresh] run %d/%d items=%d state=%@",
                  i, runs, model.visible.count, String(describing: model.state))
            try? await Task.sleep(nanoseconds: 400_000_000)
        }
        NSLog("[sava refresh] done")
        #endif
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
                // Labelled, not trailing. `MediaGrid` gained an `onRemove`
                // parameter after `onDelete`, and an unlabelled trailing
                // closure binds to the *last* closure parameter — so this had
                // silently become the collection-removal handler, and the
                // library's Delete action was showing "Remove from collection".
                MediaGrid(bookmarks: model.visible,
                          onDelete: { bookmark in
                              Task { await model.delete(bookmark, using: service) }
                          })
                .screenPadding()
            }
        }
    }
}
