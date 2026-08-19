import Foundation

/// A token provider backed directly by the Keychain, for contexts that run
/// outside the app's `SessionStore` — notably the "Save to Sava" App Intent,
/// which executes without the app UI. Reading the Keychain is thread-safe.
final class KeychainTokenProvider: AuthTokenProviding {
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
        let provider = KeychainTokenProvider()
        let client = APIClient()
        client.tokenProvider = provider
        return (client, provider)
    }

    static var hasStoredToken: Bool {
        KeychainStore().read(.authToken)?.isEmpty == false
    }
}
