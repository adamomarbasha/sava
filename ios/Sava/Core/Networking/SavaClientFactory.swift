import Foundation

/// A token provider backed directly by the Keychain, for contexts that run
/// outside the app's `SessionStore` — notably the "Save to Sava" App Intent,
/// which executes without the app UI. Reading the Keychain is thread-safe.
final class KeychainTokenProvider: AuthTokenProviding {
    /// Shared, process-lifetime instance.
    ///
    /// `APIClient.tokenProvider` is deliberately `weak` so that SessionStore —
    /// which owns its client and acts as its own provider — does not create a
    /// retain cycle. That makes lifetime the caller's problem, and a headless
    /// caller that let its provider go out of scope silently lost the
    /// Authorization header and got 403s. A shared instance is stateless (it
    /// only reads the Keychain) and can never be deallocated, so headless
    /// contexts are safe by construction.
    static let shared = KeychainTokenProvider()

    private let keychain = KeychainStore()

    var currentToken: String? { keychain.read(.authToken) }

    func handleUnauthorized() async {
        // No UI here — the intent surfaces a "sign in" message to the user.
    }
}

/// Builds API clients. The app uses `SessionStore`'s client; intents and other
/// headless entry points use this factory so token injection stays centralized.
enum SavaClient {
    static func authenticated() -> (client: APIClient, provider: AuthTokenProviding) {
        // Must be the shared instance: `tokenProvider` is weak, so a
        // locally-created provider would deallocate the moment the caller
        // discarded it and every request would go out unauthenticated.
        let provider = KeychainTokenProvider.shared
        let client = APIClient()
        client.tokenProvider = provider
        return (client, provider)
    }

    static var hasStoredToken: Bool {
        KeychainStore().read(.authToken)?.isEmpty == false
    }
}
