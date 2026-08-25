import Foundation

/// Account lifecycle: deletion and export.
///
/// Separate from `IntelligenceService` because these are not intelligence
/// operations and because they are the two calls that must keep working when
/// everything else is failing — someone who wants their data out, or wants their
/// account gone, should not be blocked by an unrelated outage.
struct AccountService {
    let client: APIClient

    /// `DELETE /api/account` — irreversible.
    ///
    /// The password is re-checked server-side; sending it here is not a
    /// client-side gate that could be skipped by calling the API directly.
    ///
    /// Content other users have also saved survives; the server reference-counts
    /// shared canonical content and only removes what nobody else holds. See
    /// `api/services/account.py`.
    @discardableResult
    func deleteAccount(password: String, confirm: String = "DELETE") async throws -> Data {
        struct Body: Encodable {
            let password: String
            let confirm: String
        }
        return try await client.send(Endpoint.json(
            "api/account", method: .delete,
            body: Body(password: password, confirm: confirm)))
    }

    /// `GET /api/account/export` — the user's own data as JSON.
    ///
    /// Returned as `Data` rather than a decoded model on purpose: the point of an
    /// export is to hand over everything the server chose to include, and a Swift
    /// struct would silently drop any field the client does not know about.
    func exportData() async throws -> Data {
        try await client.send(Endpoint(path: "api/account/export", method: .get))
    }
}
