import SwiftUI
import UIKit

/// How to save from anywhere: install the official Shortcut, then put it on the
/// Action Button.
///
/// One screen, two honest claims. The Shortcut is **installed by Apple** — this
/// opens the iCloud link and stops there, because there is no API to add a
/// Shortcut to somebody's library and there should not be. The Action Button is
/// **assigned by the user** — likewise no API, public or private: the button
/// belongs to the person holding the phone. So this screen does the one thing
/// an app can do (open the two places) and never claims to have done anything
/// itself.
///
/// The Shortcut is a wrapper, not a second implementation. It gathers what
/// Shortcuts can see and an App Intent cannot — the foreground app's URLs, the
/// clipboard, a screenshot — and hands it to `SaveLinkToSavaIntent` /
/// `SaveScreenshotToSavaIntent`. Every save still goes through `CapturePipeline`
/// and the same backend call the share extension and in-app save use.
struct SaveAnywhereView: View {
    @Environment(\.openURL) private var openURL
    @Environment(\.dismiss) private var dismiss

    /// Set when iOS declined to open the iCloud link — no Shortcuts app, a
    /// managed device, or no network. Shown rather than swallowed, with the
    /// link offered as text so the user still has a way through.
    @State private var couldNotOpen = false
    @State private var copiedLink = false

    private var shortcut: URL { AppConfig.saveShortcutURL }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                hero
                install
                actionButton
                elsewhere
            }
            .screenPadding()
            .padding(.top, Space.l)
            .padding(.bottom, Space.xxl)
        }
        .background(SavaColor.ground)
        .navigationTitle("Save from anywhere")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Done") { dismiss() }
            }
        }
        .tint(SavaColor.primary)
    }

    // MARK: Hero

    private var hero: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            HStack(spacing: Space.m) {
                SavaMark(size: 40)
                // The Action Button as the system draws it. A real SF Symbol
                // rather than a drawn illustration: it is the glyph Apple uses
                // in Settings, so it is the one the user is about to look for.
                Image(systemName: "button.horizontal.top.press")
                    .font(.system(size: 26, weight: .regular))
                    // Glyph, so the tint token rather than the fill token.
                    .foregroundStyle(SavaColor.accentTint)
                    .accessibilityHidden(true)
            }

            VStack(alignment: .leading, spacing: Space.s) {
                Text("Save from anywhere.")
                    .font(SavaType.display)
                    .tracking(Tracking.tight)
                    .foregroundStyle(SavaColor.primary)
                    .fixedSize(horizontal: false, vertical: true)

                Text("TikTok. Instagram. YouTube. The web.")
                    .font(SavaType.prose)
                    .foregroundStyle(SavaColor.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: Install

    private var install: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SavaButton(title: "Add Save to Sava") { addShortcut() }

            Text("One shortcut. Adds “\(AppConfig.officialSaveShortcutName)” to "
                 + "your Shortcuts library.")
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

    // MARK: Action Button

    /// Three steps, and the name the picker actually shows.
    ///
    /// The installed Shortcut and Sava's built-in action are **two separate
    /// entries** in Settings → Action Button → Shortcut: the Shortcut appears
    /// under Shortcuts by its own name, Sava's action appears under Sava. Both
    /// end at the same save. Naming only one of them is what sends people back
    /// to scroll a list looking for a title that is not in it.
    private var actionButton: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "Use with Action Button")

            VStack(alignment: .leading, spacing: Space.l) {
                StepRow(number: 1, text: "Open iPhone Settings")
                StepRow(number: 2, text: "Choose Action Button, then swipe to Shortcut")
                StepRow(number: 3,
                        text: "Pick “\(AppConfig.officialSaveShortcutName)” — or "
                            + "“Save to Sava” under Sava")
            }

            VStack(spacing: 0) {
                // Deep-links straight to Sava's own Settings page, which is as
                // close as iOS lets an app get. There is no URL that opens
                // Settings > Action Button directly, and inventing one that
                // silently lands on the Settings root would be worse than
                // naming the destination and letting the user tap twice.
                SavaRow(title: "Open iPhone Settings", symbol: "gear") {
                    if let url = URL(string: UIApplication.openSettingsURLString) {
                        openURL(url)
                    }
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
}

/// A numbered step.
///
/// The numeral sits in a hairline circle rather than a filled citron disc:
/// three accent marks on one screen is exactly the overuse that stops the
/// accent meaning anything.
private struct StepRow: View {
    let number: Int
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: Space.m) {
            Text("\(number)")
                .font(SavaType.numeric)
                .foregroundStyle(SavaColor.secondary)
                .frame(width: 26, height: 26)
                .overlay(Circle().strokeBorder(SavaColor.hairline, lineWidth: 0.5))

            Text(text)
                .font(SavaType.body)
                .foregroundStyle(SavaColor.primary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 2)

            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Step \(number). \(text)")
    }
}
