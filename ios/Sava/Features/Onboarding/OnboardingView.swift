import SwiftUI

/// Sava's first run.
///
/// ── One job per screen ──────────────────────────────────────────────────
///
///   01  WHY SAVA      a live constellation of content you can drag into Sava
///   02  HOW TO SAVE   TikTok / Instagram / YouTube × share sheet / button
///   03  FIND ANYTHING search narrows a real library, then Ask answers
///   04  YOU'RE READY  setup status, and the door
///
/// The previous version explained the Shortcut twice — once as one of three
/// "ways to save" and again on the final screen — which made the tour feel
/// longer than it was and taught the Shortcut as a *saving method*, which it is
/// not. It now appears once, inside the Action Button path, where it is
/// visibly a prerequisite rather than an alternative.
///
/// ── Rules it holds to ───────────────────────────────────────────────────
///
///   * **No paywall.** Nothing here mentions plans, prices or limits. First run
///     is not a funnel.
///   * **No permission prompts.** Nothing asks for notifications, photos or
///     tracking. Sava asks in context, when it needs something.
///   * **Never a trap.** Every stage can be left, and nothing is required to
///     finish.
///   * **Nothing is faked, and nothing is oversold.** No screen claims Sava
///     can capture anything it was not given a link to, and first run never
///     asks for the optional iCloud Shortcut — neither save method needs it,
///     so that CTA lives in Learn Sava where somebody can evaluate it.
///   * **Reduce Motion is a real path**, not a degraded one: every demo has a
///     finished still composition that says the whole thing in one frame.
struct OnboardingView: View {
    let userID: Int?
    /// Called when the tour is finished or skipped. The caller persists.
    let onFinish: () -> Void

    @State private var stage = 0
    @State private var savedInDemo: Set<String> = []
    @State private var demoPlatform: DemoPlatform = .tiktok
    @State private var demoMethod: SaveMethod = .shareSheet

    @Environment(\.openURL) private var openURL
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let stageCount = 4

    var body: some View {
        ZStack {
            SavaColor.ground.ignoresSafeArea()

            VStack(spacing: 0) {
                header
                // The stages scroll. At the largest Dynamic Type sizes on the
                // smallest phone no fixed layout fits, and a clipped headline is
                // worse than a scroll indicator.
                ScrollView {
                    VStack(alignment: .leading, spacing: Space.xl) {
                        stageContent
                    }
                    .screenPadding()
                    .padding(.top, Space.l)
                    .padding(.bottom, Space.xxl)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .scrollBounceBehavior(.basedOnSize)
                .devScrollAnchor()
                footer
            }
        }
        .onAppear {
            if let forced = DevFlags.onboardingStage {
                stage = max(0, min(stageCount - 1, forced))
            }
        }
    }

    // MARK: Header

    private var header: some View {
        VStack(spacing: Space.m) {
            HStack {
                SavaMark(size: 26)
                Spacer()
                if stage < stageCount - 1 {
                    Button("Skip") { finish() }
                        .font(SavaType.caption)
                        .foregroundStyle(SavaColor.tertiary)
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(Rectangle())
                        .accessibilityHint("Skips the tour. You can reopen it from Profile.")
                }
            }
            OnboardingProgress(stage: stage, total: stageCount)
        }
        .screenPadding()
        .padding(.top, Space.s)
    }

    // MARK: Stages

    @ViewBuilder private var stageContent: some View {
        switch stage {
        case 0: stageWhy
        case 1: stageHowToSave
        case 2: stageFind
        default: stageReady
        }
    }

    /// 01 — why Sava.
    private var stageWhy: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            StageHeadline(
                title: "Everything you\nscroll past.\nKept.",
                lede: "TikToks, Reels, YouTube, articles — Sava holds them and "
                    + "reads them, so you can find them again.")
            // Tall enough for two rows of cards plus the save target beneath
            // them; `ContentUniverse` lays out in fractions of whatever it is
            // given, and below about 380 the rows start to touch.
            ContentUniverse(savedIDs: $savedInDemo)
                .frame(height: 420)
        }
    }

    /// 02 — how to save. The most useful screen in the tour.
    private var stageHowToSave: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            StageHeadline(
                title: "Two ways in.",
                lede: "You never have to open Sava to save something.")
            SaveFlowDemo(platform: $demoPlatform, method: $demoMethod)

            if demoMethod == .actionButton {
                // One line, not a second diagram.
                //
                // This was `ActionButtonChain` — assign → copy → press → saved
                // — directly under a flow that had *just* shown copy → press →
                // saved. Two chains ending in "Saved" on one screen is the
                // docs-style duplication the tour is meant to avoid. The only
                // thing the diagram above does not cover is the one-time
                // assignment, so that is all this says.
                HStack(alignment: .top, spacing: Space.m) {
                    Image(systemName: "gear")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(SavaColor.accentTint)
                        .frame(width: 20)
                    // Two `Text`s, not markdown.
                    //
                    // `Text` parses markdown only from a single string
                    // *literal*; a `+` concatenation is an expression, so
                    // `**bold**` rendered as literal asterisks on screen.
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Settings → Action Button → Shortcut")
                            .font(SavaType.caption)
                            .foregroundStyle(SavaColor.secondary)
                        Text("Assign it once. “Save to Sava” is already there "
                             + "— nothing to install.")
                            .font(SavaType.meta)
                            .foregroundStyle(SavaColor.tertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 0)
                }
                .padding(Space.m)
                .background(SavaColor.fill,
                            in: RoundedRectangle(cornerRadius: Radius.control,
                                                 style: .continuous))
                .transition(.opacity)
            }
        }
        .animation(Motion.respecting(Motion.standard, reduceMotion), value: demoMethod)
    }

    /// 03 — find anything.
    private var stageFind: some View {
        // Tighter than the other stages, and a two-line headline rather than
        // three: this screen carries a search field, six cards and an Ask
        // exchange, and at three lines the answer — the actual payoff — was
        // pushed off the bottom of an iPhone 17 Pro.
        VStack(alignment: .leading, spacing: Space.l) {
            StageHeadline(
                title: "Find it by what\nyou remember.",
                lede: "Not where you saw it. Then ask Sava about what it found.")
            FindDemo()
        }
    }

    /// 04 — the payoff.
    ///
    /// This was a setup form: three status rows, a Settings breadcrumb, and an
    /// "Open Settings" button in the same citron as the real CTA, so the screen
    /// had two things competing to be pressed and ended on a chore. Setup
    /// detail belongs in Learn Sava, which is where somebody goes when they
    /// have decided to do it.
    ///
    /// What belongs here is what they now have. The counter is the library they
    /// just built in Stage 1 — their own actions, played back — so the last
    /// screen is evidence rather than a promise.
    private var stageReady: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            StageHeadline(
                title: savedInDemo.isEmpty ? "Ready when you are."
                                           : "That's the whole idea.",
                lede: "Save from anywhere. Sava reads it. You find it later by "
                    + "whatever you happen to remember.")

            ReadyRecap(savedCount: savedInDemo.count)

            VStack(alignment: .leading, spacing: Space.s) {
                Text("Want one-press saving?")
                    .font(SavaType.body)
                    .foregroundStyle(SavaColor.primary)
                Text("Assign “Save to Sava” to the Action Button in Settings — "
                     + "it's already there. Profile → Learn Sava walks you "
                     + "through it whenever you like.")
                    .font(SavaType.meta)
                    .foregroundStyle(SavaColor.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: Footer

    private var footer: some View {
        VStack(spacing: Space.s) {
            SavaButton(title: stage == stageCount - 1 ? "Start using Sava" : "Continue") {
                advance()
            }
            if stage == stageCount - 1 {
                Button("Skip for now") { finish() }
                    .font(SavaType.caption)
                    .foregroundStyle(SavaColor.tertiary)
                    .frame(minHeight: 44)
            }
        }
        .screenPadding()
        .padding(.bottom, Space.s)
        .background(
            SavaColor.ground
                .overlay(alignment: .top) {
                    Rectangle().fill(SavaColor.hairline).frame(height: 0.5)
                }
                .ignoresSafeArea(edges: .bottom))
    }

    // MARK: Actions

    private func advance() {
        guard stage < stageCount - 1 else { finish(); return }
        Haptics.tap()
        withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
            stage += 1
        }
    }

    private func finish() {
        Haptics.success()
        onFinish()
    }


}
