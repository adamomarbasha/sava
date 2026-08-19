import SwiftUI
import UIKit

/// A small, shared image cache + async loader. Memory-cached by URL, backed by
/// URLSession's HTTP cache on disk. Loads are cancellable so off-screen cells
/// stop fetching as the user scrolls.
actor ImagePipeline {
    static let shared = ImagePipeline()

    private let cache = NSCache<NSURL, UIImage>()
    private let session: URLSession

    init() {
        cache.countLimit = 200
        let config = URLSessionConfiguration.default
        config.requestCachePolicy = .returnCacheDataElseLoad
        config.urlCache = URLCache(memoryCapacity: 32 * 1024 * 1024,
                                   diskCapacity: 256 * 1024 * 1024)
        session = URLSession(configuration: config)
    }

    func cached(_ url: URL) -> UIImage? { cache.object(forKey: url as NSURL) }

    func image(for url: URL) async throws -> UIImage {
        if let hit = cache.object(forKey: url as NSURL) { return hit }
        let (data, _) = try await session.data(from: url)
        try Task.checkCancellation()
        guard let image = UIImage(data: data) else {
            throw URLError(.cannotDecodeContentData)
        }
        cache.setObject(image, forKey: url as NSURL, cost: data.count)
        return image
    }
}
