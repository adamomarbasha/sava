import SwiftUI

/// Collections — the way a person's saves actually group.
///
/// One grid, not two. Sava's groupings and the user's sit together with no
/// badge, no "auto" label and no separate shelf, because the distinction is
/// Sava's implementation detail and not something the owner of a library should
/// have to think about. A collection you made and a collection Sava found
/// should feel equally yours; the only place the difference surfaces is in what
/// the long-press menu offers, where it is actually actionable.
///
/// Covers are built from real member imagery. Nothing here is a folder glyph.
struct CollectionsView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var library: LibraryViewModel
    @EnvironmentObject private var shortForm: ShortFormContext

    @State private var collections: [SavaCollection] = []
    @State private var loading = true
    @State private var loadFailed: String?
    @State private var showCreate = false
    @State private var newName = ""
    @State private var renaming: SavaCollection?
    @State private var renameText = ""
    @State private var pendingDelete: SavaCollection?
    @State private var rebuilding = false

    private var intelligence: IntelligenceService { IntelligenceService(client: session.api) }

    private let columns = [
        GridItem(.flexible(), spacing: Space.gutter),
        GridItem(.flexible(), spacing: Space.gutter),
    ]

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.xxl) {
                content
            }
            .padding(.top, Space.s)
            .padding(.bottom, Space.xl)
        }
        .background(SavaColor.ground)
        .navigationTitle("Collections")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button { showCreate = true } label: {
                        Label("New collection", systemImage: "plus")
                    }
                    Button { Task { await rebuild() } } label: {
                        Label("Look for new groupings", systemImage: "arrow.clockwise")
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
        } message: {
            Text("Sava will find the items that belong in it.")
        }
        .alert("Rename", isPresented: Binding(
            get: { renaming != nil },
            set: { if !$0 { renaming = nil } })) {
            TextField("Name", text: $renameText)
            Button("Cancel", role: .cancel) { renaming = nil }
            Button("Save") { commitRename() }
        }
        .confirmationDialog(
            pendingDelete.map { "Delete \($0.name)?" } ?? "",
            isPresented: Binding(get: { pendingDelete != nil },
                                 set: { if !$0 { pendingDelete = nil } }),
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) { commitDelete() }
            Button("Cancel", role: .cancel) { pendingDelete = nil }
        } message: {
            // Said plainly, because the two cases genuinely differ: deleting a
            // grouping Sava found also teaches it not to suggest that grouping
            // again, and the user should know that is what the button does.
            Text(pendingDelete?.isAutomatic == true
                 ? "Your saves stay in your library. Sava won't suggest this grouping again."
                 : "Your saves stay in your library.")
        }
    }

    @ViewBuilder private var content: some View {
        if loading {
            skeleton.screenPadding()
        } else if let loadFailed {
            SavaEmptyState(title: "Can't reach Sava", message: loadFailed,
                           actionTitle: "Try again") { Task { await load() } }
        } else if collections.isEmpty {
            emptyState
        } else {
            if !collections.isEmpty {
                LazyVGrid(columns: columns, spacing: Space.xl) {
                    ForEach(collections) { collection in
                        NavigationLink(value: collection) {
                            CollectionCard(collection: collection)
                        }
                        .buttonStyle(.pressable)
                        .contextMenu { menu(for: collection) }
                    }
                }
                .screenPadding()
            }
        }
    }

    @ViewBuilder private func menu(for collection: SavaCollection) -> some View {
        Button {
            renameText = collection.name
            renaming = collection
        } label: { Label("Rename", systemImage: "pencil") }

        Button(role: .destructive) {
            pendingDelete = collection
        } label: { Label("Delete", systemImage: "trash") }
    }

    private var emptyState: some View {
        SavaEmptyState(
            title: "No collections yet",
            message: library.all.count < 8
                ? "Sava starts grouping your library once you have a few more items. You can make your own any time."
                : "Sava hasn't found groupings in these yet. Make your own, or ask it to look again.",
            actionTitle: "New collection") { showCreate = true }
    }

    private var skeleton: some View {
        LazyVGrid(columns: columns, spacing: Space.xl) {
            ForEach(0..<4, id: \.self) { _ in
                VStack(alignment: .leading, spacing: Space.s) {
                    Skeleton().aspectRatio(4.0 / 3.0, contentMode: .fit)
                    Skeleton(cornerRadius: 4).frame(height: 13)
                    Skeleton(cornerRadius: 4).frame(width: 44, height: 10)
                }
            }
        }
        .accessibilityHidden(true)
    }

    // MARK: Actions

    private func load() async {
        let loaded = try? await intelligence.collections()
        if let loaded {
            collections = loaded
            loadFailed = nil
        } else if collections.isEmpty {
            loadFailed = "Couldn't load your collections."
        }
        loading = false
    }

    private func create() {
        let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        newName = ""
        guard !name.isEmpty else { return }
        Task {
            _ = try? await intelligence.createCollection(name: name)
            await load()
            Haptics.success()
        }
    }

    private func commitRename() {
        guard let target = renaming else { return }
        let name = renameText.trimmingCharacters(in: .whitespacesAndNewlines)
        renaming = nil
        guard !name.isEmpty, name != target.name else { return }
        Task {
            _ = try? await intelligence.renameCollection(id: target.id, name: name)
            await load()
            Haptics.success()
        }
    }

    private func commitDelete() {
        guard let target = pendingDelete else { return }
        pendingDelete = nil
        // Removed from the shelf immediately; the reload confirms it. Waiting
        // for a round trip to make a deletion visible feels broken.
        collections.removeAll { $0.id == target.id }
        Task {
            _ = try? await intelligence.deleteCollection(id: target.id)
            await load()
            Haptics.tap()
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

/// A collection on the shelf: its own media, its name, how much is in it.
///
/// No description line, no "auto" badge, no icon. The cover already says what
/// the collection is about, and a badge announcing that software chose it adds
/// nothing the user wanted to know.
struct CollectionCard: View {
    let collection: SavaCollection

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            CollectionCover(name: collection.name, thumbnails: collection.coverURLs)

            VStack(alignment: .leading, spacing: 2) {
                Text(collection.name)
                    .font(SavaType.mediaTitle)
                    .foregroundStyle(SavaColor.primary)
                    .lineLimit(1)
                Text(collection.countLabel)
                    .font(SavaType.meta)
                    .foregroundStyle(SavaColor.tertiary)
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(collection.name), \(collection.countLabel)")
        .accessibilityHint("Opens this collection")
    }
}
