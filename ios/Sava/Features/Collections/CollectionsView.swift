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
    /// Guards a double-tap on Create while the first is still in flight.
    @State private var creating = false
    /// Where discovery is, and what the user is being told about it.
    @State private var discovery: DiscoveryPhase = .idle
    /// Cleared after a moment so a result does not become permanent chrome.
    @State private var discoveryDismiss: Task<Void, Never>?
    /// Create, rename and delete report through here.
    @State private var reporter = ActionReporter()

    private var intelligence: IntelligenceService { IntelligenceService(client: session.api) }

    private let columns = [
        GridItem(.flexible(), spacing: Space.gutter),
        GridItem(.flexible(), spacing: Space.gutter),
    ]

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.xxl) {
                if let status = discovery.actionStatus {
                    InlineStatus(status: status,
                                 onRetry: discovery.isFailure ? { retry() } : nil)
                        .screenPadding()
                }
                if let status = reporter.status {
                    InlineStatus(status: status).screenPadding()
                }
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
                    .disabled(discovery.isRunning)
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 15, weight: .semibold))
                }
                .accessibilityLabel("Collection actions")
            }
        }
        .tint(SavaColor.primary)
        .task {
            await load()
            if DevFlags.autoRegroup { await rebuild() }
        }
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

    /// Create a collection, and say whether it worked.
    ///
    /// This used to swallow the error with `try?` and then play
    /// `Haptics.success()` unconditionally — a failed create buzzed as though
    /// it had succeeded and nothing appeared, which is the app asserting
    /// something untrue rather than merely staying quiet.
    private func create() {
        let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        newName = ""
        guard !name.isEmpty, !creating else { return }
        creating = true
        Task {
            await reporter.run(working: "Creating “\(name)”…",
                               success: "Created “\(name)”",
                               failure: "Couldn't create that collection.") {
                _ = try await intelligence.createCollection(name: name)
            }
            await load()
            creating = false
        }
    }

    private func commitRename() {
        guard let target = renaming else { return }
        let name = renameText.trimmingCharacters(in: .whitespacesAndNewlines)
        renaming = nil
        guard !name.isEmpty, name != target.name else { return }
        Task {
            await reporter.run(working: "Renaming…",
                               success: "Renamed to “\(name)”",
                               failure: "Couldn't rename this collection.") {
                _ = try await intelligence.renameCollection(id: target.id, name: name)
            }
            await load()
        }
    }

    private func commitDelete() {
        guard let target = pendingDelete else { return }
        pendingDelete = nil
        // Removed from the shelf immediately; the reload confirms it. Waiting
        // for a round trip to make a deletion visible feels broken.
        //
        // The optimism has to be undone out loud, though. Previously a failed
        // delete was swallowed and `load()` simply put the collection back —
        // it vanished, reappeared, and nothing said why.
        let restore = collections
        withAnimation(Motion.standard) {
            collections.removeAll { $0.id == target.id }
        }
        Task {
            do {
                _ = try await intelligence.deleteCollection(id: target.id)
                reporter.report(.success("Deleted “\(target.name)”"))
                Haptics.tap()
                await load()
            } catch {
                withAnimation(Motion.standard) { collections = restore }
                reporter.report(.failure((error as? APIError)?.userMessage
                                         ?? "Couldn't delete this collection."))
                Haptics.error()
            }
        }
    }

    /// Look for groupings, and say what is happening the whole way through.
    ///
    /// The old version set a `rebuilding` flag that was never rendered, threw
    /// the result away with `_ = try?`, and reloaded the list *before* the
    /// background job it had just queued could have run. Tapping the button
    /// therefore produced no indicator, no result, and no error — it genuinely
    /// did nothing observable.
    private func rebuild() async {
        guard !discovery.isRunning else { return }
        discoveryDismiss?.cancel()
        Haptics.tap()

        // The phases are advanced on a timer rather than reported by the
        // server. Being honest about that: the call is a single synchronous
        // request that takes tens of milliseconds, so there is no progress to
        // stream — these exist so a fast operation still *reads* as work rather
        // than as a flicker, and they are cosmetic pacing, not fake progress.
        // Nothing here claims a group was found; only the result does that.
        withAnimation(Motion.gentle) { discovery = .starting }
        let pacing = Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(220))
            if discovery.isRunning { withAnimation(Motion.gentle) { discovery = .analyzing } }
            try? await Task.sleep(for: .milliseconds(420))
            if discovery.isRunning { withAnimation(Motion.gentle) { discovery = .grouping } }
            try? await Task.sleep(for: .milliseconds(420))
            if discovery.isRunning { withAnimation(Motion.gentle) { discovery = .saving } }
        }

        do {
            let result = try await intelligence.rebuildCollections()
            pacing.cancel()
            await load()
            withAnimation(Motion.standard) { discovery = phase(for: result) }
            if case .complete = discovery { Haptics.success() } else { Haptics.tap() }
        } catch {
            pacing.cancel()
            withAnimation(Motion.standard) {
                discovery = .failed((error as? APIError)?.userMessage
                                    ?? "Couldn't look for groupings.")
            }
            Haptics.error()
        }
        scheduleDiscoveryDismiss()
    }

    /// The result, as something to say.
    private func phase(for result: CollectionDiscovery) -> DiscoveryPhase {
        switch result.status {
        case "not_enough_content":
            return .notEnoughContent(minimum: result.minimum ?? 3)
        case "empty_library":
            return .notEnoughContent(minimum: result.minimum ?? 3)
        default:
            if result.foundNothingNew { return .noNewGroups }
            return .complete(created: result.created, updated: result.updated)
        }
    }

    /// A result is news; guidance is not.
    ///
    /// "2 new groups" and "No new groups yet" are answers to something the user
    /// just did, and become clutter once read — they fade. "Save a few more
    /// things" is an instruction they have to act on, and a failure has a
    /// button on it; both stay until the next attempt.
    private func scheduleDiscoveryDismiss() {
        switch discovery {
        case .complete, .noNewGroups: break
        default: return
        }
        discoveryDismiss = Task { @MainActor in
            try? await Task.sleep(for: .seconds(4))
            guard !Task.isCancelled else { return }
            withAnimation(Motion.gentle) { discovery = .idle }
        }
    }

    private func retry() {
        Task { await rebuild() }
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

/// The discovery phase, as an `ActionStatus`.
///
/// `DiscoveryBanner` used to be its own view with its own glyphs, colours and
/// layout — a second implementation of "say what happened" that would drift
/// from the first the moment either was touched. It is now a mapping onto
/// `InlineStatus`, so there is one component and one set of rules about which
/// results fade.
extension DiscoveryPhase {
    var actionStatus: ActionStatus? {
        switch self {
        case .idle: return nil
        case .starting, .analyzing, .grouping, .saving:
            return .working(message ?? "")
        case .complete:
            return .success(message ?? "")
        case .noNewGroups, .notEnoughContent:
            return .info(message ?? "")
        case .failed:
            return .failure(message ?? "")
        }
    }
}
