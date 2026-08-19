import SwiftUI

/// A titled content section used throughout the detail screen.
struct DetailSection<Content: View>: View {
    let title: String
    var subtitle: String? = nil
    var systemImage: String? = nil
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            HStack(spacing: Spacing.xs) {
                if let systemImage {
                    Image(systemName: systemImage)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(SavaColors.accent)
                }
                Text(title)
                    .font(SavaFont.headline)
                    .foregroundStyle(SavaColors.textPrimary)
                Spacer()
                if let subtitle {
                    Text(subtitle)
                        .font(SavaFont.caption)
                        .foregroundStyle(SavaColors.textTertiary)
                }
            }
            content()
        }
        .padding(.horizontal, Spacing.md)
    }
}

/// A small labeled stat (views, likes).
struct StatChip: View {
    let icon: String
    let value: String

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: icon).font(.system(size: 12, weight: .semibold))
            Text(value).font(SavaFont.footnote).monospacedDigit()
        }
        .foregroundStyle(SavaColors.textSecondary)
    }
}

/// Opens the original content in its native app / browser.
struct OpenOriginalButton: View {
    let bookmark: Bookmark
    @Environment(\.openURL) private var openURL

    var body: some View {
        Button {
            guard let url = URL(string: bookmark.url) else { return }
            Haptics.tap()
            openURL(url)
        } label: {
            HStack(spacing: Spacing.xs) {
                Image(systemName: "arrow.up.forward.app.fill")
                Text("Open in \(bookmark.platform.displayName)")
            }
            .font(SavaFont.headline)
            .foregroundStyle(SavaColors.background)
            .frame(maxWidth: .infinity)
            .frame(height: 52)
            .background(SavaColors.textPrimary, in: RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        }
        .buttonStyle(.pressable)
        .padding(.horizontal, Spacing.md)
    }
}
