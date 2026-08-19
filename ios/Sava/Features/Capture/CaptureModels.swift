import Foundation

/// A candidate link discovered during capture, with its detected platform.
struct CapturedLink {
    let url: String
    let platform: Platform
    let source: Source

    enum Source: String {
        case direct       // passed in by the Shortcut (e.g. TikTok on-screen URL)
        case clipboard    // copy-link fallback
        case resolved     // returned by the backend screenshot resolver
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
    /// A screenshot the Shortcut took, supplied ONLY when it found no URL.
    /// Held in memory and never written to the Photos library.
    var screenshot: Data?
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
