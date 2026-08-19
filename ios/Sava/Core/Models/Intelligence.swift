import Foundation

/// Models for Sava's intelligence endpoints.
///
/// Deliberately provider-neutral: nothing here names a model or a vendor. The
/// product sells Sava's intelligence, not somebody else's model.

// MARK: - Processing state

/// Where a save is in the ingestion ladder. Mirrors the server's canonical state.
enum ProcessingState: String, Decodable, Equatable {
    case queued, fetching, transcribing, analyzing, ready, partial, failed

    /// Short, human phrasing for the UI. Never exposes pipeline internals.
    var label: String {
        switch self {
        case .queued:       return "Saving"
        case .fetching:     return "Processing"
        case .transcribing: return "Processing"
        case .analyzing:    return "Processing"
        case .ready:        return "Ready"
        case .partial:      return "Ready"
        case .failed:       return "Couldn't process"
        }
    }

    var isWorking: Bool {
        switch self {
        case .queued, .fetching, .transcribing, .analyzing: return true
        default: return false
        }
    }

    var isUsable: Bool { self == .ready || self == .partial }

    /// Tolerant lookup for unknown server values. Not an `init(rawValue:)`
    /// override — that shadows the synthesized failable initializer and
    /// recurses forever.
    static func from(_ raw: String?) -> ProcessingState {
        guard let raw else { return .queued }
        return ProcessingState(rawValue: raw.lowercased()) ?? .queued
    }
}

struct ProcessingStatus: Decodable, Equatable {
    let bookmarkID: Int
    let canonicalID: Int?
    let linked: Bool
    let state: ProcessingState
    let level: Int
    let contentType: String?
    let hasTranscript: Bool
    let hasUnderstanding: Bool
    let error: String?

    enum CodingKeys: String, CodingKey {
        case bookmarkID = "bookmark_id"
        case canonicalID = "canonical_id"
        case linked, state, level, error
        case contentType = "content_type"
        case hasTranscript = "has_transcript"
        case hasUnderstanding = "has_understanding"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        bookmarkID = try c.decode(Int.self, forKey: .bookmarkID)
        canonicalID = try? c.decodeIfPresent(Int.self, forKey: .canonicalID)
        linked = (try? c.decode(Bool.self, forKey: .linked)) ?? false
        state = ProcessingState.from(try? c.decode(String.self, forKey: .state))
        level = (try? c.decode(Int.self, forKey: .level)) ?? 0
        contentType = try? c.decodeIfPresent(String.self, forKey: .contentType)
        hasTranscript = (try? c.decode(Bool.self, forKey: .hasTranscript)) ?? false
        hasUnderstanding = (try? c.decode(Bool.self, forKey: .hasUnderstanding)) ?? false
        error = try? c.decodeIfPresent(String.self, forKey: .error)
    }
}

// MARK: - Ask mode (Sava Auto / Fast / Advanced)

/// The user-facing intelligence selector. `auto` is Sava's routing layer, not a
/// model — the client never learns which model actually ran.
enum AskMode: String, CaseIterable, Identifiable, Codable {
    case auto, fast, advanced

    var id: String { rawValue }

    var title: String {
        switch self {
        case .auto:     return "Sava Auto"
        case .fast:     return "Fast"
        case .advanced: return "Advanced"
        }
    }

    var subtitle: String {
        switch self {
        case .auto:     return "Best model automatically"
        case .fast:     return "Quick everyday questions"
        case .advanced: return "Deeper reasoning"
        }
    }
}

// MARK: - Understanding

struct SaveUnderstanding: Decodable, Equatable {
    let available: Bool
    let cached: Bool?
    let reason: String?
    let message: String?
    let processingState: ProcessingState?
    let contentType: String?
    let tlDr: String?
    let keyPoints: [String]
    let topics: [String]
    let entities: [String: [String]]
    let chapters: [Chapter]
    let sourcesUsed: [String]

    struct Chapter: Decodable, Equatable, Identifiable {
        let title: String
        let start: Int
        var id: String { "\(start)-\(title)" }
    }

    enum CodingKeys: String, CodingKey {
        case available, cached, reason, message, topics, entities, chapters
        case processingState = "processing_state"
        case contentType = "content_type"
        case tlDr = "tl_dr"
        case keyPoints = "key_points"
        case sourcesUsed = "sources_used"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = (try? c.decode(Bool.self, forKey: .available)) ?? false
        cached = try? c.decodeIfPresent(Bool.self, forKey: .cached)
        reason = try? c.decodeIfPresent(String.self, forKey: .reason)
        message = try? c.decodeIfPresent(String.self, forKey: .message)
        if let raw = try? c.decodeIfPresent(String.self, forKey: .processingState) {
            processingState = ProcessingState.from(raw)
        } else {
            processingState = nil
        }
        contentType = try? c.decodeIfPresent(String.self, forKey: .contentType)
        tlDr = try? c.decodeIfPresent(String.self, forKey: .tlDr)
        keyPoints = (try? c.decodeIfPresent([String].self, forKey: .keyPoints)) ?? []
        topics = (try? c.decodeIfPresent([String].self, forKey: .topics)) ?? []
        entities = (try? c.decodeIfPresent([String: [String]].self, forKey: .entities)) ?? [:]
        chapters = (try? c.decodeIfPresent([Chapter].self, forKey: .chapters)) ?? []
        sourcesUsed = (try? c.decodeIfPresent([String].self, forKey: .sourcesUsed)) ?? []
    }

    /// True when there is genuinely something to show.
    var hasContent: Bool {
        available && !(tlDr ?? "").isEmpty
    }
}

// MARK: - Answers

struct AskAnswer: Decodable, Equatable {
    let ok: Bool
    let answer: String?
    let message: String?
    let reason: String?
    let mode: String?
    let groundedIn: Int
    let citations: [Citation]
    let sources: [RelatedSave]
    let threadID: Int?

    struct Citation: Decodable, Equatable, Identifiable {
        let startS: Int?
        let endS: Int?
        let timestamp: String?
        let source: String?
        let text: String?
        var id: String { "\(startS ?? -1)-\(text?.prefix(16) ?? "")" }

        enum CodingKeys: String, CodingKey {
            case startS = "start_s", endS = "end_s", timestamp, source, text
        }
    }

    enum CodingKeys: String, CodingKey {
        case ok, answer, message, reason, mode, citations, sources
        case groundedIn = "grounded_in"
        case threadID = "thread_id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = (try? c.decode(Bool.self, forKey: .ok)) ?? false
        answer = try? c.decodeIfPresent(String.self, forKey: .answer)
        message = try? c.decodeIfPresent(String.self, forKey: .message)
        reason = try? c.decodeIfPresent(String.self, forKey: .reason)
        mode = try? c.decodeIfPresent(String.self, forKey: .mode)
        groundedIn = (try? c.decodeIfPresent(Int.self, forKey: .groundedIn)) ?? 0
        citations = (try? c.decodeIfPresent([Citation].self, forKey: .citations)) ?? []
        sources = (try? c.decodeIfPresent([RelatedSave].self, forKey: .sources)) ?? []
        threadID = try? c.decodeIfPresent(Int.self, forKey: .threadID)
    }
}

/// A save referenced by search, related, or an Ask Sava answer.
struct RelatedSave: Decodable, Equatable, Identifiable {
    let id: Int
    let canonicalID: Int?
    let title: String?
    let author: String?
    let platformRaw: String
    let url: String
    let thumbnailURL: String?
    let note: String?
    let tlDr: String?
    let topics: [String]
    let contentType: String?
    let score: Double

    var platform: Platform { Platform(rawValue: platformRaw) }

    enum CodingKeys: String, CodingKey {
        case id, title, author, url, note, topics, score
        case canonicalID = "canonical_id"
        case platformRaw = "platform"
        case thumbnailURL = "thumbnail_url"
        case tlDr = "tl_dr"
        case contentType = "content_type"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        canonicalID = try? c.decodeIfPresent(Int.self, forKey: .canonicalID)
        title = try? c.decodeIfPresent(String.self, forKey: .title)
        author = try? c.decodeIfPresent(String.self, forKey: .author)
        platformRaw = (try? c.decode(String.self, forKey: .platformRaw)) ?? "other"
        url = (try? c.decode(String.self, forKey: .url)) ?? ""
        thumbnailURL = try? c.decodeIfPresent(String.self, forKey: .thumbnailURL)
        note = try? c.decodeIfPresent(String.self, forKey: .note)
        tlDr = try? c.decodeIfPresent(String.self, forKey: .tlDr)
        topics = (try? c.decodeIfPresent([String].self, forKey: .topics)) ?? []
        contentType = try? c.decodeIfPresent(String.self, forKey: .contentType)
        score = (try? c.decodeIfPresent(Double.self, forKey: .score)) ?? 0
    }

    var displayTitle: String {
        if let t = title?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty { return t }
        if let n = note?.trimmingCharacters(in: .whitespacesAndNewlines), !n.isEmpty { return n }
        return URL(string: url)?.host?.replacingOccurrences(of: "www.", with: "") ?? url
    }
}

// MARK: - Collections

struct SavaCollection: Decodable, Equatable, Identifiable {
    let id: Int
    let name: String
    let kind: String            // "manual" | "auto"
    let description: String?
    let count: Int
    let coverThumbnailURL: String?

    var isAutomatic: Bool { kind == "auto" }

    enum CodingKeys: String, CodingKey {
        case id, name, kind, description, count
        case coverThumbnailURL = "cover_thumbnail_url"
    }
}

struct SearchResponse: Decodable {
    let count: Int
    let results: [RelatedSave]
    let semantic: Bool
    let tookMs: Int?

    enum CodingKeys: String, CodingKey {
        case count, results, semantic
        case tookMs = "took_ms"
    }
}
