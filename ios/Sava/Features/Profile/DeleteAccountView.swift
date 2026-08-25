import SwiftUI

/// Permanent account deletion.
///
/// Required by App Store Review Guideline 5.1.1(v): an app that lets you create
/// an account has to let you delete it from inside the app, without emailing
/// anyone. It is one of the most consistently enforced rules Apple has.
///
/// Written to be honest rather than frictionless. Deletion is irreversible, so
/// this screen states plainly what goes, asks for the password, and requires the
/// word DELETE typed out. The password is not theatre: the session token lives
/// in the Keychain and lasts thirty days, so an unlocked phone is otherwise
/// enough to erase somebody's library.
struct DeleteAccountView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss

    @State private var password = ""
    @State private var confirmation = ""
    @State private var working = false
    @State private var errorMessage: String?

    private var canDelete: Bool {
        !password.isEmpty
            && confirmation.trimmingCharacters(in: .whitespaces).uppercased() == "DELETE"
            && !working
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                explanation
                fields
                if let errorMessage { failure(errorMessage) }
                deleteButton
            }
            .screenPadding()
            .padding(.top, Space.l)
            .padding(.bottom, Space.xxl)
        }
        .background(SavaColor.ground)
        .navigationTitle("Delete account")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button("Cancel") { dismiss() }
            }
        }
        .tint(SavaColor.primary)
    }

    // MARK: Copy

    private var explanation: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            Text("This cannot be undone")
                .font(SavaType.title)
                .foregroundStyle(SavaColor.primary)

            Text("Deleting your account removes your saves, collections, "
                 + "conversations and everything Sava worked out about them.")
                .font(SavaType.body)
                .foregroundStyle(SavaColor.secondary)
                .fixedSize(horizontal: false, vertical: true)

            // Stated because it is true and because a vague privacy claim is
            // worse than a precise one. Sava understands a video once and shares
            // that work between everyone who saved it; deleting an account
            // removes that person's link to it, and the shared analysis only
            // goes when the last person holding it does.
            Text("Videos other people have also saved stay in their libraries. "
                 + "Anything nobody else saved is deleted with your account.")
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var fields: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            VStack(alignment: .leading, spacing: Space.xs) {
                SectionHeader(text: "Password")
                SavaField(placeholder: "Your password", text: $password,
                          isSecure: true, contentType: .password)
            }

            VStack(alignment: .leading, spacing: Space.xs) {
                SectionHeader(text: "Type DELETE to confirm")
                SavaField(placeholder: "DELETE", text: $confirmation,
                          submitLabel: .done)
            }
        }
    }

    private func failure(_ message: String) -> some View {
        Text(message)
            .font(SavaType.callout)
            .foregroundStyle(SavaColor.danger)
            .fixedSize(horizontal: false, vertical: true)
            .transition(.opacity)
    }

    private var deleteButton: some View {
        SavaButton(title: "Delete my account",
                   isLoading: working,
                   isEnabled: canDelete,
                   role: .destructive) {
            Task { await performDelete() }
        }
    }

    // MARK: Action

    private func performDelete() async {
        guard canDelete else { return }
        working = true
        errorMessage = nil
        defer { working = false }

        do {
            try await AccountService(client: session.api)
                .deleteAccount(password: password, confirm: "DELETE")
            Haptics.press()
            // Sign out locally regardless of anything else: the account is gone,
            // so the stored token is now a token for nothing.
            session.signOut()
        } catch {
            errorMessage = (error as? APIError)?.userMessage
                ?? "Couldn't delete your account. Check your password and try again."
        }
    }
}
