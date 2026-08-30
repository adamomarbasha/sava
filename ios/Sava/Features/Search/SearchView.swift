import SwiftUI

/// Search should feel like finding something in your own visual memory, so the
/// results are the same media grid the library uses. Finding a save looks like
/// looking at it.
struct SearchView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var library: LibraryViewModel
    @EnvironmentObject private var shortForm: ShortFormContext
    @StateObject private var model = SearchViewModel()
    @State private var fieldFocused = false

    private var bookmarks: BookmarkService { BookmarkService(client: session.api) }
    private var intelligence: IntelligenceService { IntelligenceService(client: session.api) }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.l) {
                filterRow
                content
                    .transition(.opacity)
                    .animation(Motion.gentle, value: model.state)
            }
            .padding(.top, Space.s)
            .padding(.bottom, Space.xl)
        }
        .background(SavaColor.ground)
        .navigationTitle("Search")
        .navigationBarTitleDisplayMode(.large)
        // Focused on appear. Search is now reached by deliberately tapping a
        // search button, so the intent is already unambiguous — making someone
        // tap the field as well is a second tap for a decision they made before
        // the screen existed.
        .searchable(text: $model.query,
                    isPresented: $fieldFocused,
                    placement: .navigationBarDrawer(displayMode: .always),
                    prompt: "Search your library")
        .onAppear { fieldFocused = true }
        .onChange(of: model.query) { _, _ in
            model.queryChanged(bookmarks: bookmarks, intelligence: intelligence)
        }
        .onSubmit(of: .search) {
            model.submit(bookmarks: bookmarks, intelligence: intelligence)
        }
        .tint(SavaColor.accent)
        .task { runDevQueryIfRequested() }
    }

    // MARK: Filters

    /// Compact and only present while there is a query — a filter bar over an
    /// empty search screen is a dashboard nobody asked for.
    @ViewBuilder private var filterRow: some View {
        if !model.trimmedQuery.isEmpty, library.availablePlatforms.count > 1 {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Space.s) {
                    SavaChip(title: "All", selected: model.platform == nil) {
                        model.setPlatform(nil, bookmarks: bookmarks, intelligence: intelligence)
                    }
                    ForEach(library.availablePlatforms) { platform in
                        SavaChip(title: platform.displayName,
                                 selected: model.platform == platform) {
                            model.setPlatform(platform, bookmarks: bookmarks,
                                              intelligence: intelligence)
                        }
                    }
                }
                .screenPadding()
            }
            .scrollClipDisabled()
        }
    }

    // MARK: States

    @ViewBuilder private var content: some View {
        switch model.state {
        case .idle:
            idle

        case .searching:
            MediaGridSkeleton(rows: 2).screenPadding()

        case .results(let items):
            VStack(alignment: .leading, spacing: Space.m) {
                SectionHeader(text: "Results", trailing: "\(items.count)")
                MediaGrid(bookmarks: items)
            }
            .screenPadding()
            // Results are a list like any other, so the viewer swipes through
            // the matches rather than jumping back out to the whole library.
            .onAppear { shortForm.publish(items, source: .search(model.trimmedQuery)) }

        case .empty:
            SavaEmptyState(title: "No matches",
                           message: "Nothing in your library matches “\(model.trimmedQuery)”.")

        case .failed(let message):
            SavaEmptyState(title: "Search failed", message: message,
                           actionTitle: "Try again") {
                model.submit(bookmarks: bookmarks, intelligence: intelligence)
            }
        }
    }

    /// The resting state shows recent searches only when there are any. An empty
    /// screen with a single sentence beats a screen of invented suggestions.
    @ViewBuilder private var idle: some View {
        if model.recents.isEmpty {
            SavaEmptyState(
                title: "Find anything in your library",
                message: "Search by what's in it — a dish, a place, a creator, a topic.")
        } else {
            VStack(alignment: .leading, spacing: Space.s) {
                HStack {
                    SectionHeader(text: "Recent")
                    Button("Clear") { model.clearRecents() }
                        .font(SavaType.caption)
                        .foregroundStyle(SavaColor.accent)
                }
                VStack(spacing: 0) {
                    ForEach(model.recents, id: \.self) { term in
                        Button {
                            Haptics.select()
                            model.useRecent(term, bookmarks: bookmarks,
                                            intelligence: intelligence)
                        } label: {
                            HStack(spacing: Space.m) {
                                Image(systemName: "clock.arrow.circlepath")
                                    .font(.system(size: 13))
                                    .foregroundStyle(SavaColor.tertiary)
                                Text(term)
                                    .font(SavaType.callout)
                                    .foregroundStyle(SavaColor.primary)
                                    .lineLimit(1)
                                Spacer()
                            }
                            .frame(height: 44)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .hairline(leading: 28)
                    }
                }
            }
            .screenPadding()
        }
    }

    private func runDevQueryIfRequested() {
        guard model.query.isEmpty, let seed = DevFlags.searchQuery else { return }
        model.query = seed
        model.submit(bookmarks: bookmarks, intelligence: intelligence)
    }
}
