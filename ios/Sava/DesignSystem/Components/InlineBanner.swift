import SwiftUI

/// A compact, non-modal message row for inline errors and notices.
struct InlineBanner: View {
    enum Kind { case error, info }

    let text: String
    var kind: Kind = .error

    private var tint: Color { kind == .error ? SavaColors.danger : SavaColors.accent }
    private var fill: Color { kind == .error ? SavaColors.dangerSoft : SavaColors.accentSoft }
    private var icon: String { kind == .error ? "exclamationmark.triangle.fill" : "info.circle.fill" }

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.xs) {
            Image(systemName: icon)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(tint)
            Text(text)
                .font(SavaFont.footnote)
                .foregroundStyle(SavaColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.vertical, Spacing.sm)
        .padding(.horizontal, Spacing.sm)
        .background(
            RoundedRectangle(cornerRadius: Radius.sm, style: .continuous).fill(fill)
        )
        .accessibilityElement(children: .combine)
    }
}
