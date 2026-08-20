import SwiftUI
import UIKit

/// Backs the in-app "Save a link" sheet. Reuses the same capture pipeline the
/// Action Button uses, so behavior stays identical across entry points.
@MainActor
final class QuickSaveViewModel: ObservableObject {
    enum Phase: Equatable {
        case editing, saving, saved(Bookmark), failed(String)
    }

    @Published var urlText = ""
    @Published private(set) var phase: Phase = .editing
    @Published private(set) var clipboardMayHoldLink = false

    var canSave: Bool {
        guard case .editing = phase else { return false }
        return CapturedLink(rawURL: urlText, source: .direct) != nil
    }

    var detectedPlatform: Platform? {
        CapturedLink(rawURL: urlText, source: .direct)?.platform
    }

    /// True when the clipboard holds something that could be a link.
    ///
    /// `hasURLs` and `hasStrings` report on the pasteboard's *contents* without
    /// reading them, so neither trips the system's "would like to paste" alert.
    /// Reading the value silently — which is what this used to do — put a
    /// permission modal in front of the user every single time they opened the
    /// save sheet, on the one flow whose entire point is to be invisible. The
    /// value itself is only ever read through a `PasteButton`, which the user
    /// taps and which never prompts.
    func refreshClipboardHint() {
        clipboardMayHoldLink = UIPasteboard.general.hasURLs
            || UIPasteboard.general.hasStrings
    }

    /// Accept a value handed over by the system paste control.
    func acceptPasted(_ values: [String]) {
        guard let candidate = values.first(where: {
            CapturedLink(rawURL: $0, source: .clipboard) != nil
        }) ?? values.first else { return }
        urlText = candidate.trimmingCharacters(in: .whitespacesAndNewlines)
        Haptics.tap()
    }

    func save(service: BookmarkService, onSaved: @escaping (Bookmark) -> Void) async {
        guard canSave else { return }
        phase = .saving
        do {
            let bookmark = try await service.create(url: urlText.trimmingCharacters(in: .whitespacesAndNewlines))
            Haptics.success()
            phase = .saved(bookmark)
            onSaved(bookmark)
        } catch {
            Haptics.error()
            phase = .failed((error as? APIError)?.userMessage ?? "Couldn't save that link.")
        }
    }

    func reset() {
        urlText = ""
        phase = .editing
    }
}
