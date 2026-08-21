import Foundation
import SwiftUI

/// DEBUG-only launch instrumentation for QA and screenshotting against a real
/// account. Reads launch-environment variables and is compiled out of Release.
///
/// This exists so every screen can be reached deterministically without tapping
/// through the app, which is what makes it practical to review the whole design
/// against a real library — including the ugly parts of it — rather than against
/// hand-written previews.
enum DevFlags {
    #if DEBUG
    private static let env = ProcessInfo.processInfo.environment

    /// SAVA_DEV_TAB = library | collections | search | ask
    static var initialTab: String? { env["SAVA_DEV_TAB"] }

    /// SAVA_DEV_SEARCH = a query to run automatically on the Search tab.
    static var searchQuery: String? { env["SAVA_DEV_SEARCH"] }

    /// SAVA_DEV_SCREEN opens one screen straight away:
    ///   `detail`            first save in the library
    ///   `detail:<id>`       a specific save
    ///   `collection:<id>`   a specific collection
    ///   `profile`           the profile sheet
    ///   `save`              the quick-save sheet
    ///   `askThis`           Ask, scoped to the first item
    ///   `transcript`        the transcript sheet for a processed item
    ///   `history`           the Ask conversation history
    ///   `addTo`             the add-to-collection sheet for the first save
    ///   `shortform`         the swipe viewer on the first playable save
    ///   `shortform:<id>`    the swipe viewer opened on a specific save
    ///   `shortformIn:<id>`  the swipe viewer opened from collection <id>,
    ///                       which is how the feed's source context is tested
    static var screen: DevScreen? { DevScreen(env["SAVA_DEV_SCREEN"]) }

    /// SAVA_DEV_ASK = a question to send automatically on the Ask tab.
    static var askQuestion: String? { env["SAVA_DEV_ASK"] }

    /// SAVA_DEV_STREAM = 1 forces the first-time reveal animation, so it can be
    /// inspected without having to generate fresh content each run.
    static var forceStreamReveal: Bool { env["SAVA_DEV_STREAM"] == "1" }

    /// SAVA_DEV_SHORTFORM_ADVANCE = n — steps the swipe viewer forward n pages,
    /// a beat apart. There is no way to synthesise a drag against a simulator
    /// from the command line, so this drives the same state change a real swipe
    /// makes: it moves `scrollPosition`'s binding and lets everything else —
    /// player activation, release, prefetch — follow from it.
    static var shortFormAdvance: Int { Int(env["SAVA_DEV_SHORTFORM_ADVANCE"] ?? "") ?? 0 }

    /// SAVA_DEV_REFRESH = n — runs the Library's refresh action n times in a
    /// row. There is no way to synthesise a pull gesture against a simulator
    /// from the command line, so this exercises the half that *can* be driven:
    /// whether repeated refresh completions disturb the scroll container's
    /// resting position.
    static var refreshRuns: Int { Int(env["SAVA_DEV_REFRESH"] ?? "") ?? 0 }

    /// SAVA_DEV_SCROLL = bottom — opens long screens scrolled to the end, so the
    /// lower half of a design can be captured without driving the UI.
    static var scrollToBottom: Bool { env["SAVA_DEV_SCROLL"] == "bottom" }
    #else
    static var refreshRuns: Int { 0 }
    static var shortFormAdvance: Int { 0 }
    static var initialTab: String? { nil }
    static var searchQuery: String? { nil }
    static var screen: DevScreen? { nil }
    static var askQuestion: String? { nil }
    static var forceStreamReveal: Bool { false }
    static var scrollToBottom: Bool { false }
    #endif
}

extension View {
    /// Applies the QA scroll anchor. A no-op in Release.
    @ViewBuilder func devScrollAnchor() -> some View {
        if DevFlags.scrollToBottom {
            defaultScrollAnchor(.bottom)
        } else {
            self
        }
    }
}

enum DevScreen: Equatable {
    case detail(Int?)
    case collection(Int)
    case profile
    case save
    case askThis
    case addTo
    case transcript(Int?)
    case history
    case shortForm(Int?)
    case shortFormInCollection(Int)

    init?(_ raw: String?) {
        guard let raw, !raw.isEmpty else { return nil }
        let parts = raw.split(separator: ":", maxSplits: 1)
        let name = String(parts[0])
        let argument = parts.count > 1 ? Int(parts[1]) : nil

        switch name {
        case "detail":     self = .detail(argument)
        case "collection": guard let argument else { return nil }; self = .collection(argument)
        case "profile":    self = .profile
        case "save":       self = .save
        case "askThis":    self = .askThis
        case "addTo":      self = .addTo
        case "transcript": self = .transcript(argument)
        case "history":    self = .history
        case "shortform":  self = .shortForm(argument)
        case "shortformIn":
            guard let argument else { return nil }
            self = .shortFormInCollection(argument)
        default:           return nil
        }
    }
}
