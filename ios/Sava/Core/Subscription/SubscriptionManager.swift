import Foundation
import StoreKit

/// Sava Pro, on the client. One object owns every StoreKit interaction.
///
/// ── The division of trust ───────────────────────────────────────────────
///
/// StoreKit decides what the **paywall looks like**: which products exist, what
/// they cost in the user's currency, whether they are already subscribed, and
/// whether an introductory offer applies.
///
/// The **server** decides what the user may actually do. Every expensive
/// operation is authorised backend-side against `entitlement`, which arrives
/// from `/api/me/subscription` and is derived from a transaction Apple signed.
///
/// This split is deliberate and it is the whole security model. `isPro` below
/// is a *display* property. Nothing in the app grants capability from it, from
/// `UserDefaults`, or from any local boolean — because all three are editable on
/// a jailbroken device, and none of them can make the backend run a job it did
/// not authorise.
///
/// ── Why a transaction listener ──────────────────────────────────────────
///
/// Renewals, refunds, family-sharing changes and Ask-to-Buy approvals all
/// arrive *outside* any purchase call — sometimes while the app is backgrounded,
/// sometimes on another device. `Transaction.updates` is the only way to see
/// them. Not listening is how apps end up serving a refunded subscriber for a
/// year, or failing to notice a renewal until the user forces a relaunch.
@MainActor
final class SubscriptionManager: ObservableObject {

    /// Where the purchase flow currently is. Drives every button state on the
    /// paywall — there is no separate `isLoading` flag to fall out of sync.
    enum PurchasePhase: Equatable {
        case idle
        case purchasing(productID: String)
        case restoring
        /// Apple needs a parent's approval (Ask to Buy) or a bank action. Not
        /// a failure — the purchase may complete minutes or days later, which
        /// is exactly what the transaction listener is for.
        case pending
        case succeeded
        case cancelled
        case failed(String)
    }

    /// What the App Store told us about the catalogue.
    enum ProductsPhase: Equatable {
        case loading
        case loaded
        /// StoreKit returned nothing. In production this means the products are
        /// not yet approved in App Store Connect or the identifiers disagree;
        /// in the simulator it usually means no `.storekit` file is selected in
        /// the scheme. Surfaced rather than shown as an empty list, because an
        /// empty paywall looks like a bug to the user and like nothing at all
        /// to us.
        case unavailable(String)
    }

    // MARK: Published state

    /// The authoritative entitlement, from Sava's backend.
    @Published private(set) var entitlement: Entitlement = .free
    /// This period's consumption, from Sava's backend.
    @Published private(set) var usage: UsageSnapshot = .empty

    /// What each plan includes, from the server. Never compiled-in numbers.
    @Published private(set) var catalogue: PricingCatalogue = .fallback
    @Published private(set) var products: [Product] = []
    @Published private(set) var productsPhase: ProductsPhase = .loading
    @Published private(set) var purchasePhase: PurchasePhase = .idle

    /// True when the *server* says so. See the note above: display only.
    var isPro: Bool { entitlement.isPro }

    var monthly: Product? { product(SavaProducts.proMonthly) }
    var annual: Product? { product(SavaProducts.proAnnual) }

    // MARK: Dependencies

    private var service: SubscriptionService?
    private var updatesTask: Task<Void, Never>?

    init() {}

    deinit { updatesTask?.cancel() }

    /// Attach the authenticated client and start listening.
    ///
    /// Called when a session begins. Re-callable: signing out and back in as a
    /// different user must rebind the service and re-resolve the entitlement,
    /// or the second user inherits the first user's plan on screen.
    func start(client: APIClient) {
        service = SubscriptionService(client: client)
        entitlement = .free          // never carry a plan across accounts
        usage = .empty

        if updatesTask == nil {
            updatesTask = Task(priority: .background) { [weak self] in
                await self?.observeTransactions()
            }
        }

        Task {
            await loadProducts()
            await loadCatalogue()
            await refresh()
            await syncEntitlementWithStoreKit()
        }
    }

    /// Forget everything on sign-out. The next account starts from Free.
    func stop() {
        service = nil
        entitlement = .free
        usage = .empty
        purchasePhase = .idle
    }

    // MARK: Catalogue

    func loadProducts() async {
        productsPhase = .loading
        do {
            let fetched = try await Product.products(for: SavaProducts.all)
            // Keep our display order rather than StoreKit's arrival order.
            products = SavaProducts.all.compactMap { id in
                fetched.first { $0.id == id }
            }
            productsPhase = products.isEmpty
                ? .unavailable("The App Store isn't offering Sava Pro right now.")
                : .loaded
        } catch {
            products = []
            productsPhase = .unavailable("Couldn't reach the App Store.")
        }
    }

    private func product(_ id: String) -> Product? {
        products.first { $0.id == id }
    }

    /// Read the plan catalogue. Failure is silent and keeps the fallback: a
    /// paywall that cannot render because a metadata call failed would be a
    /// worse outcome than one showing last release's numbers.
    func loadCatalogue() async {
        guard let service else { return }
        if let fetched = try? await service.pricing(), !fetched.plans.isEmpty {
            catalogue = fetched
        }
    }

    func features(for plan: String) -> [String] {
        catalogue.plan(plan)?.features ?? []
    }

    func approxVideos(for plan: String) -> Int {
        catalogue.plan(plan)?.approxVideos ?? 0
    }

    // MARK: Server entitlement

    /// Re-read the plan and usage from Sava's backend.
    func refresh() async {
        guard let service else { return }
        do {
            let state = try await service.state()
            entitlement = state.subscription
            usage = state.usage
        } catch {
            // Leave the last known values in place. Dropping to Free on a
            // dropped connection would show a paying subscriber a paywall,
            // which is worse than briefly stale numbers — and it changes
            // nothing about what the backend will authorise either way.
        }
    }

    // MARK: Purchase

    func purchase(_ product: Product, context: String? = nil) async {
        purchasePhase = .purchasing(productID: product.id)
        await service?.record(event: "purchase_started", productID: product.id,
                              context: context)

        do {
            switch try await product.purchase() {
            case .success(let verification):
                // `.verified` is StoreKit's *local* check. It is necessary and
                // not sufficient: the server verifies the same JWS against
                // Apple's root before it grants anything.
                switch verification {
                case .verified(let transaction):
                    await grant(transaction, context: context)
                case .unverified(_, let error):
                    purchasePhase = .failed("That purchase couldn't be verified.")
                    await service?.record(event: "purchase_failed",
                                          productID: product.id,
                                          context: error.localizedDescription)
                }

            case .userCancelled:
                purchasePhase = .cancelled
                await service?.record(event: "purchase_cancelled",
                                      productID: product.id, context: context)

            case .pending:
                purchasePhase = .pending
                await service?.record(event: "purchase_failed",
                                      productID: product.id, context: "pending")

            @unknown default:
                purchasePhase = .idle
            }
        } catch {
            purchasePhase = .failed(Self.message(for: error))
            await service?.record(event: "purchase_failed", productID: product.id,
                                  context: context)
        }
    }

    /// Verify server-side, finish the transaction, refresh state.
    private func grant(_ transaction: StoreKit.Transaction,
                       context: String?, isRestore: Bool = false) async {
        guard let service else {
            purchasePhase = .failed("Sign in to Sava to finish setting up Pro.")
            return
        }

        let jws = transaction.jsonRepresentation
        let signed = String(decoding: jws, as: UTF8.self)

        do {
            let response = try await service.verify(signedTransaction: signed)
            entitlement = response.subscription
            usage = response.usage

            // Only now. `finish()` tells Apple we have delivered the goods, and
            // doing it before the server has recorded the entitlement is how a
            // purchase gets taken and never honoured: the transaction would not
            // be replayed on the next launch, so there would be nothing left to
            // recover from.
            await transaction.finish()

            purchasePhase = .succeeded
            await service.record(event: isRestore ? "purchase_restored"
                                                  : "purchase_completed",
                                 productID: transaction.productID, context: context)
        } catch {
            // Left unfinished on purpose, so `Transaction.updates` and
            // `currentEntitlements` will offer it again and it can be recovered.
            purchasePhase = .failed(
                "Your purchase went through, but Sava couldn't confirm it yet. "
                + "It'll finish automatically — or tap Restore Purchases.")
            await service.record(event: "purchase_failed",
                                 productID: transaction.productID,
                                 context: "verify_failed")
        }
    }

    // MARK: Restore

    /// Re-present whatever the Apple ID already owns.
    ///
    /// `AppStore.sync()` is deliberately *not* called first. It forces an
    /// authentication prompt, and in the overwhelmingly common case —
    /// reinstalling, or a new device — `currentEntitlements` already has the
    /// transaction without asking the user for anything.
    func restore() async {
        purchasePhase = .restoring

        var found = false
        for await result in StoreKit.Transaction.currentEntitlements {
            guard case .verified(let transaction) = result,
                  SavaProducts.isPro(transaction.productID) else { continue }
            found = true
            await grant(transaction, context: "restore", isRestore: true)
        }

        if !found {
            // Nothing local. Now it is worth the prompt.
            do {
                try await AppStore.sync()
                for await result in StoreKit.Transaction.currentEntitlements {
                    guard case .verified(let transaction) = result,
                          SavaProducts.isPro(transaction.productID) else { continue }
                    found = true
                    await grant(transaction, context: "restore", isRestore: true)
                }
            } catch {
                purchasePhase = .failed("Couldn't reach the App Store.")
                return
            }
        }

        if !found {
            purchasePhase = .failed("No previous Sava Pro purchase found on this Apple ID.")
            await syncEntitlementWithStoreKit()
        }
    }

    // MARK: Ongoing transactions

    /// Renewals, revocations, refunds, Ask-to-Buy approvals.
    private func observeTransactions() async {
        for await result in StoreKit.Transaction.updates {
            guard case .verified(let transaction) = result else { continue }
            guard SavaProducts.isPro(transaction.productID) else { continue }

            if transaction.revocationDate != nil {
                // Refunded or family sharing withdrawn. Tell the server so it
                // stops authorising expensive work, then finish.
                _ = try? await service?.clear(reason: "revoked")
                await refresh()
                await transaction.finish()
            } else {
                await grant(transaction, context: "storekit_update")
            }
        }
    }

    /// Reconcile: if StoreKit has no entitlement but the server thinks we are
    /// Pro, tell the server it has lapsed.
    ///
    /// Safe in exactly one direction. A client saying "I have nothing" can only
    /// remove access, so a malicious client gains nothing by lying; a client
    /// saying "I have Pro" is ignored, because only a signed transaction grants.
    private func syncEntitlementWithStoreKit() async {
        var hasEntitlement = false
        for await result in StoreKit.Transaction.currentEntitlements {
            if case .verified(let transaction) = result,
               SavaProducts.isPro(transaction.productID),
               transaction.revocationDate == nil {
                hasEntitlement = true
                break
            }
        }

        if !hasEntitlement && entitlement.isPro {
            _ = try? await service?.clear(reason: "expired")
            await refresh()
        }
    }

    // MARK: Manage subscription

    /// Apple's own management sheet. Required: an app may not build its own
    /// cancellation UI, and this is the only place the user can actually cancel.
    func manageSubscriptions() -> URL? {
        URL(string: "https://apps.apple.com/account/subscriptions")
    }

    // MARK: Display helpers

    /// What the annual plan saves against twelve months of monthly, derived
    /// from the two StoreKit prices.
    ///
    /// Computed rather than hardcoded because it has to be right in every
    /// storefront. Apple's price tiers are not uniform conversions — the ratio
    /// between the $9.99 and $79.99 tiers differs by country — so a baked-in
    /// "Save 33%" would be a false claim in most of the world, and a false
    /// price claim is an App Review rejection as well as a lie.
    var annualSavingsPercent: Int? {
        guard let monthly, let annual else { return nil }
        let twelveMonths = monthly.price * 12
        guard twelveMonths > 0, annual.price < twelveMonths else { return nil }
        let saved = (twelveMonths - annual.price) / twelveMonths
        let percent = Int((saved as NSDecimalNumber).doubleValue * 100)
        return percent > 0 ? percent : nil
    }

    /// The annual price expressed per month, in the storefront's currency —
    /// "~$6.67/month". Formatted by StoreKit so currency, symbol placement and
    /// separators are right everywhere.
    var annualPerMonth: String? {
        guard let annual else { return nil }
        let perMonth = annual.price / 12
        return annual.priceFormatStyle.format(perMonth)
    }

    func localizedPrice(for cadence: SavaProducts.Cadence) -> String? {
        switch cadence {
        case .monthly: return monthly?.displayPrice
        case .annual: return annual?.displayPrice
        }
    }

    func clearPurchasePhase() { purchasePhase = .idle }

    /// Record a paywall event.
    ///
    /// Goes through the *session's* client so the request carries the auth
    /// token. Building a fresh `APIClient()` here would produce one with no
    /// token provider, and every event would be a silent 401 — telemetry that
    /// looks wired up and records nothing is worse than none.
    func record(event: String, productID: String? = nil, context: String?) async {
        await service?.record(event: event, productID: productID, context: context)
    }

    private static func message(for error: Error) -> String {
        if let storeError = error as? StoreKitError {
            switch storeError {
            case .networkError: return "Couldn't reach the App Store."
            case .userCancelled: return "Purchase cancelled."
            case .notAvailableInStorefront:
                return "Sava Pro isn't available in your region yet."
            default: break
            }
        }
        return "That purchase didn't go through. Nothing was charged."
    }
}
