import SwiftUI

/// Routes between the launch splash, the authentication flow, and the signed-in
/// app based on the session phase.
///
/// The transition is a plain cross-fade in both directions. Signing in should
/// feel like the same surface continuing, not like arriving somewhere else —
/// which is also why the sign-in screen is built from the same ground, type and
/// controls as the app behind it.
struct RootView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @AppStorage(AppTheme.storageKey) private var themeRaw = AppTheme.dark.rawValue

    /// Dark remains the default — the palette and every contrast ratio were
    /// authored against ink — but it is now a default rather than a lock.
    private var theme: AppTheme {
        #if DEBUG
        // A launch-environment override, so a screenshot pass can pin the
        // appearance without driving Settings by hand. Same seam as
        // SAVA_DEV_TAB, and it is compiled out of Release.
        if let forced = ProcessInfo.processInfo.environment["SAVA_DEV_APPEARANCE"],
           let theme = AppTheme(rawValue: forced) {
            return theme
        }
        #endif
        return AppTheme(rawValue: themeRaw) ?? .dark
    }

    var body: some View {
        ZStack {
            SavaColor.ground.ignoresSafeArea()

            // A build that cannot reach a backend must say so.
            //
            // Without this the symptom is a normal-looking sign-in screen whose
            // every attempt times out, which is indistinguishable from a server
            // outage or a wrong password. Naming the actual cause turns a
            // mystery into a one-line fix.
            if let error = AppConfig.configurationError {
                MisconfiguredBuildView(message: error.message)
                    .transition(.opacity)
            } else {
                switch session.phase {
                case .restoring:
                    LaunchView().transition(.opacity)
                case .signedOut:
                    AuthFlowView().transition(.opacity)
                case .signedIn(let user):
                    AppShell(user: user).transition(.opacity)
                }
            }
        }
        // The user's appearance preference. `nil` for Automatic, which hands
        // the decision back to iOS — see the note in AppTheme.
        .preferredColorScheme(theme.colorScheme)
        .animation(Motion.respecting(Motion.standard, reduceMotion), value: session.phase)
        .task { await session.restore() }
    }
}


/// Shown instead of the app when this build has no usable API address.
///
/// Deliberately plain and deliberately technical: the only person who can ever
/// see it is whoever built the app, and what they need is the reason, not
/// reassurance.
private struct MisconfiguredBuildView: View {
    let message: String

    var body: some View {
        VStack(spacing: Space.l) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 30, weight: .regular))
                .foregroundStyle(SavaColor.danger)

            Text("Sava can't reach a server")
                .font(SavaType.title)
                .foregroundStyle(SavaColor.primary)

            Text(message)
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.secondary)
                .multilineTextAlignment(.center)

            Text("Set SAVA_API_BASE_URL in Info-Release.plist to Sava's deployed HTTPS origin.")
                .font(SavaType.meta)
                .foregroundStyle(SavaColor.tertiary)
                .multilineTextAlignment(.center)
                .padding(.top, Space.xs)
        }
        .padding(.horizontal, Space.xxl)
        .accessibilityElement(children: .combine)
    }
}
