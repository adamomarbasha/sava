import SwiftUI

/// A masonry of shimmering placeholders shown during the first load, so the
/// library feels instantly present instead of blank.
struct LibrarySkeleton: View {
    private let aspects: [CGFloat] = [9.0/16.0, 13.0/9.0, 9.0/16.0, 13.0/9.0,
                                      13.0/9.0, 9.0/16.0, 9.0/16.0, 13.0/9.0]

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            column(indices: [0, 2, 4, 6])
            column(indices: [1, 3, 5, 7])
        }
        .accessibilityHidden(true)
    }

    private func column(indices: [Int]) -> some View {
        VStack(spacing: Spacing.md) {
            ForEach(indices, id: \.self) { i in
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    ShimmerPlaceholder()
                        .aspectRatio(aspects[i], contentMode: .fill)
                        .frame(maxWidth: .infinity)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    ShimmerPlaceholder()
                        .frame(height: 12)
                        .frame(maxWidth: .infinity)
                        .clipShape(Capsule())
                    ShimmerPlaceholder()
                        .frame(width: 90, height: 10)
                        .clipShape(Capsule())
                }
            }
        }
    }
}
