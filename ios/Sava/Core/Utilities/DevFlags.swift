import Foundation

/// DEBUG-only launch instrumentation for QA/screenshotting against a real
/// account. Reads launch-environment variables and is compiled out of Release.
enum DevFlags {
    #if DEBUG
    private static let env = ProcessInfo.processInfo.environment

    /// SAVA_DEV_TAB = library | search | profile
    static var initialTab: String? { env["SAVA_DEV_TAB"] }

    /// SAVA_DEV_SEARCH = a query to run automatically on the Search tab.
    static var searchQuery: String? { env["SAVA_DEV_SEARCH"] }

    /// SAVA_DEV_OPEN_FIRST = 1 to auto-open the first library item's detail.
    static var openFirst: Bool { env["SAVA_DEV_OPEN_FIRST"] == "1" }
    #else
    static var initialTab: String? { nil }
    static var searchQuery: String? { nil }
    static var openFirst: Bool { false }
    #endif
}
