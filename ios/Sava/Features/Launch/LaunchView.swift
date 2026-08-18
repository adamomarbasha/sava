import SwiftUI

/// Shown briefly while the session is restored from the Keychain. Calm, branded,
/// and quick — it never blocks the user with a spinner-only screen.
struct LaunchView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false

    var body: some View {
        ZStack {
            LiquidBackground()

            VStack(spacing: Spacing.md) {
                SavaMark(size: 76)
                    .scaleEffect(appeared || reduceMotion ? 1 : 0.9)
                    .opacity(appeared || reduceMotion ? 1 : 0)

                Text("Sava")
                    .savaWordmark(30)
                    .foregroundStyle(SavaColors.textPrimary)
                    .opacity(appeared || reduceMotion ? 1 : 0)
            }
        }
        .onAppear {
            withAnimation(SavaMotion.smooth) { appeared = true }
        }
    }
}
