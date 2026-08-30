import Foundation

/// The backend half of subscriptions.
///
/// Everything here either *asks* what the server thinks, or *offers evidence*
/// for it to check. There is deliberately no method that tells the server what
/// plan the user is on — the closest thing, `clear`, can only ever take access
/// away, which is safe for a client to say.
struct SubscriptionService {
    let client: APIClient

    /// `GET /api/me/subscription` — plan and this period's usage in one call.
    func state() async throws -> SubscriptionState {
        try await client.send(Endpoint(path: "api/me/subscription"))
    }

    /// `POST /api/subscription/verify` — hand Apple's signed transaction to the
    /// server so it can check the signature and grant the entitlement.
    ///
    /// The argument is `Transaction.jsonRepresentation` decoded to a string,
    /// which despite the name is the JWS Apple signed, not our JSON.
    func verify(signedTransaction: String) async throws -> VerifyResponse {
        struct Body: Encodable { let signed_transaction: String }
        return try await client.send(Endpoint.json(
            "api/subscription/verify", method: .post,
            body: Body(signed_transaction: signedTransaction)))
    }

    /// `POST /api/subscription/clear` — StoreKit reports no current entitlement.
    @discardableResult
    func clear(reason: String = "expired") async throws -> SubscriptionState {
        struct Body: Encodable { let reason: String }
        return try await client.send(Endpoint.json(
            "api/subscription/clear", method: .post, body: Body(reason: reason)))
    }

    /// `POST /api/telemetry/subscription` — one paywall or purchase event.
    ///
    /// Fire-and-forget by design: an analytics write must never delay a
    /// purchase, and must never surface an error to somebody who is trying to
    /// give us money.
    func record(event: String, productID: String? = nil, context: String? = nil) async {
        struct Body: Encodable {
            let event: String
            let product_id: String?
            let context: String?
        }
        _ = try? await client.send(Endpoint.json(
            "api/telemetry/subscription", method: .post,
            body: Body(event: event, product_id: productID, context: context)))
    }

    struct VerifyResponse: Decodable {
        let subscription: Entitlement
        let usage: UsageSnapshot
        /// Saves that were being held back and have now been queued, if any.
        let resumed: Resumed?

        struct Resumed: Decodable {
            let scanned: Int?
            let queued: Int?
            let stillLimited: Int?

            enum CodingKeys: String, CodingKey {
                case scanned, queued
                case stillLimited = "still_limited"
            }
        }

        var state: SubscriptionState {
            SubscriptionState(subscription: subscription, usage: usage)
        }
    }
}

/// What each plan includes, as the server describes it.
///
/// Fetched rather than compiled in, so changing an allowance is an environment
/// variable on the backend and not an App Store release. The client ships
/// fallbacks for the offline case; they are the launch values and go stale
/// gracefully rather than blocking the paywall from rendering.
struct PricingCatalogue: Decodable {
    let plans: [Plan]

    struct Plan: Decodable, Identifiable {
        let plan: String
        let displayName: String
        let approxVideos: Int
        let askMessages: Int
        let enhancedAnalysis: Bool
        let priorityProcessing: Bool

        var id: String { plan }

        enum CodingKeys: String, CodingKey {
            case plan
            case displayName = "display_name"
            case approxVideos = "approx_videos"
            case askMessages = "ask_messages"
            case enhancedAnalysis = "enhanced_analysis"
            case priorityProcessing = "priority_processing"
        }

        /// The bullet list shown under a plan. Written in videos and messages,
        /// never in internal units.
        var features: [String] {
            var out = ["Understand \(approxVideos.formatted(.number)) videos a month",
                       "\(askMessages.formatted(.number)) Ask messages"]
            out.append(priorityProcessing ? "Priority processing" : "Standard processing")
            if enhancedAnalysis { out.append("Deep video analysis") }
            return out
        }
    }

    func plan(_ name: String) -> Plan? { plans.first { $0.plan == name } }

    /// Launch values, used until the fetch lands or when it fails.
    static let fallback = PricingCatalogue(plans: [
        Plan(plan: "free", displayName: "Free", approxVideos: 120,
             askMessages: 150, enhancedAnalysis: false, priorityProcessing: false),
        Plan(plan: "pro", displayName: "Sava Pro", approxVideos: 460,
             askMessages: 1500, enhancedAnalysis: true, priorityProcessing: true),
    ])
}

extension SubscriptionService {
    /// `GET /api/pricing`
    func pricing() async throws -> PricingCatalogue {
        try await client.send(Endpoint(path: "api/pricing"))
    }
}
