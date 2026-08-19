import SwiftUI

/// Profile + the Action Button setup guide — the home of Sava's signature
/// one-press capture. Explains, honestly, how capture behaves per platform.
struct ProfileView: View {
    let user: User
    @EnvironmentObject private var session: SessionStore
    @Environment(\.openURL) private var openURL

    var body: some View {
        ScrollView {
            VStack(spacing: Spacing.lg) {
                identity
                actionButtonCard
                captureBehaviorCard
                #if DEBUG
                captureDebugCard
                #endif
                signOutButton
            }
            .padding(.horizontal, Spacing.md)
            .padding(.top, Spacing.md)
            .padding(.bottom, 120)
        }
        .background(SavaColors.background)
        .navigationTitle("Profile")
    }

    private var identity: some View {
        VStack(spacing: Spacing.sm) {
            SavaMark(size: 64)
            Text(user.email)
                .font(SavaFont.headline)
                .foregroundStyle(SavaColors.textPrimary)
            if let joined = user.createdAt {
                Text("Saving since \(joined.formatted(.dateTime.month(.wide).year()))")
                    .font(SavaFont.footnote)
                    .foregroundStyle(SavaColors.textTertiary)
            }
        }
        .padding(.top, Spacing.sm)
    }

    private var actionButtonCard: some View {
        InfoCard(icon: "button.horizontal.top.press", title: "One-press save") {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text("Assign Sava to your Action Button to save whatever you're watching — without opening the app.")
                    .font(SavaFont.callout)
                    .foregroundStyle(SavaColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)

                SetupStep(number: 1, text: "Open Settings → Action Button")
                SetupStep(number: 2, text: "Swipe to Shortcut")
                SetupStep(number: 3, text: "Choose “Save to Sava”")

                Button {
                    Haptics.tap()
                    if let url = URL(string: "shortcuts://") { openURL(url) }
                } label: {
                    HStack(spacing: Spacing.xs) {
                        Image(systemName: "arrow.up.forward.app.fill")
                        Text("Open Shortcuts")
                    }
                    .font(SavaFont.subheadline)
                    .foregroundStyle(SavaColors.accent)
                }
                .buttonStyle(.pressable)
                .padding(.top, Spacing.xxs)
            }
        }
    }

    private var captureBehaviorCard: some View {
        InfoCard(icon: "wand.and.stars", title: "How capture works") {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                CaptureRow(platform: .tiktok,
                           text: "Grabs the link directly — instant save.")
                CaptureRow(platform: .youtube,
                           text: "Uses the link when available; screenshot resolution is coming.")
                CaptureRow(platform: .instagram,
                           text: "Same as YouTube — link first, then screenshot resolution.")
                Text("Screenshots are used only when a link isn't available, sent securely, and never saved to your photos.")
                    .font(SavaFont.footnote)
                    .foregroundStyle(SavaColors.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, Spacing.xxs)
            }
        }
    }

    #if DEBUG
    /// DEBUG-only: what the Action Button actually received on recent presses.
    /// Never compiled into Release.
    @ViewBuilder private var captureDebugCard: some View {
        InfoCard(icon: "ladybug", title: "Capture debug") {
            let traces = CaptureDiagnostics.recent()
            VStack(alignment: .leading, spacing: Spacing.sm) {
                if traces.isEmpty {
                    Text("No Action Button presses recorded yet. Press it once, then reopen this screen.")
                        .font(SavaFont.footnote)
                        .foregroundStyle(SavaColors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    ForEach(traces.prefix(8)) { t in
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Text(t.path)
                                    .font(.system(size: 12, weight: .bold, design: .monospaced))
                                    .foregroundStyle(t.outcome == "saved"
                                                     ? SavaColors.success : SavaColors.danger)
                                Spacer()
                                Text("\(t.durationMs)ms")
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundStyle(SavaColors.textTertiary)
                            }
                            Text("input: \(t.hadShortcutInput ? t.inputTypes.joined(separator: ", ") : "NOTHING RECEIVED")")
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(t.hadShortcutInput
                                                 ? SavaColors.textSecondary : SavaColors.danger)
                            if let u = t.providedURL {
                                Text("url: \(u)").font(.system(size: 10, design: .monospaced))
                                    .foregroundStyle(SavaColors.textTertiary).lineLimit(2)
                            }
                            if t.screenshotBytes > 0 {
                                Text("screenshot: \(t.screenshotBytes / 1024) KB")
                                    .font(.system(size: 10, design: .monospaced))
                                    .foregroundStyle(SavaColors.textTertiary)
                            }
                            if t.clipboardChecked {
                                Text("clipboard(\(t.clipboardType ?? "none")): \(t.clipboardValue ?? "empty")")
                                    .font(.system(size: 10, design: .monospaced))
                                    .foregroundStyle(SavaColors.textTertiary).lineLimit(1)
                            }
                            if let r = t.resolverReason {
                                Text("resolver: \(r)" + (t.resolverConfidence.map { String(format: " (%.2f)", $0) } ?? ""))
                                    .font(.system(size: 10, design: .monospaced))
                                    .foregroundStyle(SavaColors.textTertiary)
                            }
                            if let m = t.message {
                                Text(m).font(.system(size: 10))
                                    .foregroundStyle(SavaColors.textTertiary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .padding(Spacing.xs)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(SavaColors.surfaceMuted,
                                    in: RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
                    }
                    Button("Clear") { CaptureDiagnostics.clear() }
                        .font(SavaFont.subheadline)
                        .foregroundStyle(SavaColors.accent)
                }
            }
        }
    }
    #endif

    private var signOutButton: some View {
        Button {
            Haptics.press()
            session.signOut()
        } label: {
            Text("Sign out")
                .font(SavaFont.headline)
                .foregroundStyle(SavaColors.danger)
                .frame(maxWidth: .infinity).frame(height: 52)
                .background(SavaColors.surfaceMuted, in: RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        }
        .buttonStyle(.pressable)
    }
}

private struct InfoCard<Content: View>: View {
    let icon: String
    let title: String
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            HStack(spacing: Spacing.xs) {
                Image(systemName: icon)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(SavaColors.accent)
                Text(title)
                    .font(SavaFont.headline)
                    .foregroundStyle(SavaColors.textPrimary)
            }
            content()
        }
        .padding(Spacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                .fill(SavaColors.surface)
                .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                    .strokeBorder(SavaColors.hairline, lineWidth: 1))
        )
    }
}

private struct SetupStep: View {
    let number: Int
    let text: String
    var body: some View {
        HStack(spacing: Spacing.sm) {
            Text("\(number)")
                .font(.system(size: 13, weight: .bold, design: .rounded))
                .foregroundStyle(SavaColors.background)
                .frame(width: 24, height: 24)
                .background(Circle().fill(SavaColors.textPrimary))
            Text(text)
                .font(SavaFont.subheadline)
                .foregroundStyle(SavaColors.textPrimary)
        }
    }
}

private struct CaptureRow: View {
    let platform: Platform
    let text: String
    var body: some View {
        HStack(alignment: .top, spacing: Spacing.sm) {
            PlatformBadge(platform: platform, size: 22)
            Text(text)
                .font(SavaFont.footnote)
                .foregroundStyle(SavaColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
