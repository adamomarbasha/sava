import Foundation

/// Talks to the bookmark endpoints. Pure data access — no UI state.
struct BookmarkService {
    let client: APIClient

    /// `GET /api/bookmarks` with optional platform + query filters.
    func list(platform: Platform? = nil,
              query: String? = nil,
              limit: Int = 200,
              offset: Int = 0) async throws -> [Bookmark] {
        var items: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "offset", value: String(offset)),
        ]
        if let platform {
            items.append(URLQueryItem(name: "platform", value: platform.rawValue))
        }
        if let query, !query.trimmingCharacters(in: .whitespaces).isEmpty {
            items.append(URLQueryItem(name: "q", value: query))
        }
        return try await client.send(Endpoint(path: "api/bookmarks", method: .get, query: items))
    }

    /// `GET /api/bookmarks/{id}` — one save in the same shape as the list.
    /// Used to open a save that was referenced by an answer or a search result.
    func bookmark(id: Int) async throws -> Bookmark {
        try await client.send(Endpoint(path: "api/bookmarks/\(id)", method: .get))
    }

    /// `POST /bookmarks` — creates a bookmark from a URL. The backend resolves
    /// canonical identity and enriches asynchronously.
    @discardableResult
    func create(url: String) async throws -> Bookmark {
        struct Body: Encodable { let url: String }
        let endpoint = Endpoint.json("bookmarks", method: .post, body: Body(url: url))
        return try await client.send(endpoint)
    }

    /// `DELETE /api/bookmarks/{id}`.
    func delete(id: Int) async throws {
        try await client.send(Endpoint(path: "api/bookmarks/\(id)", method: .delete))
    }
}
