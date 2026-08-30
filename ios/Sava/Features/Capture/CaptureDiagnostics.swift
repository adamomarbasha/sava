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
    /// Whether the clipboard value survived content-URL validation.
    /// A checked-but-rejected clipboard is the normal case, not a fault.
    var clipboardAccepted: Bool = false

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

#if DEBUG
extension CaptureDiagnostics {
    /// Verifies `SaveToSavaIntent` finds the right link in whatever it is given.
    ///
    /// This is the parsing that makes the general-purpose action work at all:
    /// Shortcuts hands over text far more often than a typed URL, and every
    /// case below is a real shape it produces — a bare link, a shared caption
    /// with a trailing link, a link inside a sentence, a link in parentheses,
    /// a message with several links where only one is the content.
    ///
    /// Run on every Debug launch alongside the selector check, because a
    /// regression here is silent: the intent still succeeds, it just saves the
    /// wrong thing or nothing at all.
    static func runIntentInputSelfCheck() {
        let cases: [(name: String, input: String?, expect: String?)] = [
            ("bare URL",
             "https://www.tiktok.com/@u/video/7234567890123456789",
             "https://www.tiktok.com/@u/video/7234567890123456789"),

            ("URL with surrounding whitespace",
             "   https://youtu.be/dQw4w9WgXcQ   ",
             "https://youtu.be/dQw4w9WgXcQ"),

            ("shared caption then link (TikTok share sheet)",
             "Check this out 😂 https://vm.tiktok.com/ZMhqK1abc/",
             "https://vm.tiktok.com/ZMhqK1abc/"),

            ("link mid-sentence with trailing punctuation",
             "watch https://www.youtube.com/watch?v=aircAruvnKk, it's great.",
             "https://www.youtube.com/watch?v=aircAruvnKk"),

            ("profile and video together -> video wins",
             "https://www.instagram.com/zendaya/ and https://www.instagram.com/reel/DPMnXPeEoIi/",
             "https://www.instagram.com/reel/DPMnXPeEoIi/"),

            ("multi-line message",
             "hey\nlook at this\nhttps://www.youtube.com/shorts/dQw4w9WgXcQ\nlol",
             "https://www.youtube.com/shorts/dQw4w9WgXcQ"),

            // Falls through to the clipboard inside CaptureRunner rather than
            // failing here — an empty action is a valid way to run this intent.
            ("empty -> no candidates", "", nil),
            ("nil -> no candidates", nil, nil),
            ("plain text, no link", "just some words", nil),
            ("non-http scheme is ignored", "mailto:hi@sava.app", nil),
        ]

        var passed = 0
        for c in cases {
            let (candidates, _) = SaveToSavaIntent.candidates(from: c.input)
            let got = URLSelector.best(from: candidates)
            let ok = got == c.expect
            if ok { passed += 1 }
            NSLog("[Sava selftest] %@ intent-input: %@ -> %@",
                  ok ? "PASS" : "FAIL", c.name, got ?? "nil")
            if !ok { NSLog("[Sava selftest]   expected: %@", c.expect ?? "nil") }
        }
        NSLog("[Sava selftest] SaveToSavaIntent input %d/%d passed",
              passed, cases.count)
    }
}
#endif

#if DEBUG
extension CaptureDiagnostics {
    /// Verifies the official Shortcut link is present and points at Apple.
    ///
    /// The install button is one tap with no confirmation, so a link that has
    /// been mistyped, blanked, or overridden to somewhere else is a button that
    /// sends users off Sava's promised path without ever failing loudly. Cheap
    /// to check at launch; the matching CI check is
    /// `tests/test_ios_shortcut.py`, which also proves there is only one copy
    /// of the URL in the tree.
    static func runShortcutConfigSelfCheck() {
        let resolved = AppConfig.saveShortcutURL
        let isOfficial = resolved == AppConfig.officialSaveShortcutURL

        NSLog("[Sava selftest] shortcut link: %@  (%@)",
              resolved.absoluteString,
              isOfficial ? "official" : "OVERRIDDEN by SAVA_SHARED_SHORTCUT_URL")

        // A junk override must be ignored, not honoured. Each of these would be
        // a tap that leaves Sava's control if the validator let it through.
        let rejects = [nil, "", "   ",
                       "http://www.icloud.com/shortcuts/abc",   // not https
                       "https://www.icloud.com",                // no shortcut
                       "https://icloud.com.evil.test/shortcuts/abc",
                       "https://example.com/shortcuts/abc"]
        var passed = 0
        for raw in rejects where AppConfig.validatedShortcutURL(raw) == nil { passed += 1 }
        NSLog("[Sava selftest] shortcut override validation %d/%d rejected",
              passed, rejects.count)

        let accepted = AppConfig.validatedShortcutURL(
            AppConfig.officialSaveShortcutURL.absoluteString) != nil
        NSLog("[Sava selftest] official link validates: %@",
              accepted ? "PASS" : "FAIL")
    }
}
#endif

#if DEBUG
extension CaptureDiagnostics {
    /// Verifies `SaveLinkToSavaIntent` — the action the official Shortcut
    /// actually calls — copes with what that Shortcut sends it.
    ///
    /// Two branches send two very different shapes. The on-screen branch sends
    /// a list of URLs. The clipboard branch sends the clipboard's *contents*,
    /// which is whatever the user last copied: usually a link, often a link
    /// wrapped in a caption, sometimes nothing useful at all. Both arrive here
    /// as text, and a regression is silent — the action still succeeds, it just
    /// saves the wrong thing or nothing.
    static func runLinkIntentInputSelfCheck() {
        let cases: [(name: String, input: [String], expect: String?)] = [
            ("on-screen URL list -> video wins",
             ["https://www.tiktok.com/@mystery_jj",
              "https://www.tiktok.com/music/original-sound-7234567890",
              "https://www.tiktok.com/@mystery_jj/video/7234567890123456789"],
             "https://www.tiktok.com/@mystery_jj/video/7234567890123456789"),

            ("single URL",
             ["https://youtu.be/dQw4w9WgXcQ"],
             "https://youtu.be/dQw4w9WgXcQ"),

            // The clipboard branch. A `[URL]` parameter is what made this shape
            // a coercion error rather than a save.
            ("clipboard caption carrying a link",
             ["Check this out 😂 https://vm.tiktok.com/ZMhqK1abc/"],
             "https://vm.tiktok.com/ZMhqK1abc/"),

            ("clipboard link with whitespace",
             ["  https://www.instagram.com/reel/DPMnXPeEoIi/  "],
             "https://www.instagram.com/reel/DPMnXPeEoIi/"),

            ("duplicates collapse",
             ["https://youtu.be/dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"],
             "https://youtu.be/dQw4w9WgXcQ"),

            ("clipboard with no link", ["just some words"], nil),
            ("malformed", ["h ttps://not a url"], nil),
            ("blank entries", ["", "   "], nil),
            ("nothing at all", [], nil),
        ]

        var passed = 0
        for c in cases {
            let candidates = SaveLinkToSavaIntent.candidates(from: c.input)
            let got = URLSelector.best(from: candidates)
            let ok = got == c.expect
            if ok { passed += 1 }
            NSLog("[Sava selftest] %@ link-intent: %@ -> %@",
                  ok ? "PASS" : "FAIL", c.name, got ?? "nil")
            if !ok { NSLog("[Sava selftest]   expected: %@", c.expect ?? "nil") }
        }
        NSLog("[Sava selftest] SaveLinkToSavaIntent input %d/%d passed",
              passed, cases.count)
    }
}
#endif
