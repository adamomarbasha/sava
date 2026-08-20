import Foundation

/// Resolves a raw `thumbnail_url` into a loadable URL.
///
/// Three shapes arrive from the server:
///
///   * `/static/thumbnails/…` — a copy Sava owns. These never expire, and they
///     are the shape the backend now migrates everything toward.
///   * A signed, referer-gated CDN URL (Instagram/Facebook, TikTok, LinkedIn).
///     These fail to load directly from a client, so they go through the
///     backend's `GET /api/thumbnail` proxy — which also mirrors them to local
///     storage on first view, so the *next* request needs no proxy at all.
///   * An ordinary public URL (YouTube's `i.ytimg.com`). Loaded directly.
enum ThumbnailURL {
    private static let proxyHosts = [
        "fbcdn.net", "cdninstagram.com", "instagram.f",
        "tiktokcdn", "tiktok.com", "licdn.com", "twimg.com", "pinimg.com",
    ]

    static func resolve(_ raw: String?,
                        platform: Platform? = nil,
                        base: URL = AppConfig.apiBaseURL) -> URL? {
        guard let raw, !raw.isEmpty else { return nil }

        // A path this server already owns.
        if raw.hasPrefix("/") {
            return URL(string: base.absoluteString + raw)
        }

        guard let url = URL(string: raw), let host = url.host?.lowercased() else {
            return URL(string: raw)
        }

        guard proxyHosts.contains(where: { host.contains($0) }) else { return url }

        var components = URLComponents(url: base.appendingPathComponent("api/thumbnail"),
                                       resolvingAgainstBaseURL: false)
        var query = [URLQueryItem(name: "url", value: raw)]
        if let platform {
            // Lets the proxy send the Referer the CDN expects. Without it an
            // otherwise-live Instagram thumbnail comes back 403.
            query.append(URLQueryItem(name: "platform", value: platform.rawValue))
        }
        components?.queryItems = query
        return components?.url
    }
}
