import CoreGraphics
import Foundation

/// How one save can actually be played, as decided by the server.
///
/// The client deliberately does not work this out for itself. Whether a TikTok
/// needs proxying, whether a YouTube video must go through the sanctioned
/// embed, and whether a photo post has finished mirroring its slides are all
/// server-side facts that change without an app release. The viewer's job is to
/// render whichever of these four shapes it is handed — including the fourth,
/// which is the one most interfaces forget to design.
struct PlaybackDescriptor: Decodable, Equatable {
    enum Kind: String, Decodable {
        /// A direct MP4, proxied by Sava. Plays in `AVPlayer`.
        case video
        /// The platform's own player in a web view. YouTube Shorts.
        case embed
        /// An ordered set of images. TikTok photo posts.
        case gallery
        /// Nothing to play, with a reason worth showing.
        case unavailable
    }

    let kind: Kind
    let url: URL?
    let poster: URL?
    /// width / height. Used to size the stage before the first frame arrives.
    let aspect: CGFloat?
    let images: [GalleryImage]
    let reason: String?
    let durationSeconds: Double?

    enum CodingKeys: String, CodingKey {
        case kind, url, poster, aspect, images, reason
        case durationSeconds = "duration_seconds"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // An unrecognised kind is a newer server, not a crash. Degrade to the
        // state the viewer already knows how to draw.
        let rawKind: String = (try? c.decode(String.self, forKey: .kind)) ?? ""
        kind = Kind(rawValue: rawKind) ?? .unavailable

        let rawURL: String? = (try? c.decodeIfPresent(String.self, forKey: .url)) ?? nil
        url = rawURL.flatMap(URL.init(string:))

        let rawPoster: String? = (try? c.decodeIfPresent(String.self, forKey: .poster)) ?? nil
        poster = rawPoster.flatMap(URL.init(string:))

        let rawAspect: Double? = (try? c.decodeIfPresent(Double.self, forKey: .aspect)) ?? nil
        aspect = rawAspect.map { CGFloat($0) }

        let rawImages: [GalleryImage]? =
            (try? c.decodeIfPresent([GalleryImage].self, forKey: .images)) ?? nil
        images = rawImages ?? []

        reason = (try? c.decodeIfPresent(String.self, forKey: .reason)) ?? nil
        durationSeconds =
            (try? c.decodeIfPresent(Double.self, forKey: .durationSeconds)) ?? nil
    }

    init(kind: Kind, url: URL? = nil, poster: URL? = nil, aspect: CGFloat? = nil,
         images: [GalleryImage] = [], reason: String? = nil,
         durationSeconds: Double? = nil) {
        self.kind = kind
        self.url = url
        self.poster = poster
        self.aspect = aspect
        self.images = images
        self.reason = reason
        self.durationSeconds = durationSeconds
    }

    /// What to show when the descriptor itself could not be fetched.
    static func offline(poster: URL?) -> PlaybackDescriptor {
        PlaybackDescriptor(kind: .unavailable, poster: poster,
                           reason: "Couldn't reach Sava. Check your connection.")
    }

    struct GalleryImage: Decodable, Equatable, Identifiable {
        let url: URL?
        let width: Int?
        let height: Int?
        let index: Int

        var id: Int { index }

        enum CodingKeys: String, CodingKey { case url, width, height, index }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            let raw: String? = (try? c.decodeIfPresent(String.self, forKey: .url)) ?? nil
            url = raw.flatMap(URL.init(string:))
            width = (try? c.decodeIfPresent(Int.self, forKey: .width)) ?? nil
            height = (try? c.decodeIfPresent(Int.self, forKey: .height)) ?? nil
            index = ((try? c.decodeIfPresent(Int.self, forKey: .index)) ?? nil) ?? 0
        }

        var aspect: CGFloat? {
            guard let width, let height, width > 0, height > 0 else { return nil }
            return CGFloat(width) / CGFloat(height)
        }
    }
}
