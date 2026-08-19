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

    var canSave: Bool {
        guard case .editing = phase else { return false }
        return CapturedLink(rawURL: urlText, source: .direct) != nil
    }

    var detectedPlatform: Platform? {
        CapturedLink(rawURL: urlText, source: .direct)?.platform
    }

    /// Prefill from the clipboard if it holds a supported URL.
    func prefillFromClipboardIfEmpty() {
        guard urlText.isEmpty else { return }
        let candidate = UIPasteboard.general.hasURLs
            ? UIPasteboard.general.url?.absoluteString
            : UIPasteboard.general.string
        if let candidate, CapturedLink(rawURL: candidate, source: .clipboard) != nil {
            urlText = candidate
        }
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
