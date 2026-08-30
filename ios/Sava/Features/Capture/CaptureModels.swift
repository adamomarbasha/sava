import Foundation

/// A candidate link discovered during capture, with its detected platform.
struct CapturedLink {
    let url: String
    let platform: Platform
    let source: Source

    enum Source: String {
        case direct       // passed in by the Shortcut (e.g. TikTok on-screen URL)
        case clipboard    // copy-link fallback — the Action Button journey
    }

    /// Accepts only well-formed http(s) URLs. The backend makes the final
    /// platform determination; this is a client-side sanity gate.
    init?(rawURL: String?, source: Source) {
        guard
            let raw = rawURL?.trimmingCharacters(in: .whitespacesAndNewlines),
            !raw.isEmpty,
            let components = URLComponents(string: raw),
            let scheme = components.scheme?.lowercased(),
            scheme == "http" || scheme == "https",
            let host = components.host, !host.isEmpty
        else { return nil }
        self.url = raw
        self.source = source
        self.platform = PlatformDetector.detect(raw)
    }
}

/// Lightweight, client-side platform detection mirroring the backend's rules —
/// used only to decide capture strategy and show the right confirmation. The
/// server remains the source of truth on save.
enum PlatformDetector {
    static func detect(_ url: String) -> Platform {
        let u = url.lowercased()
        if u.contains("tiktok.com") { return .tiktok }
        if u.contains("youtube.com") || u.contains("youtu.be") { return .youtube }
        if u.contains("instagram.com") { return .instagram }
        if u.contains("twitter.com") || u.contains("x.com") { return .twitter }
        if u.contains("linkedin.com") { return .linkedin }
        if u.contains("reddit.com") { return .reddit }
        if u.contains("pinterest.com") || u.contains("pin.it") { return .pinterest }
        if u.contains("snapchat.com") { return .snapchat }
        if u.contains("facebook.com") || u.contains("fb.com") { return .facebook }
        return .other
    }
}

/// What the user gave us to work with when the Action Button fired.
struct CaptureInput {
    /// Every URL the Shortcut harvested from "Get What's On Screen". The
    /// intent ranks these and picks the real content URL.
    var candidateURLs: [String] = []
    /// The URL finally selected from `candidateURLs` (or passed directly).
    var providedURL: String?
    /// What the client believes is on screen, when the Shortcut can say.
    var platformHint: Platform?
    /// Names of the inputs the intent actually received — DEBUG diagnostics.
    var inputTypes: [String] = []
}

/// Chooses the real content URL out of everything "Get What's On Screen"
/// returned.
///
/// That action commonly yields several URLs — a profile link, a sound page, a
/// share/tracking link, a CDN asset. Saving the first one would frequently save
/// the wrong thing, so candidates are ranked: a URL that identifies a specific
/// piece of content on a supported platform always beats a bare profile or an
/// unrelated host.
enum URLSelector {
    /// Higher is better. Negative means "never pick this".
    static func score(_ raw: String) -> Int {
        guard let link = CapturedLink(rawURL: raw, source: .direct) else { return -1 }
        let url = raw.lowercased()
        var score = 0

        switch link.platform {
        case .other:
            score += 5                      // unknown host: usable but unloved
        default:
            score += 40                     // a platform we understand
        }

        // A specific item beats a profile/feed page.
        if url.contains("/video/") || url.contains("/watch?v=")
            || url.contains("/reel/") || url.contains("/reels/")
            || url.contains("/p/") || url.contains("/shorts/")
            || url.contains("youtu.be/") || url.contains("/photo/")
            || url.contains("/status/") {
            score += 50
        }

        // Short links resolve to real content server-side — still good.
        if url.contains("vm.tiktok.com") || url.contains("vt.tiktok.com")
            || url.contains("pin.it") {
            score += 35
        }

        // Things that are never the content the user is watching.
        for junk in ["/login", "/signup", "/privacy", "/terms", "/about",
                     "/download", "/help", "/support", "apple.com", "itunes.",
                     "apps.apple.com", "/music/", "/discover", "/foryou",
                     "/following", "/explore"] where url.contains(junk) {
            score -= 60
        }
        if url.hasSuffix(".jpg") || url.hasSuffix(".png") || url.hasSuffix(".mp4")
            || url.contains("cdn") || url.contains("static.") {
            score -= 70
        }
        return score
    }

    /// Pick the best candidate, or nil when none is usable.
    static func best(from candidates: [String]) -> String? {
        candidates
            .map { (url: $0, score: score($0)) }
            .filter { $0.score > 0 }
            .max { $0.score < $1.score }?
            .url
    }
}

/// Whether a URL names a specific piece of content Sava can actually ingest.
///
/// This is the gate that makes clipboard capture safe. The Instagram journey is
/// "Copy Link, then press the button", which means Sava reads whatever the user
/// last copied — and that is very often a profile they were looking at, a story
/// link, or a URL from an hour ago that has nothing to do with anything. Sending
/// those to the server would create library items for pages that are not posts.
///
/// The rules mirror `api/content/identity.py`. The server still decides; this
/// only avoids asking it about things that are obviously not content.
enum SupportedContentURL {

    /// Path prefixes on Instagram that are never a post.
    private static let instagramNonContent = [
        "/explore", "/accounts", "/direct", "/stories", "/reels/audio",
        "/directory", "/about", "/legal", "/privacy", "/terms", "/api",
        "/challenge", "/oauth", "/ads", "/business", "/shop", "/session",
    ]

    /// True when this URL identifies one post/video on a supported platform.
    static func isContent(_ raw: String?) -> Bool {
        guard let raw, let components = URLComponents(string: raw.trimmingCharacters(
            in: .whitespacesAndNewlines)),
            let scheme = components.scheme?.lowercased(),
            scheme == "http" || scheme == "https",
            let host = components.host?.lowercased()
        else { return false }

        let path = components.path.lowercased()

        if host.contains("instagram.com") {
            if instagramNonContent.contains(where: { path.hasPrefix($0) }) { return false }
            // `/p/<code>`, `/reel/<code>`, `/tv/<code>`, `/share/p/<code>`,
            // and the `/<user>/p/<code>` form. A bare `/reels/` with no code is
            // the feed, not a post.
            for marker in ["/p/", "/reel/", "/reels/", "/tv/", "/share/"] {
                guard let range = path.range(of: marker) else { continue }
                let tail = path[range.upperBound...]
                    .split(separator: "/", omittingEmptySubsequences: true)
                if let code = tail.first, code.count >= 5 { return true }
            }
            return false
        }

        if host.contains("tiktok.com") {
            if host.hasPrefix("vm.") || host.hasPrefix("vt.") { return true }
            return path.contains("/video/") || path.contains("/photo/")
        }

        if host.contains("youtube.com") || host.contains("youtu.be") {
            if host.contains("youtu.be") { return path.count > 1 }
            if path.hasPrefix("/watch") {
                return components.queryItems?.contains { $0.name == "v" } ?? false
            }
            return path.hasPrefix("/shorts/") || path.hasPrefix("/live/")
                || path.hasPrefix("/embed/")
        }

        return false
    }

    /// A clipboard value worth sending, or nil.
    ///
    /// Deliberately strict: a clipboard entry has no provenance at all, so
    /// anything that is not unmistakably a supported post is discarded rather
    /// than hopefully forwarded.
    static func fromClipboard(_ raw: String?) -> String? {
        guard let raw = raw?.trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty else { return nil }
        // A copied share sheet often carries a caption plus the link.
        let candidates = raw.split(whereSeparator: { $0.isWhitespace || $0.isNewline })
            .map(String.init)
        for candidate in candidates where isContent(candidate) {
            return candidate
        }
        return isContent(raw) ? raw : nil
    }
}

enum CaptureError: LocalizedError {
    case notSignedIn
    case saveFailed(String, CapturePipeline.Outcome?)
    case noContent(String, CapturePipeline.Outcome?)

    var errorDescription: String? {
        switch self {
        case .notSignedIn: return "Open Sava and sign in to start saving."
        case .saveFailed(let m, _): return m
        case .noContent(let m, _): return m
        }
    }

    var outcome: CapturePipeline.Outcome? {
        switch self {
        case .notSignedIn: return nil
        case .saveFailed(_, let o), .noContent(_, let o): return o
        }
    }
}
