import SwiftUI

/// The sign-in / registration experience. A single glass surface floating over
/// Sava's liquid field, switching fluidly between the two modes.
struct AuthFlowView: View {
    @EnvironmentObject private var session: SessionStore
    @StateObject private var model = AuthViewModel()
    @FocusState private var focus: Field?

    private enum Field { case email, password, confirm }

    var body: some View {
        ZStack {
            LiquidBackground()

            ScrollView {
                VStack(spacing: Spacing.xl) {
                    header
                    card
                    switcher
                }
                .padding(.horizontal, Spacing.lg)
                .padding(.top, Spacing.xxl)
                .padding(.bottom, Spacing.xxl)
                .frame(maxWidth: 520)
                .frame(maxWidth: .infinity)
            }
            .scrollDismissesKeyboard(.interactively)
            .scrollBounceBehavior(.basedOnSize)
        }
    }

    // MARK: Header

    private var header: some View {
        VStack(spacing: Spacing.md) {
            SavaMark(size: 60)

            VStack(spacing: Spacing.xxs) {
                Text(model.mode.title)
                    .font(SavaFont.title)
                    .foregroundStyle(SavaColors.textPrimary)
                    .contentTransition(.opacity)
                Text(model.mode.subtitle)
                    .font(SavaFont.callout)
                    .foregroundStyle(SavaColors.textSecondary)
                    .multilineTextAlignment(.center)
                    .contentTransition(.opacity)
            }
            .animation(SavaMotion.standard, value: model.mode)
        }
        .padding(.top, Spacing.sm)
    }

    // MARK: Card

    private var card: some View {
        GlassCard {
            VStack(spacing: Spacing.md) {
                SavaTextField(
                    label: "Email",
                    placeholder: "you@example.com",
                    text: $model.email,
                    hasError: model.emailInvalid,
                    textContentType: .username,
                    keyboardType: .emailAddress,
                    submitLabel: .next,
                    onSubmit: { focus = .password }
                )
                .focused($focus, equals: .email)
                .onChange(of: model.email) { _, _ in model.clearErrorsOnEdit() }

                SavaTextField(
                    label: "Password",
                    placeholder: model.mode == .register ? "At least 6 characters" : "Your password",
                    text: $model.password,
                    isSecure: true,
                    hasError: model.passwordInvalid,
                    textContentType: model.mode == .register ? .newPassword : .password,
                    submitLabel: model.mode == .register ? .next : .go,
                    onSubmit: {
                        if model.mode == .register { focus = .confirm }
                        else { submit() }
                    }
                )
                .focused($focus, equals: .password)
                .onChange(of: model.password) { _, _ in model.clearErrorsOnEdit() }

                if model.mode == .register {
                    SavaTextField(
                        label: "Confirm password",
                        placeholder: "Re-enter your password",
                        text: $model.confirmPassword,
                        isSecure: true,
                        hasError: model.passwordInvalid,
                        textContentType: .newPassword,
                        submitLabel: .go,
                        onSubmit: submit
                    )
                    .focused($focus, equals: .confirm)
                    .transition(.asymmetric(
                        insertion: .move(edge: .top).combined(with: .opacity),
                        removal: .opacity
                    ))
                }

                if let message = model.errorMessage {
                    InlineBanner(text: message)
                        .transition(.move(edge: .top).combined(with: .opacity))
                }

                SavaPrimaryButton(
                    title: model.mode.cta,
                    isLoading: model.isSubmitting,
                    isEnabled: model.canSubmit,
                    action: submit
                )
                .padding(.top, Spacing.xxs)
            }
            .padding(Spacing.lg)
            .animation(SavaMotion.standard, value: model.mode)
            .animation(SavaMotion.tap, value: model.errorMessage)
        }
    }

    // MARK: Mode switch

    private var switcher: some View {
        HStack(spacing: Spacing.xxs) {
            Text(model.mode.switchPrompt)
                .font(SavaFont.footnote)
                .foregroundStyle(SavaColors.textSecondary)
            Button(model.mode.switchAction) {
                focus = nil
                model.switchMode()
            }
            .font(SavaFont.subheadline)
            .foregroundStyle(SavaColors.accent)
            .buttonStyle(.pressable)
        }
        .accessibilityElement(children: .combine)
    }

    private func submit() {
        focus = nil
        Task { await model.submit(using: session) }
    }
}
