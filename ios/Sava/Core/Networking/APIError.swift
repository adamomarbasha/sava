import Foundation

/// A single, typed error surface for all networking. Carries a developer-facing
/// detail (for logging) and a `userMessage` suitable for display.
enum APIError: Error {
    case offline
    case timedOut
    case unauthorized              // 401 — token missing/expired
    case notFound(String?)         // 404
    case conflict(String?)         // 409
    /// 402 — this needs Sava Pro, or the plan's allowance is spent.
    ///
    /// Its own case rather than folding into `server`, because it is the one
    /// error with a *remedy the app can offer*. Every other failure ends in
    /// "try again"; this one ends in a paywall, and only a distinct type lets a
    /// call site tell the difference without string-matching a message.
    case upgradeRequired(String?, capability: String?)
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
        case .upgradeRequired(let detail, _):
            return detail ?? "You've reached this month's limit."
        case .badRequest(let detail):
            return detail ?? "Something about that request wasn't right."
        case .server:
            return "Sava is having a moment. Please try again shortly."
        case .decoding, .transport, .invalidResponse:
            return "Something went wrong. Please try again."
        }
    }

    /// True when showing the paywall is the right response.
    var needsUpgrade: Bool {
        if case .upgradeRequired = self { return true }
        return false
    }

    /// Which capability ran out — "ask", "processing", "pro". Used as the
    /// paywall's `context` so we can see which ceiling actually drives upgrades.
    var upgradeCapability: String? {
        if case .upgradeRequired(_, let capability) = self { return capability }
        return nil
    }
}
