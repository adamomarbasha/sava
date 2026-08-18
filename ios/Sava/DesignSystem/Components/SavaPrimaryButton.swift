import SwiftUI

/// The primary call-to-action. Ink-filled, with a Canvas-UI-inspired ripple
/// that emanates on press, plus an inline loading state. Disabled and loading
/// states are visually distinct and accessible.
struct SavaPrimaryButton: View {
    let title: String
    var isLoading: Bool = false
    var isEnabled: Bool = true
    let action: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var rippleActive = false
    @State private var isPressed = false

    private var effectiveEnabled: Bool { isEnabled && !isLoading }

    var body: some View {
        Button {
            guard effectiveEnabled else { return }
            triggerRipple()
            Haptics.press()
            action()
        } label: {
            ZStack {
                // Fill
                RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .fill(SavaColors.textPrimary)

                // Ripple
                if !reduceMotion {
                    Circle()
                        .fill(Color.white.opacity(0.18))
                        .scaleEffect(rippleActive ? 2.4 : 0.1)
                        .opacity(rippleActive ? 0 : 0.6)
                        .frame(width: 120, height: 120)
                        .allowsHitTesting(false)
                }

                // Label
                Group {
                    if isLoading {
                        HStack(spacing: Spacing.xs) {
                            ProgressView()
                                .tint(SavaColors.background)
                            Text("Just a sec…")
                        }
                    } else {
                        Text(title)
                    }
                }
                .font(SavaFont.headline)
                .foregroundStyle(SavaColors.background)
            }
            .frame(height: 54)
            .frame(maxWidth: .infinity)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .opacity(effectiveEnabled ? 1 : 0.45)
            .scaleEffect(isPressed ? 0.98 : 1)
        }
        .buttonStyle(.plain)
        .disabled(!effectiveEnabled)
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    guard !isPressed else { return }
                    withAnimation(SavaMotion.tap) { isPressed = true }
                }
                .onEnded { _ in
                    withAnimation(SavaMotion.tap) { isPressed = false }
                }
        )
        .accessibilityLabel(title)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint(isLoading ? "In progress" : "")
    }

    private func triggerRipple() {
        guard !reduceMotion else { return }
        rippleActive = false
        withAnimation(.easeOut(duration: 0.55)) { rippleActive = true }
    }
}
