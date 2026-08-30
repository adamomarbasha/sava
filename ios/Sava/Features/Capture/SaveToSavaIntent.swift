import AppIntents
import UIKit

// Two purpose-built intents rather than one action with four optional fields.
// Each Shortcut branch has exactly one thing to supply, so each action shows
// exactly one field and needs no configuration sheet.
//
//   If  Count > 0   ->  Save Link to Sava        (Link)
//   Otherwise       ->  Save Screenshot to Sava  (Screenshot)
//
// Both run in the background: `openAppWhenRun = false`, no confirmation step,
// and the only UI is a result dialog the user can silence with
// "Show When Run = Off".

// MARK: - Shared execution

@MainActor
private enum CaptureRunner {

    /// Runs the pipeline and produces the dialog string. Shared so both
    /// intents behave identically once evidence is in hand.
    static func run(candidates: [String], screenshot: Data?,
                    inputTypes: [String]) async -> String {
        let started = Date()
        var trace = CaptureTrace()
        trace.hadShortcutInput = !candidates.isEmpty || !(screenshot?.isEmpty ?? true)
        trace.inputTypes = inputTypes
        trace.onScreenURLCount = candidates.count
        trace.onScreenURLs = Array(candidates.prefix(8))
        trace.screenshotBytes = screenshot?.count ?? 0
        trace.screenshotTaken = !(screenshot?.isEmpty ?? true)

        trace.authPresent = SavaClient.hasStoredToken
        let selected = URLSelector.best(from: candidates)
        trace.selectedURL = selected
        let hint = selected.map { PlatformDetector.detect($0) }
        trace.detectedPlatform = (hint ?? .other).rawValue

        // Clipboard: the Instagram journey, and only when the on-screen path
        // did not already produce a *supported content* URL.
        //
        // Instagram does not put the post URL on screen, so "Get What's On
        // Screen" comes back with a profile link, an app-store link, or nothing
        // — which is why the previous condition (no URL *and* no screenshot)
        // never fired for Instagram: a screenshot had been taken, so the
        // clipboard was skipped and the save went down the screenshot path
        // that cannot establish identity. Whether a screenshot exists is
        // irrelevant to whether the clipboard holds the real link.
        //
        // Whatever is read is validated before use. A clipboard entry has no
        // provenance: it may be hours old and about something else entirely, so
        // anything that is not unmistakably a supported post is discarded.
        var clipboard: String?
        if !SupportedContentURL.isContent(selected) {
            trace.clipboardChecked = true
            var raw: String?
            if UIPasteboard.general.hasURLs, let u = UIPasteboard.general.url {
                raw = u.absoluteString
                trace.clipboardType = "URL"
            } else if UIPasteboard.general.hasStrings, let s = UIPasteboard.general.string {
                raw = s
                trace.clipboardType = "String"
            }
            clipboard = SupportedContentURL.fromClipboard(raw)
            trace.clipboardValue = (clipboard ?? raw).map { String($0.prefix(120)) }
            trace.clipboardAccepted = clipboard != nil
        }

        // ── No usable session ────────────────────────────────────────────
        //
        // Reached when the token is missing or expired. If we have a link, the
        // save is NOT lost: it goes into the same App Group queue the share
        // extension writes to, and the app drains it on next foreground. That
        // queue already exists, is already drained, and is already crash-safe —
        // building a second handoff for intents would be a parallel system with
        // its own bugs.
        //
        // Deliberately checked *after* URL selection, so we know whether there
        // is anything worth keeping. With no link there is nothing to defer and
        // the honest answer is the failure below.
        if !SavaClient.hasStoredToken {
            let link = selected ?? clipboard
            if let link, let deferred = Self.deferSave(link) {
                trace.resolvedURL = deferred
                return finish(&trace, started, path: "deferred", outcome: "deferred",
                              message: "Open Sava to finish saving")
            }
            return finish(&trace, started, path: "failed", outcome: "failed",
                          message: "Open Sava and sign in to start saving.")
        }

        let (client, _) = SavaClient.authenticated()
        let pipeline = CapturePipeline(
            bookmarks: BookmarkService(client: client),
            resolver: ContentResolverService(client: client)
        )
        let input = CaptureInput(
            candidateURLs: candidates,
            providedURL: selected,
            screenshot: screenshot,
            platformHint: hint,
            inputTypes: inputTypes
        )

        do {
            let (result, outcome) = try await pipeline.run(input, clipboard: clipboard)
            trace.resolvedURL = outcome.link?.url
            trace.resolverReason = outcome.resolverReason
            trace.resolverConfidence = outcome.resolverConfidence
            trace.detectedPlatform = (outcome.link?.platform ?? hint ?? .other).rawValue
            UINotificationFeedbackGenerator().notificationOccurred(.success)

            switch result {
            case .saved(let bookmark):
                trace.saveID = bookmark.id
                let what = bookmark.displayCreator.map { "from \($0)" } ?? "to your library"
                return finish(&trace, started, path: outcome.path.rawValue,
                              outcome: "saved", message: "Saved \(what) 🔖")
            case .partiallySaved(let what):
                return finish(&trace, started, path: outcome.path.rawValue,
                              outcome: "saved",
                              message: "Saved \(what) 🔖 (Sava couldn't read the exact link)")
            case .alreadySaved:
                return finish(&trace, started, path: outcome.path.rawValue,
                              outcome: "duplicate", message: "Already in your library ✓")
            }
        } catch let error as CaptureError {
            // A link we could not deliver *right now* is not a lost link.
            //
            // Three causes, one answer: the session expired (401), the phone is
            // offline, or the request timed out. In all three the URL is good
            // and only delivery failed, so it is queued and the user is told the
            // app will finish the job. Anything else — an unsupported URL, a
            // page that is not a post — is a real failure and is reported.
            if case .saveFailed(_, let outcome) = error,
               Self.isDeliveryFailure(error),
               let link = outcome?.link?.url ?? selected ?? clipboard,
               let deferred = Self.deferSave(link) {
                trace.resolvedURL = deferred
                return finish(&trace, started, path: "deferred", outcome: "deferred",
                              message: "Open Sava to finish saving")
            }

            UINotificationFeedbackGenerator().notificationOccurred(.warning)
            let message = error.errorDescription ?? "Couldn't save that."
            trace.resolverReason = error.outcome?.resolverReason
            trace.resolverConfidence = error.outcome?.resolverConfidence
            return finish(&trace, started, path: error.outcome?.path.rawValue ?? "failed",
                          outcome: "failed", message: message)
        } catch {
            UINotificationFeedbackGenerator().notificationOccurred(.warning)
            return finish(&trace, started, path: "failed", outcome: "failed",
                          message: "Couldn't save that. Try again in a moment.")
        }
    }

    /// Hand a link to the shared pending-save queue.
    ///
    /// Returns the URL when it was accepted, nil when the App Group is not
    /// provisioned — in which case the caller must report a real failure rather
    /// than promise a save that nothing will ever pick up.
    static func deferSave(_ url: String) -> String? {
        guard PendingSaveQueue.isAvailable else { return nil }
        guard PendingSaveQueue.append(PendingSave(url: url)) else { return nil }
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        return url
    }

    /// True when the save failed for a reason that says nothing about the link.
    private static func isDeliveryFailure(_ error: CaptureError) -> Bool {
        guard case .saveFailed(let message, _) = error else { return false }
        // `CapturePipeline` has already turned the `APIError` into user-facing
        // text, so the classification is made from the message. Matching on the
        // strings `APIError.userMessage` produces keeps this in one place
        // instead of threading a second error type through the pipeline.
        let lowered = message.lowercased()
        return lowered.contains("sign in")
            || lowered.contains("session")
            || lowered.contains("offline")
            || lowered.contains("internet")
            || lowered.contains("connection")
            || lowered.contains("timed out")
            || lowered.contains("took too long")
    }

    private static func finish(_ trace: inout CaptureTrace, _ started: Date,
                               path: String, outcome: String, message: String) -> String {
        trace.path = path
        trace.outcome = outcome
        trace.message = message
        trace.durationMs = Int(Date().timeIntervalSince(started) * 1000)
        CaptureDiagnostics.record(trace)
        return message
    }
}

// MARK: - Finding links in whatever Shortcuts hands over

/// Pulls the links out of text.
///
/// Shared by both link-taking intents because both receive text far more often
/// than a typed URL. Shortcuts coerces almost everything to text on its way
/// into an action, and the official Shortcut's clipboard branch passes the
/// clipboard's *contents* — a caption, a message, whatever was last copied —
/// not a tidy URL.
enum LinkText {

    /// Every http(s) link in `raw`, in the order they appear.
    ///
    /// `NSDataDetector`, not a regex. It handles the cases a regex gets wrong —
    /// trailing punctuation, URLs inside parentheses, unicode hosts,
    /// scheme-less `www.` links — and it is the same detector Messages and Mail
    /// use, so "what counts as a link" matches what the user sees highlighted
    /// everywhere else on their phone.
    static func links(in raw: String) -> [String] {
        var found: [String] = []
        if let detector = try? NSDataDetector(
            types: NSTextCheckingResult.CheckingType.link.rawValue) {
            let range = NSRange(raw.startIndex..<raw.endIndex, in: raw)
            for match in detector.matches(in: raw, options: [], range: range) {
                guard let url = match.url else { continue }
                let scheme = url.scheme?.lowercased()
                guard scheme == "http" || scheme == "https" else { continue }
                found.append(url.absoluteString)
            }
        }

        if found.isEmpty, CapturedLink(rawURL: raw, source: .direct) != nil {
            // The whole string might still be a bare URL the detector declined.
            return [raw]
        }
        return found
    }
}

// MARK: - Branch 1: a URL was found on screen

/// `Save Link to Sava` — the action for the `Count > 0` branch.
///
/// `Link` accepts the URLs variable from "Get URLs" directly, whether that is
/// one URL or several. Sava ranks them and picks the actual content URL, which
/// is why the Shortcut needs no "Get Item from List" step: "Get What's On
/// Screen" routinely returns the video *and* the creator profile *and* a sound
/// page, and taking the first item often picks the profile.
struct SaveLinkToSavaIntent: AppIntent {
    static var title: LocalizedStringResource = "Save Link to Sava"
    static var description = IntentDescription(
        "Saves a link to your Sava library. Pass the URLs from “Get What's On Screen”, or any text containing a link."
    )

    /// Background — never opens the app, never blocks the user.
    static var openAppWhenRun: Bool = false

    /// `[String]`, not `[URL]`, and that is the whole robustness story.
    ///
    /// A `[URL]` parameter looks tidier and then refuses real input. The
    /// official Shortcut's clipboard branch hands this action the **Clipboard**
    /// output directly — a caption with a link in it, a message, whatever was
    /// last copied — and Shortcuts must coerce that to the declared type before
    /// the action ever runs. Coercing "Check this out 😂 https://vm.tiktok.com/x/"
    /// to a URL is Shortcuts' judgement call, and when it declines, the user
    /// gets a coercion error instead of a save. Text always coerces, so the
    /// judgement moves here, where `NSDataDetector` makes it and the failure
    /// modes are ours to handle.
    ///
    /// URL variables still work unchanged — the on-screen branch passes a list
    /// of URLs and they arrive as their absolute strings.
    @Parameter(title: "Link")
    var link: [String]

    static var parameterSummary: some ParameterSummary {
        Summary("Save \(\.$link) to Sava")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let candidates = Self.candidates(from: link)
        let message = await CaptureRunner.run(
            candidates: candidates,
            screenshot: nil,
            inputTypes: ["Link[\(link.count)]→URL[\(candidates.count)]"]
        )
        return .result(dialog: IntentDialog(stringLiteral: message))
    }

    /// Every link in every entry, de-duplicated, order preserved.
    ///
    /// Returning all of them rather than the first matches the rest of the
    /// capture path: `URLSelector` ranks candidates and picks the actual
    /// content URL, so a screen carrying both a profile link and a video link
    /// resolves to the video.
    static func candidates(from entries: [String]) -> [String] {
        var seen = Set<String>()
        var out: [String] = []
        for entry in entries {
            let trimmed = entry.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else { continue }
            for link in LinkText.links(in: trimmed) where seen.insert(link).inserted {
                out.append(link)
            }
        }
        return out
    }
}

// MARK: - Branch 2: nothing on screen, use a screenshot

/// `Save Screenshot to Sava` — the action for the Otherwise branch.
///
/// The screenshot goes to the server, which reads the on-screen text and
/// matches it to real content. Bytes stay in memory and are never written to
/// Photos or to disk.
struct SaveScreenshotToSavaIntent: AppIntent {
    static var title: LocalizedStringResource = "Save Screenshot to Sava"
    static var description = IntentDescription(
        "Identifies what's in a screenshot and saves it to your Sava library."
    )

    static var openAppWhenRun: Bool = false

    @Parameter(title: "Screenshot", supportedTypeIdentifiers: ["public.image"])
    var screenshot: IntentFile

    static var parameterSummary: some ParameterSummary {
        Summary("Save \(\.$screenshot) to Sava")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        #if DEBUG
        NSLog("[Sava screenshot] parameter received: filename=%@ type=%@ hasURL=%@",
              screenshot.filename,
              screenshot.type?.identifier ?? "unknown",
              screenshot.fileURL != nil ? "yes" : "no")
        #endif

        // Materialise into Sava-owned bytes and prove they decode, before any
        // upload is attempted.
        let payload: Data
        do {
            let materialised = try ScreenshotTransport.materialize(screenshot)
            #if DEBUG
            NSLog("[Sava screenshot] readable=yes source=%@ bytes=%d pixels=%.0fx%.0f",
                  materialised.source, materialised.byteCount,
                  materialised.pixelSize.width, materialised.pixelSize.height)
            #endif
            payload = materialised.data
        } catch {
            #if DEBUG
            NSLog("[Sava screenshot] readable=NO error=%@", error.localizedDescription)
            #endif
            var trace = CaptureTrace()
            trace.hadShortcutInput = true
            trace.inputTypes = ["Screenshot"]
            trace.screenshotTaken = true
            trace.path = "failed"
            trace.outcome = "failed"
            trace.message = error.localizedDescription
            CaptureDiagnostics.record(trace)
            UINotificationFeedbackGenerator().notificationOccurred(.warning)
            return .result(dialog: IntentDialog(
                stringLiteral: error.localizedDescription))
        }

        let message = await CaptureRunner.run(
            candidates: [],
            screenshot: payload,
            inputTypes: ["Screenshot"]
        )
        return .result(dialog: IntentDialog(stringLiteral: message))
    }
}

// MARK: - The general-purpose action

/// `Save to Sava` — the one users find by searching "Sava" in Shortcuts, and
/// the one that goes on the Action Button.
///
/// Where `SaveLinkToSavaIntent` is built for one branch of the capture Shortcut
/// and takes a typed `[URL]`, this takes **whatever the user has**:
///
///   * a URL,
///   * text that happens to contain a URL (a shared caption, a message, the
///     output of almost any other Shortcut action),
///   * nothing at all — in which case it reads the clipboard.
///
/// The flexible parameter is the reason it exists. A `[URL]` parameter looks
/// tidy and then refuses half of real input: "Share Sheet" hands over text on
/// TikTok, "Get Text from Input" produces a string, and a user who pasted a
/// caption gets a type-mismatch error instead of a save. A `String` accepts all
/// of it, because Shortcuts coerces almost everything to text, and the URLs are
/// then found with the system's own data detector rather than a hand-rolled
/// regex that will disagree with it.
///
/// Runs in the background: it never opens Sava, and the only UI is a result
/// dialog the user can silence per-action with "Show When Run → Off".
struct SaveToSavaIntent: AppIntent {
    static var title: LocalizedStringResource = "Save to Sava"
    // One literal, not a concatenation: `LocalizedStringResource` is built from
    // a string *literal* so the compiler can extract it for localisation, and
    // `"a" + "b"` is a runtime String that cannot be converted.
    static var description = IntentDescription(
        "Saves a link to your Sava library. Give it a URL, some text containing one, or nothing at all to use whatever you last copied.",
        categoryName: "Saving",
        searchKeywords: ["save", "sava", "bookmark", "link", "add"])

    static var openAppWhenRun: Bool = false

    /// Optional so the action can be run with no configuration at all — which
    /// is what makes it usable as a bare Siri phrase and as a one-action
    /// Shortcut on the Action Button.
    @Parameter(title: "Link or text",
               description: "A URL, or text containing one. Leave empty to use the clipboard.")
    var input: String?

    static var parameterSummary: some ParameterSummary {
        Summary("Save \(\.$input) to Sava")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let (candidates, types) = Self.candidates(from: input)
        let message = await CaptureRunner.run(
            candidates: candidates, screenshot: nil, inputTypes: types)
        return .result(dialog: IntentDialog(stringLiteral: message))
    }

    /// Everything that looks like a link in `raw`, plus a description of what
    /// arrived (for the DEBUG capture trace).
    ///
    /// Returning *all* of them rather than the first is deliberate and matches
    /// the rest of the capture path: `URLSelector` ranks candidates and picks
    /// the actual content URL, so a shared caption carrying both a profile link
    /// and a video link resolves to the video.
    static func candidates(from raw: String?) -> ([String], [String]) {
        guard let raw = raw?.trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty else {
            // No input. `CaptureRunner` falls through to the clipboard, which is
            // the documented behaviour of this action when left empty.
            return ([], ["Empty→Clipboard"])
        }

        let found = LinkText.links(in: raw)
        return (found, ["Text→URL[\(found.count)]"])
    }
}
