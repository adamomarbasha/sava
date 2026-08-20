import SwiftUI

/// Putting a save into a collection.
///
/// Without this, a user could create a collection and then have no way to fill
/// it — the shelf would only ever hold what Sava grouped by itself, which makes
/// "make your own" a dead end. A list of the collections that exist, a row to
/// make a new one, and nothing else.
struct AddToCollectionSheet: View {
    let bookmark: Bookmark

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss

    @State private var collections: [SavaCollection] = []
    @State private var loading = true
    @State private var busyID: Int?
    @State private var addedIDs: Set<Int> = []
    @State private var showCreate = false
    @State private var newName = ""
    @State private var errorMessage: String?

    private var intelligence: IntelligenceService {
        IntelligenceService(client: session.api)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    newCollectionRow

                    if loading {
                        ForEach(0..<3, id: \.self) { _ in
                            Skeleton(cornerRadius: 4)
                                .frame(height: 18)
                                .padding(.vertical, Space.l)
                        }
                    } else if collections.isEmpty {
                        Text("You haven't made any collections yet.")
                            .font(SavaType.callout)
                            .foregroundStyle(SavaColor.secondary)
                            .padding(.vertical, Space.xl)
                    } else {
                        ForEach(collections) { collection in
                            row(collection)
                        }
                    }

                    if let errorMessage {
                        Text(errorMessage)
                            .font(SavaType.callout)
                            .foregroundStyle(SavaColor.danger)
                            .padding(.top, Space.l)
                    }
                }
                .screenPadding()
                .padding(.vertical, Space.s)
            }
            .background(SavaColor.ground)
            .navigationTitle("Add to collection")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Done") { dismiss() }
                }
            }
            .tint(SavaColor.primary)
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .task { await load() }
        .alert("New collection", isPresented: $showCreate) {
            TextField("Name", text: $newName)
            Button("Cancel", role: .cancel) { newName = "" }
            Button("Create") { create() }
                .disabled(newName.trimmingCharacters(in: .whitespaces).isEmpty)
        } message: {
            Text("This item will be added to it.")
        }
    }

    private var newCollectionRow: some View {
        Button {
            Haptics.tap()
            showCreate = true
        } label: {
            HStack(spacing: Space.m) {
                Image(systemName: "plus")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(SavaColor.accent)
                    .frame(width: 22)
                Text("New collection")
                    .font(SavaType.body)
                    .foregroundStyle(SavaColor.accent)
                Spacer()
            }
            .frame(height: 52)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .hairline()
    }

    private func row(_ collection: SavaCollection) -> some View {
        let added = addedIDs.contains(collection.id)
        return Button {
            add(to: collection)
        } label: {
            HStack(spacing: Space.m) {
                CollectionCover(name: collection.name,
                                thumbnails: collection.coverURLs, height: 40)
                    .frame(width: 60)

                VStack(alignment: .leading, spacing: 1) {
                    Text(collection.name)
                        .font(SavaType.body)
                        .foregroundStyle(SavaColor.primary)
                        .lineLimit(1)
                    Text(collection.countLabel)
                        .font(SavaType.meta)
                        .foregroundStyle(SavaColor.tertiary)
                }
                Spacer(minLength: Space.s)

                if busyID == collection.id {
                    ProgressView().controlSize(.small)
                } else if added {
                    Image(systemName: "checkmark")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(SavaColor.success)
                }
            }
            .padding(.vertical, Space.s)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(added || busyID != nil)
        .hairline()
        .accessibilityLabel("\(collection.name), \(collection.countLabel)")
        .accessibilityHint(added ? "Already added" : "Adds this item")
        .accessibilityAddTraits(added ? [.isSelected, .isButton] : .isButton)
    }

    // MARK: Actions

    private func load() async {
        collections = (try? await intelligence.collections()) ?? []
        loading = false
    }

    private func add(to collection: SavaCollection) {
        guard busyID == nil else { return }
        busyID = collection.id
        errorMessage = nil
        Task {
            do {
                try await intelligence.addToCollection(collectionID: collection.id,
                                                       bookmarkIDs: [bookmark.id])
                addedIDs.insert(collection.id)
                Haptics.success()
                await load()
            } catch {
                errorMessage = (error as? APIError)?.userMessage
                    ?? "Couldn't add this to \(collection.name)."
                Haptics.error()
            }
            busyID = nil
        }
    }

    private func create() {
        let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        newName = ""
        guard !name.isEmpty else { return }
        Task {
            guard let created = try? await intelligence.createCollection(name: name) else {
                errorMessage = "Couldn't create that collection."
                return
            }
            _ = try? await intelligence.addToCollection(collectionID: created.id,
                                                        bookmarkIDs: [bookmark.id])
            addedIDs.insert(created.id)
            Haptics.success()
            await load()
        }
    }
}
