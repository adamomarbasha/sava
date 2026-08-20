import CoreGraphics
import Foundation

/// A saved item, matching `GET /api/bookmarks`.
///
/// The server falls back to canonical content for title, creator, thumbnail,
/// duration, content type and processing state, so a save carries the same
/// metadata regardless of which platform it came from or how old it is. Every
/// field here is optional on purpose: the UI's job is to render the richest
/// thing available and degrade gracefully, never to hide a save because one
/// field is missing.
struct Bookmark: Decodable, Identifiable, Equatable, Hashable {
    let id: Int
    let platformRaw: String
    let url: String
    let title: String?
    /// The caption. For TikTok and Instagram this is often the same text as the
    /// title, so it is only shown where it genuinely adds something.
    let caption: String?
    let author: String?
    let creatorHandle: String?
    let thumbnailURL: String?
    let publishedAt: Date?
    let createdAt: Date?
    let canonicalID: Int?
    let processingStateRaw: String?
    let contentType: String?
    let durationSeconds: Int?
    /// video | carousel | image | article | unknown, from canonical content.
    let mediaKind: String?
    /// Whether this belongs in the vertical swipe viewer. Decided server-side
    /// so the rule lives in one place; see `api/content/shortform.py`.
    let isShort: Bool
    let width: Int?
    let height: Int?
    let meta: MediaMeta?

    enum CodingKeys: String, CodingKey {
        case id, platform, url, title, author, meta, description
        case creatorHandle = "creator_handle"
        case thumbnailURL = "thumbnail_url"
        case publishedAt = "published_at"
        case createdAt = "created_at"
        case canonicalID = "canonical_id"
        case processingStateRaw = "processing_state"
        case contentType = "content_type"
        case durationSeconds = "duration_seconds"
        case mediaKind = "media_kind"
        case isShort = "is_short"
        case width, height
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        platformRaw = (try? c.decode(String.self, forKey: .platform)) ?? "other"
        url = (try? c.decode(String.self, forKey: .url)) ?? ""
        title = Self.asTitle(Self.clean(try? c.decodeIfPresent(String.self, forKey: .title)))
        caption = Self.clean(try? c.decodeIfPresent(String.self, forKey: .description))
        author = Self.clean(try? c.decodeIfPresent(String.self, forKey: .author))
        creatorHandle = Self.clean(try? c.decodeIfPresent(String.self, forKey: .creatorHandle))
        thumbnailURL = try? c.decodeIfPresent(String.self, forKey: .thumbnailURL)
        publishedAt = try? c.decodeIfPresent(Date.self, forKey: .publishedAt)
        createdAt = try? c.decodeIfPresent(Date.self, forKey: .createdAt)
        canonicalID = try? c.decodeIfPresent(Int.self, forKey: .canonicalID)
        processingStateRaw = try? c.decodeIfPresent(String.self, forKey: .processingStateRaw)
        contentType = Self.clean(try? c.decodeIfPresent(String.self, forKey: .contentType))
        durationSeconds = try? c.decodeIfPresent(Int.self, forKey: .durationSeconds)
        mediaKind = Self.clean(try? c.decodeIfPresent(String.self, forKey: .mediaKind))
        isShort = (try? c.decodeIfPresent(Bool.self, forKey: .isShort)) ?? false
        width = try? c.decodeIfPresent(Int.self, forKey: .width)
        height = try? c.decodeIfPresent(Int.self, forKey: .height)
        if let m = try? c.decodeIfPresent(MediaMeta.self, forKey: .meta), m.hasContent {
            meta = m
        } else {
            meta = nil
        }
    }

    /// Trim, and treat whitespace-only as absent — a blank string is not a value.
    private static func clean(_ raw: String??) -> String? {
        guard let value = raw ?? nil else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// Tidies a social caption enough to be set as a title.
    ///
    /// Short-form captions end in a run of hashtags and, when Instagram
    /// truncated them, a literal "… more". Both are artefacts of the source
    /// interface rather than content, and both are what make a grid of TikToks
    /// read as noise. Only *trailing* tags are dropped — a caption that opens
    /// with "#CapitalOnePartner" is still saying something — and if stripping
    /// leaves nothing, the original is kept. No word is ever added.
    static func asTitle(_ raw: String?) -> String? {
        guard var text = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty
        else { return nil }
        let original = text

        for suffix in ["… more", "... more", "…more"] where text.hasSuffix(suffix) {
            text = String(text.dropLast(suffix.count))
            break
        }

        var words = text.split(separator: " ", omittingEmptySubsequences: true).map(String.init)
        while let last = words.last, last.hasPrefix("#") || last.hasPrefix("@") {
            words.removeLast()
        }

        let stripped = words.joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "|-–—·,"))
        return stripped.isEmpty ? original : stripped
    }

    var platform: Platform { Platform(rawValue: platformRaw) }

    var processingState: ProcessingState { ProcessingState.from(processingStateRaw) }

    /// True only while enrichment is genuinely still running. `partial` and
    /// `ready` are both finished states — a save that is as good as it will get
    /// must not show a spinner for ever.
    var isProcessing: Bool { processingState.isWorking }

    /// Resolved image URL, proxied when the CDN requires it.
    var imageURL: URL? { ThumbnailURL.resolve(thumbnailURL, platform: platform) }

    // MARK: Display

    /// The best human label available. Falls back to the creator, then the host
    /// — never to an empty row, and never to invented text.
    var displayTitle: String {
        if let title { return title }
        if let creator = displayCreator { return creator }
        if let host = URL(string: url)?.host?.replacingOccurrences(of: "www.", with: "") {
            return host
        }
        return "Untitled"
    }

    /// True when the stored title is one Sava generated because the platform
    /// gave it nothing — "Instagram Post", "Tweet by @x", "Reddit post in r/y".
    ///
    /// These are honest placeholders, not content. They belong under the card
    /// where a title goes; they do not belong set large inside the media plate,
    /// where they would repeat the caption a centimetre below it and turn a
    /// missing image into a stutter.
    var hasGenericTitle: Bool {
        guard let title else { return true }
        let lowered = title.lowercased()
        let patterns = ["instagram post", "instagram video", "tiktok video by",
                        "tweet by", "post by", "reddit post in", "linkedin post by",
                        "pinterest pin", "snapchat profile", "facebook post",
                        "youtube video", "untitled"]
        return patterns.contains { lowered.hasPrefix($0) }
    }

    /// The title set on the no-image plate. Nil when the only thing we could
    /// print is a placeholder — a bare plate beats a repeated one.
    var plateTitle: String? { hasGenericTitle ? nil : title }

    var displayCreator: String? { author ?? creatorHandle }

    /// `@handle`, but only when it is genuinely a handle and genuinely adds
    /// something.
    ///
    /// TikTok's API returns a numeric account id in this field for some saves —
    /// printing "@7518866689892680759" under a creator's name would be leaking a
    /// database key into the interface, so a purely numeric value is treated as
    /// absent rather than shown.
    var handleSuffix: String? {
        guard let handle = creatorHandle,
              handle.caseInsensitiveCompare(author ?? "") != .orderedSame,
              handle.contains(where: { !$0.isNumber })
        else { return nil }
        return handle.hasPrefix("@") ? handle : "@\(handle)"
    }

    /// The caption, only when it is not simply the title again.
    var distinctCaption: String? {
        guard let caption, caption != title else { return nil }
        guard let title else { return caption }
        // A caption that merely extends a truncated title adds nothing.
        return caption.hasPrefix(title) ? nil : caption
    }

    var durationLabel: String? { Format.duration(durationSeconds ?? meta?.durationSeconds) }

    // MARK: Short-form

    /// True when this save can open in the vertical viewer.
    ///
    /// Trusts the server's `is_short` and adds no client-side guessing. A save
    /// from before the classification existed reports `false` and simply opens
    /// the ordinary way — which is the right failure direction, because putting
    /// a twenty-minute landscape video into a swipe feed is far worse than
    /// making someone tap once more.
    var isShortForm: Bool { isShort }

    /// A photo post rather than a video. The viewer pages its images instead of
    /// pretending there is a video that never starts.
    var isCarousel: Bool { mediaKind == "carousel" }

    /// Native aspect ratio when the platform told us, else the platform's
    /// conventional one. Used to letterbox correctly on the very first frame,
    /// before any player has reported anything.
    var mediaAspect: CGFloat {
        if let width, let height, width > 0, height > 0 {
            return CGFloat(width) / CGFloat(height)
        }
        return platform.prefersPortrait ? 9.0 / 16.0 : MediaRatio.landscape
    }

    /// The quiet line under a title: creator, platform, and age.
    var metaLine: String {
        var parts: [String] = []
        if let creator = displayCreator { parts.append(creator) }
        parts.append(platform.displayName)
        if let age = Format.relativeAge(createdAt) { parts.append(age) }
        return parts.joined(separator: " · ")
    }

    /// The same line for a half-width grid card, where the age is the first
    /// thing to be truncated away and the least useful of the three. Dropping
    /// it up front is better than letting it be cut off mid-word.
    var gridMetaLine: String {
        guard let creator = displayCreator else { return platform.displayName }
        return "\(creator) · \(platform.displayName)"
    }

    /// The detail screen's attribution line. Platform first — it frames what you
    /// are looking at before it names who made it.
    var attributionLine: String {
        var parts: [String] = [platform.displayName]
        if let creator = displayCreator { parts.append(creator) }
        if let duration = durationLabel { parts.append(duration) }
        if let published = publishedAt, published > .distantPast {
            parts.append(published.formatted(.dateTime.month(.abbreviated).year()))
        } else if let age = Format.relativeAge(createdAt) {
            parts.append("Added \(age)")
        }
        return parts.joined(separator: " · ")
    }

    /// Engagement, when the platform actually gave us any. Never shown as zero.
    var engagementLine: String? {
        var parts: [String] = []
        if let views = Format.compactCount(meta?.viewCount), meta?.viewCount ?? 0 > 0 {
            parts.append("\(views) views")
        }
        if let likes = Format.compactCount(meta?.likeCount), meta?.likeCount ?? 0 > 0 {
            parts.append("\(likes) likes")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// Platform metadata. YouTube supplies the most; others may supply only duration.
struct MediaMeta: Decodable, Equatable, Hashable {
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

    var hasContent: Bool {
        videoID != nil || durationSeconds != nil || viewCount != nil || !tags.isEmpty
    }
}
