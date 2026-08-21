import PhotosUI
import SwiftUI

/// Change Cover.
///
/// Three sources, in the order they are most likely to be wanted: images Sava
/// found for this subject, images already inside the collection, and a photo
/// from the user's own library. Everything is a tile in one grid at one size,
/// so the screen reads as a set of options rather than as three configuration
/// sections.
///
/// A choice made here is authoritative — the server marks the cover as the
/// user's and automatic selection never overwrites it — which is why "Use
/// Automatic Cover" is offered explicitly rather than left implicit.
struct CoverPickerSheet: View {
    let collection: SavaCollection
    var onChanged: () -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss

    @State private var suggestions: CoverSuggestions?
    @State private var loading = true
    @State private var applying: String?
    @State private var photo: PhotosPickerItem?
    @State private var failure: String?

    private var intelligence: IntelligenceService { IntelligenceService(client: session.api) }

    private let columns = [
        GridItem(.flexible(), spacing: Space.m),
        GridItem(.flexible(), spacing: Space.m),
        GridItem(.flexible(), spacing: Space.m),
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Space.xl) {
                    if loading {
                        skeleton
                    } else {
                        if let failure {
                            Text(failure)
                                .font(SavaType.callout)
                                .foregroundStyle(SavaColor.secondary)
                        }
                        section("Suggested", suggestions?.suggested ?? [],
                                source: "suggested")
                        section("From this collection", suggestions?.fromCollection ?? [],
                                source: "collection_media")
                        uploadRow
                        if collection.hasManualCover { automaticRow }
                    }
                }
                .screenPadding()
                .padding(.vertical, Space.l)
            }
            .background(SavaColor.ground)
            .navigationTitle("Change cover")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .tint(SavaColor.primary)
        .task { await load() }
        .onChange(of: photo) { _, item in
            guard let item else { return }
            Task { await upload(item) }
        }
    }

    // MARK: Sections

    @ViewBuilder private func section(_ title: String, _ items: [CoverCandidate],
                                      source: String) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: Space.m) {
                SectionHeader(text: title)
                LazyVGrid(columns: columns, spacing: Space.m) {
                    ForEach(items) { candidate in
                        tile(candidate, source: source)
                    }
                }
            }
        }
    }

    /// Every tile is the same square regardless of the image inside it, so the
    /// grid stays aligned while pictures arrive at different times and in
    /// different shapes.
    private func tile(_ candidate: CoverCandidate, source: String) -> some View {
        Button {
            Task { await apply(candidate, source: source) }
        } label: {
            Color.clear
                .aspectRatio(1, contentMode: .fit)
                .overlay {
                    MediaImage(url: candidate.url, fallback: .transparent,
                               fit: .fill, cornerRadius: 0)
                }
                .background(SavaColor.fill)
                .clipShape(RoundedRectangle(cornerRadius: Radius.media,
                                            style: .continuous))
                .overlay {
                    if applying == candidate.candidateID {
                        ZStack {
                            Color.black.opacity(0.45)
                            ProgressView().tint(.white)
                        }
                        .clipShape(RoundedRectangle(cornerRadius: Radius.media,
                                                    style: .continuous))
                    }
                }
        }
        .buttonStyle(.pressable)
        .disabled(applying != nil)
        .accessibilityLabel(candidate.title ?? "Cover option")
    }

    private var uploadRow: some View {
        PhotosPicker(selection: $photo, matching: .images, photoLibrary: .shared()) {
            SavaRow(title: "Upload a photo",
                    detail: "From your phone",
                    symbol: "photo.on.rectangle", showsChevron: false)
        }
        .buttonStyle(.plain)
        .disabled(applying != nil)
    }

    private var automaticRow: some View {
        Button {
            Task { await resetToAutomatic() }
        } label: {
            SavaRow(title: "Use automatic cover",
                    detail: "Let Sava choose", symbol: "arrow.clockwise",
                    showsChevron: false)
        }
        .buttonStyle(.plain)
        .disabled(applying != nil)
    }

    private var skeleton: some View {
        LazyVGrid(columns: columns, spacing: Space.m) {
            ForEach(0..<9, id: \.self) { _ in
                Skeleton().aspectRatio(1, contentMode: .fit)
            }
        }
        .accessibilityHidden(true)
    }

    // MARK: Actions

    private func load() async {
        suggestions = try? await intelligence.coverSuggestions(collectionID: collection.id)
        if suggestions == nil {
            failure = "Couldn't load cover suggestions. You can still upload a photo."
        }
        loading = false
    }

    private func apply(_ candidate: CoverCandidate, source: String) async {
        applying = candidate.candidateID
        defer { applying = nil }
        do {
            try await intelligence.setCover(
                collectionID: collection.id,
                imageURL: candidate.bookmarkID == nil ? candidate.imageURL : nil,
                bookmarkID: candidate.bookmarkID, source: source)
            Haptics.success()
            onChanged()
            dismiss()
        } catch {
            failure = "Couldn't use that image."
        }
    }

    private func upload(_ item: PhotosPickerItem) async {
        applying = "upload"
        defer { applying = nil; photo = nil }
        guard let data = try? await item.loadTransferable(type: Data.self),
              let image = UIImage(data: data),
              let jpeg = image.jpegData(compressionQuality: 0.85) else {
            failure = "Couldn't read that photo."
            return
        }
        do {
            try await intelligence.uploadCover(collectionID: collection.id, jpeg: jpeg)
            Haptics.success()
            onChanged()
            dismiss()
        } catch {
            failure = "Couldn't upload that photo."
        }
    }

    private func resetToAutomatic() async {
        applying = "automatic"
        defer { applying = nil }
        try? await intelligence.resetCover(collectionID: collection.id)
        Haptics.tap()
        onChanged()
        dismiss()
    }
}
