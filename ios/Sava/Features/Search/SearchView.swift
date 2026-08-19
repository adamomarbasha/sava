import SwiftUI

/// Search your library by what you remember about the content. Wraps the real
/// `q` search endpoint in a focused, native experience with recents and a
/// teaching empty state.
struct SearchView: View {
    @EnvironmentObject private var session: SessionStore
    @StateObject private var model = SearchViewModel()

    private var service: BookmarkService { BookmarkService(client: session.api) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.lg) {
                content
                    .padding(.horizontal, Spacing.md)
                    .padding(.bottom, 120)
            }
            .padding(.top, Spacing.sm)
        }
        .background(SavaColors.background)
        .navigationTitle("Search")
        .searchable(text: $model.query, placement: .navigationBarDrawer(displayMode: .always),
                    prompt: "Try “cooking”, “Japan”, “basketball”")
        .onChange(of: model.query) { _, _ in model.queryChanged(service) }
        .onSubmit(of: .search) { model.submit(service) }
        .onAppear {
            if let seed = DevFlags.searchQuery, model.query.isEmpty {
                model.query = seed
                model.submit(service)
            }
        }
    }

    @ViewBuilder private var content: some View {
        switch model.state {
        case .idle:
            idle
        case .searching:
            LibrarySkeleton()
        case .results(let items):
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text("\(items.count) result\(items.count == 1 ? "" : "s")")
                    .font(SavaFont.subheadline)
                    .foregroundStyle(SavaColors.textTertiary)
                BookmarkGrid(bookmarks: items)
            }
        case .empty:
            StatusView(icon: "magnifyingglass",
                       title: "No matches",
                       message: "Nothing in your library matches “\(model.query)”. Try different words.")
                .frame(maxWidth: .infinity).padding(.top, Spacing.xl)
        case .failed(let message):
            StatusView(icon: "wifi.exclamationmark", title: "Search failed",
                       message: message, tint: SavaColors.danger,
                       actionTitle: "Try again") { model.submit(service) }
                .frame(maxWidth: .infinity).padding(.top, Spacing.xl)
        }
    }

    private var idle: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            if !model.recents.isEmpty {
                VStack(alignment: .leading, spacing: Spacing.sm) {
                    HStack {
                        Text("Recent").font(SavaFont.headline).foregroundStyle(SavaColors.textPrimary)
                        Spacer()
                        Button("Clear") { model.clearRecents() }
                            .font(SavaFont.subheadline)
                            .foregroundStyle(SavaColors.accent)
                    }
                    FlexibleWrap(spacing: Spacing.xs, lineSpacing: Spacing.xs) {
                        ForEach(model.recents, id: \.self) { term in
                            Button {
                                model.useRecent(term, service: service)
                            } label: {
                                HStack(spacing: 5) {
                                    Image(systemName: "clock.arrow.circlepath").font(.system(size: 11))
                                    Text(term)
                                }
                                .font(SavaFont.footnote)
                                .foregroundStyle(SavaColors.textPrimary)
                                .padding(.horizontal, Spacing.sm)
                                .frame(height: 34)
                                .background(SavaColors.surfaceMuted, in: Capsule())
                            }
                            .buttonStyle(.pressable)
                        }
                    }
                }
            }

            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text("Search your memory")
                    .font(SavaFont.headline)
                    .foregroundStyle(SavaColors.textPrimary)
                Text("Find saved videos by what happens in them — a recipe, a place, a topic, a moment.")
                    .font(SavaFont.callout)
                    .foregroundStyle(SavaColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.top, model.recents.isEmpty ? Spacing.xl : 0)
        }
    }
}
