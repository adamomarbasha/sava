import SwiftUI

/// The Sava glyph: a bookmark ribbon on an ink tile. Simple, geometric, and
/// recognizable at any size — used in the launch splash, auth header, and nav.
struct SavaMark: View {
    var size: CGFloat = 64
    var showsTile: Bool = true

    var body: some View {
        ZStack {
            if showsTile {
                RoundedRectangle(cornerRadius: size * 0.28, style: .continuous)
                    .fill(SavaColors.textPrimary)
                    .overlay(
                        RoundedRectangle(cornerRadius: size * 0.28, style: .continuous)
                            .strokeBorder(Color.white.opacity(0.08), lineWidth: 1)
                    )
            }
            BookmarkRibbon()
                .fill(showsTile ? SavaColors.background : SavaColors.textPrimary)
                .overlay(
                    BookmarkRibbon()
                        .fill(SavaColors.accent)
                        .mask(
                            Rectangle()
                                .frame(height: size * 0.22)
                                .offset(y: size * 0.16)
                        )
                )
                .frame(width: size * 0.34, height: size * 0.44)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

/// A bookmark shape: rectangle with a V-notch cut from the bottom edge.
private struct BookmarkRibbon: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        p.addLine(to: CGPoint(x: rect.midX, y: rect.maxY - rect.height * 0.32))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        p.closeSubpath()
        return p
    }
}
