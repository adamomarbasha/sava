import SwiftUI

/// Raw-but-tidy details: source link, platform, dates, and YouTube tags.
/// Lowest in the hierarchy — understanding comes first, metadata last.
struct MetadataSection: View {
    let bookmark: Bookmark
    @Environment(\.openURL) private var openURL

    var body: some View {
        DetailSection(title: "Details", systemImage: "info.circle") {
            VStack(spacing: 0) {
                row("Platform", value: bookmark.platform.displayName)
                divider
                if let saved = bookmark.createdAt {
                    row("Saved", value: saved.formatted(date: .abbreviated, time: .omitted))
                    divider
                }
                if let published = bookmark.publishedAt {
                    row("Published", value: published.formatted(date: .abbreviated, time: .omitted))
                    divider
                }
                linkRow

                if let tags = bookmark.meta?.tags, !tags.isEmpty {
                    VStack(alignment: .leading, spacing: Spacing.xs) {
                        Text("Topics")
                            .font(SavaFont.caption)
                            .foregroundStyle(SavaColors.textTertiary)
                        FlowChips(items: Array(tags.prefix(12)))
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, Spacing.sm)
                }
            }
            .padding(Spacing.md)
            .background(
                RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                    .fill(SavaColors.surface)
                    .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                        .strokeBorder(SavaColors.hairline, lineWidth: 1))
            )
        }
    }

    private func row(_ label: String, value: String) -> some View {
        HStack {
            Text(label).font(SavaFont.footnote).foregroundStyle(SavaColors.textTertiary)
            Spacer()
            Text(value).font(SavaFont.subheadline).foregroundStyle(SavaColors.textPrimary)
        }
        .padding(.vertical, Spacing.xs)
    }

    private var linkRow: some View {
        Button {
            if let url = URL(string: bookmark.url) { Haptics.tap(); openURL(url) }
        } label: {
            HStack {
                Text("Source").font(SavaFont.footnote).foregroundStyle(SavaColors.textTertiary)
                Spacer()
                Text(URL(string: bookmark.url)?.host?.replacingOccurrences(of: "www.", with: "") ?? "Link")
                    .font(SavaFont.subheadline)
                    .foregroundStyle(SavaColors.accent)
                    .lineLimit(1)
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(SavaColors.accent)
            }
            .padding(.vertical, Spacing.xs)
        }
        .buttonStyle(.plain)
    }

    private var divider: some View {
        Rectangle().fill(SavaColors.hairline).frame(height: 1)
    }
}
