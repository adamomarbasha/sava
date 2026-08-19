import SwiftUI

/// Shows real saved comments for a bookmark. Loads quietly; if none are stored,
/// it renders nothing rather than inventing engagement.
struct CommentsSection: View {
    @ObservedObject var model: DetailViewModel
    let service: ContentService

    var body: some View {
        Group {
            switch model.commentsState {
            case .loaded(let comments):
                DetailSection(title: "What people are saying",
                              subtitle: "\(comments.count)",
                              systemImage: "quote.bubble") {
                    VStack(spacing: Spacing.sm) {
                        ForEach(comments.prefix(20)) { comment in
                            CommentRow(comment: comment)
                        }
                    }
                }
            default:
                EmptyView()
            }
        }
        .task { await model.loadComments(service) }
    }
}

private struct CommentRow: View {
    let comment: SavedComment

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: Spacing.xs) {
                if let author = comment.author, !author.isEmpty {
                    Text(author)
                        .font(SavaFont.caption)
                        .foregroundStyle(SavaColors.textSecondary)
                }
                Spacer()
                if let likes = Format.compactCount(comment.likeCount), comment.likeCount ?? 0 > 0 {
                    HStack(spacing: 3) {
                        Image(systemName: "heart.fill").font(.system(size: 9))
                        Text(likes).font(.system(size: 11, weight: .semibold)).monospacedDigit()
                    }
                    .foregroundStyle(SavaColors.textTertiary)
                }
            }
            Text(comment.text)
                .font(SavaFont.footnote)
                .foregroundStyle(SavaColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(Spacing.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .fill(SavaColors.surface)
                .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .strokeBorder(SavaColors.hairline, lineWidth: 1))
        )
    }
}
