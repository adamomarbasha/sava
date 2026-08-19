import Foundation

/// A saved item, matching `GET /api/bookmarks` exactly. Optional fields reflect
/// that the backend may omit them; nothing here is invented.
struct Bookmark: Decodable, Identifiable, Equatable, Hashable {
    let id: Int
    let platformRaw: String
    let url: String
    let title: String?
    let author: String?
    let thumbnailURL: String?
    let note: String?
    let publishedAt: Date?
    let createdAt: Date?
    let meta: YouTubeMeta?

    enum CodingKeys: String, CodingKey {
        case id, platform, url, title, author, note
        case thumbnailURL = "thumbnail_url"
        case publishedAt = "published_at"
        case createdAt = "created_at"
        case meta
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        platformRaw = (try? c.decode(String.self, forKey: .platform)) ?? "other"
        url = try c.decode(String.self, forKey: .url)
        title = try? c.decodeIfPresent(String.self, forKey: .title)
        author = try? c.decodeIfPresent(String.self, forKey: .author)
        thumbnailURL = try? c.decodeIfPresent(String.self, forKey: .thumbnailURL)
        note = try? c.decodeIfPresent(String.self, forKey: .note)
        publishedAt = try? c.decodeIfPresent(Date.self, forKey: .publishedAt)
        createdAt = try? c.decodeIfPresent(Date.self, forKey: .createdAt)
        // `meta` is an empty object for non-YouTube items; decode leniently.
        if let m = try? c.decodeIfPresent(YouTubeMeta.self, forKey: .meta), m.videoID != nil {
            meta = m
        } else {
            meta = nil
        }
    }

    var platform: Platform { Platform(rawValue: platformRaw) }

    /// Best human title, falling back to the note or the URL host.
    var displayTitle: String {
        if let t = title?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty { return t }
        if let n = note?.trimmingCharacters(in: .whitespacesAndNewlines), !n.isEmpty { return n }
        return URL(string: url)?.host?.replacingOccurrences(of: "www.", with: "") ?? url
    }

    var displayAuthor: String? {
        guard let a = author?.trimmingCharacters(in: .whitespacesAndNewlines), !a.isEmpty else { return nil }
        return a.hasPrefix("@") ? a : a
    }
}

/// YouTube-specific metadata from the `meta` object.
struct YouTubeMeta: Decodable, Equatable, Hashable {
    let videoID: String?
    let channelID: String?
    let durationSeconds: Int?
    let viewCount: Int?
    let likeCount: Int?
    let tags: [String]

    enum CodingKeys: String, CodingKey {
        case videoID = "video_id"
        case channelID = "channel_id"
        case durationSeconds = "duration_seconds"
        case viewCount = "view_count"
        case likeCount = "like_count"
        case tags
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        videoID = try? c.decodeIfPresent(String.self, forKey: .videoID)
        channelID = try? c.decodeIfPresent(String.self, forKey: .channelID)
        durationSeconds = try? c.decodeIfPresent(Int.self, forKey: .durationSeconds)
        viewCount = try? c.decodeIfPresent(Int.self, forKey: .viewCount)
        likeCount = try? c.decodeIfPresent(Int.self, forKey: .likeCount)
        tags = (try? c.decodeIfPresent([String].self, forKey: .tags)) ?? []
    }
}
