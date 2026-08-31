import SwiftUI

@main
struct SavaApp: App {
    init() {
        // Before anything reads the preference.
        //
        // The appearance moved from `UserDefaults.standard` to the app group so
        // the share extension can see it. `@AppStorage` reads the group store
        // directly, so without this an upgrading user's stored choice — still
        // sitting in the old per-process store — is simply not found, and they
        // silently revert to the default on first launch of this build.
        AppearancePreference.migrateIfNeeded()
        SavaAppearance.apply()
        // So the share extension talks to the same backend this build does.
        AppConfig.publishOriginForExtension()
        #if DEBUG
        CaptureDiagnostics.runSelectorSelfCheck()
        CaptureDiagnostics.runAuthLifetimeSelfCheck()
        CaptureDiagnostics.runIntentInputSelfCheck()
        CaptureDiagnostics.runLinkIntentInputSelfCheck()
        CaptureDiagnostics.runShortcutConfigSelfCheck()
        #endif
    }

    @StateObject private var session = SessionStore()

    /// One subscription manager for the whole app.
    ///
    /// App-level rather than per-screen because it owns the `Transaction.updates`
    /// listener, and that has to outlive any view: a renewal or a refund can
    /// arrive while the user is anywhere in the app, or while it is in the
    /// background. A manager created by the paywall would only be listening
    /// while the paywall was open, which is the one time it matters least.
    @StateObject private var subscriptions = SubscriptionManager()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(session)
                .environmentObject(subscriptions)
                .tint(SavaColor.primary)
        }
    }
}
