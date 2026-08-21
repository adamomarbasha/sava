import SwiftUI

/// The signed-out entry point.
///
/// Built from exactly the same material as the signed-in app: the same ground,
/// the same type, the same field and the same button. There is no gradient hero,
/// no feature list, no card floating on a backdrop, and no paragraph explaining
/// what AI is — none of which would appear again after sign-in, and all of which
/// would make the first screen belong to a different product than the second.
///
/// A wordmark, a two-line promise, two fields, one action.
struct AuthFlowView: View {
    @EnvironmentObject private var session: SessionStore
    @StateObject private var model = AuthViewModel()
    @FocusState private var field: Field?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false

    private enum Field { case email, password, confirm }

    var body: some View {
        ZStack {
            SavaColor.ground.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    masthead
                        .padding(.top, 56)
                        .padding(.bottom, 56)

                    fields

                    if let error = model.errorMessage {
                        errorLine(error)
                    }

                    action
                        .padding(.top, Space.l)

                    modeToggle
                        .padding(.top, Space.xl)

                    Spacer(minLength: Space.xxl)
                }
                .screenPadding()
                .frame(maxWidth: 460)
                .frame(maxWidth: .infinity)
            }
            .scrollDismissesKeyboard(.interactively)
            .scrollBounceBehavior(.basedOnSize)
        }
        .opacity(appeared ? 1 : 0)
        .onAppear {
            withAnimation(Motion.respecting(Motion.standard, reduceMotion)) { appeared = true }
        }
        .animation(Motion.respecting(Motion.gentle, reduceMotion), value: model.mode)
        .animation(Motion.respecting(Motion.gentle, reduceMotion), value: model.errorMessage)
    }

    // MARK: Masthead

    private var masthead: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            SavaLockup(markSize: 46)

            VStack(alignment: .leading, spacing: 1) {
                Text("Save what matters.")
                Text("Find it again.")
            }
            .font(SavaType.lede)
            .foregroundStyle(SavaColor.secondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Sava. Save what matters. Find it again.")
        .accessibilityAddTraits(.isHeader)
    }

    // MARK: Fields

    private var fields: some View {
        VStack(spacing: Space.m) {
            SavaField(placeholder: "Email", text: $model.email,
                      keyboard: .emailAddress, contentType: .username,
                      submitLabel: .next, isInvalid: model.emailInvalid) {
                field = .password
            }
            .focused($field, equals: .email)
            .onChange(of: model.email) { _, _ in model.clearErrorsOnEdit() }

            SavaField(placeholder: "Password", text: $model.password,
                      isSecure: true,
                      contentType: model.mode == .signIn ? .password : .newPassword,
                      submitLabel: model.mode == .signIn ? .go : .next,
                      isInvalid: model.passwordInvalid) {
                if model.mode == .register { field = .confirm } else { submit() }
            }
            .focused($field, equals: .password)
            .onChange(of: model.password) { _, _ in model.clearErrorsOnEdit() }

            if model.mode == .register {
                SavaField(placeholder: "Confirm password", text: $model.confirmPassword,
                          isSecure: true, contentType: .newPassword,
                          submitLabel: .go, isInvalid: model.passwordInvalid) {
                    submit()
                }
                .focused($field, equals: .confirm)
                .onChange(of: model.confirmPassword) { _, _ in model.clearErrorsOnEdit() }
                .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
    }

    private func errorLine(_ message: String) -> some View {
        Text(message)
            .font(SavaType.callout)
            .foregroundStyle(SavaColor.danger)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, Space.m)
            .transition(.opacity)
            .accessibilityAddTraits(.isStaticText)
    }

    private var action: some View {
        SavaButton(title: model.mode.cta,
                   isLoading: model.isBusy,
                   isEnabled: model.canSubmit,
                   action: submit)
    }

    private var modeToggle: some View {
        HStack(spacing: Space.xs) {
            Text(model.mode.switchPrompt)
                .foregroundStyle(SavaColor.tertiary)
            Button(model.mode.switchAction) {
                Haptics.select()
                model.switchMode()
                field = .email
            }
            .foregroundStyle(SavaColor.accent)
        }
        .font(SavaType.callout)
        .frame(maxWidth: .infinity)
    }

    private func submit() {
        field = nil
        Task { await model.submit(using: session) }
    }
}
