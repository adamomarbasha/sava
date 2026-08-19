import Foundation

/// Client boundary for screenshot-assisted content resolution (YouTube/
/// Instagram, where the current URL isn't reliably exposed on-device).
///
/// PLANNED CONTRACT (server work remaining):
///   POST /api/resolve   (multipart/form-data, field "image": JPEG/PNG bytes)
///   → 200 { "url": "<canonical url>", "confidence": 0.0...1.0 }
///   → 422 when nothing can be confidently identified
///
/// The backend does not implement this yet, so `resolve` reports
/// `.unavailable` instead of pretending. No on-device image recognition is
/// performed here — that would be fake. Screenshot bytes are forwarded to the
/// server (when live) and never persisted to Photos.
struct ContentResolverService {
    let client: APIClient

    /// Flip to true when `POST /api/resolve` ships server-side.
    static let isEnabled = false

    enum Resolution {
        case resolved(String)     // canonical URL
        case notConfident         // resolver ran but couldn't identify
        case unavailable          // endpoint not deployed yet
    }

    struct ResolveResponse: Decodable {
        let url: String
        let confidence: Double?
    }

    func resolve(screenshot: Data) async -> Resolution {
        guard Self.isEnabled else { return .unavailable }
        // Real multipart upload wiring lives here once the endpoint exists.
        // Intentionally not implemented against a non-existent route.
        return .unavailable
    }
}
