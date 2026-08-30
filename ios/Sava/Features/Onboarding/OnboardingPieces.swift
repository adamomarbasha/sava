import SwiftUI

// MARK: - Progress

/// Where you are in the tour, as a library filling up.
///
/// Four bars would have worked. This does the same job and says something while
/// doing it: each stage is a card that lands in a stack, so progress through
/// onboarding looks like the thing onboarding is about — saves accumulating.
/// Completed stages are solid citron cards, the current one is outlined, and
/// the ones ahead are faint.
///
/// It is still a progress indicator first: four discrete marks, left to right,
/// with the position announced to VoiceOver. The restraint is deliberate —
/// a progress control that has to be decoded is a worse progress control.
struct OnboardingProgress: View {
    let stage: Int
    let total: Int

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<total, id: \.self) { index in
                mark(for: index)
            }
            Spacer(minLength: 0)
        }
        .frame(height: 16)
        .animation(Motion.respecting(Motion.standard, reduceMotion), value: stage)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Step \(stage + 1) of \(total)")
    }

    @ViewBuilder private func mark(for index: Int) -> some View {
        let done = index < stage
        let current = index == stage
        RoundedRectangle(cornerRadius: 2.5, style: .continuous)
            .fill(done ? SavaColor.accent : Color.clear)
            .overlay(
                RoundedRectangle(cornerRadius: 2.5, style: .continuous)
                    .strokeBorder(current ? SavaColor.accent : SavaColor.hairline,
                                  lineWidth: current ? 1.6 : 1))
            .frame(width: done || current ? 22 : 14, height: 15)
            .scaleEffect(current ? 1.0 : 0.86, anchor: .bottom)
    }
}

// MARK: - Section heading used across the stages

/// A stage's supporting headline. Kept here so every stage sets type the same
/// way and the vertical rhythm cannot drift between them.
struct StageHeadline: View {
    let title: String
    let lede: String

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Text(title)
                .font(SavaType.display)
                .tracking(Tracking.tight)
                .foregroundStyle(SavaColor.primary)
                .fixedSize(horizontal: false, vertical: true)
            Text(lede)
                // Serif for Sava's own voice, and only here.
                //
                // The audit that produced this rule: serif had been used for the
                // lede *and* the Ask answer *and* body copy, at which point it
                // stopped being a voice and became the font. It now marks
                // exactly two things — Sava explaining itself, and Sava
                // answering a question — and everything else is the system face.
                .font(SavaType.prose)
                .foregroundStyle(SavaColor.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }
}
