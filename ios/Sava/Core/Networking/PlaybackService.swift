import Foundation

/// Client for the playback endpoints.
struct PlaybackService {
    let client: APIClient

    /// `GET /api/bookmarks/{id}/playback`
    ///
    /// Resolving a TikTok stream is a live call to the platform, so this is
    /// slower than the app's other GETs and is given room accordingly. The
    /// viewer treats a failure as a renderable state rather than an error.
    func descriptor(bookmarkID: Int) async throws -> PlaybackDescriptor {
        try await client.send(Endpoint(
            path: "api/bookmarks/\(bookmarkID)/playback", method: .get,
            timeout: 45))
    }
}
