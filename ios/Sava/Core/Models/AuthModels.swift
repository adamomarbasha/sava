import Foundation

/// Response from `POST /auth/login`.
struct TokenResponse: Decodable {
    let accessToken: String
    let tokenType: String

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case tokenType = "token_type"
    }
}

/// Response from `GET /auth/me`.
struct User: Decodable, Identifiable, Equatable {
    let id: Int
    let email: String
    let createdAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, email
        case createdAt = "created_at"
    }
}

/// Response from `POST /auth/register`.
struct RegisterResponse: Decodable {
    let id: Int
    let email: String
    let message: String
}

// MARK: - Request bodies

struct Credentials: Encodable {
    let email: String
    let password: String
}
