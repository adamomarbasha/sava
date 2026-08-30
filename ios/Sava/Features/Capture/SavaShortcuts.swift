import AppIntents

/// Siri phrases and the Shortcuts/Spotlight entries.
///
/// An `AppShortcutsProvider` is **not** what makes Sava's actions appear in the
/// Shortcuts action library — every `AppIntent` in the app is listed there
/// automatically. What it does is put them in Spotlight, give them Siri
/// phrases, and — the reason this matters here — make them findable by somebody
/// who opens Settings → Action Button → Shortcut and expects to see "Save to
/// Sava" without first having to build a Shortcut by hand.
///
/// Kept to two entries. Apple allows ten, and every extra one dilutes the two
/// that people actually want; a provider listing four variants of "save" is how
/// Siri ends up asking which one you meant.
struct SavaShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        // The one people look for. Takes a URL, text containing one, or falls
        // back to the clipboard — so it is useful with no configuration, which
        // is exactly what the Action Button picker needs.
        //
        // `.applicationName` is required by AppIntents in at least one phrase,
        // and every phrase must contain it. "Save to Sava" therefore works as
        // written; a phrase like "Save this link" would be rejected at build
        // time, not at runtime.
        AppShortcut(
            intent: SaveToSavaIntent(),
            phrases: [
                "Save to \(.applicationName)",
                "Add to \(.applicationName)",
                "Save this to \(.applicationName)",
            ],
            shortTitle: "Save to Sava",
            systemImageName: "bookmark.fill"
        )

        // The screenshot path, which is what the full capture Shortcut uses in
        // its Otherwise branch and what "save this screen" means by voice.
        AppShortcut(
            intent: SaveScreenshotToSavaIntent(),
            phrases: [
                "Save this screen to \(.applicationName)",
            ],
            shortTitle: "Save Screenshot to Sava",
            systemImageName: "camera.viewfinder"
        )
    }

    static var shortcutTileColor: ShortcutTileColor = .navy
}
