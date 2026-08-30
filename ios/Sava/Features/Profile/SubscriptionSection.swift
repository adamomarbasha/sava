import SwiftUI

/// The Sava Pro row on the Profile screen.
///
/// One row, not a card stack. It states the plan, and — when there is something
/// worth saying — one line of context underneath: a renewal date for a
/// subscriber, a billing problem that needs attention, nothing at all for a
/// Free account that is not near a limit.
///
/// It does not nag. A Free account well inside its allowance is told what it
/// *includes* rather than how close it is to a wall; the remaining-videos line
/// only appears once the allowance is genuinely nearly gone.
struct SubscriptionRow: View {
    @EnvironmentObject private var subscriptions: SubscriptionManager
    let onOpen: () -> Void

    var body: some View {
        Button(action: { Haptics.tap(); onOpen() }) {
            HStack(spacing: Space.m) {
                mark
                VStack(alignment: .leading, spacing: 2) {
                    Text(subscriptions.entitlement.displayName)
                        .font(SavaType.body)
                        .foregroundStyle(SavaColor.primary)
                    if let note {
                        Text(note)
                            .font(SavaType.meta)
                            .foregroundStyle(noteIsUrgent ? SavaColor.danger
                                                          : SavaColor.tertiary)
                    }
                }
                Spacer(minLength: Space.s)
                if !subscriptions.isPro {
                    Text("Upgrade")
                        .font(SavaType.caption)
                        .foregroundStyle(SavaColor.onAccent)
                        .padding(.horizontal, Space.m)
                        .padding(.vertical, 5)
                        .background(SavaColor.accent, in: Capsule())
                }
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(SavaColor.tertiary)
            }
            .frame(minHeight: 56)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .hairline()
        .accessibilityElement(children: .combine)
        .accessibilityLabel(subscriptions.isPro
                            ? "Sava Pro. \(note ?? "")"
                            : "Free plan. Upgrade to Sava Pro.")
    }

    /// Citron only when it means something. A Pro account is the one state
    /// where the accent is earned; Free gets a neutral glyph rather than a
    /// greyed-out version of the same badge.
    private var mark: some View {
        Image(systemName: subscriptions.isPro ? "sparkles" : "square.stack")
            .font(.system(size: 15, weight: .medium))
            // A 15pt glyph, so `accentTint` — the fill token would render it
            // black on paper and it would stop reading as "this one is Pro".
            .foregroundStyle(subscriptions.isPro ? SavaColor.accentTint
                                                 : SavaColor.secondary)
            .frame(width: 22)
    }

    private var noteIsUrgent: Bool { subscriptions.entitlement.inBillingRetry }

    private var note: String? {
        let entitlement = subscriptions.entitlement
        if entitlement.inBillingRetry {
            return "There's a problem with your payment method"
        }
        if entitlement.isPro, let expires = entitlement.expiresAt,
           expires > .distantPast {
            let date = expires.formatted(.dateTime.month(.abbreviated).day())
            return entitlement.autoRenew ? "Renews \(date)" : "Ends \(date)"
        }
        if !entitlement.isPro {
            let units = subscriptions.usage.processingUnits
            // Escalating: what you have, then what is left, then what stopped.
            if units.exhausted { return "Video understanding paused until reset" }
            if units.fraction >= 0.8, let left = units.approxVideosRemaining {
                return "About \(left) more videos this month"
            }
            // Otherwise say what Free *includes*. A row that reads "Free ›" and
            // nothing else invites the assumption that Free is a trial, when in
            // fact saving is unlimited on it — which is the single most useful
            // fact about the plan and was going unsaid everywhere.
            let plan = subscriptions.catalogue.plan("free")
            if let videos = plan?.approxVideos {
                return "Unlimited saves · \(videos.formatted(.number)) videos a month"
            }
        }
        return nil
    }
}

/// This period's consumption.
///
/// Two meters and a reset date. Not a dashboard: the only question a person
/// actually has here is "how much have I got left", and two bars answer it
/// without a chart library or a settings screen full of numbers.
struct UsageSection: View {
    @EnvironmentObject private var subscriptions: SubscriptionManager

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            HStack(alignment: .firstTextBaseline) {
                SectionHeader(text: "This month")
                Spacer(minLength: Space.s)
                if let resets = subscriptions.usage.resetDescription {
                    Text(resets)
                        .font(SavaType.meta)
                        .foregroundStyle(SavaColor.tertiary)
                }
            }

            VStack(spacing: Space.l) {
                // "Videos understood", not "AI Processing". The internal
                // meter counts units — a text-routed video costs 1 and a
                // frame-routed one costs 8 — but nobody should have to know
                // that. One unit is calibrated to one ordinary video, so the
                // count the user sees is honest and needs no explanation.
                Meter(label: "Videos understood",
                      meter: subscriptions.usage.processingUnits)
                Meter(label: "Ask messages",
                      meter: subscriptions.usage.askMessages)
            }
        }
        .task { await subscriptions.refresh() }
    }
}

/// One allowance bar.
private struct Meter: View {
    let label: String
    let meter: UsageSnapshot.Meter

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            HStack {
                Text(label)
                    .font(SavaType.callout)
                    .foregroundStyle(SavaColor.secondary)
                Spacer(minLength: Space.s)
                Text(meter.headline)
                    // Monospaced digits: the number changes as work completes,
                    // and proportional figures make the row twitch sideways
                    // every time a digit width changes.
                    .font(SavaType.numeric)
                    .foregroundStyle(meter.exhausted ? SavaColor.danger
                                                     : SavaColor.primary)
            }

            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(SavaColor.fill)
                    Capsule()
                        .fill(fillColor)
                        .frame(width: max(meter.fraction > 0 ? 3 : 0,
                                          geometry.size.width * meter.fraction))
                }
            }
            .frame(height: 4)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label): \(meter.headline) used")
    }

    /// Neutral until it matters. A bar that is citron from the first unit
    /// spends the accent on nothing and leaves nowhere to escalate to.
    private var fillColor: Color {
        if meter.exhausted { return SavaColor.danger }
        // `accentTint` keeps the "nearly out" step visible as a *colour* change
        // in both appearances. `accent` inverts to ink on paper, which against
        // the grey of the normal state reads as "slightly darker" rather than
        // as a warning.
        if meter.fraction >= 0.8 { return SavaColor.accentTint }
        return SavaColor.secondary
    }
}
