import Foundation

/// What the *server* says this account is entitled to.
///
/// Deliberately the only type in the app that answers "is this user Pro?" for
/// anything that costs money. StoreKit tells the app what the user bought;
/// this tells it what Sava will actually do — and when those two disagree, this
/// one is right, because the backend is what refuses the work.
///
/// The distinction matters more than it looks. A jailbroken StoreKit, a
/// receipt-forging tweak, or a plain UserDefaults edit can all make the *phone*
/// believe it is Pro. None of them can make the server run a job it did not
/// authorise. So the app is free to use StoreKit's answer for how the paywall
/// looks, and must use this one for anything that spends.
struct Entitlement: Codable, Equatable {
    let plan: String
    let displayName: String
    let isPro: Bool
    let status: String
    let expiresAt: Date?
    let autoRenew: Bool
    let inBillingRetry: Bool
    let productID: String?
    let limits: Limits

    struct Limits: Codable, Equatable {
        let processingUnits: Int
        /// Roughly how many ordinary videos the allowance understands.
        ///
        /// Computed server-side from the same constant the meter uses, so the
        /// number on the paywall and the number in the usage bar cannot drift.
        /// This is the only form the UI ever shows: nobody should have to read
        /// "1,200 processing units" and work out what it buys them.
        let approxVideos: Int
        let askMessages: Int
        let concurrentJobs: Int
        let enhancedAnalysis: Bool
        let priorityProcessing: Bool

        enum CodingKeys: String, CodingKey {
            case processingUnits = "processing_units"
            case approxVideos = "approx_videos"
            case askMessages = "ask_messages"
            case concurrentJobs = "concurrent_jobs"
            case enhancedAnalysis = "enhanced_analysis"
            case priorityProcessing = "priority_processing"
        }

        /// "460 videos" — the phrase the product uses everywhere.
        var videoPhrase: String {
            "\(approxVideos.formatted(.number)) videos"
        }
    }

    enum CodingKeys: String, CodingKey {
        case plan, status, limits
        case displayName = "display_name"
        case isPro = "is_pro"
        case expiresAt = "expires_at"
        case autoRenew = "auto_renew"
        case inBillingRetry = "in_billing_retry"
        case productID = "product_id"
    }

    /// The state to assume before the server has answered, and whenever it
    /// cannot. Free — never Pro. An offline phone showing Pro would be a
    /// one-line bypass of the entire subscription.
    static let free = Entitlement(
        plan: "free", displayName: "Free", isPro: false, status: "none",
        expiresAt: nil, autoRenew: false, inBillingRetry: false, productID: nil,
        limits: Limits(processingUnits: 300, approxVideos: 120, askMessages: 150,
                       concurrentJobs: 1, enhancedAnalysis: false,
                       priorityProcessing: false))
}

/// This billing period's consumption, as the server counts it.
struct UsageSnapshot: Codable, Equatable {
    let periodStart: Date?
    let resetsAt: Date?
    let processingUnits: Meter
    let askMessages: Meter
    let concurrentJobs: Concurrency

    struct Meter: Codable, Equatable {
        let used: Int
        let limit: Int
        let remaining: Int
        let exhausted: Bool
        /// Present on the understanding meter only. Optional so the Ask meter,
        /// which is genuinely counted in messages, decodes with the same type.
        var approxVideosRemaining: Int? = nil
        var approxVideosLimit: Int? = nil

        /// 0...1, clamped. A limit of zero would otherwise divide by zero and
        /// draw a NaN-width bar, which SwiftUI renders as a blank row.
        var fraction: Double {
            guard limit > 0 else { return 0 }
            return min(1, max(0, Double(used) / Double(limit)))
        }

        /// "18 / 30"
        var display: String { "\(used) / \(limit)" }

        /// What the understanding meter shows: videos left, not units left.
        ///
        /// Falls back to the raw count for the Ask meter, where the unit *is*
        /// the thing being counted and a translation would be a lie.
        var headline: String {
            guard let remainingVideos = approxVideosRemaining,
                  let limitVideos = approxVideosLimit, limitVideos > 0
            else { return display }
            return "\(limitVideos - remainingVideos) / \(limitVideos)"
        }

        enum CodingKeys: String, CodingKey {
            case used, limit, remaining, exhausted
            case approxVideosRemaining = "approx_videos_remaining"
            case approxVideosLimit = "approx_videos_limit"
        }
    }

    struct Concurrency: Codable, Equatable {
        let running: Int
        let limit: Int
    }

    enum CodingKeys: String, CodingKey {
        case periodStart = "period_start"
        case resetsAt = "resets_at"
        case processingUnits = "processing_units"
        case askMessages = "ask_messages"
        case concurrentJobs = "concurrent_jobs"
    }

    static let empty = UsageSnapshot(
        periodStart: nil, resetsAt: nil,
        processingUnits: Meter(used: 0, limit: 300, remaining: 300, exhausted: false,
                               approxVideosRemaining: 120, approxVideosLimit: 120),
        askMessages: Meter(used: 0, limit: 150, remaining: 150, exhausted: false),
        concurrentJobs: Concurrency(running: 0, limit: 1))

    /// "Resets Sep 14", in the user's locale and calendar.
    ///
    /// Formatted on the device rather than sent as a string by the server,
    /// which knows nothing about the reader's locale, calendar or time zone.
    var resetDescription: String? {
        guard let resetsAt else { return nil }
        return "Resets \(resetsAt.formatted(.dateTime.month(.abbreviated).day()))"
    }
}

/// The combined payload from `GET /api/me/subscription`.
struct SubscriptionState: Codable, Equatable {
    let subscription: Entitlement
    let usage: UsageSnapshot

    static let free = SubscriptionState(subscription: .free, usage: .empty)
}
