import StoreKit
import SwiftUI

/// Sava Pro.
///
/// ── The design argument ─────────────────────────────────────────────────
///
/// Not a three-column pricing table. Columns are a *comparison* device built
/// for a desktop browser where the reader's eye can cross them; on a 390-point
/// phone they become three cramped strips of six-point text, and the thing you
/// are trying to make legible — what you get — is the first casualty.
///
/// So the plans are stacked rows, the way a considered app presents a choice.
/// Each row states its own price and what it includes; the differences read
/// vertically, one line at a time, at full size.
///
/// What the screen does *not* do, and why:
///
///   * **No countdown, no "limited offer", no fake scarcity.** There is no
///     deadline, so inventing one is a lie the user finds out about the moment
///     they reopen the screen.
///   * **No pre-ticked upsell, no disguised dismiss.** The close control is a
///     plain X at full tap size in the usual corner.
///   * **Free is a listed choice, not a punishment.** It is shown first, with
///     its real allowance, because a paywall that hides what you already have
///     is trying to confuse you.
///   * **No purple AI gradient, no glass.** Sava's palette is ink and citron;
///     the accent appears on exactly one thing — the action.
///
/// Prices come from StoreKit and are never hardcoded. Savings are derived from
/// the two live prices rather than baked in, because Apple's price tiers are not
/// uniform conversions and "Save 33%" is false in most storefronts.
struct PaywallView: View {
    @EnvironmentObject private var subscriptions: SubscriptionManager
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL

    /// Where this was opened from. Recorded with the paywall event so we can
    /// tell an intentional visit from a quota interruption.
    var context: String = "profile"
    /// Shown above the hero when the user arrived by hitting a ceiling.
    var reason: String? = nil

    @State private var selection: SavaProducts.Cadence = .annual

    /// True only while StoreKit might still answer, so a blank price shows a
    /// placeholder rather than an indefinite one.
    private var catalogueIsLoading: Bool {
        if case .loading = subscriptions.productsPhase { return true }
        return false
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            SavaColor.ground.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    // Understand, then choose.
                    //
                    // The previous order put Monthly and Annual above the
                    // explanation, which asked the user to pick a price for
                    // something they had not been told the shape of yet. Now:
                    // what Pro changes, then Free against Pro in full, and only
                    // then the two prices.
                    if let reason { limitNotice(reason) }
                    hero
                    whatProChanges
                    comparison
                    plans
                    disclosure
                    Color.clear.frame(height: 132)   // room for the pinned action
                }
                .screenPadding()
                .padding(.top, Space.l)
            }
            .devScrollAnchor()

            actionBar
        }
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { dismiss() } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(SavaColor.secondary)
                        .frame(width: 44, height: 44)   // full tap target
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("Close")
            }
        }
        .tint(SavaColor.primary)
        .task {
            await subscriptions.record(event: "paywall_viewed", context: context)
            if case .unavailable = subscriptions.productsPhase {
                await subscriptions.loadProducts()
            }
        }
        .onChange(of: subscriptions.purchasePhase) { _, phase in
            if phase == .succeeded {
                Haptics.press()
                dismiss()
            }
        }
    }

    // MARK: Hero

    private func limitNotice(_ text: String) -> some View {
        HStack(spacing: Space.s) {
            Image(systemName: "gauge.with.dots.needle.33percent")
                .font(.system(size: 13, weight: .semibold))
            Text(text)
                .font(SavaType.callout)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .foregroundStyle(SavaColor.secondary)
        .padding(Space.m)
        .background(SavaColor.fill,
                    in: RoundedRectangle(cornerRadius: Radius.control, style: .continuous))
    }

    private var hero: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            Text("Sava Pro")
                .font(SavaType.display)
                .tracking(Tracking.tight)
                .foregroundStyle(SavaColor.primary)

            // Serif: this is Sava speaking, not the interface labelling itself.
            Text("Keep saving everything. Understand far more of it.")
                .font(SavaType.proseTitle)
                .foregroundStyle(SavaColor.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if !subscriptions.isPro {
                Text("You're on Free")
                    .font(SavaType.caption)
                    .foregroundStyle(SavaColor.tertiary)
                    .padding(.horizontal, Space.m)
                    .padding(.vertical, 5)
                    .background(SavaColor.fill, in: Capsule())
            }
        }
        .padding(.bottom, Space.xs)
    }

    /// The three things a subscription actually changes, before any table.
    ///
    /// Deliberately not a feature list: it is the *shape* of the upgrade —
    /// nothing about saving changes, the intelligence allowances grow, and one
    /// capability appears. Someone who reads only this should already be able
    /// to decide.
    private var whatProChanges: some View {
        let free = plan("free"), pro = plan("pro")
        return VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "What Pro changes")
            changeLine("arrow.up.right.circle",
                       "\(free.approxVideos.formatted(.number)) → "
                       + "\(pro.approxVideos.formatted(.number)) videos understood a month")
            changeLine("bubble.left.and.text.bubble.right",
                       "\(free.askMessages.formatted(.number)) → "
                       + "\(pro.askMessages.formatted(.number)) Ask messages")
            changeLine("wand.and.sparkles",
                       "Deep video analysis, and your saves jump the queue")
            Text("Saving stays unlimited on both.")
                .font(SavaType.meta)
                .foregroundStyle(SavaColor.tertiary)
        }
    }

    private func changeLine(_ symbol: String, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.m) {
            Image(systemName: symbol)
                .font(.system(size: 13, weight: .medium))
                // `accentTint`, not `accent`: at this size the fill token
                // inverts to ink on paper and the citron family would vanish
                // from the screen exactly where it identifies the paid plan.
                .foregroundStyle(SavaColor.accentTint)
                .frame(width: 18)
            Text(text)
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.primary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }

    private var comparison: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "Free and Pro, side by side")
            PlanComparison(free: plan("free"), pro: plan("pro"))
        }
    }

    /// The catalogue's entry for a plan, or the launch defaults. Never nil, so
    /// no surface has to render a half-built comparison.
    private func plan(_ name: String) -> PricingCatalogue.Plan {
        subscriptions.catalogue.plan(name)
            ?? PricingCatalogue.fallback.plan(name)!
    }

    // MARK: Plans

    /// Three compact rows, then one feature list.
    ///
    /// The first version gave every plan its own feature list, which meant
    /// Monthly and Annual printed the same four bullets one after the other —
    /// and pushed Annual, the default selection, below the fold on a 6.3"
    /// screen. A paywall whose recommended option is off-screen when it opens
    /// is a broken paywall.
    ///
    /// Monthly and Annual buy *identical* things; the only difference is
    /// cadence and price. So the shared list is stated once, and the rows carry
    /// only what actually differs. All three fit above the action bar.
    /// Monthly and Annual — and *only* when the App Store has actually priced
    /// them.
    ///
    /// When the catalogue is unavailable the selector is replaced outright
    /// rather than rendered with blank price slots. Two selectable rows whose
    /// prices are missing look like a half-loaded screen and invite the user to
    /// tap a button that cannot work; a sentence saying purchasing is
    /// unavailable, with a retry, is the truth and is actionable. The
    /// comparison above stays either way, so the screen still explains what
    /// Sava Pro is even when it cannot sell it.
    private var plans: some View {
        VStack(spacing: Space.m) {
            if case .unavailable(let why) = subscriptions.productsPhase {
                unavailable(why)
            } else {
                SectionHeader(text: "Choose a plan")
                    .frame(maxWidth: .infinity, alignment: .leading)

                PlanRow(
                    title: "Monthly",
                    price: subscriptions.localizedPrice(for: .monthly),
                    detail: "per month",
                    badge: nil,
                    isSelected: selection == .monthly,
                    isCurrent: currentProduct == SavaProducts.proMonthly,
                    isSelectable: subscriptions.monthly != nil,
                    isLoading: catalogueIsLoading) {
                        select(.monthly)
                    }

                PlanRow(
                    title: "Annual",
                    price: subscriptions.localizedPrice(for: .annual),
                    detail: annualDetailLine,
                    badge: "Best value",
                    isSelected: selection == .annual,
                    isCurrent: currentProduct == SavaProducts.proAnnual,
                    isSelectable: subscriptions.annual != nil,
                    isLoading: catalogueIsLoading) {
                        select(.annual)
                    }
            }
        }
    }

    /// "per year · ~$6.67/mo · Save 33%" — every part derived from StoreKit.
    private var annualDetailLine: String {
        var parts = ["per year"]
        if let perMonth = subscriptions.annualPerMonth { parts.append("~\(perMonth)/mo") }
        if let saved = subscriptions.annualSavingsPercent { parts.append("Save \(saved)%") }
        return parts.joined(separator: " · ")
    }

    private var currentProduct: String? {
        subscriptions.isPro ? subscriptions.entitlement.productID : nil
    }

    private func select(_ cadence: SavaProducts.Cadence) {
        guard selection != cadence else { return }
        Haptics.tap()
        selection = cadence
        Task {
            await subscriptions.record(event: cadence.selectionEvent,
                                       productID: cadence.productID,
                                       context: context)
        }
    }

    // MARK: Unavailable

    private func unavailable(_ why: String) -> some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Text(why)
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.secondary)
            Button("Try again") {
                Task { await subscriptions.loadProducts() }
            }
            .font(SavaType.caption)
            .foregroundStyle(SavaColor.accentBlueText)
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    // MARK: Action bar

    private var actionBar: some View {
        VStack(spacing: Space.m) {
            if case .failed(let message) = subscriptions.purchasePhase {
                Text(message)
                    .font(SavaType.caption)
                    .foregroundStyle(SavaColor.danger)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            } else if subscriptions.purchasePhase == .pending {
                Text("Waiting for approval. Sava will unlock automatically.")
                    .font(SavaType.caption)
                    .foregroundStyle(SavaColor.secondary)
                    .multilineTextAlignment(.center)
            }

            SavaButton(title: primaryTitle,
                       isLoading: isWorking,
                       isEnabled: canPurchase) {
                Task { await buy() }
            }

            HStack(spacing: Space.l) {
                quietButton("Restore Purchases") {
                    Task { await subscriptions.restore() }
                }
                if subscriptions.isPro {
                    quietButton("Manage") {
                        Task {
                            await subscriptions.record(
                                event: "manage_subscription_opened", context: context)
                            openURL(AppConfig.Links.manageSubscription)
                        }
                    }
                }
            }
        }
        .screenPadding()
        .padding(.top, Space.m)
        .padding(.bottom, Space.s)
        .background(
            // A plain surface with a hairline, not a blur. The palette has three
            // flat levels and depth comes from which one a thing sits on.
            SavaColor.surface
                .overlay(alignment: .top) {
                    Rectangle()
                        .fill(SavaColor.hairline)
                        .frame(height: 0.5)
                }
                .ignoresSafeArea(edges: .bottom))
    }

    private func quietButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: { Haptics.tap(); action() }) {
            Text(title)
                .font(SavaType.caption)
                .foregroundStyle(SavaColor.tertiary)
                .frame(minHeight: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var primaryTitle: String {
        if subscriptions.isPro { return "You're on Sava Pro" }
        // With no catalogue there is no plan selector on screen, so naming a
        // cadence the user cannot see and did not choose reads as a bug.
        if case .unavailable = subscriptions.productsPhase {
            return "Purchasing unavailable"
        }
        switch selection {
        case .monthly: return "Start Monthly"
        case .annual: return "Start Annual"
        }
    }

    private var isWorking: Bool {
        switch subscriptions.purchasePhase {
        case .purchasing, .restoring: return true
        default: return false
        }
    }

    private var canPurchase: Bool {
        !subscriptions.isPro && !isWorking && selectedProduct != nil
    }

    private var selectedProduct: Product? {
        switch selection {
        case .monthly: return subscriptions.monthly
        case .annual: return subscriptions.annual
        }
    }

    private func buy() async {
        guard let product = selectedProduct else { return }
        await subscriptions.purchase(product, context: context)
    }

    // MARK: Disclosure

    /// Guideline 3.1.2 requires the length, the price, the renewal terms, and
    /// links to Terms of Use and Privacy Policy on the screen that sells the
    /// subscription. Written plainly rather than in six-point grey: the rules
    /// exist because burying this is a dark pattern, and satisfying them by
    /// making the text unreadable would be observing the letter and breaking
    /// the point.
    private var disclosure: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Text("Sava Pro renews automatically until cancelled. Your Apple "
                 + "Account is charged at confirmation, and again each period "
                 + "unless you turn off auto-renew at least 24 hours before it "
                 + "ends. Manage or cancel anytime in Settings.")
                .font(SavaType.meta)
                .foregroundStyle(SavaColor.tertiary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: Space.l) {
                legalLink("Terms of Use", AppConfig.Links.terms)
                legalLink("Privacy Policy", AppConfig.Links.privacy)
            }
        }
    }

    private func legalLink(_ title: String, _ url: URL) -> some View {
        Button { openURL(url) } label: {
            Text(title)
                .font(SavaType.meta)
                .underline()
                .foregroundStyle(SavaColor.secondary)
                .frame(minHeight: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - One plan

/// A plan, as a compact row.
///
/// Title, price, one line of detail, and a selection mark. No feature list —
/// the paid plans buy the same thing and that is stated once above.
///
/// Selection is shown by a citron border *and* a filled mark, never by colour
/// alone: colour as the only state indicator fails for anyone who cannot
/// distinguish it, and this row is the difference between paying and not.
private struct PlanRow: View {
    let title: String
    let price: String?
    let detail: String
    let badge: String?
    let isSelected: Bool
    let isCurrent: Bool
    let isSelectable: Bool
    /// Whether a missing price is still on its way. See `content`.
    var isLoading: Bool = false
    let action: (() -> Void)?

    var body: some View {
        Group {
            if let action, isSelectable {
                Button(action: action) { content }.buttonStyle(.pressable)
            } else {
                content
            }
        }
    }

    private var content: some View {
        HStack(alignment: .center, spacing: Space.m) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: Space.s) {
                    Text(title)
                        .font(SavaType.title)
                        .foregroundStyle(SavaColor.primary)
                    if let badge { badgeView(badge) }
                    if isCurrent { currentView }
                }

                if let price {
                    Text(price)
                        .font(SavaType.lede)
                        .foregroundStyle(SavaColor.primary)
                } else if action != nil && isLoading {
                    // Price still loading from StoreKit. A shaped placeholder
                    // rather than a spinner or — worse — a guessed number: the
                    // storefront decides the price and we do not know it yet.
                    //
                    // Only while it is genuinely loading: a skeleton that never
                    // resolves reads as a hung screen, so once the catalogue is
                    // known to be unavailable the row goes quiet and the notice
                    // above the plans carries the explanation instead.
                    Skeleton().frame(width: 88, height: 17)
                }

                Text(detail)
                    .font(SavaType.meta)
                    .foregroundStyle(SavaColor.tertiary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }

            Spacer(minLength: 0)

            if action != nil {
                Image(systemName: isSelected ? "largecircle.fill.circle" : "circle")
                    .font(.system(size: 20))
                    .foregroundStyle(isSelected ? SavaColor.accent : SavaColor.tertiary)
            }
        }
        .padding(Space.l)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SavaColor.surface,
                    in: RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                .strokeBorder(isSelected ? SavaColor.accent : SavaColor.hairline,
                              lineWidth: isSelected ? 1.5 : 0.5))
        .animation(Motion.gentle, value: isSelected)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
    }

    private func badgeView(_ text: String) -> some View {
        Text(text.uppercased())
            .font(SavaType.section)
            .tracking(Tracking.wide)
            .foregroundStyle(SavaColor.onAccent)
            .padding(.horizontal, Space.s)
            .padding(.vertical, 3)
            .background(SavaColor.accent, in: Capsule())
    }

    private var currentView: some View {
        Text("CURRENT")
            .font(SavaType.section)
            .tracking(Tracking.wide)
            .foregroundStyle(SavaColor.secondary)
            .padding(.horizontal, Space.s)
            .padding(.vertical, 3)
            .overlay(Capsule().strokeBorder(SavaColor.hairline, lineWidth: 0.5))
    }
}
