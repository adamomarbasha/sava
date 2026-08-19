import SwiftUI

/// A compact platform identifier — glyph on a tinted disc.
struct PlatformBadge: View {
    let platform: Platform
    var size: CGFloat = 22

    var body: some View {
        Image(systemName: platform.symbol)
            .font(.system(size: size * 0.5, weight: .bold))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(Circle().fill(platform.tint))
            .overlay(Circle().strokeBorder(.white.opacity(0.25), lineWidth: 0.5))
            .accessibilityLabel(platform.displayName)
    }
}

/// A quiet "Analyzing" indicator — shown only for items saved this session
/// while the backend ingests/analyzes them. Never fabricated for fetched items.
struct ProcessingPill: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pulse = false

    var body: some View {
        HStack(spacing: 5) {
            Circle()
                .fill(SavaColors.accent)
                .frame(width: 6, height: 6)
                .opacity(pulse && !reduceMotion ? 0.35 : 1)
            Text("Analyzing")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(SavaColors.textPrimary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(.ultraThinMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(SavaColors.hairline, lineWidth: 0.5))
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { pulse = true }
        }
        .accessibilityLabel("Still analyzing")
    }
}

/// A content-first bookmark tile: the media leads, with a tight caption below.
struct BookmarkCard: View {
    let bookmark: Bookmark
    var isProcessing: Bool = false

    private var thumbURL: URL? { ThumbnailURL.resolve(bookmark.thumbnailURL) }
    private var aspect: CGFloat { bookmark.platform.prefersPortrait ? 9.0/13.0 : 16.0/9.0 }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            media
            caption
        }
        .contentShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
    }

    private var media: some View {
        RemoteImage(url: thumbURL, platform: bookmark.platform)
            .aspectRatio(aspect, contentMode: .fill)
            .frame(maxWidth: .infinity)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .overlay(alignment: .topLeading) {
                PlatformBadge(platform: bookmark.platform)
                    .padding(Spacing.xs)
            }
            .overlay(alignment: .topTrailing) {
                if isProcessing {
                    ProcessingPill().padding(Spacing.xs)
                }
            }
            .overlay(alignment: .bottomTrailing) {
                if let duration = Format.duration(bookmark.meta?.durationSeconds) {
                    Text(duration)
                        .font(.system(size: 11, weight: .bold, design: .rounded))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 6).padding(.vertical, 3)
                        .background(.black.opacity(0.6), in: Capsule())
                        .padding(Spacing.xs)
                }
            }
            .overlay(
                RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .strokeBorder(SavaColors.hairline, lineWidth: 0.5)
            )
    }

    private var caption: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(bookmark.displayTitle)
                .font(SavaFont.subheadline)
                .foregroundStyle(SavaColors.textPrimary)
                .lineLimit(2)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 4) {
                if let author = bookmark.displayAuthor {
                    Text(author).lineLimit(1)
                }
                if bookmark.displayAuthor != nil, Format.relativeAge(bookmark.createdAt) != nil {
                    Text("·")
                }
                if let age = Format.relativeAge(bookmark.createdAt) {
                    Text(age)
                }
            }
            .font(SavaFont.caption)
            .foregroundStyle(SavaColors.textTertiary)
            .lineLimit(1)
        }
        .padding(.horizontal, 2)
    }
}
