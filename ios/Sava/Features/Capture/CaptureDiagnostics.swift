import Foundation

/// DEBUG-only record of what each Action Button press actually received.
///
/// The App Intent runs without UI, so a failure is otherwise invisible — you
/// get a one-line dialog and no way to tell whether the Shortcut passed a URL,
/// passed a screenshot, or passed nothing at all. This writes a structured
/// trace of every invocation that the Profile screen can display.
///
/// Compiled out of Release entirely: the type exists so call sites stay clean,
/// but every method is a no-op and nothing is persisted.
struct CaptureTrace: Codable, Identifiable {
    var id: String = UUID().uuidString
    var timestamp: Date = Date()

    // What arrived
    var hadShortcutInput: Bool = false
    var inputTypes: [String] = []          // onscreen item type(s)
    var onScreenURLs: [String] = []        // URLs found
    var onScreenURLCount: Int = 0          // URL count
    var selectedURL: String?               // selected URL
    var screenshotTaken: Bool = false      // screenshot taken yes/no
    var providedURL: String?
    var screenshotBytes: Int = 0
    var clipboardChecked: Bool = false
    var clipboardType: String?
    var clipboardValue: String?

    // What we decided
    var detectedPlatform: String = "unknown"
    var path: String = "unknown"        // direct_url | screenshot_resolution | clipboard_fallback | failed
    var resolvedURL: String?
    var resolverReason: String?
    var resolverConfidence: Double?
    var outcome: String = "pending"     // saved | duplicate | failed
    var message: String?
    var durationMs: Int = 0

    // Save-path detail
    var saveID: Int?
    var canonicalID: Int?
    var httpStatus: Int?
    var authPresent: Bool = false

    var summary: String {
        "\(path) → \(outcome)"
    }
}

enum CaptureDiagnostics {
    private static let key = "sava.debug.captureTraces"
    private static let limit = 25

    /// Record a completed capture attempt. No-op in Release.
    static func record(_ trace: CaptureTrace) {
        #if DEBUG
        var all = recent()
        all.insert(trace, at: 0)
        let trimmed = Array(all.prefix(limit))
        if let data = try? JSONEncoder().encode(trimmed) {
            UserDefaults.standard.set(data, forKey: key)
        }
        NSLog("[Sava capture] %@", describe(trace))
        #endif
    }

    /// Most recent attempts, newest first. Always empty in Release.
    static func recent() -> [CaptureTrace] {
        #if DEBUG
        guard
            let data = UserDefaults.standard.data(forKey: key),
            let decoded = try? JSONDecoder().decode([CaptureTrace].self, from: data)
        else { return [] }
        return decoded
        #else
        return []
        #endif
    }

    static func clear() {
        #if DEBUG
        UserDefaults.standard.removeObject(forKey: key)
        #endif
    }

    #if DEBUG
    static func describe(_ t: CaptureTrace) -> String {
        var parts: [String] = [
            "input=\(t.hadShortcutInput ? "yes" : "NONE")",
            "types=[\(t.inputTypes.joined(separator: ","))]",
            "urlCount=\(t.onScreenURLCount)",
            "urls=[\(t.onScreenURLs.prefix(4).joined(separator: " | "))]",
            "selected=\(t.selectedURL ?? "nil")",
            "screenshotTaken=\(t.screenshotTaken ? "yes" : "no")",
            "screenshot=\(t.screenshotBytes > 0 ? "\(t.screenshotBytes)B" : "nil")",
            "clipboard=\(t.clipboardChecked ? (t.clipboardValue ?? "empty") : "not checked")",
            "platform=\(t.detectedPlatform)",
            "path=\(t.path)",
            "outcome=\(t.outcome)",
        ]
        if let r = t.resolvedURL { parts.append("resolved=\(r)") }
        if let r = t.resolverReason { parts.append("reason=\(r)") }
        if let s = t.saveID { parts.append("save_id=\(s)") }
        if let c = t.canonicalID { parts.append("canonical_id=\(c)") }
        if let h = t.httpStatus { parts.append("http=\(h)") }
        parts.append("auth=\(t.authPresent ? "yes" : "NO")")
        // The actual failure text — previously omitted, which is why a failed
        // direct_url save showed no reason at all.
        if let m = t.message { parts.append("message=\(m)") }
        parts.append("\(t.durationMs)ms")
        return parts.joined(separator: "  ")
    }
    #endif
}

#if DEBUG
extension CaptureDiagnostics {
    /// Self-check for URL ranking, run once at launch in Debug builds.
    ///
    /// "Get What's On Screen" returns a mixed bag — profile links, sound pages,
    /// CDN assets, app-store links. Picking the wrong one saves the wrong
    /// video, so the ranking is verified against realistic captures rather
    /// than trusted by eye.
    static func runSelectorSelfCheck() {
        let cases: [(name: String, input: [String], expect: String?)] = [
            ("TikTok video among profile + sound + CDN", [
                "https://www.tiktok.com/@mystery_jj",
                "https://www.tiktok.com/music/original-sound-7234567890",
                "https://www.tiktok.com/@mystery_jj/video/7234567890123456789",
                "https://p16-sign.tiktokcdn-us.com/obj/tos-useast5-p-0068/abc.jpg",
             ], "https://www.tiktok.com/@mystery_jj/video/7234567890123456789"),

            ("TikTok short link only", [
                "https://vm.tiktok.com/ZMhqK1abc/",
             ], "https://vm.tiktok.com/ZMhqK1abc/"),

            ("YouTube watch beats channel", [
                "https://www.youtube.com/@3blue1brown",
                "https://www.youtube.com/watch?v=aircAruvnKk",
             ], "https://www.youtube.com/watch?v=aircAruvnKk"),

            ("YouTube Shorts", [
                "https://youtube.com/shorts/dQw4w9WgXcQ",
             ], "https://youtube.com/shorts/dQw4w9WgXcQ"),

            ("Instagram reel beats profile", [
                "https://www.instagram.com/zendaya/",
                "https://www.instagram.com/reel/DPMnXPeEoIi/",
             ], "https://www.instagram.com/reel/DPMnXPeEoIi/"),

            ("junk only -> nothing", [
                "https://apps.apple.com/app/tiktok/id835599320",
                "https://www.tiktok.com/legal/privacy-policy",
             ], nil),

            ("feed pages are not content", [
                "https://www.tiktok.com/foryou",
             ], nil),

            ("empty", [], nil),
        ]

        var passed = 0
        for c in cases {
            let got = URLSelector.best(from: c.input)
            let ok = got == c.expect
            if ok { passed += 1 }
            NSLog("[Sava selftest] %@ %@ -> %@",
                  ok ? "PASS" : "FAIL", c.name, got ?? "nil")
            if !ok { NSLog("[Sava selftest]   expected: %@", c.expect ?? "nil") }
        }
        NSLog("[Sava selftest] URLSelector %d/%d passed", passed, cases.count)
    }
}
#endif

#if DEBUG
extension CaptureDiagnostics {
    /// Verifies the headless auth path survives the caller discarding the
    /// provider — the exact pattern that shipped 403s from the Action Button.
    static func runAuthLifetimeSelfCheck() {
        // Reproduce the original bug shape: take the client, throw the
        // provider away, then check the client can still authenticate.
        let (client, _) = SavaClient.authenticated()

        // Force a turn of the autorelease pool so a non-retained provider
        // would definitely be gone by now.
        autoreleasepool { }

        let providerAlive = client.tokenProvider != nil
        let tokenReadable = client.tokenProvider?.currentToken != nil
        let keychainHasToken = SavaClient.hasStoredToken

        NSLog("[Sava selftest] auth provider survives discard: %@",
              providerAlive ? "PASS" : "FAIL (would send 403s)")
        NSLog("[Sava selftest] token readable via provider: %@  (keychain=%@)",
              tokenReadable ? "PASS" : (keychainHasToken ? "FAIL" : "n/a — signed out"),
              keychainHasToken ? "has token" : "empty")
    }
}
#endif
