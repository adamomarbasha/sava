import SwiftUI

/// The two-column masonry every media surface uses — library, search, inside a
/// collection.
///
/// Cards have no border, no shadow and no container. The image *is* the card and
/// the caption sits directly on the page beneath it, so the only structure on
/// screen comes from the grid's rhythm. That rhythm is what makes deterministic
/// aspect ratios matter: with them, two columns of mixed TikTok and YouTube read
/// as a designed page; without them, as a pile.
struct MediaGrid: View {
    let bookmarks: [Bookmark]
    var onDelete: ((Bookmark) -> Void)? = nil
    /// Taking a save out of the collection being viewed, which is a different
    /// act from deleting it — the save stays in the library. Only supplied when
    /// the grid is showing a collection.
    var onRemove: ((Bookmark) -> Void)? = nil

    @EnvironmentObject private var shortForm: ShortFormContext

    /// Column assignment is computed from *relative* card heights — aspect ratio
    /// plus a caption allowance, both expressed in column widths. Because the
    /// ratio is fixed by us, this needs no measurement, no `GeometryReader`, and
    /// no second layout pass. That is what keeps the two `LazyVStack`s lazy, so
    /// a library of thousands only builds the rows on screen.
    private var columns: [[Bookmark]] {
        var columns: [[Bookmark]] = [[], []]
        var heights: [CGFloat] = [0, 0]
        for bookmark in bookmarks {
            let index = heights[0] <= heights[1] ? 0 : 1
            columns[index].append(bookmark)
            heights[index] += 1 / MediaRatio.forPlatform(bookmark.platform) + captionAllowance
        }
        return columns
    }

    /// Title, meta line and the gap below, as a fraction of column width.
    private let captionAllowance: CGFloat = 0.34

    /// A card, plus — for anything that plays — a small affordance that opens
    /// the viewer directly.
    ///
    /// The play control is layered *beside* the navigation link rather than
    /// inside it, because a button nested in a `NavigationLink`'s label does
    /// not reliably win the tap. The transparent sizer above it reproduces the
    /// thumbnail's geometry so the glyph lands on the image and not on the
    /// caption, and is explicitly not hit-testable so the rest of the card
    /// still opens the detail screen.
    @ViewBuilder private func cell(for bookmark: Bookmark) -> some View {
        ZStack(alignment: .top) {
            NavigationLink(value: bookmark) {
                SaveCard(bookmark: bookmark)
            }
            .buttonStyle(.pressable)

            if bookmark.isShortForm {
                Color.clear
                    .aspectRatio(MediaRatio.forPlatform(bookmark.platform),
                                 contentMode: .fit)
                    .allowsHitTesting(false)
                    .overlay(alignment: .bottomLeading) {
                        PlayAffordance { shortForm.open(bookmark) }
                            .padding(Space.s)
                    }
            }
        }
        .contextMenu {
            if bookmark.isShortForm {
                Button {
                    shortForm.open(bookmark)
                } label: { Label("Play", systemImage: "play.fill") }
            }
            if let onRemove {
                Button {
                    onRemove(bookmark)
                } label: { Label("Remove from collection", systemImage: "minus.circle") }
            }
            if let onDelete {
                Button(role: .destructive) {
                    onDelete(bookmark)
                } label: { Label("Delete", systemImage: "trash") }
            }
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: Space.gutter) {
            ForEach(Array(columns.enumerated()), id: \.offset) { _, column in
                LazyVStack(spacing: Space.row) {
                    ForEach(column) { bookmark in
                        cell(for: bookmark)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .top)
            }
        }
    }
}

/// One save. Media, then two lines of quiet text. No card, no chrome.
struct SaveCard: View {
    let bookmark: Bookmark

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            MediaThumbnail(bookmark: bookmark)

            VStack(alignment: .leading, spacing: 3) {
                Text(bookmark.displayTitle)
                    .font(SavaType.mediaTitle)
                    .foregroundStyle(SavaColor.primary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)

                Text(bookmark.gridMetaLine)
                    .font(SavaType.meta)
                    .foregroundStyle(SavaColor.tertiary)
                    .lineLimit(1)
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(bookmark.displayTitle). \(bookmark.metaLine)")
        .accessibilityHint("Opens this item")
    }
}

/// Loading placeholder that matches the real grid's rhythm, so nothing jumps
/// when the content arrives.
struct MediaGridSkeleton: View {
    var rows: Int = 3

    private let ratios: [CGFloat] = [
        MediaRatio.portrait, MediaRatio.landscape, MediaRatio.landscape,
        MediaRatio.portrait, MediaRatio.portrait, MediaRatio.landscape,
    ]

    var body: some View {
        HStack(alignment: .top, spacing: Space.gutter) {
            ForEach(0..<2, id: \.self) { column in
                VStack(spacing: Space.row) {
                    ForEach(0..<rows, id: \.self) { row in
                        VStack(alignment: .leading, spacing: Space.s) {
                            Skeleton()
                                .aspectRatio(ratios[(column * rows + row) % ratios.count],
                                             contentMode: .fit)
                            Skeleton(cornerRadius: 4).frame(height: 12)
                            Skeleton(cornerRadius: 4).frame(width: 90, height: 10)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .top)
            }
        }
        .accessibilityHidden(true)
        .accessibilityLabel("Loading")
    }
}
