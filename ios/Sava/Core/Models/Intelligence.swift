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
        case .fetching, .transcribing, .analyzing: return "Reading"
        case .ready, .partial: return "Ready"
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
        guard let raw else { return .ready }
        return ProcessingState(rawValue: raw.lowercased()) ?? .ready
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
        case .auto:     return "Picks the right depth for the question"
        case .fast:     return "Quick everyday answers"
        case .advanced: return "Deeper synthesis across more of your library"
        }
    }
}

// MARK: - Structured understanding

/// One block of extracted knowledge, ready to be set as editorial content.
///
/// The server's typed extraction is a nested JSON document. Rendering that
/// document directly would put JSON on screen; flattening it into titled blocks
/// with one of four shapes lets the detail screen read like an article about
/// the save instead of a dump of its record.
struct StructuredSection: Identifiable, Equatable {
    enum Content: Equatable {
        /// Free-standing lines: key points, tips, pros.
        case lines([String])
        /// Ordered instructions.
        case steps([String])
        /// A quantity against a name: "2 tbsp — olive oil", "3×12 — squats".
        case pairs([Pair])
        /// A sentence or two.
        case prose(String)
    }

    struct Pair: Equatable, Hashable {
        let lead: String
        let detail: String?
    }

    let title: String
    let content: Content
    var id: String { title }
}

/// The typed extraction schemas the pipeline produces, one per content type.
/// Every field is optional because extraction genuinely fails to find things —
/// a recipe video that never states a temperature simply has none.
struct TypedData: Decodable, Equatable {
    var recipe: Recipe?
    var restaurant: Restaurant?
    var travel: Travel?
    var product: Product?
    var fitness: Fitness?
    var beauty: Beauty?
    var fashion: Fashion?
    var coding: Coding?

    struct Recipe: Decodable, Equatable {
        var dish: String?
        var ingredients: [Ingredient] = []
        var steps: [String] = []
        var temperature: String?
        var cookTime: String?
        var servings: String?
        var equipment: [String] = []
        var tips: [String] = []

        struct Ingredient: Decodable, Equatable {
            var item: String?
            var quantity: String?
        }

        enum CodingKeys: String, CodingKey {
            case dish, ingredients, steps, temperature, servings, equipment, tips
            case cookTime = "cook_time"
        }
    }

    struct Restaurant: Decodable, Equatable {
        var name: String?
        var city: String?
        var neighborhood: String?
        var cuisine: String?
        var dishes: [String] = []
        var priceRange: String?
        var reservationNotes: String?
        var verdict: String?

        enum CodingKeys: String, CodingKey {
            case name, city, neighborhood, cuisine, dishes, verdict
            case priceRange = "price_range"
            case reservationNotes = "reservation_notes"
        }
    }

    struct Travel: Decodable, Equatable {
        var destination: String?
        var places: [Place] = []
        var hotels: [String] = []
        var restaurants: [String] = []
        var activities: [String] = []
        var bestTime: String?
        var budgetNotes: String?

        struct Place: Decodable, Equatable {
            var name: String?
            var kind: String?
            var note: String?
        }

        enum CodingKeys: String, CodingKey {
            case destination, places, hotels, restaurants, activities
            case bestTime = "best_time"
            case budgetNotes = "budget_notes"
        }
    }

    struct Product: Decodable, Equatable {
        var items: [Item] = []
        var pros: [String] = []
        var cons: [String] = []
        var verdict: String?
        var alternatives: [String] = []
        var whereToBuy: String?

        struct Item: Decodable, Equatable {
            var name: String?
            var brand: String?
            var price: String?
        }

        enum CodingKeys: String, CodingKey {
            case items, pros, cons, verdict, alternatives
            case whereToBuy = "where_to_buy"
        }
    }

    struct Fitness: Decodable, Equatable {
        var focus: String?
        var exercises: [Exercise] = []
        var equipment: [String] = []
        var duration: String?
        var level: String?

        struct Exercise: Decodable, Equatable {
            var name: String?
            var sets: String?
            var reps: String?
        }
    }

    struct Beauty: Decodable, Equatable {
        var products: [Item] = []
        var concerns: [String] = []
        var routineSteps: [String] = []
        var skinType: String?

        struct Item: Decodable, Equatable {
            var name: String?
            var brand: String?
            var shade: String?
        }

        enum CodingKeys: String, CodingKey {
            case products, concerns
            case routineSteps = "routine_steps"
            case skinType = "skin_type"
        }
    }

    struct Fashion: Decodable, Equatable {
        var items: [Item] = []
        var style: String?
        var occasion: String?
        var whereToBuy: String?

        struct Item: Decodable, Equatable {
            var item: String?
            var brand: String?
            var price: String?
        }

        enum CodingKeys: String, CodingKey {
            case items, style, occasion
            case whereToBuy = "where_to_buy"
        }
    }

    struct Coding: Decodable, Equatable {
        var languages: [String] = []
        var frameworks: [String] = []
        var concepts: [String] = []
        var commands: [String] = []
        var gotchas: [String] = []
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
    let typedData: TypedData?
    let chapters: [Chapter]
    let sourcesUsed: [String]

    struct Chapter: Decodable, Equatable, Identifiable {
        let title: String
        let start: Int
        var id: String { "\(start)-\(title)" }
        var timestamp: String { Format.timestamp(Double(start)) }
    }

    enum CodingKeys: String, CodingKey {
        case available, cached, reason, message, topics, entities, chapters
        case processingState = "processing_state"
        case contentType = "content_type"
        case tlDr = "tl_dr"
        case keyPoints = "key_points"
        case typedData = "typed_data"
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
        typedData = try? c.decodeIfPresent(TypedData.self, forKey: .typedData)
        chapters = (try? c.decodeIfPresent([Chapter].self, forKey: .chapters)) ?? []
        sourcesUsed = (try? c.decodeIfPresent([String].self, forKey: .sourcesUsed)) ?? []
    }

    /// True when there is genuinely something to show.
    var hasContent: Bool { available && !(tlDr ?? "").isEmpty }

    /// Key points as the screen should show them.
    ///
    /// Generation now returns two to four short lines, or none at all for
    /// something simple. Understanding generated under the older prompt is
    /// cached against canonical content and can carry seven long ones, so the
    /// display caps what it renders. Without this, every item processed before
    /// today would still show the wall of bullets this pass set out to remove.
    var displayKeyPoints: [String] { Array(keyPoints.prefix(4)) }

    // MARK: Presentation

    /// The extracted record, flattened into titled blocks in reading order.
    ///
    /// Deliberately narrow. The pipeline extracts a great deal — people, brands,
    /// products, places, prices, key facts — and almost none of it earns a
    /// heading on this screen. Printing "Brands: App Store, Google Play, TikTok"
    /// under a comedy clip is a metadata viewer, not a product; it is also the
    /// single clearest tell that a screen was generated rather than designed,
    /// because every item ends up wearing the same set of sections.
    ///
    /// So only structure that a *specific kind of content* genuinely benefits
    /// from survives: a recipe's ingredients and method, a trip's places, a
    /// review's verdict. Everything else stays in the database, where search,
    /// collections and Ask still use it.
    var sections: [StructuredSection] {
        guard let data = typedData else { return [] }
        var out: [StructuredSection] = []

        if let recipe = data.recipe {
            let pairs = recipe.ingredients.compactMap { ingredient -> StructuredSection.Pair? in
                guard let item = Self.text(ingredient.item) else { return nil }
                return .init(lead: item, detail: Self.text(ingredient.quantity))
            }
            out.append(contentsOf: [
                Self.section("Ingredients", .pairs(pairs)),
                Self.section("Method", .steps(Self.list(recipe.steps))),
                Self.section("Tips", .lines(Self.list(recipe.tips))),
                // The only label/value block that survives: for a recipe these
                // three numbers are the reason you reopen it.
                Self.facts([("Cook time", recipe.cookTime), ("Temperature", recipe.temperature),
                            ("Serves", recipe.servings)]),
            ].compactMap { $0 })
        }

        if let place = data.restaurant {
            out.append(contentsOf: [
                Self.section("Dishes", .lines(Self.list(place.dishes))),
                Self.prose("Verdict", place.verdict),
            ].compactMap { $0 })
        }

        if let travel = data.travel {
            let places = travel.places.compactMap { place -> StructuredSection.Pair? in
                guard let name = Self.text(place.name) else { return nil }
                return .init(lead: name, detail: Self.text(place.note) ?? Self.text(place.kind))
            }
            out.append(contentsOf: [
                Self.section("Places", .pairs(places)),
                Self.section("Where to eat", .lines(Self.list(travel.restaurants))),
                Self.section("What to do", .lines(Self.list(travel.activities))),
            ].compactMap { $0 })
        }

        if let product = data.product {
            let items = product.items.compactMap { item -> StructuredSection.Pair? in
                guard let name = Self.text(item.name) ?? Self.text(item.brand) else { return nil }
                let detail = [Self.text(item.brand).flatMap { $0 == name ? nil : $0 },
                              Self.text(item.price)].compactMap { $0 }.joined(separator: " · ")
                return .init(lead: name, detail: detail.isEmpty ? nil : detail)
            }
            out.append(contentsOf: [
                Self.section("Products", .pairs(items)),
                Self.section("Pros", .lines(Self.list(product.pros))),
                Self.section("Cons", .lines(Self.list(product.cons))),
                Self.prose("Verdict", product.verdict),
            ].compactMap { $0 })
        }

        if let fitness = data.fitness {
            let exercises = fitness.exercises.compactMap { exercise -> StructuredSection.Pair? in
                guard let name = Self.text(exercise.name) else { return nil }
                let detail = [Self.text(exercise.sets), Self.text(exercise.reps)]
                    .compactMap { $0 }.joined(separator: " \u{00D7} ")
                return .init(lead: name, detail: detail.isEmpty ? nil : detail)
            }
            out.append(contentsOf: [
                Self.section("Exercises", .pairs(exercises)),
            ].compactMap { $0 })
        }

        if let beauty = data.beauty {
            let products = beauty.products.compactMap { item -> StructuredSection.Pair? in
                guard let name = Self.text(item.name) ?? Self.text(item.brand) else { return nil }
                let detail = [Self.text(item.brand).flatMap { $0 == name ? nil : $0 },
                              Self.text(item.shade)].compactMap { $0 }.joined(separator: " · ")
                return .init(lead: name, detail: detail.isEmpty ? nil : detail)
            }
            out.append(contentsOf: [
                Self.section("Products", .pairs(products)),
                Self.section("Routine", .steps(Self.list(beauty.routineSteps))),
            ].compactMap { $0 })
        }

        if let fashion = data.fashion {
            let items = fashion.items.compactMap { entry -> StructuredSection.Pair? in
                guard let name = Self.text(entry.item) ?? Self.text(entry.brand) else { return nil }
                let detail = [Self.text(entry.brand).flatMap { $0 == name ? nil : $0 },
                              Self.text(entry.price)].compactMap { $0 }.joined(separator: " · ")
                return .init(lead: name, detail: detail.isEmpty ? nil : detail)
            }
            out.append(contentsOf: [
                Self.section("Pieces", .pairs(items)),
            ].compactMap { $0 })
        }

        return out
    }

    // MARK: Helpers

    private static func text(_ value: String?) -> String? {
        guard let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty else { return nil }
        return trimmed
    }

    private static func list(_ values: [String]) -> [String] {
        var seen = Set<String>()
        return values.compactMap(text).filter { seen.insert($0.lowercased()).inserted }
    }

    private static func section(_ title: String,
                                _ content: StructuredSection.Content) -> StructuredSection? {
        switch content {
        case .lines(let v), .steps(let v):
            guard !v.isEmpty else { return nil }
        case .pairs(let v):
            guard !v.isEmpty else { return nil }
        case .prose(let v):
            guard !v.isEmpty else { return nil }
        }
        return StructuredSection(title: title, content: content)
    }

    /// A sentence or two, dropped entirely when the extraction found none.
    private static func prose(_ title: String, _ value: String?) -> StructuredSection? {
        guard let value = text(value) else { return nil }
        return StructuredSection(title: title, content: .prose(value))
    }

    /// A short label/value block — the facts that would otherwise each need
    /// their own heading and turn the screen into a form.
    private static func facts(_ entries: [(String, String?)]) -> StructuredSection? {
        let pairs = entries.compactMap { label, value -> StructuredSection.Pair? in
            guard let value = text(value) else { return nil }
            return .init(lead: label, detail: value)
        }
        guard !pairs.isEmpty else { return nil }
        return StructuredSection(title: "At a glance", content: .pairs(pairs))
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

        /// A citation is only worth rendering if it points somewhere.
        var label: String? {
            if let timestamp, !timestamp.isEmpty { return timestamp }
            if let startS { return Format.timestamp(Double(startS)) }
            return nil
        }

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

/// A save referenced by search, related, or an Ask answer.
struct RelatedSave: Decodable, Equatable, Identifiable, Hashable {
    let id: Int
    let canonicalID: Int?
    let title: String?
    let author: String?
    let platformRaw: String
    let url: String
    let thumbnailURL: String?
    let tlDr: String?
    let topics: [String]
    let contentType: String?
    let score: Double

    var platform: Platform { Platform(rawValue: platformRaw) }

    enum CodingKeys: String, CodingKey {
        case id, title, author, url, topics, score
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
        tlDr = try? c.decodeIfPresent(String.self, forKey: .tlDr)
        topics = (try? c.decodeIfPresent([String].self, forKey: .topics)) ?? []
        contentType = try? c.decodeIfPresent(String.self, forKey: .contentType)
        score = (try? c.decodeIfPresent(Double.self, forKey: .score)) ?? 0
    }

    /// Cleaned the same way a library card's title is, so the same save does not
    /// read one way in the grid and another way inside an answer.
    var displayTitle: String {
        if let cleaned = Bookmark.asTitle(title) { return cleaned }
        if let a = author?.trimmingCharacters(in: .whitespacesAndNewlines), !a.isEmpty { return a }
        return URL(string: url)?.host?.replacingOccurrences(of: "www.", with: "") ?? "Untitled"
    }

    var imageURL: URL? { ThumbnailURL.resolve(thumbnailURL, platform: platform) }

    var metaLine: String {
        var parts: [String] = [platform.displayName]
        if let a = author?.trimmingCharacters(in: .whitespacesAndNewlines), !a.isEmpty {
            parts.append(a)
        }
        return parts.joined(separator: " · ")
    }
}

// MARK: - Collections

struct SavaCollection: Decodable, Equatable, Identifiable, Hashable {
    let id: Int
    let name: String
    let kind: String            // "manual" | "auto"
    let description: String?
    let count: Int
    let coverThumbnailURL: String?
    let coverThumbnails: [String]

    var isAutomatic: Bool { kind == "auto" }

    /// Cover imagery, resolved and ready to load. Falls back to the single
    /// designated cover for servers that predate the mosaic field.
    var coverURLs: [URL?] {
        let raw = coverThumbnails.isEmpty
            ? [coverThumbnailURL].compactMap { $0 }
            : coverThumbnails
        return raw.map { ThumbnailURL.resolve($0) }
    }

    var countLabel: String { "\(count) item\(count == 1 ? "" : "s")" }

    enum CodingKeys: String, CodingKey {
        case id, name, kind, description, count
        case coverThumbnailURL = "cover_thumbnail_url"
        case coverThumbnails = "cover_thumbnails"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        name = (try? c.decode(String.self, forKey: .name)) ?? "Untitled"
        kind = (try? c.decode(String.self, forKey: .kind)) ?? "manual"
        description = try? c.decodeIfPresent(String.self, forKey: .description)
        count = (try? c.decodeIfPresent(Int.self, forKey: .count)) ?? 0
        coverThumbnailURL = try? c.decodeIfPresent(String.self, forKey: .coverThumbnailURL)
        coverThumbnails = (try? c.decodeIfPresent([String].self, forKey: .coverThumbnails)) ?? []
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

// MARK: - Transcript

/// What was said, with timings. Read from storage — never re-acquired.
struct Transcript: Decodable, Equatable {
    let available: Bool
    let source: String?
    let language: String?
    let text: String
    let segments: [Segment]

    struct Segment: Decodable, Equatable, Identifiable {
        let text: String
        let start: Double
        let duration: Double

        var id: String { "\(start)-\(text.prefix(24))" }
        var timestamp: String { Format.timestamp(start) }
    }

    enum CodingKeys: String, CodingKey {
        case available, source, language, text, segments
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = (try? c.decode(Bool.self, forKey: .available)) ?? false
        source = try? c.decodeIfPresent(String.self, forKey: .source)
        language = try? c.decodeIfPresent(String.self, forKey: .language)
        text = (try? c.decodeIfPresent(String.self, forKey: .text)) ?? ""
        segments = (try? c.decodeIfPresent([Segment].self, forKey: .segments)) ?? []
    }

    /// Provenance, in the user's terms. "Captions" came from the platform and
    /// are verbatim; anything else a model heard, and may be imperfect.
    var sourceLabel: String {
        switch source {
        case "captions": return "From captions"
        case "asr":      return "Transcribed by Sava"
        default:         return "Transcript"
        }
    }

    /// Timestamped plain text, which is what someone pasting this elsewhere
    /// actually wants — a wall of unbroken prose loses every reference point.
    var plainText: String {
        segments.map { "[\($0.timestamp)] \($0.text)" }.joined(separator: "\n")
    }
}

// MARK: - Conversations

/// One past conversation, as it appears in the history list.
struct ChatThreadSummary: Decodable, Equatable, Identifiable, Hashable {
    let id: Int
    let title: String
    let scope: String
    let bookmarkID: Int?
    let messageCount: Int
    let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, title, scope
        case bookmarkID = "bookmark_id"
        case messageCount = "message_count"
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        let raw = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        title = raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? "Untitled" : raw
        scope = (try? c.decodeIfPresent(String.self, forKey: .scope)) ?? "library"
        bookmarkID = try? c.decodeIfPresent(Int.self, forKey: .bookmarkID)
        messageCount = (try? c.decodeIfPresent(Int.self, forKey: .messageCount)) ?? 0
        updatedAt = try? c.decodeIfPresent(Date.self, forKey: .updatedAt)
    }

    /// Exchanges, not messages — a user turn and its reply are one thing to a
    /// person looking at a list.
    var exchangeCount: Int { max(1, messageCount / 2) }
}

/// A stored message, used to rebuild a conversation when it is reopened.
struct ChatMessageRecord: Decodable, Equatable {
    let role: String
    let content: String
    /// Ask Sava stores the items it drew on here; Ask This stores its citations.
    /// The shapes differ, so both are attempted and whichever decodes is used.
    let sources: [RelatedSave]
    let citations: [AskAnswer.Citation]

    enum CodingKeys: String, CodingKey { case role, content, citations }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        role = (try? c.decode(String.self, forKey: .role)) ?? "assistant"
        content = (try? c.decode(String.self, forKey: .content)) ?? ""
        sources = (try? c.decodeIfPresent([RelatedSave].self, forKey: .citations)) ?? []
        citations = sources.isEmpty
            ? ((try? c.decodeIfPresent([AskAnswer.Citation].self, forKey: .citations)) ?? [])
            : []
    }

    var isUser: Bool { role == "user" }
}
