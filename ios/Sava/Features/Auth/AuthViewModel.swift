import SwiftUI

/// Drives both sign-in and registration. Owns field state, lightweight
/// client-side validation, submission, and human error messaging.
@MainActor
final class AuthViewModel: ObservableObject {
    enum Mode: Equatable {
        case signIn
        case register

        var title: String { self == .signIn ? "Welcome back" : "Create your account" }
        var subtitle: String {
            self == .signIn
                ? "Sign in to your Sava library."
                : "Start saving and understanding what you watch."
        }
        var cta: String { self == .signIn ? "Sign in" : "Create account" }
        var switchPrompt: String { self == .signIn ? "New to Sava?" : "Already have an account?" }
        var switchAction: String { self == .signIn ? "Create one" : "Sign in" }
    }

    @Published var mode: Mode = .signIn
    @Published var email = ""
    @Published var password = ""
    @Published var confirmPassword = ""

    @Published private(set) var isSubmitting = false
    @Published var errorMessage: String?
    @Published var emailInvalid = false
    @Published var passwordInvalid = false

    var canSubmit: Bool {
        guard !isSubmitting else { return false }
        guard !email.trimmed.isEmpty, !password.isEmpty else { return false }
        if mode == .register && confirmPassword.isEmpty { return false }
        return true
    }

    func switchMode() {
        withAnimation(SavaMotion.standard) {
            mode = (mode == .signIn) ? .register : .signIn
            errorMessage = nil
            emailInvalid = false
            passwordInvalid = false
            confirmPassword = ""
        }
        Haptics.selection()
    }

    func clearErrorsOnEdit() {
        if errorMessage != nil { errorMessage = nil }
        if emailInvalid { emailInvalid = false }
        if passwordInvalid { passwordInvalid = false }
    }

    func submit(using session: SessionStore) async {
        guard validate() else {
            Haptics.error()
            return
        }
        isSubmitting = true
        errorMessage = nil
        defer { isSubmitting = false }

        let cleanEmail = email.trimmed
        do {
            switch mode {
            case .signIn:
                try await session.signIn(email: cleanEmail, password: password)
            case .register:
                try await session.register(email: cleanEmail, password: password)
            }
            // On success the session flips phase and the view is replaced.
        } catch is CancellationError {
            // User navigated away; ignore.
        } catch {
            Haptics.error()
            errorMessage = friendlyMessage(for: error)
        }
    }

    // MARK: - Validation

    private func validate() -> Bool {
        emailInvalid = false
        passwordInvalid = false
        errorMessage = nil

        if !email.trimmed.isValidEmail {
            emailInvalid = true
            errorMessage = "Enter a valid email address."
            return false
        }
        if password.count < 6 {
            passwordInvalid = true
            errorMessage = "Password must be at least 6 characters."
            return false
        }
        if mode == .register, password != confirmPassword {
            passwordInvalid = true
            errorMessage = "Those passwords don't match."
            return false
        }
        return true
    }

    private func friendlyMessage(for error: Error) -> String {
        guard let apiError = error as? APIError else {
            return "Something went wrong. Please try again."
        }
        // In the sign-in context a 401/404 is a credential problem, not an
        // expired session — phrase accordingly.
        switch apiError {
        case .notFound:
            return mode == .signIn ? "We couldn't find an account with that email." : apiError.userMessage
        case .unauthorized:
            return mode == .signIn ? "That password doesn't look right." : apiError.userMessage
        case .conflict, .badRequest:
            return apiError.userMessage
        default:
            return apiError.userMessage
        }
    }
}

private extension String {
    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }

    var isValidEmail: Bool {
        // Deliberately permissive — the backend is the authority. This only
        // catches obvious typos before a round trip.
        let pattern = "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"
        return range(of: pattern, options: .regularExpression) != nil
    }
}
