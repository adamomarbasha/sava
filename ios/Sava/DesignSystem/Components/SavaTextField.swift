import SwiftUI

/// A labeled input with a focus ring that lights up in the Signal accent.
/// Supports secure entry with a reveal toggle, and an error appearance.
struct SavaTextField: View {
    let label: String
    let placeholder: String
    @Binding var text: String

    var isSecure: Bool = false
    var hasError: Bool = false
    var textContentType: UITextContentType? = nil
    var keyboardType: UIKeyboardType = .default
    var submitLabel: SubmitLabel = .next
    var onSubmit: () -> Void = {}

    @FocusState private var focused: Bool
    @State private var revealed = false

    private var showingSecure: Bool { isSecure && !revealed }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text(label)
                .font(SavaFont.caption)
                .foregroundStyle(SavaColors.textSecondary)
                .textCase(.uppercase)
                .tracking(0.6)

            HStack(spacing: Spacing.sm) {
                Group {
                    if showingSecure {
                        SecureField(placeholder, text: $text)
                    } else {
                        TextField(placeholder, text: $text)
                    }
                }
                .font(SavaFont.body)
                .foregroundStyle(SavaColors.textPrimary)
                .tint(SavaColors.accent)
                .focused($focused)
                .textContentType(textContentType)
                .keyboardType(keyboardType)
                .textInputAutocapitalization(keyboardType == .emailAddress ? .never : .sentences)
                .autocorrectionDisabled(isSecure || keyboardType == .emailAddress)
                .submitLabel(submitLabel)
                .onSubmit(onSubmit)

                if isSecure {
                    Button {
                        revealed.toggle()
                        Haptics.selection()
                    } label: {
                        Image(systemName: revealed ? "eye.slash" : "eye")
                            .font(.system(size: 16, weight: .medium))
                            .foregroundStyle(SavaColors.textTertiary)
                            .contentTransition(.symbolEffect(.replace))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(revealed ? "Hide password" : "Show password")
                }
            }
            .padding(.horizontal, Spacing.md)
            .frame(height: 54)
            .background(
                RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .fill(SavaColors.surface)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .strokeBorder(borderColor, lineWidth: focused || hasError ? 2 : 1)
            )
            .animation(SavaMotion.tap, value: focused)
            .animation(SavaMotion.tap, value: hasError)
        }
    }

    private var borderColor: Color {
        if hasError { return SavaColors.danger }
        if focused { return SavaColors.accent }
        return SavaColors.separator
    }
}
