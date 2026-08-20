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

    var body: some View {
        ZStack {
            SavaColor.ground.ignoresSafeArea()

            switch session.phase {
            case .restoring:
                LaunchView().transition(.opacity)
            case .signedOut:
                AuthFlowView().transition(.opacity)
            case .signedIn(let user):
                AppShell(user: user).transition(.opacity)
            }
        }
        .animation(Motion.respecting(Motion.standard, reduceMotion), value: session.phase)
        .task { await session.restore() }
    }
}
