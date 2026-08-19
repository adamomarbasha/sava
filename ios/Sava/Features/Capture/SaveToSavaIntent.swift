import AppIntents
import UIKit

/// The "Save to Sava" App Intent — the native entry point for one-press capture
/// from the Action Button (or Shortcuts, Spotlight, Siri).
///
/// Runs WITHOUT opening the app. A Shortcut can pass a `link` (e.g. TikTok's
/// on-screen URL) and/or a `screenshot`; the pipeline picks the right strategy:
/// direct URL first, screenshot resolution next (YouTube/Instagram), clipboard
/// as a fallback. On success the user gets a short confirmation and keeps
/// scrolling — Sava ingests and analyzes in the background.
struct SaveToSavaIntent: AppIntent {
    static var title: LocalizedStringResource = "Save to Sava"
    static var description = IntentDescription(
        "Save what you're watching to your Sava library. Pass a link when the app exposes one; otherwise attach a screenshot for Sava to resolve."
    )

    /// Keep it background — the whole point is not to interrupt the user.
    static var openAppWhenRun: Bool = false

    @Parameter(title: "Link", description: "A direct URL to the content, if available.")
    var link: URL?

    @Parameter(title: "Screenshot", description: "Used only when a direct link isn't available.")
    var screenshot: IntentFile?

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard SavaClient.hasStoredToken else {
            return .result(dialog: "Open Sava and sign in to start saving.")
        }

        let (client, _) = SavaClient.authenticated()
        let pipeline = CapturePipeline(
            bookmarks: BookmarkService(client: client),
            resolver: ContentResolverService(client: client)
        )

        let input = CaptureInput(providedURL: link?.absoluteString, screenshot: screenshot?.data)

        // Clipboard fallback — only inspected if no direct URL / resolution.
        let clipboard = UIPasteboard.general.hasURLs
            ? UIPasteboard.general.url?.absoluteString
            : UIPasteboard.general.string

        do {
            let bookmark = try await pipeline.run(input, clipboard: clipboard)
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            let what = bookmark.displayAuthor.map { "from \($0)" } ?? "to your library"
            return .result(dialog: "Saved \(what) 🔖")
        } catch let error as CaptureError {
            UINotificationFeedbackGenerator().notificationOccurred(.warning)
            return .result(dialog: IntentDialog(stringLiteral: error.errorDescription ?? "Couldn't save that."))
        }
    }
}
