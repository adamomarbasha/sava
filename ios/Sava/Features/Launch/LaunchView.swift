import SwiftUI

/// Shown for the moment it takes to restore the session from the Keychain.
/// Just the wordmark on the app's own ground — no spinner, no animation to sit
/// through.
struct LaunchView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false

    var body: some View {
        ZStack {
            SavaColor.ground.ignoresSafeArea()
            Text("Sava")
                .font(.system(size: 34, weight: .bold))
                .tracking(-1)
                .foregroundStyle(SavaColor.primary)
                .opacity(appeared || reduceMotion ? 1 : 0)
        }
        .onAppear {
            withAnimation(Motion.respecting(Motion.gentle, reduceMotion)) { appeared = true }
        }
    }
}
