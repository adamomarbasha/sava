import SwiftUI

/// Loads the real analysis data for a bookmark on demand. Each data source has
/// its own honest state so the UI can show exactly what's available and never
/// fabricate the rest.
@MainActor
final class DetailViewModel: ObservableObject {
    enum TranscriptState: Equatable {
        case idle, loading, loaded([TranscriptSegment], language: String?)
        case unsupported, unavailable(String), failed(String)
    }
    enum CommentsState: Equatable {
        case idle, loading, loaded([SavedComment]), empty, failed(String)
    }

    @Published private(set) var transcriptState: TranscriptState = .idle
    @Published private(set) var commentsState: CommentsState = .idle

    let bookmark: Bookmark

    init(bookmark: Bookmark) {
        self.bookmark = bookmark
    }

    private var supportsTranscript: Bool {
        bookmark.platform == .youtube || bookmark.platform == .tiktok
    }

    func loadTranscript(_ service: ContentService) async {
        guard supportsTranscript else { transcriptState = .unsupported; return }
        guard transcriptState == .idle else { return }
        transcriptState = .loading
        do {
            let response = try await service.transcript(for: bookmark.url)
            if response.success, let segments = response.transcript, !segments.isEmpty {
                transcriptState = .loaded(segments, language: response.language)
            } else {
                transcriptState = .unavailable(response.error ?? "No transcript is available for this video.")
            }
        } catch is CancellationError {
            transcriptState = .idle
        } catch {
            transcriptState = .failed((error as? APIError)?.userMessage ?? "Couldn't load the transcript.")
        }
    }

    func loadComments(_ service: ContentService) async {
        guard commentsState == .idle else { return }
        commentsState = .loading
        do {
            let response = try await service.savedComments(bookmarkID: bookmark.id)
            if let comments = response.comments, !comments.isEmpty {
                commentsState = .loaded(comments)
            } else {
                commentsState = .empty
            }
        } catch is CancellationError {
            commentsState = .idle
        } catch {
            commentsState = .failed((error as? APIError)?.userMessage ?? "Couldn't load comments.")
        }
    }
}
