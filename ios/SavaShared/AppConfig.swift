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

    // MARK: Sharing the origin with the share extension

    /// Where the app publishes its resolved origin for the extension to read.
    ///
    /// The extension cannot work this out for itself. It has its own bundle and
    /// its own Info.plist, so it does not see the app's `SAVA_API_BASE_URL`, and
    /// in development the address is stamped into the *app's* built plist at
    /// compile time by `Scripts/stamp-dev-host.sh` — a value that exists nowhere
    /// the extension can reach.
    ///
    /// Duplicating the setting into the extension's plist would create a second
    /// source of truth that silently drifts the first time somebody updates one
    /// and not the other. Publishing the already-resolved value instead means
    /// there is exactly one answer, and it is the one the app is actually using.
    private static let sharedOriginKey = "sava.resolvedAPIOrigin"

    private static var sharedDefaults: UserDefaults? {
        UserDefaults(suiteName: PendingSaveQueue.appGroup)
    }

    /// Called by the app on launch. Cheap, idempotent.
    static func publishOriginForExtension() {
        guard configurationError == nil else { return }
        sharedDefaults?.set(apiBaseURL.absoluteString, forKey: sharedOriginKey)
    }

    /// The origin, as published by the containing app.
    ///
    /// Nil until the app has launched at least once. That is the correct
    /// behaviour rather than a limitation: the extension also needs the auth
    /// token from the shared Keychain, which only exists after signing in, so
    /// there is no state where a published origin would have helped and its
    /// absence hurts.
    static var sharedOrigin: URL? {
        guard let raw = sharedDefaults?.string(forKey: sharedOriginKey) else { return nil }
        return URL(string: raw)
    }

    /// True when the app is talking to a real, secure, remote backend.
    static var isProductionReady: Bool { configurationError == nil && apiBaseURL.scheme == "https" }

    // MARK: Externally hosted links

    /// The official **Save to Sava** Shortcut, published from the Shortcuts app.
    ///
    /// This is the one source of truth for the link. No other Swift file and
    /// neither plist carries the id — replacing the published Shortcut means
    /// editing this one line, and `tests/test_ios_shortcut.py` fails CI if a
    /// second copy appears anywhere under `ios/`. (`docs/ACTION_BUTTON.md`
    /// quotes it, which is documentation rather than a second configuration
    /// point; the test allows that and nothing else.)
    ///
    /// Hardcoded rather than read from `Info.plist`, deliberately. A plist
    /// value would have to be duplicated into `Info.plist` *and*
    /// `Info-Release.plist` — two places to keep in step, which is exactly the
    /// drift this constant exists to prevent. It is a public link, not a
    /// secret, and it is identical in every configuration.
    ///
    /// The Shortcut is a thin wrapper: it gathers evidence Shortcuts can see
    /// and an App Intent cannot (the foreground app's URLs, the clipboard, a
    /// screenshot) and hands it to Sava's own intents. All save behaviour lives
    /// in `CapturePipeline`. See `docs/ACTION_BUTTON.md` for its exact actions.
    static let officialSaveShortcutURL = URL(
        string: "https://www.icloud.com/shortcuts/c718dbc210a646cea3326d596d1895ef")!

    /// The name the Shortcut installs under, and therefore the name the user
    /// has to look for in **Settings → Action Button → Shortcut**.
    ///
    /// Not "Save to Sava". That is the *App Shortcut*, which the same picker
    /// lists separately under Sava's own heading. Both work; telling the user
    /// to look for a name that is not there does not. Read from the published
    /// Shortcut's own record, not from memory.
    static let officialSaveShortcutName = "Sava Save"

    /// The Shortcut the install button actually opens.
    ///
    /// The official link, unless a build overrides it with
    /// `SAVA_SHARED_SHORTCUT_URL` (scheme environment variable or Info.plist) —
    /// an escape hatch for testing an unpublished Shortcut without touching
    /// tracked source. An override that does not validate is *ignored* rather
    /// than honoured or fatal: a typo in a plist should cost the tester their
    /// override, not give every user a button that opens somewhere else.
    static var saveShortcutURL: URL {
        let raw = ProcessInfo.processInfo.environment["SAVA_SHARED_SHORTCUT_URL"]
            ?? Bundle.main.object(forInfoDictionaryKey: "SAVA_SHARED_SHORTCUT_URL") as? String
        return validatedShortcutURL(raw) ?? officialSaveShortcutURL
    }

    /// https, and hosted by Apple.
    ///
    /// Only Apple can host a Shortcut. Accepting any https URL would turn a
    /// plist typo — or anything able to write to the plist — into a tap that
    /// opens an arbitrary website from a trusted-looking button.
    static func validatedShortcutURL(_ raw: String?) -> URL? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty,
              let url = URL(string: trimmed),
              url.scheme?.lowercased() == "https",
              let host = url.host?.lowercased(),
              host == "icloud.com" || host.hasSuffix(".icloud.com"),
              !url.path.isEmpty, url.path != "/"
        else { return nil }
        return url
    }

    /// Where Sava's public pages live. Used for the subscription disclosures
    /// Apple requires on a paywall.
    enum Links {
        static let privacy = URL(string: "https://sava.app/privacy")!
        static let support = URL(string: "https://sava.app/support")!

        /// Apple's standard EULA.
        ///
        /// Guideline 3.1.2 requires a Terms of Use link on any screen selling a
        /// subscription. Apple's standard licence is the correct answer until
        /// Sava publishes its own — linking to a `sava.app/terms` page that does
        /// not exist would fail review for a broken link rather than pass for
        /// having one.
        static let terms = URL(
            string: "https://www.apple.com/legal/internet-services/itunes/dev/stdeula/")!

        /// Apple's subscription management. An app may not build its own
        /// cancellation flow; this is where cancelling actually happens.
        static let manageSubscription = URL(
            string: "https://apps.apple.com/account/subscriptions")!
    }

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
        var lowered = host.lowercased()

        // A URL wraps an IPv6 literal in brackets (RFC 3986), so `URLComponents`
        // reports the host of `https://[::1]:8000` as `[::1]` and never as the
        // bare `::1` this function used to compare against. That comparison was
        // therefore unreachable, and IPv6 loopback passed validation into a
        // Release build. Unwrap first, then classify.
        if lowered.hasPrefix("["), lowered.hasSuffix("]") {
            lowered = String(lowered.dropFirst().dropLast())
        }

        if lowered == "localhost" || lowered.hasSuffix(".local") { return true }
        if lowered.contains(":") { return isPrivateIPv6(lowered) }

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

    /// The IPv6 equivalent, parsed rather than pattern-matched.
    ///
    /// `::1` can be written `0:0:0:0:0:0:0:1`, link-local carries a zone
    /// (`fe80::1%en0`), and `::ffff:127.0.0.1` is loopback wearing an IPv6 hat.
    /// String comparison catches none of those; `inet_pton` gives the 16 bytes
    /// the ranges are actually defined over.
    private static func isPrivateIPv6(_ address: String) -> Bool {
        let bare = String(address.split(separator: "%").first ?? "")
        var parsed = in6_addr()
        guard inet_pton(AF_INET6, bare, &parsed) == 1 else { return false }
        let b = withUnsafeBytes(of: &parsed) { Array($0) }
        guard b.count == 16 else { return false }

        // ::1 loopback and :: unspecified.
        if b[0..<15].allSatisfy({ $0 == 0 }) { return b[15] <= 1 }
        // ::ffff:a.b.c.d is exactly as private as the IPv4 address inside it.
        if b[0..<10].allSatisfy({ $0 == 0 }), b[10] == 0xff, b[11] == 0xff {
            return isPrivateHost("\(b[12]).\(b[13]).\(b[14]).\(b[15])")
        }
        if b[0] == 0xfe, b[1] & 0xc0 == 0x80 { return true }   // fe80::/10 link-local
        if b[0] & 0xfe == 0xfc { return true }                 // fc00::/7  unique local
        return false
    }
}
