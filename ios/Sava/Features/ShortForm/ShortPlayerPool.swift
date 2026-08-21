import AVFoundation
import Combine
import SwiftUI

/// A bounded set of reusable `AVPlayer`s shared by the whole swipe feed.
///
/// The naive version of this screen gives every page its own player. That looks
/// fine for five items and then falls over: each `AVPlayer` holds a decoder
/// session, iOS grants a small number of those per process, and every live
/// player keeps buffering whether or not it is on screen — so a feed of forty
/// TikToks would be paying to download forty videos to show one. On a proxied
/// stream that cost is Sava's, not the platform's.
///
/// So there are three players for any feed length. They are *reused* rather
/// than recreated — allocating an `AVPlayer` is expensive, swapping its item is
/// not — and the pool guarantees the two things the feed depends on:
///
///   * exactly one player is ever playing,
///   * a player more than one page away is torn down, not merely paused.
@MainActor
final class ShortPlayerPool: ObservableObject {

    /// Current, next, previous. Three is what paging actually needs: one
    /// playing, one warmed for the swipe the user is most likely to make, and
    /// one still holding the page they can flick back to.
    static let capacity = 3

    /// Sound follows the user across items and across sessions, the way it does
    /// in every video app. Muting each new item would be a new decision every
    /// swipe.
    @AppStorage("sava.shortform.muted") var isMuted: Bool = false {
        didSet { entries.values.forEach { $0.player.isMuted = isMuted } }
    }

    @Published private(set) var activeID: Int?

    private final class Entry {
        let player: AVPlayer
        var itemID: Int?
        var endObserver: NSObjectProtocol?
        var lastUsed: Date = .now

        init(player: AVPlayer) { self.player = player }
    }

    private var entries: [Int: Entry] = [:]      // bookmark id -> entry
    private var spares: [AVPlayer] = []
    private var audioSessionReady = false

    // MARK: Lifecycle

    /// Bring the pool in line with where the feed is, in one pass.
    ///
    /// This is the only thing that creates or destroys players, and it is
    /// called from one place. The earlier version created them lazily from
    /// inside a page's `body`, which looked tidy and was wrong twice over:
    /// SwiftUI evaluates a body whenever it likes, so players were torn down
    /// and rebuilt on unrelated redraws, and `activate` could run before the
    /// player it was meant to start existed — so nothing played at all.
    ///
    /// - Parameters:
    ///   - retain: ids worth keeping. Anything else is released now.
    ///   - ensure: ids that must have a player, with the URL to load.
    ///   - active: the one id that should be playing, if any.
    func reconcile(retain: Set<Int>, ensure: [(id: Int, url: URL)], active: Int?) {
        for key in entries.keys where !retain.contains(key) {
            release(key)
        }
        for request in ensure where entries[request.id] == nil {
            make(request.id, url: request.url)
        }
        if let active {
            activate(active)
        } else {
            pauseAll()
            activeID = nil
        }
        objectWillChange.send()
    }

    /// The player for this item, if the pool is holding one. Pure — a view may
    /// call it as often as SwiftUI decides to re-evaluate.
    func existingPlayer(for id: Int) -> AVPlayer? { entries[id]?.player }

    private func make(_ id: Int, url: URL) {
        prepareAudioSession()
        evictIfNeeded(keeping: id)

        let player = spares.popLast() ?? makePlayer()
        let entry = Entry(player: player)
        entry.itemID = id
        entries[id] = entry

        #if DEBUG
        NSLog("[sava pool] create id=%d live=%d spares=%d", id, entries.count, spares.count)
        #endif

        let asset = AVURLAsset(url: url)
        let item = AVPlayerItem(asset: asset)
        // Short-form is watched start to finish, so a large read-ahead buys
        // nothing and costs proxied bytes for a swipe that may never happen.
        item.preferredForwardBufferDuration = 6
        player.replaceCurrentItem(with: item)
        player.isMuted = isMuted
        player.actionAtItemEnd = .none

        // Short-form loops. Seeking beats re-creating the item — the buffer
        // survives, so the second pass starts instantly.
        entry.endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main
        ) { [weak self, weak player] _ in
            guard let self, let player else { return }
            player.seek(to: .zero) { _ in
                // Only the page the user is on loops. A neighbour that reached
                // its end while buffering must stay stopped.
                //
                // The seek completion runs off the main actor, so `activeID` is
                // hopped onto it rather than read across the isolation
                // boundary. Both values are bound to constants before the hop:
                // capturing the optionals directly would capture the *vars*,
                // which is an error under the Swift 6 language mode.
                Task { @MainActor [weak self, weak player] in
                    guard let self, let player, self.activeID == id else { return }
                    player.play()
                }
            }
        }
    }

    /// Make exactly this item the playing one.
    func activate(_ id: Int) {
        activeID = id
        #if DEBUG
        NSLog("[sava pool] activate id=%d live=%d", id, entries.count)
        #endif
        for (key, entry) in entries {
            if key == id {
                entry.lastUsed = .now
                entry.player.isMuted = isMuted
                if entry.player.rate == 0 { entry.player.play() }
            } else if entry.player.rate != 0 {
                entry.player.pause()
            }
        }
    }

    func pauseAll() {
        entries.values.forEach { $0.player.pause() }
    }

    #if DEBUG
    /// "how many players are actually running right now" — the invariant the
    /// whole class exists to keep at one.
    var debugPlayingCount: Int { entries.values.filter { $0.player.rate > 0 }.count }
    var debugLiveCount: Int { entries.count }
    #endif

    /// Stop every player and record which page is current anyway.
    ///
    /// Used for embed and gallery pages: there is no `AVPlayer` to activate,
    /// but the pool still has to know where the feed is so its retention window
    /// stays centred on the right item.
    func markActiveWithoutPlaying(_ id: Int) {
        pauseAll()
        activeID = id
    }

    func toggleMute() {
        isMuted.toggle()
    }

    func isPlaying(_ id: Int) -> Bool {
        (entries[id]?.player.rate ?? 0) > 0
    }

    func togglePlayback(_ id: Int) {
        guard let entry = entries[id] else { return }
        if entry.player.rate > 0 { entry.player.pause() } else { entry.player.play() }
        objectWillChange.send()
    }

    /// Tear the whole pool down. The viewer calls this on dismiss — without it
    /// audio keeps playing behind the library, which is the single most
    /// noticeable bug this class exists to prevent.
    func teardown() {
        for key in entries.keys { release(key) }
        spares.removeAll()
        activeID = nil
        deactivateAudioSession()
    }

    // MARK: Internals

    private func makePlayer() -> AVPlayer {
        let player = AVPlayer()
        player.automaticallyWaitsToMinimizeStalling = true
        return player
    }

    private func release(_ id: Int) {
        guard let entry = entries.removeValue(forKey: id) else { return }
        #if DEBUG
        NSLog("[sava pool] release id=%d live=%d", id, entries.count)
        #endif
        if let observer = entry.endObserver {
            NotificationCenter.default.removeObserver(observer)
        }
        entry.player.pause()
        entry.player.replaceCurrentItem(with: nil)
        // The player object goes back in the pool; only its item is discarded.
        if spares.count < Self.capacity { spares.append(entry.player) }
    }

    private func evictIfNeeded(keeping id: Int) {
        guard entries.count >= Self.capacity else { return }
        let victim = entries
            .filter { $0.key != id && $0.key != activeID }
            .min { $0.value.lastUsed < $1.value.lastUsed }?.key
        if let victim { release(victim) }
    }

    private func prepareAudioSession() {
        guard !audioSessionReady else { return }
        audioSessionReady = true
        // `.playback` so the ring/silent switch does not silence a video the
        // user deliberately opened — the same behaviour as every video app.
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .moviePlayback)
        try? AVAudioSession.sharedInstance().setActive(true)
    }

    private func deactivateAudioSession() {
        guard audioSessionReady else { return }
        audioSessionReady = false
        try? AVAudioSession.sharedInstance().setActive(
            false, options: .notifyOthersOnDeactivation)
    }
}

/// `AVPlayerLayer` in SwiftUI, sized to fill without stretching.
///
/// `VideoPlayer` is not usable here: it draws Apple's transport controls, which
/// cannot be removed, and they are the exact chrome a full-bleed swipe feed must
/// not have.
struct PlayerSurface: UIViewRepresentable {
    let player: AVPlayer
    /// `.resizeAspect` letterboxes, `.resizeAspectFill` crops. Vertical video
    /// fills; anything else is shown whole.
    let gravity: AVLayerVideoGravity

    func makeUIView(context: Context) -> PlayerHostView {
        let view = PlayerHostView()
        view.playerLayer.player = player
        view.playerLayer.videoGravity = gravity
        return view
    }

    func updateUIView(_ view: PlayerHostView, context: Context) {
        if view.playerLayer.player !== player { view.playerLayer.player = player }
        if view.playerLayer.videoGravity != gravity { view.playerLayer.videoGravity = gravity }
    }

    final class PlayerHostView: UIView {
        override static var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }

        override init(frame: CGRect) {
            super.init(frame: frame)
            backgroundColor = .black
            isUserInteractionEnabled = false
        }

        @available(*, unavailable)
        required init?(coder: NSCoder) { fatalError() }
    }
}
