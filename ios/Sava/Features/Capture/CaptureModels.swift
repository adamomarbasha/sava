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
    /// A URL the Shortcut extracted from the current app (TikTok share/copy).
    var providedURL: String?
    /// A screenshot the Shortcut took, used only when no direct URL exists.
    /// Held in memory and never written to the Photos library.
    var screenshot: Data?
}

/// The result of resolving a capture into something saveable.
enum CaptureResolution {
    case ready(CapturedLink)
    case needsResolutionUnavailable   // screenshot present but resolver not live
    case nothingFound
}

enum CaptureError: LocalizedError {
    case notSignedIn
    case saveFailed(String)

    var errorDescription: String? {
        switch self {
        case .notSignedIn: return "Open Sava and sign in to start saving."
        case .saveFailed(let m): return m
        }
    }
}
