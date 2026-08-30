import SwiftUI

/// Sava's first run.
///
/// ── What this is trying to avoid ────────────────────────────────────────
///
/// The default onboarding is four screens of centred text with an illustration
/// above it, and everyone swipes through it without reading a word. It teaches
/// nothing because there is nothing to do.
///
/// So each stage here has one thing you can *touch*, and that thing is the
/// lesson: a pile of saves you can push around (Sava holds many kinds of
/// content), a real Shortcut button (this is how saving works), a search that
/// types itself and resolves (you do not have to remember where you saw it),
/// and then the actual actions. The copy is short because the interaction is
/// carrying the meaning.
///
/// ── Rules it holds to ───────────────────────────────────────────────────
///
///   * **No paywall.** Stage 3 says Sava understands what you save; it does not
///     mention plans, prices, or limits. First run is not a funnel.
///   * **No permission prompts.** Nothing here asks for notifications, photos,
///     or tracking. Sava asks when it needs something, in context.
///   * **Never a trap.** Every stage can be left — "Skip" on the first three,
///     and Stage 4's actions are all optional.
///   * **Reduce Motion is a real path**, not a degraded one: every demo has a
///     still composition that reads correctly with no animation at all.
struct OnboardingView: View {
    let userID: Int?
    /// Called when the tour is finished or skipped. The caller persists.
    let onFinish: () -> Void

    @State private var stage = 0
    @State private var appeared = false
    @Environment(\.openURL) private var openURL
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize

    private let stageCount = 4

    var body: some View {
        ZStack {
            SavaColor.ground.ignoresSafeArea()

            VStack(spacing: 0) {
                header
                // The stages themselves scroll: at the largest Dynamic Type
                // sizes on the smallest phone, no fixed layout fits, and a
                // clipped headline is worse than a scroll bar.
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
                footer
            }
        }
        .onAppear {
            if let forced = DevFlags.onboardingStage {
                stage = max(0, min(stageCount - 1, forced))
            }
            withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
                appeared = true
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
        case 0: stageIdea
        case 1: stageSaving
        case 2: stageMagic
        default: stageActivate
        }
    }

    /// Stage 1 — what Sava is.
    private var stageIdea: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            headline("Save anything.\nFind it later.",
                     lede: "TikToks, Reels, videos, articles, screenshots. "
                         + "Sava keeps them and reads them.")
            OnboardingCardFan(cards: OnboardingCard.showcase, appeared: $appeared)
            if !reduceMotion {
                Text("Drag the pile")
                    .font(SavaType.meta)
                    .foregroundStyle(SavaColor.tertiary)
                    .frame(maxWidth: .infinity, alignment: .center)
            }
        }
    }

    /// Stage 2 — the three real ways in. The most useful screen in the tour.
    private var stageSaving: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            headline("Save from anywhere.",
                     lede: "You never have to open Sava to save something.")
            SaveMethodRow(
                symbol: "square.and.arrow.up",
                title: "The share sheet",
                detail: "In TikTok, Instagram, YouTube or Safari: Share, then Sava.")
            SaveMethodRow(
                symbol: "bolt.fill",
                title: "The Sava Shortcut",
                detail: "Adds “\(AppConfig.officialSaveShortcutName)” to your "
                    + "Shortcuts library. Apple will ask you to confirm.",
                accent: true,
                action: addShortcut,
                actionTitle: "Add Sava Shortcut")
            SaveMethodRow(
                symbol: "button.horizontal.top.press",
                title: "The Action Button",
                detail: "One press. Saved. Assign the Shortcut in Settings.",
                badge: "Optional")
        }
    }

    /// Stage 3 — why it is worth saving into Sava rather than a notes app.
    private var stageMagic: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            headline("You don't need to remember\nwhere you saw it.",
                     lede: "Search by what you remember — the words, or just the gist.")
            SearchDemo()
            VStack(alignment: .leading, spacing: Space.m) {
                SectionHeader(text: "Then ask about it")
                AskDemo()
            }
        }
    }

    /// Stage 4 — real actions, none of them required.
    private var stageActivate: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            headline("Save your first thing.",
                     lede: "Add the Shortcut now, or just start using Sava.")
            SaveMethodRow(
                symbol: "bolt.fill",
                title: "Add the Sava Shortcut",
                detail: "The fastest way to save without opening the app.",
                accent: true,
                action: addShortcut,
                actionTitle: "Add Sava Shortcut")
            SaveMethodRow(
                symbol: "button.horizontal.top.press",
                title: "Set up the Action Button",
                detail: "Settings → Action Button → Shortcut → "
                    + "“\(AppConfig.officialSaveShortcutName)”.",
                badge: "Optional",
                action: openSettings,
                actionTitle: "Open Settings")
            Text("Everything here lives in Profile → Learn Sava, so you can come "
                 + "back to it whenever you like.")
                .font(SavaType.meta)
                .foregroundStyle(SavaColor.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Shared bits

    private func headline(_ title: String, lede: String) -> some View {
        VStack(alignment: .leading, spacing: Space.m) {
            Text(title)
                .font(SavaType.display)
                .tracking(Tracking.tight)
                .foregroundStyle(SavaColor.primary)
                .fixedSize(horizontal: false, vertical: true)
            Text(lede)
                // Serif: this is Sava explaining itself, which is its voice.
                .font(SavaType.prose)
                .foregroundStyle(SavaColor.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    private var footer: some View {
        VStack(spacing: Space.s) {
            SavaButton(title: stage == stageCount - 1 ? "Continue to Sava" : "Continue") {
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
        // `appeared` is reset and re-set so each stage's staggered entrance
        // plays on arrival rather than only on the very first screen.
        appeared = false
        withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
            stage += 1
        }
        withAnimation(Motion.respecting(Motion.standard, reduceMotion).delay(0.05)) {
            appeared = true
        }
    }

    private func finish() {
        Haptics.success()
        onFinish()
    }

    /// Opens the official iCloud Shortcut. Apple shows its own Add Shortcut
    /// sheet; Sava never writes to the Shortcuts library and does not pretend to.
    private func addShortcut() {
        openURL(AppConfig.saveShortcutURL)
    }

    private func openSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        openURL(url)
    }
}
