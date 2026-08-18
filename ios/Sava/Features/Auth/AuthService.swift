import Foundation

/// Talks to the existing Sava auth endpoints. Pure network/data — no UI state.
struct AuthService {
    let client: APIClient

    /// `POST /auth/login` → bearer token.
    func login(email: String, password: String) async throws -> String {
        let endpoint = Endpoint.json(
            "auth/login",
            method: .post,
            body: Credentials(email: email, password: password),
            requiresAuth: false
        )
        let token: TokenResponse = try await client.send(endpoint)
        return token.accessToken
    }

    /// `POST /auth/register`. The web client logs in immediately afterward with
    /// the same credentials, so we mirror that to hand back a usable token.
    func register(email: String, password: String) async throws -> String {
        let endpoint = Endpoint.json(
            "auth/register",
            method: .post,
            body: Credentials(email: email, password: password),
            requiresAuth: false
        )
        let _: RegisterResponse = try await client.send(endpoint)
        return try await login(email: email, password: password)
    }

    /// `GET /auth/me` — validates the current token and returns the user.
    func me() async throws -> User {
        try await client.send(Endpoint(path: "auth/me", method: .get))
    }
}
