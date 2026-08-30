import Foundation

/// Decides *how* to capture what the user is looking at, then saves it.
///
/// Sava saves **links**. There are two ways one arrives, and they are the two
/// workflows the app teaches:
///
///   1. DIRECT URL      — the share sheet, or a Shortcut that ran "Get What's
///                        On Screen". TikTok and YouTube put the post URL on
///                        screen, so this is the common path.
///   2. CLIPBOARD LINK  — a validated supported URL the user copied. This is
///                        the Action Button journey, and the only path that
///                        works on Instagram, which never exposes the post URL
///                        on screen. Copy Link, then press the button.
///   3. HONEST FAILURE  — describes what was missing. Never blames the user.
///
/// ── Why there is no screenshot branch ───────────────────────────────────
///
/// There used to be a third source: screenshot the screen, upload it, and have
/// a vision model guess which video it showed. It was removed because it could
/// not do the one thing a save has to do — establish *identity*. TikTok and
/// Instagram expose no public search mapping a caption or handle to an exact
/// post id, so the best possible outcome was a confident-looking guess at the
/// wrong video, and the honest outcome was a failure message after spending a
/// vision call. A copied link is free, instant, and exactly right.
struct CapturePipeline {
    let bookmarks: BookmarkService

    enum Path: String {
        case directURL = "direct_url"
        case clipboardFallback = "clipboard_fallback"
        case failed
    }

    struct Outcome {
        let link: CapturedLink?
        let path: Path
        let failureMessage: String?
    }

    /// Work out what to save, without saving. Pure decision logic.
    func resolve(_ input: CaptureInput, clipboard: String?) async -> Outcome {
        // ── 1. A URL that was on screen, or handed straight to the intent.
        if let link = CapturedLink(rawURL: input.providedURL, source: .direct) {
            return Outcome(link: link, path: .directURL, failureMessage: nil)
        }

        // ── 2. A validated clipboard link.
        //
        // The user copied it, so the clipboard holds *authoritative* identity.
        // Whatever is read is still validated before use: a clipboard entry has
        // no provenance — it may be hours old and about something else — so
        // anything that is not unmistakably a supported post is discarded by
        // `SupportedContentURL` before it reaches here.
        if let link = CapturedLink(rawURL: clipboard, source: .clipboard) {
            return Outcome(link: link, path: .clipboardFallback, failureMessage: nil)
        }

        return Outcome(link: nil, path: .failed,
                       failureMessage: message(for: input))
    }

    /// What happened to the save.
    enum SaveResult {
        case saved(Bookmark)
        /// Already in the library. From the user's point of view pressing the
        /// button on something they already saved succeeded — the content is
        /// there — so this is NOT an error.
        case alreadySaved
    }

    /// Resolve, save, and return both the result and how we got there.
    func run(_ input: CaptureInput, clipboard: String?) async throws -> (SaveResult, Outcome) {
        guard SavaClient.hasStoredToken else { throw CaptureError.notSignedIn }

        let outcome = await resolve(input, clipboard: clipboard)
        guard let link = outcome.link else {
            throw CaptureError.noContent(outcome.failureMessage
                ?? "Couldn't find a link to save.", outcome)
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

    /// An honest message for when nothing usable arrived.
    ///
    /// Both branches describe a *next action the user can actually take*. The
    /// second is the one that matters on Instagram, where there is no on-screen
    /// URL and copying the link is not a fallback but the intended route.
    private func message(for input: CaptureInput) -> String {
        if input.candidateURLs.isEmpty {
            return "Nothing to save yet. Copy the link to the post first, then press again."
        }
        return "Sava couldn't find a supported link there. Use Copy Link on the post, then press again."
    }
}
