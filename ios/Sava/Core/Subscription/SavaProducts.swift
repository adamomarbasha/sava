import Foundation

/// The App Store products, in one place.
///
/// **Why these identifiers.** The app's bundle identifier is `com.sava.mobile`,
/// so the products follow it. App Store Connect does not require a product id
/// to share the app's prefix — `app.sava.pro.monthly` would be accepted — but
/// every tool that lists products sorts them alphabetically, and an `app.sava.*`
/// product sitting under a `com.sava.mobile` app is the kind of inconsistency
/// that costs somebody twenty minutes every time they go looking for it.
///
/// These strings must match App Store Connect and `Sava.storekit` exactly, and
/// they must match the server's `SAVA_PRODUCT_PRO_MONTHLY` /
/// `SAVA_PRODUCT_PRO_ANNUAL`. A mismatch is not a crash: StoreKit simply
/// returns no products and the paywall shows nothing to buy, which is why
/// `SubscriptionManager` reports an explicit "no products" state rather than an
/// empty list.
enum SavaProducts {

    static let proMonthly = "com.sava.mobile.pro.monthly"
    static let proAnnual = "com.sava.mobile.pro.annual"

    /// Both products, in the order the paywall shows them.
    static let all: [String] = [proMonthly, proAnnual]

    /// Every product here unlocks the same `pro` entitlement. The only
    /// difference is billing cadence — there is no "Pro Plus", and the server
    /// maps both identifiers to one plan.
    static func isPro(_ productID: String) -> Bool { all.contains(productID) }

    enum Cadence {
        case monthly, annual

        var productID: String {
            switch self {
            case .monthly: return SavaProducts.proMonthly
            case .annual: return SavaProducts.proAnnual
            }
        }

        /// The analytics event name for choosing this option. Matches the
        /// server's allowlist in `routes_subscription.py`.
        var selectionEvent: String {
            switch self {
            case .monthly: return "pro_monthly_selected"
            case .annual: return "pro_annual_selected"
            }
        }
    }

    static func cadence(for productID: String) -> Cadence? {
        switch productID {
        case proMonthly: return .monthly
        case proAnnual: return .annual
        default: return nil
        }
    }
}
