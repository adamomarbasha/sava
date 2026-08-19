import AppIntents

/// Registers Sava's App Shortcuts so "Save to Sava" is discoverable in the
/// Shortcuts app, Spotlight, and Siri — and, crucially, assignable to the
/// iPhone Action Button (Settings → Action Button → Shortcut → Save to Sava).
struct SavaShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: SaveToSavaIntent(),
            phrases: [
                "Save to \(.applicationName)",
                "Save this to \(.applicationName)",
                "Add to \(.applicationName)"
            ],
            shortTitle: "Save to Sava",
            systemImageName: "bookmark.fill"
        )
    }

    static var shortcutTileColor: ShortcutTileColor = .navy
}
