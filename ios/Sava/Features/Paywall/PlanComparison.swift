import SwiftUI

/// Free against Pro, in one table.
///
/// ── Why a table, and why this content ───────────────────────────────────
///
/// The paywall used to list what Pro includes and leave the reader to infer
/// what Free does not. That inference is always the pessimistic one — people
/// assume the free tier is crippled — so Sava was quietly failing to say the
/// most persuasive true thing about it: **saving is unlimited and everything
/// structural is free**. What Pro buys is *more understanding*, and that is a
/// much easier thing to sell honestly once the rest is visible.
///
/// ── Every row is checked against the implementation ─────────────────────
///
/// Nothing here is aspirational. The rows map to real behaviour:
///
///   * *Unlimited saves* — `api/plans.py` sets no `saved_items` ceiling, and at
///     quota `services/save.py` still stores the bookmark (`LIMIT_REACHED`) and
///     `resume_limited_saves` picks it up later. The save is never refused.
///   * *Ways to save, search, collections* — no entitlement check exists on any
///     of these paths for either plan.
///   * *Videos understood* / *Ask messages* — the two metered allowances, from
///     `/api/pricing`, which serves `plans.py` so an env-var change reaches the
///     paywall without a client release.
///   * *Deep video analysis* — `enhanced_analysis`, enforced at
///     `routes_intelligence.py` with a 402.
///   * *Priority / at once* — `job_priority` and `concurrent_jobs`, both
///     enforced in the queue (`api/jobs.py`).
///
/// Numbers come from the fetched catalogue rather than being written here, so
/// this file cannot drift from the server. And they are stated in videos and
/// messages: units and route names are internal and never shown.
struct PlanComparison: View {
    let free: PricingCatalogue.Plan
    let pro: PricingCatalogue.Plan
    /// Draws a subtle citron wash down the Pro column.
    var highlightPro: Bool = true

    private var rows: [Row] {
        [
            Row("Save as much as you like", .yes, .yes),
            Row("TikTok, Instagram, YouTube, web", .yes, .yes),
            Row("Share sheet, Shortcut, Action Button", .yes, .yes),
            Row("Search, collections, summaries", .yes, .yes),
            Row("Videos understood each month",
                .text(free.approxVideos.formatted(.number)),
                .text(pro.approxVideos.formatted(.number))),
            Row("Ask messages each month",
                .text(free.askMessages.formatted(.number)),
                .text(pro.askMessages.formatted(.number))),
            Row("Deep video analysis",
                free.enhancedAnalysis ? .yes : .no,
                pro.enhancedAnalysis ? .yes : .no),
            Row("Processing queue",
                .text(free.priorityProcessing ? "Priority" : "Standard"),
                .text(pro.priorityProcessing ? "Priority" : "Standard")),
            Row("Saves processed at once",
                .text("\(free.concurrentJobs)"), .text("\(pro.concurrentJobs)")),
        ]
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            ForEach(Array(rows.enumerated()), id: \.element.title) { index, row in
                if index > 0 {
                    Rectangle().fill(SavaColor.hairline).frame(height: 0.5)
                }
                line(row)
            }
        }
        .background {
            // The Pro column reads as the answer without needing a badge.
            HStack(spacing: 0) {
                Color.clear
                Color.clear.frame(width: Self.columnWidth)
                SavaColor.accent.opacity(highlightPro ? 0.055 : 0)
                    .frame(width: Self.columnWidth)
            }
        }
        .background(SavaColor.surface,
                    in: RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
            .strokeBorder(SavaColor.hairline, lineWidth: 0.5))
        .accessibilityElement(children: .contain)
    }

    private static let columnWidth: CGFloat = 74

    private var header: some View {
        HStack(spacing: 0) {
            Spacer(minLength: 0)
            Text("FREE")
                .font(.system(size: 10, weight: .black))
                .tracking(0.8)
                .foregroundStyle(SavaColor.tertiary)
                .frame(width: Self.columnWidth)
            Text("PRO")
                .font(.system(size: 10, weight: .black))
                .tracking(0.8)
                .foregroundStyle(SavaColor.accent)
                .frame(width: Self.columnWidth)
        }
        .padding(.top, Space.m)
        .padding(.bottom, Space.s)
        .padding(.horizontal, Space.l)
        .accessibilityHidden(true)
    }

    private func line(_ row: Row) -> some View {
        HStack(spacing: 0) {
            Text(row.title)
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.primary)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            cell(row.free, emphasised: false)
            cell(row.pro, emphasised: true)
        }
        .padding(.vertical, Space.m)
        .padding(.horizontal, Space.l)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(row.title). Free: \(row.free.spoken). Pro: \(row.pro.spoken).")
    }

    @ViewBuilder private func cell(_ value: Value, emphasised: Bool) -> some View {
        Group {
            switch value {
            case .yes:
                Image(systemName: "checkmark")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(emphasised ? SavaColor.accent : SavaColor.secondary)
            case .no:
                // An em dash, not a red cross. "Not included" is not a failure,
                // and a wall of red crosses down the Free column is a way of
                // insulting the people who are about to become customers.
                Text("—")
                    .font(SavaType.callout)
                    .foregroundStyle(SavaColor.tertiary)
            case .text(let string):
                Text(string)
                    .font(.system(size: 14, weight: emphasised ? .semibold : .regular))
                    .foregroundStyle(emphasised ? SavaColor.primary : SavaColor.secondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
        }
        .frame(width: Self.columnWidth)
    }

    // MARK: Model

    private struct Row {
        let title: String
        let free: Value
        let pro: Value
        init(_ title: String, _ free: Value, _ pro: Value) {
            self.title = title; self.free = free; self.pro = pro
        }
    }

    enum Value {
        case yes, no
        case text(String)

        var spoken: String {
            switch self {
            case .yes: return "included"
            case .no: return "not included"
            case .text(let s): return s
            }
        }
    }
}
