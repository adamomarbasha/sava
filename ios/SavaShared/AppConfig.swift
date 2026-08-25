import Foundation

/// The single source of truth for where Sava's backend lives.
///
/// Nothing else in the app may construct an API origin. Every service builds
/// requests from `Endpoint`, which resolves against `AppConfig.apiBaseURL`, so
/// changing the deployment target is a one-line change in one plist rather than
/// a search for hardcoded addresses.
///
/// **How the value is chosen.** Debug and Release read different Info.plist
/// files (`Info.plist` and `Info-Release.plist`, wired per build configuration
/// in the project), so a development address is not merely discouraged in a
/// shipping build — it is not present in the bundle at all.
///
///   * **Debug** — `SAVA_API_BASE` environment variable, else the plist value,
///     else `http://localhost:8000`. The environment variable is what makes a
///     physical phone able to point at a Mac without editing tracked files.
///   * **Release** — the plist value, and only if it survives validation:
///     https, a real host, not a private or loopback address. There is no
///     fallback, because a fallback is exactly how a LAN URL ships.
///
/// A Release build whose value has not been replaced fails at *build* time via
/// the "Validate API configuration" phase, so this runtime check is the second
/// line of defence rather than the first.
enum AppConfig {

    /// The sentinel shipped in `Info-Release.plist`. Replaced at deploy time.
    static let productionPlaceholder = "REPLACE_WITH_PRODUCTION_API_URL"

    /// Why a configured origin was rejected. Surfaced to the user rather than
    /// swallowed: a build that cannot reach a backend should say so plainly
    /// instead of presenting a login screen that will always time out.
    enum ConfigurationError: Error, Equatable {
        case missing
        case placeholderNotReplaced
        case notHTTPS(String)
        case privateAddress(String)
        case malformed(String)
        case deviceNeedsLANAddress

        var message: String {
            switch self {
            case .missing:
                return "This build has no API address configured."
            case .placeholderNotReplaced:
                return "This build still has the placeholder API address."
            case .notHTTPS(let host):
                return "This build points at \(host) over an insecure connection."
            case .privateAddress(let host):
                return "This build points at \(host), which is a private address."
            case .malformed(let raw):
                return "This build has an unusable API address: \(raw)."
            case .deviceNeedsLANAddress:
                return "This is a device build with no LAN address for your Mac. "
                    + "Build again with the Mac on Wi-Fi, or set SAVA_API_BASE in the scheme."
            }
        }
    }

    /// The resolved origin, or nil when the build is misconfigured.
    private(set) static var configurationError: ConfigurationError?

    /// The API origin every request is built against.
    ///
    /// Non-optional because every call site needs *a* URL, and making them all
    /// handle nil would spread misconfiguration handling across the codebase.
    /// When configuration fails, this is a deliberately unreachable sentinel
    /// and `configurationError` explains why — `RootView` shows that instead of
    /// letting the app look merely broken.
    static let apiBaseURL: URL = {
        switch resolve() {
        case .success(let url):
            return url
        case .failure(let error):
            configurationError = error
            #if DEBUG
            // In development the useful behaviour is to keep going against a
            // sane default rather than to stop the app.
            return URL(string: "http://localhost:8000")!
            #else
            // Unreachable by construction: `.invalid` is not a routable host,
            // so every request fails fast and visibly rather than hanging.
            return URL(string: "https://sava-build-misconfigured.invalid")!
            #endif
        }
    }()

    /// True when the app is talking to a real, secure, remote backend.
    static var isProductionReady: Bool { configurationError == nil && apiBaseURL.scheme == "https" }

    // MARK: Resolution

    private static func resolve() -> Result<URL, ConfigurationError> {
        #if DEBUG
        // A scheme environment variable wins, so a device build can be pointed
        // at a laptop without editing anything that gets committed.
        if let env = ProcessInfo.processInfo.environment["SAVA_API_BASE"],
           !env.trimmingCharacters(in: .whitespaces).isEmpty {
            return validate(env, requireSecure: false)
        }

        // On a physical phone, `localhost` is the phone.
        //
        // This is the whole reason a device build could not sign in: the
        // simulator shares the Mac's loopback, so localhost is correct there
        // and completely wrong on hardware, where nothing is listening on port
        // 8000. The device needs the Mac's LAN address instead — and that
        // address is stamped in at build time by Scripts/stamp-dev-host.sh
        // rather than written down, because it is a DHCP lease that changes
        // (it moved from .227 to .75 and every build kept using the old one).
        #if !targetEnvironment(simulator)
        if let lan = Bundle.main.object(forInfoDictionaryKey: "SAVA_DEV_LAN_URL") as? String,
           !lan.trimmingCharacters(in: .whitespaces).isEmpty {
            return validate(lan, requireSecure: false)
        }
        #endif
        #endif

        guard let raw = Bundle.main.object(forInfoDictionaryKey: "SAVA_API_BASE_URL") as? String,
              !raw.trimmingCharacters(in: .whitespaces).isEmpty
        else { return .failure(.missing) }

        if raw == productionPlaceholder { return .failure(.placeholderNotReplaced) }

        #if DEBUG
        return validate(raw, requireSecure: false)
        #else
        return validate(raw, requireSecure: true)
        #endif
    }

    /// Shared validation, so the build-time script and the app agree on what a
    /// usable origin is.
    static func validate(_ raw: String, requireSecure: Bool) -> Result<URL, ConfigurationError> {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let components = URLComponents(string: trimmed),
              let scheme = components.scheme?.lowercased(),
              let host = components.host, !host.isEmpty,
              let url = components.url
        else { return .failure(.malformed(trimmed)) }

        guard scheme == "http" || scheme == "https" else {
            return .failure(.malformed(trimmed))
        }

        if requireSecure {
            guard scheme == "https" else { return .failure(.notHTTPS(host)) }
            guard !isPrivateHost(host) else { return .failure(.privateAddress(host)) }
        }

        // Trailing slashes would double up when paths are appended.
        let normalized = trimmed.hasSuffix("/") ? String(trimmed.dropLast()) : trimmed
        return .success(URL(string: normalized) ?? url)
    }

    /// Hosts that can only ever mean "a machine on the developer's desk".
    static func isPrivateHost(_ host: String) -> Bool {
        let lowered = host.lowercased()
        if lowered == "localhost" || lowered.hasSuffix(".local") { return true }
        if lowered == "::1" { return true }

        let parts = lowered.split(separator: ".").compactMap { UInt8($0) }
        guard parts.count == 4 else { return false }
        switch (parts[0], parts[1]) {
        case (10, _):                       return true    // 10.0.0.0/8
        case (127, _):                      return true    // loopback
        case (192, 168):                    return true    // 192.168.0.0/16
        case (169, 254):                    return true    // link-local
        case (172, 16...31):                return true    // 172.16.0.0/12
        default:                            return false
        }
    }
}
