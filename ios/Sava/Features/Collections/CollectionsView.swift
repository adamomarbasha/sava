import SwiftUI

/// Collections — the way a person's saves naturally group.
///
/// Covers are built from real member imagery, never a folder glyph, so the shelf
/// itself tells you what is inside. Sava's own groupings and the user's sit in
/// the same visual language, separated by a heading rather than by styling:
/// a collection you made and a collection Sava found should feel equally yours.
struct CollectionsView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var library: LibraryViewModel

    @State private var collections: [SavaCollection] = []
    @State private var loading = true
    @State private var loadFailed: String?
    @State private var showCreate = false
    @State private var newName = ""
    @State private var creating = false
    @State private var rebuilding = false

    private var intelligence: IntelligenceService {
        IntelligenceService(client: session.api)
    }

    private var automatic: [SavaCollection] { collections.filter(\.isAutomatic) }
    private var manual: [SavaCollection] { collections.filter { !$0.isAutomatic } }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.xl) {
                content
            }
            .padding(.top, Space.s)
            .padding(.bottom, Space.xl)
            .screenPadding()
        }
        .background(SavaColor.ground)
        .navigationTitle("Collections")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button {
                        showCreate = true
                    } label: { Label("New collection", systemImage: "plus") }

                    Button {
                        Task { await rebuild() }
                    } label: {
                        Label("Refresh Sava's groupings", systemImage: "arrow.clockwise")
                    }
                    .disabled(rebuilding)
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 15, weight: .semibold))
                }
                .accessibilityLabel("Collection actions")
            }
        }
        .tint(SavaColor.primary)
        .task { await load() }
        .refreshable { await load() }
        .alert("New collection", isPresented: $showCreate) {
            TextField("Name", text: $newName)
            Button("Cancel", role: .cancel) { newName = "" }
            Button("Create") { create() }
                .disabled(newName.trimmingCharacters(in: .whitespaces).isEmpty)
        } message: {
            Text("Sava will find the saves that belong in it.")
        }
    }

    @ViewBuilder private var content: some View {
        if loading {
            skeleton
        } else if let loadFailed {
            SavaEmptyState(title: "Can't reach Sava", message: loadFailed,
                           actionTitle: "Try again") { Task { await load() } }
        } else if collections.isEmpty {
            emptyState
        } else {
            if !automatic.isEmpty {
                shelf(automatic, label: "From your library")
            }
            if !manual.isEmpty {
                shelf(manual, label: "Yours")
            }
        }
    }

    /// Sava only groups automatically once there is enough to group. Saying so
    /// is more useful than an empty shelf that looks broken.
    private var emptyState: some View {
        SavaEmptyState(
            title: "No collections yet",
            message: library.all.count < 8
                ? "Sava starts grouping your saves once you have a few more. You can make your own any time."
                : "Sava hasn't grouped these yet. Make your own, or ask Sava to look again.",
            actionTitle: "New collection") { showCreate = true }
    }

    private func shelf(_ items: [SavaCollection], label: String) -> some View {
        VStack(alignment: .leading, spacing: Space.l) {
            SectionHeader(text: label, trailing: "\(items.count)")
            ForEach(items) { collection in
                NavigationLink(value: collection) {
                    CollectionCard(collection: collection)
                }
                .buttonStyle(.pressable)
            }
        }
    }

    private var skeleton: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            ForEach(0..<3, id: \.self) { _ in
                VStack(alignment: .leading, spacing: Space.s) {
                    Skeleton().frame(height: 132)
                    Skeleton(cornerRadius: 4).frame(width: 140, height: 14)
                }
            }
        }
        .accessibilityHidden(true)
    }

    // MARK: Actions

    private func load() async {
        do {
            collections = try await intelligence.collections()
            loadFailed = nil
        } catch {
            if collections.isEmpty {
                loadFailed = (error as? APIError)?.userMessage
                    ?? "Couldn't load your collections."
            }
        }
        loading = false
    }

    private func create() {
        let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, !creating else { return }
        creating = true
        newName = ""
        Task {
            _ = try? await intelligence.createCollection(name: name)
            await load()
            creating = false
            Haptics.success()
        }
    }

    private func rebuild() async {
        guard !rebuilding else { return }
        rebuilding = true
        Haptics.tap()
        _ = try? await intelligence.rebuildCollections()
        await load()
        rebuilding = false
    }
}

/// A collection on the shelf: a cover built from its own media, the name, the
/// count. Nothing else — a description line here would repeat what the pictures
/// already say.
struct CollectionCard: View {
    let collection: SavaCollection

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            CollectionCover(name: collection.name, thumbnails: collection.coverURLs)

            HStack(alignment: .firstTextBaseline) {
                Text(collection.name)
                    .font(SavaType.mediaTitle)
                    .foregroundStyle(SavaColor.primary)
                    .lineLimit(1)
                Spacer(minLength: Space.s)
                Text("\(collection.count)")
                    .font(SavaType.numeric)
                    .foregroundStyle(SavaColor.tertiary)
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(collection.name), \(collection.countLabel)")
        .accessibilityHint("Opens this collection")
    }
}
