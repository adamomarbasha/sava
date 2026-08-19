import Foundation

/// Resolves a raw `thumbnail_url` into a loadable URL.
///
/// Some CDNs (Instagram/Facebook, TikTok) serve signed, referer-gated images
/// that fail to load directly from a client, so we route them through the
/// backend's `GET /api/thumbnail?url=` proxy — the same approach the web app
/// uses. YouTube and ordinary hosts load directly.
enum ThumbnailURL {
    private static let proxyHosts = ["fbcdn.net", "cdninstagram.com", "instagram.f", "tiktokcdn", "tiktok.com"]

    static func resolve(_ raw: String?, base: URL = AppConfig.apiBaseURL) -> URL? {
        guard let raw, !raw.isEmpty else { return nil }

        // Relative/static path served by the backend.
        if raw.hasPrefix("/") {
            return URL(string: base.absoluteString + raw)
        }

        guard let url = URL(string: raw), let host = url.host?.lowercased() else {
            return URL(string: raw)
        }

        if proxyHosts.contains(where: { host.contains($0) }) {
            var comps = URLComponents(url: base.appendingPathComponent("api/thumbnail"), resolvingAgainstBaseURL: false)
            comps?.queryItems = [URLQueryItem(name: "url", value: raw)]
            return comps?.url
        }
        return url
    }
}
