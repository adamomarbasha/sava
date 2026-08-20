import SwiftUI

/// Inside a collection.
///
/// The media dominates. Above it sit exactly three things: the count, an
/// optional descriptor, and the one action that belongs here — asking the
/// collection a question. Automatic and manual collections use the same screen,
/// because to the person looking at it there is no difference worth designing.
struct CollectionDetailView: View {
    let collection: SavaCollection

    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var shortForm: ShortFormContext
    @State private var items: [Bookmark] = []
    @State private var loading = true
    @State private var loadFailed: String?
    @State private var showAsk = false

    private var intelligence: IntelligenceService {
        IntelligenceService(client: session.api)
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.xl) {
                header
                if !items.isEmpty || loading {
                    askButton
                }
                content
            }
            .screenPadding()
            .padding(.top, Space.s)
            .padding(.bottom, Space.xl)
        }
        .devScrollAnchor()
        .background(SavaColor.ground)
        .navigationTitle(collection.name)
        .navigationBarTitleDisplayMode(.large)
        .tint(SavaColor.primary)
        .task { await load() }
        .refreshable { await load() }
        .sheet(isPresented: $showAsk) {
            AskView(scope: .collection(collection))
                .environmentObject(session)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(loading ? collection.countLabel
                         : "\(items.count) item\(items.count == 1 ? "" : "s")")
                .font(SavaType.meta)
                .foregroundStyle(SavaColor.tertiary)
                .monospacedDigit()

            if let description = collection.description, !description.isEmpty {
                Text(description)
                    .font(SavaType.callout)
                    .foregroundStyle(SavaColor.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var askButton: some View {
        SavaInlineAction(title: "Ask this collection", symbol: "text.bubble") {
            showAsk = true
        }
    }

    @ViewBuilder private var content: some View {
        if loading {
            MediaGridSkeleton(rows: 2)
        } else if let loadFailed {
            SavaEmptyState(title: "Can't load this collection", message: loadFailed,
                           actionTitle: "Try again") { Task { await load() } }
        } else if items.isEmpty {
            SavaEmptyState(
                title: "Nothing here yet",
                message: collection.isAutomatic
                    ? "Saves that belong in \(collection.name) will appear here as you add them."
                    : "Add items to \(collection.name) and they'll appear here.")
        } else {
            MediaGrid(bookmarks: items)
        }
    }

    private func load() async {
        do {
            items = try await intelligence.collection(id: collection.id).items
            // Swiping from inside a collection stays inside that collection.
            shortForm.publish(items, source: .collection(collection.name))
            loadFailed = nil
        } catch {
            if items.isEmpty {
                loadFailed = (error as? APIError)?.userMessage
                    ?? "Couldn't load this collection."
            }
        }
        loading = false
    }
}
