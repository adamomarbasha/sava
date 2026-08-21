import SwiftUI

/// Renders one block of extracted understanding as editorial content.
///
/// The whole point of this file is that nothing here looks like a data
/// structure. The pipeline produces a nested JSON record; a recipe's
/// `ingredients: [{item, quantity}]` becomes a quantity column against a name
/// and its `steps: [str]` becomes numbered instructions. Same information, set
/// the way a magazine would set it.
///
/// Only content types that genuinely earn structure reach this view at all —
/// recipes, trips, reviews. Everything the extractor finds for a comedy clip
/// stays in the database.
struct StructuredSectionView: View {
    let section: StructuredSection

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: section.title)
            content
        }
    }

    @ViewBuilder private var content: some View {
        switch section.content {
        case .prose(let text):
            Text(text)
                .font(SavaType.prose)
                .foregroundStyle(SavaColor.primary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

        case .lines(let items):
            VStack(alignment: .leading, spacing: Space.s) {
                ForEach(items, id: \.self) { BulletLine(text: $0) }
            }

        case .steps(let items):
            VStack(alignment: .leading, spacing: Space.m) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, step in
                    HStack(alignment: .firstTextBaseline, spacing: Space.m) {
                        Text("\(index + 1)")
                            .font(.system(size: 12, weight: .semibold, design: .rounded))
                            .monospacedDigit()
                            .foregroundStyle(SavaColor.tertiary)
                            .frame(width: 16, alignment: .trailing)
                        Text(step)
                            .font(SavaType.callout)
                            .foregroundStyle(SavaColor.primary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }

        case .pairs(let pairs):
            // A quantity column, not a bullet list: the value is what the eye
            // scans for, so it gets its own alignment.
            VStack(spacing: 0) {
                ForEach(pairs, id: \.self) { pair in
                    HStack(alignment: .firstTextBaseline, spacing: Space.m) {
                        Text(pair.lead)
                            .font(SavaType.callout)
                            .foregroundStyle(SavaColor.primary)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: Space.m)
                        if let detail = pair.detail {
                            Text(detail)
                                .font(SavaType.callout)
                                .foregroundStyle(SavaColor.secondary)
                                .multilineTextAlignment(.trailing)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .padding(.vertical, Space.s)
                    .hairline()
                }
            }
        }
    }
}

/// A bullet that is a mark, not a glyph — 3pt, tertiary, aligned to the first
/// baseline so long lines still hang correctly.
struct BulletLine: View {
    let text: String
    /// Kept so existing call sites compile. It no longer changes the drawing:
    /// numbered accent tiles were tried here and were simply worse — a column
    /// of coloured squares beside four short lines reads as decoration, and it
    /// pulled the eye away from the words, which are the point.
    var index: Int? = nil

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.m) {
            // A hairline rule rather than a dot or a tile. It sits on the text
            // baseline, takes no colour, and disappears the moment you start
            // reading — which is what a bullet is for.
            Rectangle()
                .fill(SavaColor.tertiary)
                .frame(width: 10, height: 1)
                .offset(y: -5)
            Text(text)
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
