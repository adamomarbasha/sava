import SwiftUI

/// A reusable two-column masonry of bookmark cards with navigation and an
/// optional delete action. Shared by the library and search results.
struct BookmarkGrid: View {
    let bookmarks: [Bookmark]
    var processingIDs: Set<Int> = []
    var onDelete: ((Bookmark) -> Void)? = nil

    var body: some View {
        let columns = Masonry.distribute(bookmarks, columns: 2) { estimatedHeight(for: $0) }
        HStack(alignment: .top, spacing: Spacing.md) {
            ForEach(0..<columns.count, id: \.self) { index in
                LazyVStack(spacing: Spacing.md) {
                    ForEach(columns[index]) { bookmark in
                        NavigationLink(value: bookmark) {
                            BookmarkCard(bookmark: bookmark,
                                         isProcessing: processingIDs.contains(bookmark.id))
                        }
                        .buttonStyle(CardPressStyle())
                        .contextMenu {
                            if let onDelete {
                                Button(role: .destructive) {
                                    onDelete(bookmark)
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    private func estimatedHeight(for bookmark: Bookmark) -> CGFloat {
        let colWidth = (UIScreen.main.bounds.width - Spacing.md * 3) / 2
        let mediaAspect: CGFloat = bookmark.platform.prefersPortrait ? 13.0/9.0 : 9.0/16.0
        return colWidth * mediaAspect + 56
    }
}
