import SwiftUI

/// The bookmark detail experience. Hierarchy: content → understanding →
/// interaction → metadata. Only sections backed by real endpoints appear.
struct BookmarkDetailView: View {
    @EnvironmentObject private var session: SessionStore
    @StateObject private var model: DetailViewModel

    init(bookmark: Bookmark) {
        _model = StateObject(wrappedValue: DetailViewModel(bookmark: bookmark))
    }

    private var content: ContentService { ContentService(client: session.api) }
    private var bookmark: Bookmark { model.bookmark }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.lg) {
                hero
                header
                OpenOriginalButton(bookmark: bookmark)
                AskSavaSection(bookmark: bookmark)
                TranscriptSection(model: model, service: content)
                CommentsSection(model: model, service: content)
                MetadataSection(bookmark: bookmark)
            }
            .padding(.bottom, 120)
        }
        .background(SavaColors.background)
        .navigationTitle(bookmark.platform.displayName)
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: Hero media

    private var hero: some View {
        let aspect: CGFloat = bookmark.platform.prefersPortrait ? 4.0/5.0 : 16.0/9.0
        return RemoteImage(url: ThumbnailURL.resolve(bookmark.thumbnailURL), platform: bookmark.platform)
            .aspectRatio(aspect, contentMode: .fill)
            .frame(maxWidth: .infinity)
            .frame(maxHeight: 460)
            .clipped()
            .overlay(alignment: .bottomLeading) {
                if let duration = Format.duration(bookmark.meta?.durationSeconds) {
                    Text(duration)
                        .font(.system(size: 12, weight: .bold, design: .rounded))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 8).padding(.vertical, 4)
                        .background(.black.opacity(0.6), in: Capsule())
                        .padding(Spacing.md)
                }
            }
            .overlay(alignment: .bottom) {
                LinearGradient(colors: [.clear, SavaColors.background], startPoint: .top, endPoint: .bottom)
                    .frame(height: 60)
            }
    }

    // MARK: Title + author + stats + note

    private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            Text(bookmark.displayTitle)
                .font(SavaFont.title2)
                .foregroundStyle(SavaColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: Spacing.xs) {
                PlatformBadge(platform: bookmark.platform, size: 24)
                if let author = bookmark.displayAuthor {
                    Text(author)
                        .font(SavaFont.subheadline)
                        .foregroundStyle(SavaColors.textSecondary)
                        .lineLimit(1)
                }
                Spacer()
                if let views = Format.compactCount(bookmark.meta?.viewCount) {
                    StatChip(icon: "eye.fill", value: views)
                }
                if let likes = Format.compactCount(bookmark.meta?.likeCount) {
                    StatChip(icon: "hand.thumbsup.fill", value: likes)
                }
            }

            if let note = bookmark.note, !note.trimmingCharacters(in: .whitespaces).isEmpty {
                HStack(alignment: .top, spacing: Spacing.xs) {
                    Image(systemName: "note.text")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(SavaColors.accent)
                    Text(note)
                        .font(SavaFont.callout)
                        .foregroundStyle(SavaColors.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(Spacing.sm)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(SavaColors.accentSoft, in: RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            }
        }
        .padding(.horizontal, Spacing.md)
    }
}
