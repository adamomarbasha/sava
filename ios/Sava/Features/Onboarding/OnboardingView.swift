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
///   * **Never a trap.** Every stage can be left, and Stage 4's setup is
///     optional in full.
///   * **Nothing is faked.** The Shortcut CTA opens Apple's own installation
///     sheet; Settings opens through the supported system API. No screen
///     claims Sava can capture anything it was not given a link to.
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
    @State private var shortcutOpened = false
    @State private var settingsOpened = false

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

            if demoMethod == .shareSheet {
                // The Action Button path fills this space with `ShortcutChain`.
                // Rather than leave the share path visibly emptier — which
                // reads as the lesser option when it is in fact the one that
                // needs no setup — say where it works. It is the question
                // people actually have next.
                VStack(alignment: .leading, spacing: Space.m) {
                    SectionHeader(text: "Works anywhere there's a share button")
                    ShareTargets()
                }
                .transition(.opacity)
            }

            if demoMethod == .actionButton {
                VStack(alignment: .leading, spacing: Space.m) {
                    SectionHeader(text: "The Shortcut is what makes that work")
                    ShortcutChain()
                    Button(action: addShortcut) {
                        Text(shortcutOpened ? "Open the Shortcut again"
                                            : "Add Sava Shortcut")
                            .font(SavaType.caption)
                            .foregroundStyle(SavaColor.onAccentTint)
                            .frame(maxWidth: .infinity)
                            .frame(minHeight: 44)
                            .background(SavaColor.accentTint, in: Capsule())
                    }
                    .buttonStyle(.plain)
                    Text("Opens Apple's Shortcuts app, which asks you to confirm. "
                         + "You can also do this later from Profile.")
                        .font(SavaType.meta)
                        .foregroundStyle(SavaColor.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
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

    /// 04 — you're ready. Activation, and nothing is required.
    private var stageReady: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            StageHeadline(
                title: "You're ready.",
                lede: "Sharing works now. The rest is optional.")
            SetupChecklist(
                shortcutOpened: shortcutOpened,
                settingsOpened: settingsOpened,
                onAddShortcut: addShortcut,
                onOpenSettings: openSettings)

            Text("All of this lives in Profile → Learn Sava, so you can come "
                 + "back to it whenever you like.")
                .font(SavaType.meta)
                .foregroundStyle(SavaColor.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .animation(Motion.respecting(Motion.standard, reduceMotion), value: settingsOpened)
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

    /// Opens the official iCloud Shortcut. Apple shows its own Add Shortcut
    /// sheet; Sava never writes to the Shortcuts library and does not pretend to.
    private func addShortcut() {
        Haptics.tap()
        shortcutOpened = true
        openURL(AppConfig.saveShortcutURL)
    }

    /// Opens Settings through the only URL Apple supports, which lands on
    /// Sava's own Settings page rather than on Action Button. The trail shown
    /// alongside is the rest of the walk. See `ActionButtonSupport`.
    private func openSettings() {
        Haptics.tap()
        withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
            settingsOpened = true
        }
        if let url = ActionButtonSupport.appSettingsURL { openURL(url) }
    }
}
