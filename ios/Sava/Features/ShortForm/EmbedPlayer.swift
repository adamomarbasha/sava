import SwiftUI
import WebKit

/// YouTube's own player, in a web view.
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

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        // Without this the system hands the video to the fullscreen player,
        // which takes over the screen and makes the swipe feed unreachable.
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        config.allowsPictureInPictureMediaPlayback = false

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.isOpaque = false
        webView.backgroundColor = .black
        webView.scrollView.backgroundColor = .black
        // The page must never scroll or bounce — the vertical gesture belongs
        // to the feed, and a web view that eats it breaks paging entirely.
        webView.scrollView.isScrollEnabled = false
        webView.scrollView.bounces = false
        webView.navigationDelegate = context.coordinator
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard context.coordinator.isLoaded else {
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
        webView.loadHTMLString("", baseURL: nil)
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var isLoaded = false
        var pendingActive = false
        var pendingMuted = false

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            isLoaded = true
            webView.evaluateJavaScript(pendingMuted ? "savaMute()" : "savaUnmute()")
            webView.evaluateJavaScript(pendingActive ? "savaPlay()" : "savaPause()")
        }
    }

}
