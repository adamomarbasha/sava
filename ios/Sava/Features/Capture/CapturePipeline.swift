import Foundation

/// Decides *how* to capture what the user is looking at, then saves it.
///
/// Strategy priority (per Sava's capture design):
///   1. DIRECT URL          — a URL the Shortcut extracted (TikTok on-screen URL)
///   2. SCREENSHOT RESOLVE   — only if no direct URL; send evidence to the
///                             backend resolver (YouTube/Instagram)
///   3. CLIPBOARD FALLBACK   — a supported copy-link on the pasteboard
///   4. GRACEFUL FAILURE     — a clear, honest message
///
/// A screenshot is only used when a direct URL is unavailable — never captured
/// or uploaded redundantly.
struct CapturePipeline {
    let bookmarks: BookmarkService
    let resolver: ContentResolverService

    /// Resolve the input into a saveable link, without saving yet.
    func resolve(_ input: CaptureInput, clipboard: String?) async -> CaptureResolution {
        // 1. Direct URL — highest priority, no screenshot work.
        if let link = CapturedLink(rawURL: input.providedURL, source: .direct) {
            return .ready(link)
        }

        // 2. Screenshot resolution — only when we lack a direct URL.
        if let screenshot = input.screenshot {
            switch await resolver.resolve(screenshot: screenshot) {
            case .resolved(let url):
                if let link = CapturedLink(rawURL: url, source: .resolved) {
                    return .ready(link)
                }
            case .notConfident, .unavailable:
                break // fall through to clipboard
            }
        }

        // 3. Clipboard / copy-link fallback.
        if let link = CapturedLink(rawURL: clipboard, source: .clipboard) {
            return .ready(link)
        }

        // 4. Distinguish "we had evidence but can't resolve yet" from "nothing".
        if input.screenshot != nil { return .needsResolutionUnavailable }
        return .nothingFound
    }

    /// Resolve + save. Returns the created bookmark on success.
    func run(_ input: CaptureInput, clipboard: String?) async throws -> Bookmark {
        guard SavaClient.hasStoredToken else { throw CaptureError.notSignedIn }

        switch await resolve(input, clipboard: clipboard) {
        case .ready(let link):
            do {
                return try await bookmarks.create(url: link.url)
            } catch let error as APIError {
                // A 409 means it's already saved — treat as a soft success by
                // re-throwing a friendly message the intent can surface.
                throw CaptureError.saveFailed(error.userMessage)
            }
        case .needsResolutionUnavailable:
            throw CaptureError.saveFailed(
                "Couldn't read the link here yet. Copy the link and try again — automatic detection is coming soon.")
        case .nothingFound:
            throw CaptureError.saveFailed(
                "No link found. Share or copy the video's link, then press again.")
        }
    }
}
