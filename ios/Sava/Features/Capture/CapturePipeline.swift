import Foundation

/// Decides *how* to capture what the user is looking at, then saves it.
///
/// The Shortcut assigned to the Action Button gathers foreground evidence
/// ("Get What's On Screen" → "Get URLs" → count) and passes it in. This decides
/// what to do with it:
///
///   1. DIRECT URL         — the Shortcut found a supported content URL on
///                           screen. Always wins. YouTube and TikTok.
///   2. CLIPBOARD LINK     — a validated supported URL the user copied. This is
///                           the Instagram journey, not an emergency: Instagram
///                           does not expose the post URL on screen, so Copy
///                           Link is the only path to real identity.
///   3. SCREENSHOT RESOLVE — last, and only when no URL exists anywhere. It can
///                           support enrichment but never establishes identity.
///   4. HONEST FAILURE     — describes a Shortcut misconfiguration; never tells
///                           the user to go and copy a link.
///
/// Platform notes for the screenshot branch: YouTube prints its title on screen
/// and that title is searchable, so resolution genuinely works. TikTok and
/// Instagram expose no public search that maps a handle/caption to an exact
/// video id, so the server reports honestly rather than guessing. In the
/// intended flow those platforms never reach the screenshot branch.
struct CapturePipeline {
    let bookmarks: BookmarkService
    let resolver: ContentResolverService

    enum Path: String {
        case directURL = "direct_url"
        case screenshotResolution = "screenshot_resolution"
        case clipboardFallback = "clipboard_fallback"
        case failed
    }

    struct Outcome {
        let link: CapturedLink?
        let path: Path
        let resolverReason: String?
        let resolverConfidence: Double?
        let failureMessage: String?
        /// Set when the server already persisted a partial capture.
        var partialSave: String? = nil
    }

    /// Work out what to save, without saving. Pure decision logic.
    func resolve(_ input: CaptureInput, clipboard: String?) async -> Outcome {
        // ── 1. Direct URL. Highest priority; never take a screenshot for this.
        if let link = CapturedLink(rawURL: input.providedURL, source: .direct) {
            return Outcome(link: link, path: .directURL, resolverReason: nil,
                           resolverConfidence: nil, failureMessage: nil)
        }

        // ── 2. A validated clipboard link.
        //
        // Ahead of the screenshot, deliberately. This is the Instagram contract:
        // the user copied the link, so the clipboard holds *authoritative*
        // identity, while a screenshot can only ever support a guess. Running
        // the resolver first would spend a vision call to produce something
        // weaker than what is already in hand.
        if let link = CapturedLink(rawURL: clipboard, source: .clipboard) {
            return Outcome(link: link, path: .clipboardFallback, resolverReason: nil,
                           resolverConfidence: nil, failureMessage: nil)
        }

        // ── 3. Screenshot resolution, only when no URL is available anywhere.
        var resolverReason: String?
        var resolverRead: String?
        if let screenshot = input.screenshot, !screenshot.isEmpty {
            switch await resolver.resolve(screenshot: screenshot,
                                          platformHint: input.platformHint) {
            case .resolved(let url, let confidence):
                if let link = CapturedLink(rawURL: url, source: .resolved) {
                    return Outcome(link: link, path: .screenshotResolution,
                                   resolverReason: "matched", resolverConfidence: confidence,
                                   failureMessage: nil)
                }
            case .partiallySaved(let what):
                return Outcome(link: nil, path: .screenshotResolution,
                               resolverReason: "partial_capture",
                               resolverConfidence: nil,
                               failureMessage: nil, partialSave: what)
            case .notConfident(let reason, let read):
                resolverReason = reason
                resolverRead = read
            case .unavailable(let reason):
                resolverReason = reason
            }
        }

        // ── 4. Honest, platform-specific failure.
        return Outcome(link: nil, path: .failed, resolverReason: resolverReason,
                       resolverConfidence: nil,
                       failureMessage: message(for: input, reason: resolverReason,
                                               read: resolverRead))
    }

    /// What happened to the save.
    enum SaveResult {
        case saved(Bookmark)
        /// Saved server-side from screenshot evidence, without a canonical URL.
        case partiallySaved(String)
        /// Already in the library. From the user's point of view pressing the
        /// button on something they already saved succeeded — the content is
        /// there — so this is NOT an error.
        case alreadySaved
    }

    /// Resolve, save, and return both the result and how we got there.
    func run(_ input: CaptureInput, clipboard: String?) async throws -> (SaveResult, Outcome) {
        guard SavaClient.hasStoredToken else { throw CaptureError.notSignedIn }

        let outcome = await resolve(input, clipboard: clipboard)
        if let what = outcome.partialSave {
            return (.partiallySaved(what), outcome)
        }
        guard let link = outcome.link else {
            throw CaptureError.noContent(outcome.failureMessage
                ?? "Couldn't tell what you're watching.", outcome)
        }
        do {
            let bookmark = try await bookmarks.create(url: link.url)
            return (.saved(bookmark), outcome)
        } catch APIError.conflict {
            return (.alreadySaved, outcome)
        } catch let error as APIError {
            throw CaptureError.saveFailed(error.userMessage, outcome)
        }
    }

    // MARK: Messaging

    /// An honest message for the rare case where the Shortcut supplied nothing
    /// usable. The normal journey never reaches here, so these must describe a
    /// Shortcut misconfiguration rather than blame the user for not copying.
    private func message(for input: CaptureInput, reason: String?, read: String?) -> String {
        let sawNothing = input.candidateURLs.isEmpty && (input.screenshot?.isEmpty ?? true)
        if sawNothing {
            return "Sava didn't receive anything from the Shortcut. Check that it runs “Get What's On Screen” and passes the result to Save to Sava."
        }
        if let read, !read.isEmpty {
            return "Sava saw “\(read)” but couldn't match it exactly. Try again in a moment."
        }
        switch reason {
        case "screen_not_readable":
            return "Couldn't read what's on screen. Make sure the title or caption is visible, then press again."
        case "no_search_available_for_platform":
            return "Couldn't identify that from a screenshot. Try again once the link is on screen."
        case "ambiguous_match", "low_confidence":
            return "Sava wasn't confident enough to save the right video. Try again."
        default:
            return "Couldn't identify what's on screen. Try again."
        }
    }
}
