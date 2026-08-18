import SwiftUI

/// Routes between the launch splash, the authentication flow, and the signed-in
/// app based on the session phase. Transitions are soft and continuous.
struct RootView: View {
    @EnvironmentObject private var session: SessionStore

    var body: some View {
        ZStack {
            switch session.phase {
            case .restoring:
                LaunchView()
                    .transition(.opacity)
            case .signedOut:
                AuthFlowView()
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            case .signedIn(let user):
                SignedInView(user: user)
                    .transition(.opacity.combined(with: .scale(scale: 1.02)))
            }
        }
        .animation(SavaMotion.smooth, value: session.phase)
        .task {
            await session.restore()
        }
    }
}
