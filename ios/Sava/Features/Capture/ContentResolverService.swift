import Foundation
import UIKit

/// Screenshot-assisted content resolution against `POST /api/resolve`.
///
/// Used only when no direct URL is available. The server reads the on-screen
/// text with a vision model and matches it to a real video. Screenshot bytes
/// are held in memory, uploaded, and never written to Photos or to disk.
///
/// The endpoint is live, so this no longer short-circuits to `.unavailable`.
struct ContentResolverService {
    let client: APIClient

    enum Resolution {
        case resolved(url: String, confidence: Double)
        /// The screen was read clearly but the platform exposes no way to
        /// recover an exact post URL. The server saved what was observed.
        case partiallySaved(what: String)
        case notConfident(reason: String, read: String?)
        case unavailable(reason: String)

        var reasonText: String {
            switch self {
            case .resolved: return "matched"
            case .partiallySaved: return "partial_capture"
            case .notConfident(let r, _): return r
            case .unavailable(let r): return r
            }
        }
    }

    struct ResolveResponse: Decodable {
        let ok: Bool
        let url: String?
        let platform: String?
        let confidence: Double?
        let reason: String?
        let read: Read?
        let partial: Bool?
        let saved: Saved?
        let alreadySaved: Bool?

        struct Read: Decodable {
            let title: String?
            let creator: String?
        }
        struct Saved: Decodable {
            let id: Int
            let title: String?
            let author: String?
        }

        enum CodingKeys: String, CodingKey {
            case ok, url, platform, confidence, reason, read, partial, saved
            case alreadySaved = "already_saved"
        }
    }

    /// Send a screenshot for identification.
    /// - Parameter platformHint: what the client believes is on screen, if known.
    func resolve(screenshot: Data, platformHint: Platform? = nil) async -> Resolution {
        guard !screenshot.isEmpty else {
            return .unavailable(reason: "empty_screenshot")
        }

        // Shortcuts hands over a full-resolution PNG (~2.3 MB on an iPhone 15
        // Pro). Uploading that over Wi-Fi dominates end-to-end latency and the
        // vision model does not need it, so downscale to JPEG first.
        let payload = Self.compress(screenshot)
        #if DEBUG
        NSLog("[Sava screenshot] compressed %d KB -> %d KB",
              screenshot.count / 1024, payload.count / 1024)
        NSLog("[Sava screenshot] upload started -> POST api/resolve")
        #endif

        // Let the server persist a partial capture when an exact URL is not
        // recoverable — losing the save entirely is the worse outcome.
        var query: [URLQueryItem] = [URLQueryItem(name: "save", value: "true")]
        if let platformHint, platformHint != .other {
            query.append(URLQueryItem(name: "platform", value: platformHint.rawValue))
        }

        let (endpoint, contentType) = Endpoint.multipart(
            "api/resolve",
            fileField: "image",
            fileName: "screen.jpg",
            mimeType: "image/jpeg",
            fileData: payload,
            query: query
        )
        var request = endpoint
        request.contentTypeOverride = contentType
        // Server-side this runs a vision model and a search; 20s is not enough.
        request.timeout = 75

        do {
            let response: ResolveResponse = try await client.send(request)
            #if DEBUG
            NSLog("[Sava screenshot] resolver ok=%@ url=%@ conf=%.2f reason=%@",
                  response.ok ? "yes" : "no", response.url ?? "nil",
                  response.confidence ?? 0, response.reason ?? "-")
            #endif
            if response.ok, let url = response.url {
                return .resolved(url: url, confidence: response.confidence ?? 0)
            }
            if response.partial == true, let saved = response.saved {
                let who = saved.author.map { "@\($0)" } ?? "that"
                return .partiallySaved(what: who)
            }
            if response.alreadySaved == true {
                return .partiallySaved(what: "that")
            }
            let read = [response.read?.title, response.read?.creator]
                .compactMap { $0 }
                .filter { !$0.isEmpty }
                .joined(separator: " — ")
            return .notConfident(reason: response.reason ?? "not_identified",
                                 read: read.isEmpty ? nil : read)
        } catch let error as APIError {
            return .unavailable(reason: error.userMessage)
        } catch {
            return .unavailable(reason: "resolver_unreachable")
        }
    }
}

// MARK: - Screenshot compression

extension ContentResolverService {
    /// Downscale to at most 1280px on the long edge and re-encode as JPEG.
    /// Typically turns a 2.3 MB PNG into ~150 KB with no loss of legibility
    /// for on-screen text.
    static func compress(_ data: Data, maxEdge: CGFloat = 1280,
                         quality: CGFloat = 0.8) -> Data {
        guard let image = UIImage(data: data) else { return data }
        let longest = max(image.size.width, image.size.height)
        let target: UIImage
        if longest > maxEdge {
            let scale = maxEdge / longest
            let size = CGSize(width: image.size.width * scale,
                              height: image.size.height * scale)
            let renderer = UIGraphicsImageRenderer(size: size)
            target = renderer.image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
        } else {
            target = image
        }
        return target.jpegData(compressionQuality: quality) ?? data
    }
}
