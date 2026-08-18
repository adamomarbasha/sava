import Foundation

/// Runtime configuration for the Sava client.
///
/// The base URL points at the existing FastAPI backend (default `:8000`, matching
/// `run_api.py`). It can be overridden without a rebuild via the `SAVA_API_BASE`
/// Info.plist key or a launch environment variable — useful for device testing
/// against a LAN IP or a staging host.
enum AppConfig {
    static let apiBaseURL: URL = {
        if let raw = resolvedBaseString(), let url = URL(string: raw) {
            return url
        }
        return URL(string: "http://127.0.0.1:8000")!
    }()

    private static func resolvedBaseString() -> String? {
        if let env = ProcessInfo.processInfo.environment["SAVA_API_BASE"],
           !env.isEmpty {
            return env
        }
        if let plist = Bundle.main.object(forInfoDictionaryKey: "SAVA_API_BASE") as? String,
           !plist.isEmpty {
            return plist
        }
        return nil
    }
}
