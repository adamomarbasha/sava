import SwiftUI
import WebKit

/// What one embedded page is doing right now.
///
/// ── Why this exists ─────────────────────────────────────────────────────
///
/// The embed path used to have no states at all. A `WKWebView` with a black
/// background was put on screen the instant a descriptor said `.embed`, and the
/// only delegate callback implemented was `didFinish` — so a page that failed to
/// load, or loaded but whose iframe never painted, produced a full-screen black
/// rectangle with nothing behind it, no explanation and no way out. That is the
/// reported "giant unexplained black area", and it was invisible in code review
/// because nothing threw and nothing logged.
///
/// The `AVPlayer` path already did the right thing — hold the poster underneath
/// until the first frame arrives — so this brings the embed path up to the same
/// contract, with the extra states a web view needs.
enum EmbedPhase: Equatable {
    /// The web view has not been created yet.
    case preparing
    /// The page is loading. The poster is showing.
    case loadingPlayback
    /// The player reported `onReady`. Safe to take the poster down.
    case ready
    /// The provider says this cannot be embedded — removed, private,
    /// age-gated, or embedding disabled. Not retryable.
    case unavailable(String)
    /// The load failed for a reason that might not recur: no network, a
    /// timeout, a transient server error. Retryable.
    case failed(String)
    /// A retry is in flight.
    case retrying

    var showsPoster: Bool {
        switch self {
        case .ready: return false
        default: return true
        }
    }

    var isTerminalFailure: Bool {
        switch self {
        case .unavailable, .failed: return true
        default: return false
        }
    }
}

/// Observable state for one embedded item, owned by the page and driven by the
/// web view's coordinator.
///
/// Separate from the `UIViewRepresentable` because SwiftUI recreates that struct
/// constantly; state living inside it would reset on every parent redraw and the
/// poster would flicker back over a playing video.
@MainActor
final class EmbedState: ObservableObject {
    @Published private(set) var phase: EmbedPhase = .preparing
    /// Bumped to force a fresh web view on retry.
    @Published private(set) var attempt = 0

    /// Instrumentation. Not user-facing.
    private var startedAt: Date?
    private(set) var readyMilliseconds: Int?

    func begin() {
        startedAt = Date()
        phase = .loadingPlayback
    }

    /// The page reporting in.
    ///
    /// `unavailable` and `failed` are accepted even after `ready`, because
    /// YouTube fires `onReady` before it discovers the video cannot play and
    /// only then fires `onError`. Ignoring the late error is what left the
    /// user looking at YouTube's own unavailable screen.
    func report(state: String, detail: String) {
        switch state {
        case "ready":
            guard !phase.isTerminalFailure else { return }
            if let startedAt, readyMilliseconds == nil {
                readyMilliseconds = Int(Date().timeIntervalSince(startedAt) * 1000)
            }
            phase = .ready
        case "unavailable":
            phase = .unavailable(Self.describe(youTubeError: detail))
        default:
            phase = .failed(detail == "timeout"
                            ? "This took too long to load."
                            : "Couldn't load the player.")
        }
        log()
    }

    /// A navigation-level failure: the host page itself did not load.
    ///
    /// Does not override a state the *page* reported. The page knows more than
    /// the navigation layer does — a web content crash after a clean `ready`
    /// should still surface, but a stray navigation error must not overwrite
    /// "the creator disabled embedding" with "couldn't load the player".
    func fail(_ message: String) {
        guard !phase.isTerminalFailure else { return }
        phase = .failed(message)
        log()
    }

    func retry() {
        attempt += 1
        startedAt = Date()
        readyMilliseconds = nil
        phase = .retrying
    }

    /// YouTube's numeric error codes, as sentences.
    ///
    /// These are the cases that previously rendered as black. Each one is a
    /// *permanent* property of the video, so none of them offer a retry — a
    /// button that cannot work is worse than no button.
    private static func describe(youTubeError code: String) -> String {
        switch code {
        case "2":        return "This video's link isn't valid any more."
        case "5":        return "This video can't play here."
        case "100":      return "This video has been removed."
        case "101", "150": return "The creator doesn't allow this video to play outside YouTube."
        default:         return "This video can't be played here."
        }
    }

    private func log() {
        #if DEBUG
        NSLog("[Sava playback] embed phase=%@ attempt=%d readyMs=%@",
              String(describing: phase), attempt,
              readyMilliseconds.map(String.init) ?? "-")
        #endif
    }
}

/// YouTube's and Instagram's own players, in a web view.
///
/// This is not a shortcut. Extracting YouTube's media streams and re-serving
/// them is against their terms whether or not it is technically possible, so
/// Shorts play through the sanctioned IFrame player and Sava never touches the
/// bytes. The cost is that this one page type is a `WKWebView` rather than an
/// `AVPlayer` — which is why the viewer is built around a playback *descriptor*
/// instead of around a player. One viewer, two decoders.
///
/// `url` is a page served by Sava's own backend, not a `youtube.com/embed/`
/// URL. That indirection is load-bearing: a page loaded into a web view from a
/// string has no real origin, and YouTube's JS API answers a request from one
/// with "this video is unavailable". The server-hosted page has an origin it
/// can declare, so the same embed plays and stays scriptable.
///
/// The view is created only for pages inside the visible window and destroyed
/// when they leave it: a `WKWebView` is far heavier than an `AVPlayer`, and a
/// feed that accumulated them would be evicted by the system within a dozen
/// swipes.
struct EmbedPlayer: UIViewRepresentable {
    let url: URL
    /// Playback follows the page, exactly as it does for `AVPlayer` items: the
    /// page the user is on plays, every other page is stopped.
    let isActive: Bool
    let isMuted: Bool
    @ObservedObject var state: EmbedState

    func makeCoordinator() -> Coordinator { Coordinator(state: state) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        // Without this the system hands the video to the fullscreen player,
        // which takes over the screen and makes the swipe feed unreachable.
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        config.allowsPictureInPictureMediaPlayback = false
        // The page reports `ready` / `unavailable` / `failed` through this.
        // Without it a failed iframe is indistinguishable from a slow one.
        config.userContentController.add(context.coordinator, name: "sava")

        let webView = WKWebView(frame: .zero, configuration: config)
        // Transparent, not black: the poster is drawn *behind* this view and
        // must show through until the player reports ready. An opaque web view
        // is what made a slow or failed embed look like a black hole.
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        // The page must never scroll or bounce — the vertical gesture belongs
        // to the feed, and a web view that eats it breaks paging entirely.
        webView.scrollView.isScrollEnabled = false
        webView.scrollView.bounces = false
        webView.navigationDelegate = context.coordinator
        context.coordinator.load(webView, url: url)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        // A retry replaces the page in place rather than the view: recreating
        // the `WKWebView` costs a process spin-up, and the feed is already
        // close to the memory ceiling that made these views window-scoped.
        if context.coordinator.attempt != state.attempt {
            context.coordinator.attempt = state.attempt
            context.coordinator.load(webView, url: url)
            return
        }
        guard context.coordinator.isReady else {
            context.coordinator.pendingActive = isActive
            context.coordinator.pendingMuted = isMuted
            return
        }
        webView.evaluateJavaScript(isMuted ? "savaMute()" : "savaUnmute()")
        webView.evaluateJavaScript(isActive ? "savaPlay()" : "savaPause()")
    }

    static func dismantleUIView(_ webView: WKWebView, coordinator: Coordinator) {
        // Stopping playback explicitly matters: a web view released while its
        // video is running can keep the audio session alive for a beat.
        webView.evaluateJavaScript("savaPause()")
        webView.stopLoading()
        coordinator.cancelWatchdog()
        // Detach the handler or the controller retains the coordinator and the
        // whole page graph with it — a leak that only shows up after enough
        // swipes to matter.
        webView.configuration.userContentController
            .removeScriptMessageHandler(forName: "sava")
        webView.navigationDelegate = nil
        webView.loadHTMLString("", baseURL: nil)
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        let state: EmbedState
        var isReady = false
        var pendingActive = false
        var pendingMuted = false
        var attempt = 0
        private var watchdog: Task<Void, Never>?

        init(state: EmbedState) { self.state = state }

        func load(_ webView: WKWebView, url: URL) {
            isReady = false
            cancelWatchdog()
            Task { @MainActor in state.begin() }
            webView.load(URLRequest(url: url, timeoutInterval: 20))
            armWatchdog()
        }

        /// A second watchdog, on this side of the bridge.
        ///
        /// The page has its own, but it can only fire if the page ran at all.
        /// A host page that never loads, or one whose script was blocked, would
        /// otherwise leave the item on its poster indefinitely — better than
        /// black, but still a dead end with no Retry.
        private func armWatchdog() {
            watchdog = Task { @MainActor [weak self] in
                try? await Task.sleep(for: .seconds(15))
                guard let self, !Task.isCancelled, !self.isReady else { return }
                self.state.fail("This took too long to load.")
            }
        }

        func cancelWatchdog() {
            watchdog?.cancel()
            watchdog = nil
        }

        // MARK: The page speaking

        func userContentController(_ controller: WKUserContentController,
                                   didReceive message: WKScriptMessage) {
            guard let body = message.body as? [String: Any],
                  let reported = body["state"] as? String else { return }
            let detail = body["detail"] as? String ?? ""
            cancelWatchdog()
            if reported == "ready" {
                isReady = true
                if let webView = message.webView {
                    webView.evaluateJavaScript(pendingMuted ? "savaMute()" : "savaUnmute()")
                    webView.evaluateJavaScript(pendingActive ? "savaPlay()" : "savaPause()")
                }
            }
            Task { @MainActor in self.state.report(state: reported, detail: detail) }
        }

        // MARK: Navigation

        /// `didFinish` means the *host page* loaded, not that the player is up.
        /// The player's own `onReady` is the signal that matters, so this
        /// deliberately does not clear the poster.
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {}

        func webView(_ webView: WKWebView,
                     didFailProvisionalNavigation navigation: WKNavigation!,
                     withError error: Error) {
            report(error)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!,
                     withError error: Error) {
            report(error)
        }

        /// The web content process was killed — usually memory pressure after a
        /// long session in the feed. Previously this left a permanently blank
        /// view, because a terminated process renders nothing and reports
        /// nothing through any navigation callback.
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            cancelWatchdog()
            Task { @MainActor in self.state.fail("The player stopped unexpectedly.") }
        }

        private func report(_ error: Error) {
            cancelWatchdog()
            let code = (error as NSError).code
            // -999 is "cancelled", which is what a swipe away from this page
            // produces. It is not a failure and must not draw an error over an
            // item the user has already left.
            guard code != NSURLErrorCancelled else { return }
            let message = code == NSURLErrorNotConnectedToInternet
                ? "You're offline."
                : "Couldn't load the player."
            Task { @MainActor in self.state.fail(message) }
        }
    }
}
