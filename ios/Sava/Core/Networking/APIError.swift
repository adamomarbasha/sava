import Foundation

/// A single, typed error surface for all networking. Carries a developer-facing
/// detail (for logging) and a `userMessage` suitable for display.
enum APIError: Error {
    case offline
    case timedOut
    case unauthorized              // 401 — token missing/expired
    case notFound(String?)         // 404
    case conflict(String?)         // 409
    case badRequest(String?)       // 400 / 422
    case server(status: Int, detail: String?)
    case decoding(underlying: Error)
    case transport(underlying: Error)
    case invalidResponse

    /// Human-readable, non-technical message for the UI.
    var userMessage: String {
        switch self {
        case .offline:
            return "You appear to be offline. Check your connection and try again."
        case .timedOut:
            return "That took too long. Please try again."
        case .unauthorized:
            return "Your session expired. Please sign in again."
        case .notFound(let detail):
            return detail ?? "We couldn't find that."
        case .conflict(let detail):
            return detail ?? "That already exists."
        case .badRequest(let detail):
            return detail ?? "Something about that request wasn't right."
        case .server:
            return "Sava is having a moment. Please try again shortly."
        case .decoding, .transport, .invalidResponse:
            return "Something went wrong. Please try again."
        }
    }
}
