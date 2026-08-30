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
/// Kept to one entry. Apple allows ten, and every extra one dilutes the one
/// people actually want; a provider listing four variants of "save" is how Siri
/// ends up asking which one you meant.
///
/// There used to be a second entry for saving a screenshot. Sava saves links
/// now — a screenshot cannot establish which video it shows, so it could only
/// ever produce a guess — and an action nobody should use does not belong in
/// Spotlight or in the Action Button picker.
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

    }

    static var shortcutTileColor: ShortcutTileColor = .navy
}
