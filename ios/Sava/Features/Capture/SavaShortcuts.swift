import AppIntents

/// Siri phrases and Spotlight entries.
///
/// Note these are NOT how the Action Button captures. The Action Button runs a
/// *Shortcut* (Get What's On Screen → Get URLs → Count → branch), and that
/// workflow calls `SaveLinkToSavaIntent` / `SaveScreenshotToSavaIntent`
/// directly. Both intents appear in the Shortcuts action library automatically
/// — an `AppShortcutsProvider` entry is not required for that.
///
/// Only the screenshot intent is surfaced here, because it is the one that can
/// be driven meaningfully by voice ("save this screen to Sava" after taking a
/// screenshot). Registering the link intent would prompt for a URL, which is
/// exactly the configuration sheet we are avoiding.
struct SavaShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: SaveScreenshotToSavaIntent(),
            phrases: [
                "Save this screen to \(.applicationName)",
                "Save to \(.applicationName)"
            ],
            shortTitle: "Save Screenshot to Sava",
            systemImageName: "bookmark.fill"
        )
    }

    static var shortcutTileColor: ShortcutTileColor = .navy
}
