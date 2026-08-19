import Foundation

// MARK: Transcript (POST /api/transcript)

struct TranscriptResponse: Decodable {
    let success: Bool
    let transcript: [TranscriptSegment]?
    let error: String?
    let language: String?

    enum CodingKeys: String, CodingKey {
        case success, transcript, error, language
    }
}

struct TranscriptSegment: Decodable, Identifiable, Equatable {
    let text: String
    let start: Double
    let duration: Double
    var id: String { "\(start)-\(text.hashValue)" }
}

// MARK: Comments (GET /api/comments/{bookmark_id})

struct CommentsResponse: Decodable {
    let success: Bool
    let comments: [SavedComment]?
    let error: String?
    let totalCount: Int?

    enum CodingKeys: String, CodingKey {
        case success, comments, error
        case totalCount = "total_count"
    }
}

struct SavedComment: Decodable, Identifiable, Equatable {
    let id: Int
    let author: String?
    let text: String
    let likeCount: Int?
    let createdAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, author, text
        case likeCount = "like_count"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decode(Int.self, forKey: .id)) ?? Int.random(in: Int.min...Int.max)
        author = try? c.decodeIfPresent(String.self, forKey: .author)
        text = (try? c.decode(String.self, forKey: .text)) ?? ""
        likeCount = try? c.decodeIfPresent(Int.self, forKey: .likeCount)
        createdAt = try? c.decodeIfPresent(Date.self, forKey: .createdAt)
    }
}
