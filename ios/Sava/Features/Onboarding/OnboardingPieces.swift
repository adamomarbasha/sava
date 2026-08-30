import SwiftUI

// The interactive parts of onboarding, kept separate from the stage layouts so
// each one can be reasoned about — and reduced-motion-tested — on its own.
//
// One rule runs through all of them: **motion carries meaning or it does not
// happen.** The card fan shows that saves come from many places and stack up;
// the typing demo shows that search reads words; the Ask bubbles show that the
// answer comes from the video. Nothing here moves decoratively, and everything
// collapses to a static, still-legible state under Reduce Motion.

// MARK: - Progress

/// Stage progress as a segmented citron rule.
///
/// Not dots. Dots say "slideshow, please endure it"; a rule that fills says
/// "this is short and you are nearly through it", and it matches the hairline
/// vocabulary the rest of Sava already uses for structure.
struct OnboardingProgress: View {
    let stage: Int
    let total: Int
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<total, id: \.self) { index in
                Capsule()
                    .fill(index <= stage ? SavaColor.accentTint : SavaColor.fill)
                    .frame(height: 3)
                    .frame(maxWidth: .infinity)
            }
        }
        .animation(Motion.respecting(Motion.standard, reduceMotion), value: stage)
        .accessibilityElement()
        .accessibilityLabel("Step \(stage + 1) of \(total)")
    }
}

/// A platform's colour as a dot.
///
/// TikTok's brand colour is black, which is invisible on Sava's near-black
/// surfaces. The design system already knows this — `platformNeedsOutline`
/// exists for exactly this case and the filter chips already use it — so this
/// reuses that answer rather than inventing a second one.
struct PlatformDot: View {
    let platform: Platform
    var size: CGFloat = 7

    var body: some View {
        Circle()
            .fill(SavaColor.platformFill(platform))
            .frame(width: size, height: size)
            .overlay(
                Circle().strokeBorder(
                    SavaColor.platformNeedsOutline(platform)
                        ? SavaColor.secondary : Color.clear,
                    lineWidth: 1))
    }
}

// MARK: - Stage 1: the card fan

/// One saved thing, as a card.
struct OnboardingCard: Identifiable, Equatable {
    let id = UUID()
    let platform: Platform
    let title: String
    let kind: String
    /// Resting offset and rotation inside the fan. Fixed per card so the
    /// composition is designed rather than random on each launch.
    let offset: CGSize
    let angle: Double
}

extension OnboardingCard {
    /// The mix deliberately spans everything Sava takes: two short-form video
    /// platforms, long-form, an article, and an image. Someone should recognise
    /// their own saving habit in this pile within a second.
    // Laid out on a vertical rhythm wider than a card is tall, so no card ever
    // covers another's platform label. The first version fanned them tightly
    // and the YouTube label sat underneath the screenshot card — a composition
    // that looks designed in a mockup and is unreadable on a phone.
    static let showcase: [OnboardingCard] = [
        .init(platform: .tiktok, title: "3-ingredient pasta", kind: "TikTok",
              offset: CGSize(width: -62, height: -132), angle: -8),
        .init(platform: .instagram, title: "Berlin, in October", kind: "Reel",
              offset: CGSize(width: 66, height: -62), angle: 7),
        .init(platform: .youtube, title: "How compilers work", kind: "YouTube",
              offset: CGSize(width: -70, height: 8), angle: -5),
        .init(platform: .other, title: "The case for slow software", kind: "Article",
              offset: CGSize(width: 58, height: 78), angle: 6),
        .init(platform: .other, title: "Screenshot", kind: "Image",
              offset: CGSize(width: -54, height: 148), angle: -3),
    ]
}

/// A drag-responsive fan of saved things.
///
/// The cards sit at designed offsets and lean away from the finger with
/// parallax weighted by depth, so the pile reads as physical rather than as a
/// grid that happens to be rotated. Depth comes from scale, position and a
/// hairline — not from shadows, which on a near-black ground read as smudges.
struct OnboardingCardFan: View {
    let cards: [OnboardingCard]
    @Binding var appeared: Bool

    @State private var drag: CGSize = .zero
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            ForEach(Array(cards.enumerated()), id: \.element.id) { index, card in
                let depth = Double(index + 1) / Double(cards.count)
                SavedThingCard(card: card)
                    .rotationEffect(.degrees(reduceMotion ? 0 : card.angle))
                    .offset(x: card.offset.width + parallax(depth).width,
                            y: card.offset.height + parallax(depth).height)
                    .scaleEffect(appeared ? 1 : 0.86)
                    .opacity(appeared ? 1 : 0)
                    // Staggered entrance: the pile assembles itself, which is
                    // the one moment worth spending motion on.
                    .animation(reduceMotion ? .easeOut(duration: 0.12)
                               : .spring(response: 0.5, dampingFraction: 0.82)
                                   .delay(Double(index) * 0.06),
                               value: appeared)
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: 366)
        .contentShape(Rectangle())
        .gesture(
            DragGesture()
                .onChanged { value in
                    guard !reduceMotion else { return }
                    drag = value.translation
                }
                .onEnded { _ in
                    guard !reduceMotion else { return }
                    Haptics.tap()
                    withAnimation(.spring(response: 0.5, dampingFraction: 0.7)) {
                        drag = .zero
                    }
                }
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "A pile of saved things: " + cards.map(\.kind).joined(separator: ", "))
    }

    /// Nearer cards move more. Clamped so a hard flick tilts the pile rather
    /// than throwing it off screen.
    private func parallax(_ depth: Double) -> CGSize {
        guard !reduceMotion else { return .zero }
        let limit: CGFloat = 34
        return CGSize(width: max(-limit, min(limit, drag.width * 0.12)) * depth,
                      height: max(-limit, min(limit, drag.height * 0.12)) * depth)
    }
}

/// The card itself: a platform mark, a title, and a kind. No fake thumbnail —
/// an invented screenshot of content the user has not saved would be a lie in
/// the first ten seconds of the product.
private struct SavedThingCard: View {
    let card: OnboardingCard

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            HStack(spacing: 6) {
                PlatformDot(platform: card.platform)
                Text(card.kind.uppercased())
                    .font(SavaType.section)
                    .tracking(Tracking.wide)
                    .foregroundStyle(SavaColor.tertiary)
            }
            Text(card.title)
                .font(SavaType.mediaTitle)
                .foregroundStyle(SavaColor.primary)
                // One line, so every card is the same height and the spacing
                // above can guarantee no label is ever covered.
                .lineLimit(1)
                .minimumScaleFactor(0.85)
                .multilineTextAlignment(.leading)
        }
        .padding(Space.m)
        .frame(width: 176, alignment: .leading)
        .background(SavaColor.surface,
                    in: RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                .strokeBorder(SavaColor.hairline, lineWidth: 0.5))
    }
}

// MARK: - Stage 2: the save-method demos

/// One way to get something into Sava, as a small living demo.
///
/// Each row animates its own idea once on appear and then rests. A row that
/// loops forever competes with the text next to it for attention and makes the
/// screen feel like a screensaver.
struct SaveMethodRow: View {
    let symbol: String
    let title: String
    let detail: String
    var badge: String? = nil
    var accent: Bool = false
    var action: (() -> Void)? = nil
    var actionTitle: String? = nil

    @State private var pulse = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            HStack(alignment: .top, spacing: Space.m) {
                ZStack {
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(SavaColor.fill)
                        .frame(width: 38, height: 38)
                    Image(systemName: symbol)
                        .font(.system(size: 16, weight: .medium))
                        .foregroundStyle(accent ? SavaColor.accentTint : SavaColor.secondary)
                        .scaleEffect(pulse ? 1.0 : 0.82)
                        .opacity(pulse ? 1 : 0.5)
                }

                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: Space.s) {
                        Text(title)
                            .font(SavaType.body)
                            .foregroundStyle(SavaColor.primary)
                        if let badge {
                            Text(badge.uppercased())
                                .font(SavaType.section)
                                .tracking(Tracking.wide)
                                .foregroundStyle(SavaColor.tertiary)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .overlay(Capsule().strokeBorder(SavaColor.hairline,
                                                                lineWidth: 0.5))
                        }
                    }
                    Text(detail)
                        .font(SavaType.meta)
                        .foregroundStyle(SavaColor.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }

            if let action, let actionTitle {
                Button(action: { Haptics.tap(); action() }) {
                    HStack(spacing: 6) {
                        Text(actionTitle)
                            .font(SavaType.caption)
                        Image(systemName: "arrow.up.right")
                            .font(.system(size: 10, weight: .bold))
                    }
                    .foregroundStyle(SavaColor.onAccent)
                    .padding(.horizontal, Space.m)
                    .frame(minHeight: 34)
                    .background(SavaColor.accent, in: Capsule())
                }
                .buttonStyle(.pressable)
                .padding(.leading, 38 + Space.m)
            }
        }
        .padding(Space.l)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SavaColor.surface,
                    in: RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                .strokeBorder(SavaColor.hairline, lineWidth: 0.5))
        .onAppear {
            guard !reduceMotion else { pulse = true; return }
            withAnimation(.spring(response: 0.5, dampingFraction: 0.7).delay(0.15)) {
                pulse = true
            }
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Stage 3: the search demo

/// A local, fake search that types itself and resolves to a result.
///
/// Entirely offline and hard-coded. Onboarding must not depend on the network,
/// on the user having saved anything, or on an AI provider being reachable —
/// the first-run experience cannot be the place where a cold start or a rate
/// limit is discovered.
struct SearchDemo: View {
    /// The query, typed one character at a time.
    static let query = "that Speed GTA clip"
    /// What resolves. Mirrors the real item that exposed the search bug, which
    /// makes the demo a truthful illustration rather than a flattering one.
    static let resultTitle = "Speed was convinced that it looked like GTA 5 but GTA 6 💀"

    @State private var typed = ""
    @State private var showResult = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            // The field
            HStack(spacing: Space.s) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(SavaColor.tertiary)
                Text(typed.isEmpty ? " " : typed)
                    .font(SavaType.body)
                    .foregroundStyle(SavaColor.primary)
                    .lineLimit(1)
                if typed.count < Self.query.count && !reduceMotion {
                    Capsule()
                        .fill(SavaColor.accentTint)
                        .frame(width: 2, height: 18)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, Space.m)
            .frame(height: 46)
            .background(SavaColor.fill,
                        in: RoundedRectangle(cornerRadius: Radius.control, style: .continuous))

            // The result
            if showResult {
                HStack(spacing: Space.m) {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(SavaColor.fill)
                        .frame(width: 44, height: 58)
                        .overlay(
                            Image(systemName: "play.fill")
                                .font(.system(size: 13))
                                .foregroundStyle(SavaColor.tertiary))
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            PlatformDot(platform: .tiktok, size: 6)
                            Text("TIKTOK")
                                .font(SavaType.section)
                                .tracking(Tracking.wide)
                                .foregroundStyle(SavaColor.tertiary)
                        }
                        Text(Self.resultTitle)
                            .font(SavaType.mediaTitle)
                            .foregroundStyle(SavaColor.primary)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 0)
                }
                .padding(Space.m)
                .background(SavaColor.surface,
                            in: RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                        .strokeBorder(SavaColor.accentTint.opacity(0.55), lineWidth: 1))
                .transition(.asymmetric(
                    insertion: .scale(scale: 0.94).combined(with: .opacity),
                    removal: .opacity))
            }
        }
        .onAppear { play() }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "Searching for \(Self.query). One saved TikTok matches: \(Self.resultTitle)")
    }

    private func play() {
        guard !reduceMotion else {
            typed = Self.query
            showResult = true
            return
        }
        typed = ""
        showResult = false
        for (index, character) in Self.query.enumerated() {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.045 * Double(index)) {
                typed.append(character)
            }
        }
        DispatchQueue.main.asyncAfter(
            deadline: .now() + 0.045 * Double(Self.query.count) + 0.32
        ) {
            Haptics.tap()
            withAnimation(.spring(response: 0.45, dampingFraction: 0.8)) {
                showResult = true
            }
        }
    }
}

/// A two-line Ask exchange, revealed after the search resolves.
///
/// Also entirely local. It shows the *shape* of Ask — a question about one
/// saved thing, answered from what Sava read — without pretending to have run
/// a model.
struct AskDemo: View {
    static let question = "What was happening in this video?"
    static let answer = "He's reacting to the GTA 6 trailer and insisting the "
        + "graphics look like GTA 5."

    @State private var showQuestion = false
    @State private var showAnswer = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            if showQuestion {
                bubble(Self.question, mine: true)
                    .transition(.move(edge: .trailing).combined(with: .opacity))
            }
            if showAnswer {
                bubble(Self.answer, mine: false)
                    .transition(.move(edge: .leading).combined(with: .opacity))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .onAppear { play() }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Ask Sava. Question: \(Self.question). Answer: \(Self.answer)")
    }

    @ViewBuilder
    private func bubble(_ text: String, mine: Bool) -> some View {
        HStack {
            if mine { Spacer(minLength: Space.xl) }
            Text(text)
                // Sava's serif is its own voice; the user's question is not.
                .font(mine ? SavaType.callout : SavaType.prose)
                .foregroundStyle(mine ? SavaColor.onAccent : SavaColor.primary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, Space.m)
                .padding(.vertical, Space.s)
                .background(mine ? SavaColor.accent : SavaColor.fill,
                            in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            if !mine { Spacer(minLength: Space.xl) }
        }
    }

    private func play() {
        guard !reduceMotion else { showQuestion = true; showAnswer = true; return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) {
            withAnimation(Motion.standard) { showQuestion = true }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) {
            withAnimation(Motion.standard) { showAnswer = true }
        }
    }
}
