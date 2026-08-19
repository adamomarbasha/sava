import Foundation

/// Fetches the real analysis data the backend supports for a bookmark:
/// transcripts (YouTube via captions API, TikTok via server-side transcription)
/// and saved comments. No AI text is synthesized here.
struct ContentService {
    let client: APIClient

    /// `POST /api/transcript` — works for YouTube and TikTok URLs. Public.
    func transcript(for urlOrID: String) async throws -> TranscriptResponse {
        struct Body: Encodable { let video_url_or_id: String }
        let endpoint = Endpoint.json("api/transcript", method: .post,
                                     body: Body(video_url_or_id: urlOrID),
                                     requiresAuth: false)
        return try await client.send(endpoint)
    }

    /// `GET /api/comments/{bookmark_id}` — reads saved comments from the DB.
    func savedComments(bookmarkID: Int, limit: Int = 30) async throws -> CommentsResponse {
        let endpoint = Endpoint(path: "api/comments/\(bookmarkID)",
                                method: .get,
                                query: [URLQueryItem(name: "limit", value: String(limit))],
                                requiresAuth: false)
        return try await client.send(endpoint)
    }
}
