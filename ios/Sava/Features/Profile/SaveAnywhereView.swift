import SwiftUI
import UIKit

/// **Learn Sava** — everything the tour teaches, kept permanently.
///
/// Reached from Profile → Learn Sava. Onboarding happens once, in the minute
/// somebody is least able to absorb it; this is the same material without the
/// pacing, plus the two things the tour cannot do (reinstall the Shortcut, get
/// back into Settings) and the tour itself on a button.
///
/// It shares `SaveFlowDemo` with Stage 2 rather than restating it in prose. One
/// definition of "how you save from Instagram" means the tour and the help
/// screen cannot drift apart — and the last time they were written separately,
/// they disagreed about whether the Shortcut was required.
///
/// ── Two honest claims ───────────────────────────────────────────────────
///
/// The Shortcut is **installed by Apple** — this opens the iCloud link and
/// stops, because there is no API to add a Shortcut to somebody's library and
/// there should not be. The Action Button is **assigned by the user** —
/// likewise no API, public or private: the button belongs to the person holding
/// the phone. So this screen does the one thing an app can do, opens the two
/// places, and never claims to have done anything itself.
struct SaveAnywhereView: View {
    @Environment(\.openURL) private var openURL
    @Environment(\.dismiss) private var dismiss

    /// Set when iOS declined to open the iCloud link — no Shortcuts app, a
    /// managed device, or no network. Shown rather than swallowed, with the
    /// link offered as text so the user still has a way through.
    @State private var couldNotOpen = false
    @State private var copiedLink = false
    @State private var showTour = false
    @State private var demoPlatform: DemoPlatform = .tiktok
    @State private var demoMethod: SaveMethod = .shareSheet
    @EnvironmentObject private var session: SessionStore

    private var shortcut: URL { AppConfig.saveShortcutURL }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xxl) {
                hero
                howToSave
                shortcutSection
                if ActionButtonSupport.isAvailable { actionButtonSection }
                elsewhere
                replayTour
            }
            .screenPadding()
            .padding(.top, Space.l)
            .padding(.bottom, Space.xxl)
        }
        .background(SavaColor.ground.ignoresSafeArea())
        .navigationTitle("Learn Sava")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Done") { dismiss() }
                    .font(SavaType.caption)
                    .foregroundStyle(SavaColor.secondary)
            }
        }
        .tint(SavaColor.primary)
        // Presented here rather than by flipping durable state and hoping the
        // root re-evaluates: this screen sits several levels inside a
        // NavigationStack, and a cover is the honest way to show a full-screen
        // flow from it. Finishing simply dismisses — completion was already
        // recorded the first time.
        .fullScreenCover(isPresented: $showTour) {
            OnboardingView(userID: session.currentUser?.id) { showTour = false }
        }
    }

    // MARK: Hero

    private var hero: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Text("Save from anywhere.")
                .font(SavaType.display)
                .tracking(Tracking.tight)
                .foregroundStyle(SavaColor.primary)
                .fixedSize(horizontal: false, vertical: true)
            Text("TikTok, Instagram, YouTube, the web. Sava saves the link — "
                 + "you never have to open the app.")
                .font(SavaType.prose)
                .foregroundStyle(SavaColor.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: The demos, same as the tour

    private var howToSave: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "How to save")
            SaveFlowDemo(platform: $demoPlatform, method: $demoMethod)
        }
    }

    // MARK: The Shortcut

    private var shortcutSection: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "The Sava Shortcut (optional)")

            // Corrected framing.
            //
            // This said "Only needed for the Action Button", which is not true:
            // `SavaShortcuts` is an `AppShortcutsProvider`, so "Save to Sava"
            // already appears in Settings → Action Button → Shortcut with
            // nothing installed. The Shortcut is an *upgrade* — it can read a
            // URL off the screen, so it saves without copying first.
            Text("Neither way of saving needs it. Add it if you want the Action "
                 + "Button to grab the link off the screen without copying.")
                .font(SavaType.body)
                .foregroundStyle(SavaColor.primary)
                .fixedSize(horizontal: false, vertical: true)

            SavaButton(title: "Add Sava Shortcut") { addShortcut() }

            Text("Opens Apple's Shortcuts app, which asks you to confirm. Adds "
                 + "“\(AppConfig.officialSaveShortcutName)” to your library. "
                 + "Safe to run again if you removed it.")
                .font(SavaType.meta)
                .foregroundStyle(SavaColor.tertiary)
                .fixedSize(horizontal: false, vertical: true)

            if couldNotOpen {
                VStack(alignment: .leading, spacing: Space.s) {
                    Text("Couldn't open Shortcuts. Open this link on your iPhone:")
                        .font(SavaType.meta)
                        .foregroundStyle(SavaColor.danger)
                        .fixedSize(horizontal: false, vertical: true)
                    SavaRow(title: copiedLink ? "Copied" : "Copy link",
                            symbol: copiedLink ? "checkmark" : "doc.on.doc") {
                        copyLink()
                    }
                }
                .transition(.opacity)
            }
        }
    }

    // MARK: The Action Button

    /// The path, and the name the picker actually shows.
    ///
    /// The installed Shortcut and Sava's built-in action are **two separate
    /// entries** in Settings → Action Button → Shortcut: the Shortcut appears
    /// under Shortcuts by its own name, Sava's action appears under Sava. Both
    /// end at the same save. Naming only one of them is what sends people back
    /// to scroll a list looking for a title that is not in it.
    private var actionButtonSection: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "Action Button setup")

            Text("Copy a link, press the button, it's saved.")
                .font(SavaType.body)
                .foregroundStyle(SavaColor.primary)
                .fixedSize(horizontal: false, vertical: true)

            ActionButtonChain()
                .padding(.vertical, Space.s)

            SettingsPathTrail()

            Text("Or pick “Save to Sava” under Sava — both end at the same save.")
                .font(SavaType.meta)
                .foregroundStyle(SavaColor.tertiary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(spacing: 0) {
                // iOS publishes no URL that opens Settings → Action Button, so
                // this opens Sava's own Settings page, which is as close as the
                // supported API gets. See `ActionButtonSupport`.
                SavaRow(title: "Open iPhone Settings", symbol: "gear") {
                    if let url = ActionButtonSupport.appSettingsURL { openURL(url) }
                }
                SavaRow(title: "Open Shortcuts", symbol: "square.stack.3d.up") {
                    if let url = URL(string: "shortcuts://") { openURL(url) }
                }
            }

            // Said plainly rather than left for the user to discover by waiting
            // for something that is never going to happen.
            Text("Sava can't set the Action Button for you — only you can, in "
                 + "Settings.")
                .font(SavaType.meta)
                .foregroundStyle(SavaColor.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Everywhere else

    private var elsewhere: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            SectionHeader(text: "Also works")
            Text("Share → Sava, from any app's share sheet.")
                .font(SavaType.body)
                .foregroundStyle(SavaColor.primary)
                .fixedSize(horizontal: false, vertical: true)
            Text("And “Save to Sava” by voice, or from the Shortcuts app.")
                .font(SavaType.meta)
                .foregroundStyle(SavaColor.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The tour, kept available forever.
    ///
    /// Onboarding that can only ever be seen once is a document you are not
    /// allowed to re-read. Nothing in it is single-use, and the people most
    /// likely to want it again are the ones who skipped it.
    private var replayTour: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "The tour")
            SavaRow(title: "Watch it again",
                    detail: "About a minute",
                    symbol: "sparkles.rectangle.stack") {
                Haptics.tap()
                showTour = true
            }
        }
    }

    // MARK: Actions

    /// Opens the iCloud link and lets Apple install it.
    ///
    /// Sava never writes to the Shortcuts library. There is no API for it, and
    /// a home-made clone of the Shortcut would be a second thing to keep in
    /// step with the intents it calls.
    private func addShortcut() {
        openURL(shortcut) { accepted in
            withAnimation(Motion.gentle) { couldNotOpen = !accepted }
        }
    }

    private func copyLink() {
        UIPasteboard.general.string = shortcut.absoluteString
        Haptics.press()
        withAnimation(Motion.gentle) { copiedLink = true }
        Task {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            withAnimation(Motion.gentle) { copiedLink = false }
        }
    }
}
