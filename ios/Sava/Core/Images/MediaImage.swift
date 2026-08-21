import SwiftUI
import UIKit

// The one media rendering system. Everything that shows a saved item's image
// goes through here — cards, heroes, collection covers, inline answer
// references. There is no `AsyncImage` anywhere else in the app.
//
// Three invariants:
//
//   1. Geometry is ours. `scaledToFill` + clip into a box Sava decides, so a
//      vertical TikTok and a tiny Instagram thumbnail occupy the same space and
//      the grid keeps its rhythm.
//   2. A missing image is a *designed* state, never a hole. Roughly a quarter of
//      this library's saves point at signed CDN URLs that have since expired;
//      the layout must not notice, and the user must not see a broken glyph.
//   3. Nothing decodes larger than it draws. A 2 MB thumbnail rendered into a
//      180pt card is the easiest way to make a grid stutter.

// MARK: - Loading

/// Downsampling image loader with a memory cache, request coalescing, and a
/// negative cache.
///
/// The negative cache matters more than it sounds: a dead thumbnail would
/// otherwise be re-requested every time its cell scrolled back on screen,
/// turning expired media into a steady stream of failing network calls.
actor ImagePipeline {
    static let shared = ImagePipeline()

    private let cache: NSCache<NSString, UIImage> = {
        let c = NSCache<NSString, UIImage>()
        c.countLimit = 240
        c.totalCostLimit = 96 * 1024 * 1024
        return c
    }()

    private var inFlight: [String: Task<UIImage?, Never>] = [:]
    private var failed: Set<String> = []

    /// Honours `Cache-Control` rather than preferring whatever is on disk.
    ///
    /// `.returnCacheDataElseLoad` looks like the fast choice and is a trap: it
    /// serves any cached response indefinitely, including an error page, so a
    /// thumbnail that 404'd once during a deploy stays broken in the app long
    /// after the server is fixed. The protocol policy revalidates, and the
    /// backend sends a week-long max-age on stored thumbnails, so the common
    /// case is still served from disk without a request.
    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.requestCachePolicy = .useProtocolCachePolicy
        config.urlCache = URLCache(memoryCapacity: 16 * 1024 * 1024,
                                   diskCapacity: 256 * 1024 * 1024)
        config.timeoutIntervalForRequest = 15
        return URLSession(configuration: config)
    }()

    func image(for url: URL, targetWidth: CGFloat, scale: CGFloat) async -> UIImage? {
        let bucket = Self.bucket(targetWidth)
        let key = "\(url.absoluteString)|\(bucket)"

        if let hit = cache.object(forKey: key as NSString) { return hit }
        if failed.contains(url.absoluteString) { return nil }
        if let existing = inFlight[key] { return await existing.value }

        let task = Task<UIImage?, Never> { [session] in
            for candidate in Self.candidates(for: url) {
                guard let (data, response) = try? await session.data(from: candidate)
                else { continue }
                if let http = response as? HTTPURLResponse,
                   !(200...299).contains(http.statusCode) { continue }
                if let image = Self.downsample(data, to: CGFloat(bucket) * scale) {
                    return image
                }
            }
            return nil
        }
        inFlight[key] = task
        let result = await task.value
        inFlight[key] = nil

        if let result {
            cache.setObject(result, forKey: key as NSString,
                            cost: Int(result.size.width * result.size.height * 4))
        } else {
            failed.insert(url.absoluteString)
        }
        return result
    }

    /// The URLs to try, in order.
    ///
    /// YouTube only guarantees `hqdefault`. `maxresdefault` exists for popular
    /// uploads and 404s for the rest, and `vi_webp` is missing for older ones —
    /// which is why a grid of YouTube saves had holes in it while the same
    /// videos showed a thumbnail everywhere else. Rather than downgrade every
    /// thumbnail to the safe size, ask for the good one and fall back.
    ///
    /// Only YouTube gets this treatment: it is the one host whose URLs are
    /// mechanically derivable from a video id, so a fallback is a certainty
    /// rather than a guess.
    nonisolated static func candidates(for url: URL) -> [URL] {
        let text = url.absoluteString
        guard text.contains("ytimg.com"), text.contains("maxresdefault") else {
            return [url]
        }
        var out = [url]
        // webp first (smaller), then jpg, at the size that always exists.
        for replacement in ["sddefault", "hqdefault"] {
            if let alt = URL(string: text.replacingOccurrences(
                of: "maxresdefault", with: replacement)) {
                out.append(alt)
            }
        }
        if let jpg = URL(string: text
            .replacingOccurrences(of: "/vi_webp/", with: "/vi/")
            .replacingOccurrences(of: "maxresdefault.webp", with: "hqdefault.jpg")) {
            out.append(jpg)
        }
        return out
    }

    /// Quantise the requested width so two cards a point apart share one decode
    /// rather than each holding their own copy of the same picture.
    private static func bucket(_ width: CGFloat) -> Int {
        let steps: [Int] = [64, 120, 200, 320, 480, 720, 1080]
        let w = Int(width.rounded())
        return steps.first { $0 >= w } ?? 1080
    }

    /// Decode straight to the drawn size rather than decoding full-size and
    /// scaling afterwards.
    nonisolated private static func downsample(_ data: Data, to maxPixels: CGFloat) -> UIImage? {
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: max(160, maxPixels),
        ]
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let cg = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
        else { return nil }
        return UIImage(cgImage: trimmingBars(cg))
    }

    /// Removes solid bars baked into a thumbnail.
    ///
    /// Platforms letterbox their own cover images. A TikTok cover comes back
    /// 540×960 with 142px of pure black at the top *and* bottom — nearly a third
    /// of the file is padding. Show that faithfully and the detail screen is a
    /// third black; crop past it and the frame is cut. Neither is what the person
    /// saved: the picture is the 540×676 in the middle, and once the padding is
    /// gone it fills the width on its own.
    ///
    /// Deliberately timid. A row only counts as a bar if it is near-uniform *and*
    /// essentially black or essentially white, only runs touching an edge are
    /// considered, and if the result would remove more than half the image the
    /// whole thing is abandoned — a dark photograph must never be mistaken for
    /// padding.
    nonisolated private static func trimmingBars(_ image: CGImage) -> CGImage {
        let width = image.width, height = image.height
        guard width > 32, height > 32 else { return image }

        // Analysis runs on a tiny copy; the cost is a few thousand pixels.
        let sampleW = 24
        let sampleH = min(height, 320)
        guard let context = CGContext(
            data: nil, width: sampleW, height: sampleH, bitsPerComponent: 8,
            bytesPerRow: sampleW * 4, space: CGColorSpaceDeviceRGB,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        else { return image }

        context.draw(image, in: CGRect(x: 0, y: 0, width: sampleW, height: sampleH))
        guard let raw = context.data else { return image }
        let pixels = raw.bindMemory(to: UInt8.self, capacity: sampleW * sampleH * 4)

        func isBar(row y: Int) -> Bool {
            var lowest = 255, highest = 0, total = 0
            for x in 0..<sampleW {
                let i = (y * sampleW + x) * 4
                let value = (Int(pixels[i]) + Int(pixels[i + 1]) + Int(pixels[i + 2])) / 3
                lowest = min(lowest, value)
                highest = max(highest, value)
                total += value
            }
            let mean = total / sampleW
            return (highest - lowest) <= 12 && (mean <= 20 || mean >= 236)
        }

        var topRows = 0
        while topRows < sampleH, isBar(row: topRows) { topRows += 1 }
        var bottomRows = 0
        while bottomRows < sampleH - topRows, isBar(row: sampleH - 1 - bottomRows) {
            bottomRows += 1
        }
        guard topRows > 0 || bottomRows > 0 else { return image }

        let scale = CGFloat(height) / CGFloat(sampleH)
        let top = Int((CGFloat(topRows) * scale).rounded())
        let bottom = Int((CGFloat(bottomRows) * scale).rounded())
        let remaining = height - top - bottom

        // Too small a change is noise; too large a change is a mistake.
        guard remaining >= height / 2,
              (top + bottom) > height / 100
        else { return image }

        let crop = CGRect(x: 0, y: top, width: width, height: remaining)
        return image.cropping(to: crop) ?? image
    }

    private static let CGColorSpaceDeviceRGB = CGColorSpaceCreateDeviceRGB()
}

/// Shapes of pictures already seen this session.
///
/// A hero sizes its frame to the real image, which means the very first time it
/// opens it has to settle once the picture arrives. Remembering the shape makes
/// that a one-time event: every later visit lays out correctly on the first
/// frame, with no resize at all. Main-actor isolated and tiny — this stores one
/// number per URL, not pixels.
@MainActor
enum MediaAspect {
    private static var cache: [URL: CGFloat] = [:]

    static func remember(_ aspect: CGFloat, for url: URL) {
        guard aspect.isFinite, aspect > 0 else { return }
        if cache.count > 400 { cache.removeAll(keepingCapacity: true) }
        cache[url] = aspect
    }

    static func known(for url: URL?) -> CGFloat? {
        guard let url else { return nil }
        return cache[url]
    }
}

// MARK: - Core view

/// What to draw when there is no picture.
///
/// Every absence in this app is one of three things, and each wants a different
/// answer — which is why this is an explicit choice at the call site rather than
/// a single grey rectangle everywhere.
enum MediaFallback: Equatable {
    /// A save. Shows the platform mark and the save's own title, so a card with
    /// an expired thumbnail still reads as that save rather than as damage.
    case save(Platform, title: String?)
    /// A collection. Shows the collection's name — no platform, because a
    /// collection does not belong to one.
    case label(String)
    /// Nothing at all, and transparent, so whatever is behind shows through.
    /// Used for the tiles of a collection mosaic: a dead tile reveals the
    /// collection's own plate instead of punching a grey hole in the cover.
    case transparent
}

/// How the picture meets the box Sava gave it.
enum MediaFit {
    /// Crop to fill. Correct in a grid, where a shared rhythm matters more than
    /// any one image, and where every tile is small enough that a crop reads as
    /// framing rather than as loss.
    case fill

    /// Show the whole picture, on a blurred enlargement of itself.
    ///
    /// Correct for a hero, where the point is to actually see what was saved.
    /// A 9:16 TikTok cropped into a wide box loses the face and the on-screen
    /// text — the two things that identify it. Fitting keeps the frame intact
    /// and fills the leftover space with the image's own colour, so the hero is
    /// still edge-to-edge and never shows an accidental letterbox.
    case fitOnBackdrop
}

/// Where a piece of media is being shown, and therefore what shape it takes.
///
/// This is the one place that answers "how tall is this box". Before it existed
/// the answer was spelled out at every call site — `aspectRatio(4/5)` in the
/// grid, `verticalHero` in the detail screen, a hardcoded height in a row — and
/// they drifted, which is how a library ends up with one TikTok card taller than
/// its neighbour and a grid that looks crooked.
///
/// The rule the whole system rests on: **the container owns the geometry and the
/// picture fits inside it.** A remote image never gets to decide layout, because
/// its dimensions arrive after the layout has already been drawn — letting it
/// decide is exactly what makes cards jump when images load.
enum MediaPresentation {
    /// A tile in a two-column grid: library, search, inside a collection.
    case card
    /// The media at the top of a detail screen.
    case hero
    /// A collection's cover.
    case cover
    /// Full-screen, in the short-form feed.
    case stage
    /// A small thumbnail beside text, in a list row or an inline reference.
    case row

    /// Width ÷ height of the box.
    ///
    /// `intrinsic` is the real ratio of the loaded picture, when it is known.
    /// It is consulted only where a variable shape is actually wanted; every
    /// other case ignores it on purpose, because a fixed box is what keeps a
    /// row of neighbours aligned.
    func ratio(for platform: Platform, intrinsic: CGFloat? = nil) -> CGFloat {
        switch self {
        case .card:
            // Fixed per platform class, never per image. Two TikToks shot at
            // different resolutions must produce identically sized cards, or
            // the column rhythm breaks and the grid reads as crooked.
            return MediaRatio.forPlatform(platform)
        case .hero:
            // Vertical media gets a fixed square stage so a 9:16 clip does not
            // become a 700pt wall; landscape keeps its true shape, falling back
            // to the platform default until the picture reports in.
            if platform.prefersPortrait { return MediaRatio.verticalHero }
            return intrinsic ?? MediaRatio.landscape
        case .cover:
            return 4.0 / 3.0
        case .stage:
            // The stage is the screen. The media fits inside it rather than
            // reshaping it.
            return 0
        case .row:
            return 1.0
        }
    }

    /// How the picture meets that box.
    func fit(for platform: Platform) -> MediaFit {
        switch self {
        case .card, .row:
            // Small, and a shared rhythm matters more than any one image, so a
            // crop reads as framing rather than as loss.
            return .fill
        case .hero, .cover, .stage:
            // Large enough that cropping destroys the thing the user came to
            // look at. Show all of it and absorb the leftover deliberately.
            return .fitOnBackdrop
        }
    }
}

/// Fills a Sava-defined box with remote media, or a designed fallback./// Fills a Sava-defined box with remote media, or a designed fallback.
///
/// The caller decides the box. This view never asks the image how big it wants
/// to be — which is what keeps the layout from moving when a picture arrives.
struct MediaImage: View {
    let url: URL?
    var fallback: MediaFallback = .transparent
    var fit: MediaFit = .fill
    var cornerRadius: CGFloat = Radius.media
    /// Width ÷ height of the picture that loaded. Lets a hero size its frame to
    /// the real thing instead of guessing.
    var onLoadAspect: ((CGFloat) -> Void)? = nil

    @State private var image: UIImage?
    @State private var didFail = false
    @Environment(\.displayScale) private var displayScale
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var isEmpty: Bool { didFail || url == nil }

    var body: some View {
        GeometryReader { geo in
            ZStack {
                // Opaque under real media and under a designed plate, so a
                // failed load can never flash the page ground through it. Left
                // transparent only where the caller asked for that.
                if !(isEmpty && fallback == .transparent) {
                    Rectangle().fill(SavaColor.fill)
                }

                if let image {
                    picture(image, in: geo.size)
                } else if isEmpty {
                    plate.transition(.opacity)
                } else {
                    Skeleton(cornerRadius: 0)
                }
            }
            .frame(width: geo.size.width, height: geo.size.height)
            .clipped()
            .task(id: url) { await load(width: geo.size.width) }
        }
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
        .animation(Motion.respecting(Motion.gentle, reduceMotion), value: image != nil)
        .accessibilityHidden(true)
    }

    @ViewBuilder private func picture(_ image: UIImage, in size: CGSize) -> some View {
        switch fit {
        case .fill:
            Image(uiImage: image)
                .resizable()
                .scaledToFill()
                .transition(.opacity)

        case .fitOnBackdrop:
            ZStack {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
                    // Overscaled before blurring so the blur's soft edge never
                    // reaches the frame and leaves a pale rim.
                    .scaleEffect(1.25)
                    .blur(radius: 44, opaque: true)
                    .overlay(Color.black.opacity(0.28))
                    .frame(width: size.width, height: size.height)
                    .clipped()

                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
            }
            .transition(.opacity)
        }
    }

    @ViewBuilder private var plate: some View {
        switch fallback {
        case .save(let platform, let title):
            MediaPlate(platform: platform, title: title)
        case .label(let name):
            MediaPlate(platform: nil, title: name)
        case .transparent:
            Color.clear
        }
    }

    private func load(width: CGFloat) async {
        guard let url else {
            didFail = true
            return
        }
        guard image == nil, width > 0 else { return }
        let loaded = await ImagePipeline.shared.image(for: url, targetWidth: width,
                                                      scale: displayScale)
        if let loaded {
            image = loaded
            didFail = false
            if loaded.size.height > 0 {
                let aspect = loaded.size.width / loaded.size.height
                MediaAspect.remember(aspect, for: url)
                onLoadAspect?(aspect)
            }
        } else {
            didFail = true
        }
    }
}

/// The designed stand-in for a missing image — most often a signed CDN URL that
/// has expired, which is the normal end state for TikTok and Instagram covers.
///
/// It sets the save's own title instead of showing a broken-image glyph. That
/// turns an absence into an editorial text plate: it still says what the thing
/// is, it still fills its box, and it reads as a decision rather than damage.
/// Nothing here is invented — the title is the one Sava already stored.
struct MediaPlate: View {
    /// Nil for a collection, which has no platform of its own.
    let platform: Platform?
    var title: String? = nil

    private var tint: Color { platform?.tint ?? SavaColor.secondary }

    var body: some View {
        ZStack {
            tint.opacity(0.09)
            if platform == nil { collectionPlate } else { savePlate }
        }
        .accessibilityHidden(true)
    }

    /// A save with no picture. When there is a real title it sits where a
    /// caption would, top-left, so the card still scans as a card. When there
    /// isn't, the platform mark centres instead of leaving the plate lopsided.
    @ViewBuilder private var savePlate: some View {
        if let title, !title.isEmpty {
            VStack(alignment: .leading, spacing: Space.s) {
                platformMark
                Text(title)
                    .font(.system(size: 15, weight: .medium, design: .serif))
                    .foregroundStyle(SavaColor.primary.opacity(0.62))
                    .lineLimit(4)
                    .multilineTextAlignment(.leading)
                    .lineSpacing(1)
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .padding(Space.m)
        } else {
            platformMark(size: 16)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var platformMark: some View { platformMark(size: 11) }

    @ViewBuilder private func platformMark(size: CGFloat) -> some View {
        if let platform {
            Text(platform.shortMark)
                .font(.system(size: size, weight: .bold, design: .rounded))
                .tracking(0.6)
                .foregroundStyle(tint.opacity(0.6))
        }
    }

    /// A collection with no usable member imagery. Set centred and large, it
    /// reads as a title card rather than as a blank rectangle — the difference
    /// between "nothing here" and "nothing loaded".
    private var collectionPlate: some View {
        Text(title ?? "")
            .font(.system(size: 22, weight: .regular, design: .serif))
            .foregroundStyle(SavaColor.primary.opacity(0.5))
            .lineLimit(2)
            .multilineTextAlignment(.center)
            .minimumScaleFactor(0.7)
            .padding(.horizontal, Space.l)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Composed components

/// Grid/card media. Fixed aspect ratio decided by platform, never by source.
struct MediaThumbnail: View {
    let bookmark: Bookmark
    var ratio: CGFloat? = nil

    var body: some View {
        MediaImage(url: bookmark.imageURL,
                   fallback: .save(bookmark.platform, title: bookmark.plateTitle))
            .aspectRatio(ratio ?? MediaRatio.forPlatform(bookmark.platform),
                         contentMode: .fit)
            .overlay(alignment: .bottomTrailing) {
                if let duration = bookmark.durationLabel {
                    DurationBadge(text: duration).padding(Space.s)
                }
            }
            .overlay(alignment: .topLeading) {
                if bookmark.isProcessing {
                    ProcessingDot().padding(Space.s)
                }
            }
    }
}

/// Detail hero. Bounded height so a tall vertical video cannot push the whole
/// page off screen before the reader sees a word of it.
///
/// The hero runs under the status bar, which is the right look and the wrong
/// contrast: the clock and the back button would otherwise sit on an image of
/// unknown brightness. A short scrim at the top fixes that without darkening the
/// picture itself — it is legibility, not decoration, which is why it is 90pt
/// tall and stops.
struct MediaHero: View {
    let bookmark: Bookmark

    @State private var aspect: CGFloat?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(bookmark: Bookmark) {
        self.bookmark = bookmark
        // Seeded from what is already known, so a revisit is correct on frame
        // one rather than settling again.
        _aspect = State(initialValue: MediaAspect.known(for: bookmark.imageURL))
    }

    var body: some View {
        // Landscape media takes its shape from the picture: a 16:9 still fills
        // the width exactly, edge to edge, with nothing cropped and nothing
        // painted over it.
        //
        // Vertical media gets a fixed square stage instead. Sizing *it* from the
        // source made a 9:16 TikTok 700pt tall and a screenshot-shaped Instagram
        // post taller than the phone — the whole thumbnail was visible, and it
        // was all you could see. Inside the square the image is still complete,
        // just no longer the entire screen, and TikTok and Instagram come out
        // identical to each other.
        Color.clear
            .aspectRatio(frameRatio, contentMode: .fit)
            .overlay {
                MediaImage(url: bookmark.imageURL,
                           fallback: .save(bookmark.platform, title: bookmark.plateTitle),
                           fit: .fitOnBackdrop,
                           cornerRadius: 0,
                           onLoadAspect: { loaded in
                               guard !bookmark.platform.prefersPortrait,
                                     abs((aspect ?? 0) - loaded) > 0.001 else { return }
                               withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
                                   aspect = loaded
                               }
                           })
            }
    }

    /// Fixed for vertical platforms; the picture's own shape for everything
    /// else, falling back to that platform's usual shape until it loads.
    private var frameRatio: CGFloat {
        guard !bookmark.platform.prefersPortrait else { return MediaRatio.verticalHero }
        return aspect ?? placeholderAspect
    }

    /// Held for the instant before a landscape picture arrives, so the page does
    /// not start at zero height and the settle is a nudge rather than a jump.
    private var placeholderAspect: CGFloat {
        bookmark.platform == .youtube ? MediaRatio.landscape : 1
    }
}

/// A collection cover built from the media actually inside it — never a folder
/// glyph. One image reads as a photo; several read as a collection, so the
/// mosaic is the point rather than decoration.
///
/// Underneath the tiles sits a plate carrying the collection's name. Members
/// whose thumbnails have expired render transparent, so the name shows through
/// instead of a grey hole — and a collection whose imagery is entirely gone
/// still has a cover that reads as that collection.
struct CollectionCover: View {
    let name: String
    let thumbnails: [URL?]
    /// Every cover in the grid is the same shape. A shelf whose tiles are all
    /// slightly different heights reads as a bug long before it reads as
    /// variety, so the ratio is fixed and the imagery adapts to it.
    var aspect: CGFloat = 4.0 / 3.0

    private var usable: [URL] { thumbnails.compactMap { $0 } }

    var body: some View {
        Color.clear
            .aspectRatio(aspect, contentMode: .fit)
            // The named plate sits *behind* the imagery, not instead of it, so
            // a cover whose source images have expired degrades to something
            // legible rather than to an empty grey box. Instagram and TikTok
            // thumbnail URLs are signed and do expire, and a collection is not
            // broken merely because its cover art is.
            .overlay { MediaPlate(platform: nil, title: name) }
            .overlay { content }
            .background(SavaColor.fill)
            .clipShape(RoundedRectangle(cornerRadius: Radius.media, style: .continuous))
            .accessibilityHidden(true)
    }

    @ViewBuilder private var content: some View {
        if usable.isEmpty {
            // Nothing to draw; the plate behind is already showing the name.
            EmptyView()
        } else if usable.count < 4 {
            // Not enough for a mosaic that would not look broken, and with one
            // or two members the first image genuinely is the collection.
            MediaImage(url: usable[0], fallback: .transparent,
                       fit: .fitOnBackdrop, cornerRadius: 0)
        } else {
            mosaic
        }
    }

    /// Four members, evenly quartered. Hairline gaps rather than padding, so
    /// the tile still reads as one object.
    private var mosaic: some View {
        let tiles = Array(usable.prefix(4))
        return VStack(spacing: 1.5) {
            ForEach(0..<2, id: \.self) { row in
                HStack(spacing: 1.5) {
                    ForEach(0..<2, id: \.self) { column in
                        MediaImage(url: tiles[row * 2 + column],
                                   fallback: .transparent, cornerRadius: 0)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .clipped()
                    }
                }
            }
        }
    }
}

/// A saved item referenced inside an Ask answer. Small, tappable, and the
/// reason Sava's answers look different from a chatbot's.
struct InlineMediaReference: View {
    let save: RelatedSave
    /// 1-based position, shown when the answer cites sources by number so a
    /// superscript in the prose resolves to a specific piece of media.
    var index: Int? = nil

    var body: some View {
        HStack(spacing: Space.m) {
            if let index {
                Text("\(index)")
                    .font(SavaType.numeric)
                    .foregroundStyle(SavaColor.tertiary)
                    .frame(width: 14, alignment: .trailing)
            }
            MediaImage(url: save.imageURL,
                       fallback: .save(save.platform, title: nil),
                       cornerRadius: 6)
                .frame(width: 58, height: 44)

            VStack(alignment: .leading, spacing: 2) {
                Text(save.displayTitle)
                    .font(SavaType.mediaTitle)
                    .foregroundStyle(SavaColor.primary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                Text(save.metaLine)
                    .font(SavaType.meta)
                    .foregroundStyle(SavaColor.tertiary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)

            Image(systemName: "chevron.right")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(SavaColor.tertiary)
        }
        .padding(.vertical, Space.s)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(save.displayTitle). \(save.metaLine)")
    }
}

// MARK: - Badges

struct DurationBadge: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .semibold, design: .rounded))
            .monospacedDigit()
            .foregroundStyle(.white)
            .padding(.horizontal, 6)
            .padding(.vertical, 3)
            .background(.black.opacity(0.62), in: Capsule())
            .accessibilityHidden(true)
    }
}

/// Processing indicator. A single pulsing dot — enough to say "still working"
/// without a spinner, a banner, or a card that changes size when it finishes.
struct ProcessingDot: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var on = false

    var body: some View {
        Circle()
            .fill(SavaColor.accent)
            .frame(width: 7, height: 7)
            .overlay(Circle().strokeBorder(.white.opacity(0.65), lineWidth: 1))
            .opacity(on && !reduceMotion ? 0.35 : 1)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) {
                    on = true
                }
            }
            .accessibilityHidden(true)
    }
}
