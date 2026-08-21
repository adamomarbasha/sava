import SwiftUI

/// The two-column media grid every media surface uses — library, search, inside
/// a collection.
///
/// Cards have no border, no shadow and no container. The image *is* the card and
/// the caption sits directly on the page beneath it.
///
/// **The problem this layout exists to solve.** The library is roughly 60%
/// landscape YouTube and 40% vertical short-form, and at the same width those
/// two shapes differ in height by more than 2x. Put one of each in a row and the
/// row must size to the taller, leaving a hole about 120pt deep under the
/// shorter — which is what made the grid look full of ugly gaps.
///
/// Two obvious fixes were tried and rejected:
///
///   * **Crop everything to one ratio.** Removes the gaps, but a 16:9 YouTube
///     thumbnail cropped to 4:5 loses a third of the frame.
///   * **Pack the columns independently (masonry).** Removes the gaps by
///     construction, but each column has to be its own `LazyVStack`, and two
///     lazy stacks estimate the height of their unrealised content separately.
///     Scrolling to the end left one column rendered and the other blank.
///
/// What actually works is to stop mixing shapes *within a row*. Items are
/// grouped so each row holds two cards of the same shape: a vertical row is two
/// 4:5 cards, a landscape row is two 16:9 cards. Every row is then exactly as
/// tall as its contents, so there is no dead space anywhere, both cards in a row
/// share a baseline, and each platform keeps its natural proportions.
///
/// The cost, stated plainly: order becomes *near*-chronological rather than
/// strictly chronological. An item waits only until the next item of the same
/// shape arrives — usually one or two positions — so recency still reads
/// correctly while the page stops looking broken.
struct MediaGrid: View {
    let bookmarks: [Bookmark]
    var onDelete: ((Bookmark) -> Void)? = nil
    /// Taking a save out of the collection being viewed, which is a different
    /// act from deleting it — the save stays in the library. Only supplied when
    /// the grid is showing a collection.
    var onRemove: ((Bookmark) -> Void)? = nil

    @EnvironmentObject private var shortForm: ShortFormContext

    /// Two equal columns, one gutter, cells pinned to the top of their row.
    private var columns: [GridItem] {
        [GridItem(.flexible(), spacing: Space.gutter, alignment: .top),
         GridItem(.flexible(), spacing: Space.gutter, alignment: .top)]
    }

    /// Reordered so that each row holds two cards of the same shape.
    ///
    /// Items are emitted as soon as they can be paired with the previous item of
    /// their own shape, which keeps the sequence as close to the original order
    /// as pairing allows. Any odd one out at the end is emitted last rather than
    /// dropped.
    private var paired: [Bookmark] {
        var vertical: [Bookmark] = []
        var landscape: [Bookmark] = []
        var out: [Bookmark] = []
        out.reserveCapacity(bookmarks.count)

        for bookmark in bookmarks {
            if MediaRatio.forItem(bookmark) < 1 {
                vertical.append(bookmark)
                if vertical.count == 2 { out.append(contentsOf: vertical); vertical.removeAll() }
            } else {
                landscape.append(bookmark)
                if landscape.count == 2 { out.append(contentsOf: landscape); landscape.removeAll() }
            }
        }
        // At most one of each can be left over. They go last, and are the only
        // place a mixed row can occur.
        out.append(contentsOf: vertical)
        out.append(contentsOf: landscape)
        return out
    }

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
                // Mirrors the thumbnail's box exactly, so the glyph always
                // lands on the image rather than drifting onto the caption.
                Color.clear
                    .aspectRatio(MediaRatio.forItem(bookmark), contentMode: .fit)
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
        LazyVGrid(columns: columns, alignment: .leading, spacing: Space.row) {
            ForEach(paired) { bookmark in
                cell(for: bookmark)
            }
        }
    }
}

/// One save. Media, then two lines of quiet text. No card, no chrome.
struct SaveCard: View {
    let bookmark: Bookmark

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            // Vertical media (TikTok, Instagram, Shorts) all share 4:5;
            // ordinary YouTube keeps its natural 16:9. The picture is cropped
            // to fill its box, never stretched — `MediaImage`'s grid fit is
            // already `.fill`, which scales to fill and clips.
            MediaThumbnail(bookmark: bookmark, ratio: MediaRatio.forItem(bookmark))

            VStack(alignment: .leading, spacing: 3) {
                Text(bookmark.displayTitle)
                    .font(SavaType.mediaTitle)
                    .foregroundStyle(SavaColor.primary)
                    // Reserves two lines whether or not the title needs them,
                    // so a one-line and a two-line card are the same height and
                    // every row in the grid has the same rhythm. Reserving
                    // lines rather than hardcoding a height keeps this correct
                    // under Dynamic Type.
                    .lineLimit(2, reservesSpace: true)
                    .multilineTextAlignment(.leading)

                Text(bookmark.gridMetaLine)
                    .font(SavaType.meta)
                    .foregroundStyle(SavaColor.tertiary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
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

    private var columns: [GridItem] {
        [GridItem(.flexible(), spacing: Space.gutter, alignment: .top),
         GridItem(.flexible(), spacing: Space.gutter, alignment: .top)]
    }

    var body: some View {
        // Same shape-paired rhythm as the real grid: rows are homogeneous, so
        // the placeholder occupies the geometry the content will.
        LazyVGrid(columns: columns, alignment: .leading, spacing: Space.row) {
            ForEach(0..<(rows * 2), id: \.self) { index in
                VStack(alignment: .leading, spacing: Space.s) {
                    Skeleton()
                        .aspectRatio((index / 2) % 2 == 0 ? MediaRatio.portrait
                                                          : MediaRatio.landscape,
                                     contentMode: .fit)
                    VStack(alignment: .leading, spacing: 3) {
                        Skeleton(cornerRadius: 4).frame(height: 12)
                        Skeleton(cornerRadius: 4).frame(height: 12)
                        Skeleton(cornerRadius: 4).frame(width: 90, height: 10)
                    }
                }
            }
        }
        .accessibilityHidden(true)
        .accessibilityLabel("Loading")
    }
}
