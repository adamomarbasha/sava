import Foundation
import UIKit

/// Where Sava's appearance preference lives, for every process that has a UI.
///
/// ── Why this is in SavaShared and not in the app ────────────────────────
///
/// The preference used to be an `@AppStorage` key in `UserDefaults.standard`,
/// which is per-process. The share extension is a *separate process* with its
/// own window, so it could not read the value and never applied it: somebody
/// pinned to Dark, on a phone set to Light, got a light share sheet. That is
/// not a redraw problem — no amount of invalidation in the app can reach
/// another process — so the fix has to be the storage, not the refresh.
///
/// The app group is the only container both binaries can see, and it is already
/// used for the pending-save queue and the API origin. One key, one reader,
/// two processes.
///
/// ── Automatic is not a third colour ─────────────────────────────────────
///
/// It is the *absence* of an override: `.unspecified` hands the decision back
/// to iOS, which already knows the time of day, already runs the user's
/// sunrise/sunset schedule, and already honours Focus modes and the
/// accessibility settings that force one appearance. An app running its own
/// clock would fight all of that.
public enum AppearancePreference: String, CaseIterable, Sendable {
    case automatic, light, dark

    public static let storageKey = "sava.appearance"

    /// `.unspecified` hands the decision back to the system.
    public var interfaceStyle: UIUserInterfaceStyle {
        switch self {
        case .automatic: return .unspecified
        case .light:     return .light
        case .dark:      return .dark
        }
    }

    /// The shared store, or `.standard` when the app group is unavailable.
    ///
    /// A missing app group is a provisioning fault, not a user-facing one — the
    /// app still works, it just stops sharing the preference with the
    /// extension. Falling back keeps the app correct rather than crashing over
    /// a capability the *extension* needs.
    /// One instance, not one per access.
    ///
    /// This was a computed property, which minted a *new* `UserDefaults` object
    /// for the suite on every read. `@AppStorage(_:store:)` captures whatever
    /// instance it is handed, so `RootView` and `ProfileView` ended up
    /// observing two different objects over the same container: a write through
    /// one did not notify the other, the view that owns `apply` never
    /// re-evaluated, and the appearance changed on disk while the screen stayed
    /// as it was. That is the "stale until you navigate" report — navigating
    /// rebuilt the view, which re-read the store and picked up the new value.
    ///
    /// A single shared instance is the source of truth every observer attaches
    /// to.
    public static let store: UserDefaults =
        UserDefaults(suiteName: PendingSaveQueue.appGroup) ?? .standard

    /// The current preference. Dark is the default — the palette and every
    /// contrast ratio in the design system were authored against ink.
    public static var current: AppearancePreference {
        migrateIfNeeded()
        let raw = store.string(forKey: storageKey) ?? ""
        return AppearancePreference(rawValue: raw) ?? .dark
    }

    /// Move a value written before the preference was shared.
    ///
    /// Without this, upgrading users silently revert to the default the first
    /// time they launch a build that reads the group — their choice is still in
    /// `.standard`, where nothing looks any more.
    public static func migrateIfNeeded() {
        let shared = store
        guard shared !== UserDefaults.standard else { return }

        // `persistentDomain`, not `string(forKey:)`.
        //
        // A `UserDefaults(suiteName:)` object's *search list* also contains the
        // process's own standard domain, so `string(forKey:)` happily returns
        // the legacy value and this looked like it had nothing to do. Reads in
        // the app therefore worked by accident while the group container stayed
        // empty — and the extension, whose standard domain is its own, still
        // saw nothing. Asking the suite's persistent domain directly is the
        // only way to know whether the value is really *in the group*.
        let inGroup = shared.persistentDomain(forName: PendingSaveQueue.appGroup)?[storageKey]
        guard inGroup == nil,
              let legacy = UserDefaults.standard.persistentDomain(
                forName: Bundle.main.bundleIdentifier ?? "")?[storageKey] as? String
                ?? UserDefaults.standard.string(forKey: storageKey),
              AppearancePreference(rawValue: legacy) != nil else { return }
        shared.set(legacy, forKey: storageKey)
    }

    // Applying the preference is deliberately *not* here.
    //
    // It needs `UIApplication.shared` to walk the connected scenes' windows,
    // and that symbol is unavailable in an app extension — the compiler
    // enforces it. The extension does not own the host app's window anyway and
    // must never override it, so it sets the style on its own view controller
    // instead. See `AppTheme.apply` for the app-side implementation.
}
