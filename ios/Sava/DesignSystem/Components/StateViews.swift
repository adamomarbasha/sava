import SwiftUI

/// A calm, teaching empty/error state: a soft glyph, a title, a line of copy,
/// and an optional action. Reused across the app for consistency.
struct StatusView: View {
    let icon: String
    let title: String
    let message: String
    var tint: Color = SavaColors.accent
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: Spacing.md) {
            ZStack {
                Circle()
                    .fill(tint.opacity(0.12))
                    .frame(width: 76, height: 76)
                Image(systemName: icon)
                    .font(.system(size: 30, weight: .semibold))
                    .foregroundStyle(tint)
            }

            VStack(spacing: Spacing.xs) {
                Text(title)
                    .font(SavaFont.title2)
                    .foregroundStyle(SavaColors.textPrimary)
                    .multilineTextAlignment(.center)
                Text(message)
                    .font(SavaFont.callout)
                    .foregroundStyle(SavaColors.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, Spacing.lg)

            if let actionTitle, let action {
                Button(action: action) {
                    Text(actionTitle)
                        .font(SavaFont.headline)
                        .foregroundStyle(SavaColors.background)
                        .padding(.horizontal, Spacing.lg)
                        .frame(height: 48)
                        .background(SavaColors.textPrimary, in: Capsule())
                }
                .buttonStyle(.pressable)
                .padding(.top, Spacing.xs)
            }
        }
        .frame(maxWidth: 380)
        .padding(Spacing.xl)
    }
}
