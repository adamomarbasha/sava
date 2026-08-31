import Foundation

/// Client for Sava's intelligence endpoints.
///
/// Replaces the previous placeholder that hard-coded `isEnabled = false`; these
/// endpoints now exist server-side. Provider credentials stay on the server —
/// the app never holds an API key.
struct IntelligenceService {
    let client: APIClient

    // MARK: Processing

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

    // MARK: Transcript

    /// `GET /api/bookmarks/{id}/transcript` — the stored transcript. Free: the
    /// text was acquired once at ingestion and cached against the content.
    func transcript(bookmarkID: Int) async throws -> Transcript {
        try await client.send(Endpoint(path: "api/bookmarks/\(bookmarkID)/transcript",
                                       method: .get))
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

    /// `POST /api/ask` — a question across the library, or scoped to one
    /// collection. Retrieval happens before generation; the whole library is
    /// never sent to a model.
    func askSava(question: String, mode: AskMode = .auto,
                 threadID: Int? = nil, collectionID: Int? = nil) async throws -> AskAnswer {
        struct Body: Encodable {
            let question: String
            let mode: String
            let thread_id: Int?
            let collection_id: Int?
        }
        return try await client.send(Endpoint.json(
            "api/ask", method: .post,
            body: Body(question: question, mode: mode.rawValue,
                       thread_id: threadID, collection_id: collectionID)))
    }

    // MARK: Conversations

    /// `GET /api/threads` — past conversations, most recently active first.
    /// `bookmarkID` narrows it to the conversations about one item.
    func threads(scope: String, bookmarkID: Int? = nil) async throws -> [ChatThreadSummary] {
        struct Response: Decodable { let threads: [ChatThreadSummary] }
        var query = [URLQueryItem(name: "scope", value: scope)]
        if let bookmarkID {
            query.append(URLQueryItem(name: "bookmark_id", value: String(bookmarkID)))
        }
        let response: Response = try await client.send(
            Endpoint(path: "api/threads", method: .get, query: query))
        return response.threads
    }

    /// `GET /api/threads/{id}/messages` — everything said in one conversation.
    func messages(threadID: Int) async throws -> [ChatMessageRecord] {
        struct Response: Decodable { let messages: [ChatMessageRecord] }
        let response: Response = try await client.send(
            Endpoint(path: "api/threads/\(threadID)/messages", method: .get))
        return response.messages
    }

    // MARK: Retrieval

    /// `GET /api/search` — hybrid semantic + keyword. No model runs; fast.
    func search(query: String, platform: Platform? = nil, limit: Int = 30) async throws -> SearchResponse {
        var items = [URLQueryItem(name: "q", value: query),
                     URLQueryItem(name: "limit", value: String(limit))]
        if let platform { items.append(URLQueryItem(name: "platform", value: platform.rawValue)) }
        return try await client.send(Endpoint(path: "api/search", method: .get, query: items))
    }

    /// `GET /api/search`, decoded as library items — **the** search.
    ///
    /// Search used to run two passes: `GET /api/bookmarks?q=` for the primary
    /// grid, then this endpoint for a secondary "Also related" strip. The first
    /// only matches `bookmarks.title/author/description/note`, so anything whose
    /// text lives on the canonical row or in the derived understanding —
    /// transcript summary, topics, entities — could not appear in the primary
    /// results at all, and surfaced under "Also related" instead. Searching
    /// "Speed" showed "No matches" above a saved TikTok titled "Speed was
    /// convinced…".
    ///
    /// One endpoint, one ranked list. The server already fuses lexical and
    /// semantic retrieval and dedupes by canonical id; splitting that back into
    /// two buckets on the client threw the ranking away and hid real matches.
    func searchLibrary(query: String, platform: Platform? = nil,
                       limit: Int = 60) async throws -> [Bookmark] {
        struct Response: Decodable { let results: [Bookmark] }
        var items = [URLQueryItem(name: "q", value: query),
                     URLQueryItem(name: "limit", value: String(limit))]
        if let platform { items.append(URLQueryItem(name: "platform", value: platform.rawValue)) }
        let response: Response = try await client.send(
            Endpoint(path: "api/search", method: .get, query: items))
        return response.results
    }

    /// `GET /api/bookmarks/{id}/related` — vector similarity, zero inference.
    func related(bookmarkID: Int, limit: Int = 8) async throws -> [RelatedSave] {
        struct Response: Decodable { let results: [RelatedSave] }
        let response: Response = try await client.send(Endpoint(
            path: "api/bookmarks/\(bookmarkID)/related", method: .get,
            query: [URLQueryItem(name: "limit", value: String(limit))]))
        return response.results
    }

    /// `GET /api/ask/suggestions` — opening questions built from this user's own
    /// library, so they are answerable and differ between opens.
    ///
    /// Failure is not surfaced: suggestions are an optional way in, and an error
    /// banner over an empty conversation would be worse than simply not offering
    /// any. The caller treats an empty array and a thrown error identically.
    func askSuggestions(scope: String, collectionID: Int? = nil,
                        bookmarkID: Int? = nil, limit: Int = 4) async throws -> [AskSuggestion] {
        struct Response: Decodable { let suggestions: [AskSuggestion] }
        var query = [URLQueryItem(name: "scope", value: scope),
                     URLQueryItem(name: "limit", value: String(limit))]
        if let collectionID {
            query.append(URLQueryItem(name: "collection_id", value: String(collectionID)))
        }
        if let bookmarkID {
            query.append(URLQueryItem(name: "bookmark_id", value: String(bookmarkID)))
        }
        let response: Response = try await client.send(
            Endpoint(path: "api/ask/suggestions", method: .get, query: query))
        return response.suggestions
    }

    // MARK: Collections

    func collections() async throws -> [SavaCollection] {
        struct Response: Decodable { let collections: [SavaCollection] }
        let response: Response = try await client.send(
            Endpoint(path: "api/collections", method: .get))
        return response.collections
    }

    /// `GET /api/collections/{id}` — the collection and its saves.
    func collection(id: Int) async throws -> CollectionDetail {
        try await client.send(Endpoint(path: "api/collections/\(id)", method: .get))
    }

    struct CollectionDetail: Decodable {
        let id: Int
        let name: String
        let kind: String
        let description: String?
        let items: [Bookmark]
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

    /// `PATCH /api/collections/{id}` — rename.
    ///
    /// Renaming an automatic collection makes it the user's: the server flips
    /// it to manual so no later rebuild can rename it back.
    @discardableResult
    func renameCollection(id: Int, name: String) async throws -> Data {
        try await client.send(Endpoint.json(
            "api/collections/\(id)", method: .patch, body: ["name": name]))
    }

    /// `DELETE /api/collections/{id}`
    ///
    /// For an automatic collection the server also records the rejection, so
    /// the grouping is not rediscovered and recreated on the next rebuild.
    @discardableResult
    func deleteCollection(id: Int) async throws -> Data {
        try await client.send(Endpoint(path: "api/collections/\(id)", method: .delete))
    }

    /// `DELETE /api/collections/{id}/items/{bookmarkID}` — remove one item, and
    /// remember the removal when the collection is automatic.
    @discardableResult
    func removeFromCollection(collectionID: Int, bookmarkID: Int) async throws -> Data {
        try await client.send(Endpoint(
            path: "api/collections/\(collectionID)/items/\(bookmarkID)", method: .delete))
    }

    /// `GET /api/collections/{id}/cover/suggestions`
    ///
    /// The only collection call that performs image search, and it runs only
    /// when the user opens Change Cover. Listing and opening collections stay
    /// free of search and inference.
    func coverSuggestions(collectionID: Int) async throws -> CoverSuggestions {
        try await client.send(Endpoint(
            path: "api/collections/\(collectionID)/cover/suggestions",
            method: .get, timeout: 45))
    }

    /// `PUT /api/collections/{id}/cover` — install a cover the user picked.
    @discardableResult
    func setCover(collectionID: Int, imageURL: String? = nil,
                  bookmarkID: Int? = nil, source: String) async throws -> Data {
        struct Body: Encodable {
            let image_url: String?
            let bookmark_id: Int?
            let source: String
        }
        return try await client.send(Endpoint.json(
            "api/collections/\(collectionID)/cover", method: .put,
            body: Body(image_url: imageURL, bookmark_id: bookmarkID, source: source)))
    }

    /// `POST /api/collections/{id}/cover/upload` — a photo from the user's device.
    @discardableResult
    func uploadCover(collectionID: Int, jpeg: Data) async throws -> Data {
        let (endpoint, contentType) = Endpoint.multipart(
            "api/collections/\(collectionID)/cover/upload",
            fileField: "file", fileName: "cover.jpg", mimeType: "image/jpeg",
            fileData: jpeg)
        var request = endpoint
        request.contentTypeOverride = contentType
        request.timeout = 60
        return try await client.send(request)
    }

    /// `DELETE /api/collections/{id}/cover` — hand choice back to Sava.
    @discardableResult
    func resetCover(collectionID: Int) async throws -> Data {
        try await client.send(Endpoint(
            path: "api/collections/\(collectionID)/cover", method: .delete,
            timeout: 60))
    }

    /// `GET /api/resurfacing` — older saves worth another look.
    func resurfacing(limit: Int = 8) async throws -> [ResurfacedSave] {
        struct Wrapper: Decodable { let items: [ResurfacedSave] }
        let wrapper: Wrapper = try await client.send(Endpoint(
            path: "api/resurfacing", method: .get,
            query: [URLQueryItem(name: "limit", value: String(limit))]))
        return wrapper.items
    }

    /// `POST /api/bookmarks/{id}/opened` — fire-and-forget; feeds resurfacing.
    func markOpened(bookmarkID: Int) async {
        _ = try? await client.send(Endpoint(
            path: "api/bookmarks/\(bookmarkID)/opened", method: .post))
    }

    /// Rebuilds automatic collections from the user's own save patterns.
    @discardableResult
    /// `POST /api/collections/rebuild` — look for groupings, and say what
    /// happened.
    ///
    /// `background=false` on purpose. The endpoint defaults to enqueuing a job
    /// and returning `{"queued": true}` in milliseconds, which is what made the
    /// button appear to do nothing: the app got an instant reply, reloaded the
    /// list *before the worker had run*, and stopped. Nothing polled, so the
    /// groups only ever appeared on some later pull-to-refresh.
    ///
    /// Running it inline is justified by measurement rather than preference —
    /// 43ms at 50 saves, 35ms at 200, 48ms at 500. The work is dominated by one
    /// query, not by clustering, so there is nothing here worth the complexity
    /// of a job, a status endpoint and a polling loop. The response *is* the
    /// result.
    func rebuildCollections() async throws -> CollectionDiscovery {
        try await client.send(Endpoint(
            path: "api/collections/rebuild", method: .post,
            query: [URLQueryItem(name: "background", value: "false")]))
    }
}
