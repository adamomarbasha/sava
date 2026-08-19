import Foundation

/// Client for Sava's intelligence endpoints.
///
/// Replaces the previous placeholder that hard-coded `isEnabled = false`; these
/// endpoints now exist server-side. Provider credentials stay on the server —
/// the app never holds an API key.
struct IntelligenceService {
    let client: APIClient

    // MARK: Processing

    /// `GET /api/bookmarks/{id}/status` — where a save is in the ladder.
    func status(bookmarkID: Int) async throws -> ProcessingStatus {
        try await client.send(Endpoint(path: "api/bookmarks/\(bookmarkID)/status", method: .get))
    }

    /// `POST /api/bookmarks/{id}/reprocess` — re-queue after a failure.
    @discardableResult
    func reprocess(bookmarkID: Int, force: Bool = false) async throws -> Data {
        try await client.send(Endpoint(
            path: "api/bookmarks/\(bookmarkID)/reprocess", method: .post,
            query: [URLQueryItem(name: "force", value: force ? "true" : "false")]
        ))
    }

    // MARK: Summary

    /// `GET /api/bookmarks/{id}/summary` — cached structured understanding.
    /// Generated once server-side and shared by everyone who saved the content.
    func summary(bookmarkID: Int, refresh: Bool = false) async throws -> SaveUnderstanding {
        var query: [URLQueryItem] = []
        if refresh { query.append(URLQueryItem(name: "refresh", value: "true")) }
        return try await client.send(Endpoint(
            path: "api/bookmarks/\(bookmarkID)/summary", method: .get, query: query))
    }

    // MARK: Ask

    /// `POST /api/bookmarks/{id}/ask` — grounded Q&A about one save.
    func askThis(bookmarkID: Int, question: String, mode: AskMode = .auto,
                 threadID: Int? = nil) async throws -> AskAnswer {
        struct Body: Encodable {
            let question: String
            let mode: String
            let thread_id: Int?
        }
        return try await client.send(Endpoint.json(
            "api/bookmarks/\(bookmarkID)/ask", method: .post,
            body: Body(question: question, mode: mode.rawValue, thread_id: threadID)))
    }

    /// `POST /api/ask` — library-wide question. Retrieval happens before
    /// generation; the whole library is never sent to a model.
    func askSava(question: String, mode: AskMode = .auto,
                 threadID: Int? = nil) async throws -> AskAnswer {
        struct Body: Encodable {
            let question: String
            let mode: String
            let thread_id: Int?
        }
        return try await client.send(Endpoint.json(
            "api/ask", method: .post,
            body: Body(question: question, mode: mode.rawValue, thread_id: threadID)))
    }

    // MARK: Retrieval

    /// `GET /api/search` — hybrid semantic + keyword. No model runs; fast.
    func search(query: String, platform: Platform? = nil, limit: Int = 30) async throws -> SearchResponse {
        var items = [URLQueryItem(name: "q", value: query),
                     URLQueryItem(name: "limit", value: String(limit))]
        if let platform { items.append(URLQueryItem(name: "platform", value: platform.rawValue)) }
        return try await client.send(Endpoint(path: "api/search", method: .get, query: items))
    }

    /// `GET /api/bookmarks/{id}/related` — vector similarity, zero inference.
    func related(bookmarkID: Int, limit: Int = 8) async throws -> [RelatedSave] {
        struct Response: Decodable { let results: [RelatedSave] }
        let response: Response = try await client.send(Endpoint(
            path: "api/bookmarks/\(bookmarkID)/related", method: .get,
            query: [URLQueryItem(name: "limit", value: String(limit))]))
        return response.results
    }

    /// `GET /api/resurface` — deterministic ranking, no inference.
    func resurface(limit: Int = 6) async throws -> [RelatedSave] {
        struct Response: Decodable { let results: [RelatedSave] }
        let response: Response = try await client.send(Endpoint(
            path: "api/resurface", method: .get,
            query: [URLQueryItem(name: "limit", value: String(limit))]))
        return response.results
    }

    // MARK: Collections

    func collections() async throws -> [SavaCollection] {
        struct Response: Decodable { let collections: [SavaCollection] }
        let response: Response = try await client.send(
            Endpoint(path: "api/collections", method: .get))
        return response.collections
    }

    /// Creates immediately and returns likely members for the user to confirm.
    func createCollection(name: String, description: String? = nil) async throws -> CollectionCreated {
        struct Body: Encodable {
            let name: String
            let description: String?
            let auto_populate: Bool
        }
        return try await client.send(Endpoint.json(
            "api/collections", method: .post,
            body: Body(name: name, description: description, auto_populate: false)))
    }

    struct CollectionCreated: Decodable {
        let id: Int
        let name: String
        let kind: String
        let suggestions: [Suggestion]

        struct Suggestion: Decodable, Identifiable {
            let bookmarkID: Int
            let title: String?
            let author: String?
            let thumbnailURL: String?
            let score: Double
            var id: Int { bookmarkID }

            enum CodingKeys: String, CodingKey {
                case bookmarkID = "bookmark_id"
                case title, author, score
                case thumbnailURL = "thumbnail_url"
            }
        }
    }

    @discardableResult
    func addToCollection(collectionID: Int, bookmarkIDs: [Int]) async throws -> Data {
        struct Body: Encodable { let bookmark_ids: [Int] }
        return try await client.send(Endpoint.json(
            "api/collections/\(collectionID)/items", method: .post,
            body: Body(bookmark_ids: bookmarkIDs)))
    }

    /// Rebuilds automatic collections from the user's own save patterns.
    @discardableResult
    func rebuildCollections() async throws -> Data {
        try await client.send(Endpoint(path: "api/collections/rebuild", method: .post))
    }
}
