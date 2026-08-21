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
        guard SavaClient.hasStoredToken else {
            return finish(&trace, started, path: "failed", outcome: "failed",
                          message: "Open Sava and sign in to start saving.")
        }

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
        "Saves a link to your Sava library. Pass the URLs from “Get What's On Screen”."
    )

    /// Background — never opens the app, never blocks the user.
    static var openAppWhenRun: Bool = false

    @Parameter(title: "Link")
    var link: [URL]

    static var parameterSummary: some ParameterSummary {
        Summary("Save \(\.$link) to Sava")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let candidates = link.map(\.absoluteString)
        let message = await CaptureRunner.run(
            candidates: candidates,
            screenshot: nil,
            inputTypes: ["Link[\(candidates.count)]"]
        )
        return .result(dialog: IntentDialog(stringLiteral: message))
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
