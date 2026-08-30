import Foundation
import SwiftUI

/// Whether this person has been shown how Sava works.
///
/// ── Why per-account, and why not `@AppStorage` ──────────────────────────
///
/// Onboarding completion belongs to a *person*, not to an install. Two things
/// follow from that, and both are why this is a small store rather than a
/// property wrapper:
///
///   * **Keyed by user id.** Signing out and back in must not replay the tour
///     for somebody who has already seen it, and handing the phone to a second
///     account must not skip it for somebody who has not. `@AppStorage` needs a
///     static key, which cannot depend on who is signed in.
///   * **Versioned deliberately, never by build.** The key contains a hand-set
///     `v1`, not the app version or build number. Tying it to either is how an
///     ordinary bug-fix release re-runs onboarding for the entire user base;
///     the constant moves only when the flow has genuinely changed enough to be
///     worth showing again.
///
/// Stored as a set of ids rather than one boolean so the answer survives
/// account switching in both directions.
enum OnboardingState {

    /// Bump only when the flow changes enough that everyone should see it again.
    /// An app update on its own must never reset this.
    static let version = 1

    private static var key: String { "sava.onboarding.completed.v\(version)" }

    private static var store: UserDefaults { .standard }

    /// The accounts that have finished (or deliberately skipped) onboarding.
    private static func completedIDs() -> Set<Int> {
        Set((store.array(forKey: key) as? [Int]) ?? [])
    }

    /// Has this account seen it?
    ///
    /// A nil user id means "we do not know who this is yet", and the honest
    /// answer there is *not* to show onboarding — the caller only asks once a
    /// session exists, and guessing "yes, show it" would flash the tour at
    /// somebody mid-launch.
    static func isComplete(for userID: Int?) -> Bool {
        guard let userID else { return true }
        return completedIDs().contains(userID)
    }

    static func markComplete(for userID: Int?) {
        guard let userID else { return }
        var ids = completedIDs()
        guard !ids.contains(userID) else { return }
        ids.insert(userID)
        store.set(Array(ids), forKey: key)
    }

    /// Show it again. Used by **Profile → Learn Sava**, so the tour is a place
    /// you can go back to rather than a thing that happened once.
    static func reset(for userID: Int?) {
        guard let userID else { return }
        var ids = completedIDs()
        ids.remove(userID)
        store.set(Array(ids), forKey: key)
    }

    #if DEBUG
    /// Test and QA hook. Never compiled into Release.
    static func resetAll() { store.removeObject(forKey: key) }
    #endif
}
