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
    /// instance it is handed, so two views ended up observing two different
    /// objects over the same container: a write through one did not notify the
    /// other, and the appearance changed on disk while the screen stayed as it
    /// was.
    ///
    /// ── And why it is not simply the app group ──────────────────────────
    ///
    /// It was, and that made the app's own appearance depend on an entitlement.
    /// The Simulator does not enforce entitlements; a device does. On a build
    /// whose provisioning profile lacks `group.com.sava.mobile`, the suite is
    /// unusable and the device logs
    ///
    ///     Couldn't read values in CFPrefsPlistSource … Domain:
    ///     group.com.sava.mobile … kCFPreferencesAnyUser with a container is
    ///     only allowed for System Containers
    ///
    /// — after which the preference cannot be written or read, and Light/Dark
    /// stops persisting. Appearance is a *local* setting; it must not be able
    /// to fail because of a capability only the share extension needs.
    ///
    /// So `.standard` is authoritative for the app, the group is written to as
    /// well when it genuinely works, and the extension reads the group. The
    /// group being unavailable costs the extension its theme and nothing else.
    public static let store: UserDefaults = .standard

    /// The shared container, when it is actually usable.
    ///
    /// Verified by a write-and-read round trip rather than by a non-nil check:
    /// `UserDefaults(suiteName:)` returns an object for an unentitled group and
    /// silently drops everything written to it.
    public static let sharedStore: UserDefaults? = {
        guard let suite = UserDefaults(suiteName: PendingSaveQueue.appGroup) else {
            return nil
        }
        let probe = "sava.groupProbe"
        suite.set(true, forKey: probe)
        let usable = suite.bool(forKey: probe)
        suite.removeObject(forKey: probe)
        return usable ? suite : nil
    }()

    /// The current preference. Dark is the default — the palette and every
    /// contrast ratio in the design system were authored against ink.
    public static var current: AppearancePreference {
        migrateIfNeeded()
        // The app's own value first; the group is what the *extension* reads,
        // and it is the one that may be missing.
        let raw = store.string(forKey: storageKey)
            ?? sharedStore?.string(forKey: storageKey)
            ?? ""
        return AppearancePreference(rawValue: raw) ?? .dark
    }

    /// Write the preference everywhere it is read from.
    ///
    /// The picker binds to `@AppStorage(store:)`, which writes only the local
    /// store, so this mirrors it into the group for the extension. Called from
    /// the app whenever the preference changes.
    public static func publish(_ preference: AppearancePreference) {
        store.set(preference.rawValue, forKey: storageKey)
        sharedStore?.set(preference.rawValue, forKey: storageKey)
    }

    /// Move a value written before the preference was shared.
    ///
    /// Without this, upgrading users silently revert to the default the first
    /// time they launch a build that reads the group — their choice is still in
    /// `.standard`, where nothing looks any more.
    public static func migrateIfNeeded() {
        // A value written by a build that stored only in the group.
        //
        // Read the group's *own* domain, not through the suite object. A
        // `UserDefaults(suiteName:)` search list also contains this process's
        // standard domain, so `string(forKey:)` on it can answer with a local
        // value and make a migration look necessary when the group is in fact
        // empty. The guard above happens to rule that out today; reading the
        // domain directly means the correctness does not depend on the order
        // of these two conditions.
        guard store.string(forKey: storageKey) == nil,
              let domain = UserDefaults.standard
                  .persistentDomain(forName: PendingSaveQueue.appGroup),
              let fromGroup = domain[storageKey] as? String,
              AppearancePreference(rawValue: fromGroup) != nil else { return }
        store.set(fromGroup, forKey: storageKey)
    }

    // Applying the preference is deliberately *not* here.
    //
    // It needs `UIApplication.shared` to walk the connected scenes' windows,
    // and that symbol is unavailable in an app extension — the compiler
    // enforces it. The extension does not own the host app's window anyway and
    // must never override it, so it sets the style on its own view controller
    // instead. See `AppTheme.apply` for the app-side implementation.
}
