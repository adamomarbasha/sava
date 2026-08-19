import SwiftUI

/// Owns authentication state for the whole app and is the single source of
/// truth for "are we signed in". Persists the token in the Keychain, restores
/// sessions on launch, and injects the token into the API client.
@MainActor
final class SessionStore: ObservableObject, AuthTokenProviding {

    enum Phase: Equatable {
        case restoring     // checking keychain on launch
        case signedOut
        case signedIn(User)
    }

    @Published private(set) var phase: Phase = .restoring

    private let keychain = KeychainStore()

    /// The shared, authenticated API client. Feature services reuse this so
    /// token injection and 401 handling live in one place.
    let api: APIClient
    private let auth: AuthService

    init() {
        let client = APIClient()
        self.api = client
        self.auth = AuthService(client: client)
        client.tokenProvider = self
    }

    // MARK: AuthTokenProviding

    /// The Keychain is the source of truth for the token and is thread-safe,
    /// so this is safe to read from the networking layer on any thread.
    nonisolated var currentToken: String? {
        KeychainStore().read(.authToken)
    }

    func handleUnauthorized() async {
        // A 401 on any authenticated call means the token expired (30-min TTL,
        // no refresh). Fall back to signed-out cleanly.
        signOut()
    }

    // MARK: Session lifecycle

    /// Called once on launch to restore a prior session.
    func restore() async {
        #if DEBUG
        // Dev-only: seed a token from the launch environment so the app can be
        // driven against a real account during development/QA. Never compiled
        // into Release builds.
        if let devToken = ProcessInfo.processInfo.environment["SAVA_DEV_TOKEN"], !devToken.isEmpty {
            keychain.save(devToken, for: .authToken)
        }
        #endif
        guard let token = keychain.read(.authToken), !token.isEmpty else {
            phase = .signedOut
            return
        }
        do {
            let user = try await auth.me()
            phase = .signedIn(user)
        } catch {
            // Invalid/expired token — clear it and present sign in.
            keychain.delete(.authToken)
            phase = .signedOut
        }
    }

    func signIn(email: String, password: String) async throws {
        let token = try await auth.login(email: email, password: password)
        try await finishSignIn(with: token)
    }

    func register(email: String, password: String) async throws {
        let token = try await auth.register(email: email, password: password)
        try await finishSignIn(with: token)
    }

    func signOut() {
        keychain.delete(.authToken)
        withAnimation(SavaMotion.smooth) { phase = .signedOut }
    }

    // MARK: - Helpers

    private func finishSignIn(with token: String) async throws {
        keychain.save(token, for: .authToken)
        do {
            let user = try await auth.me()
            Haptics.success()
            withAnimation(SavaMotion.smooth) { phase = .signedIn(user) }
        } catch {
            // If /auth/me fails right after obtaining a token, don't leave a
            // half-authenticated state around.
            keychain.delete(.authToken)
            throw error
        }
    }
}
