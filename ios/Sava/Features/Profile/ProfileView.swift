import SwiftUI

/// Account and capture setup.
///
/// This is a settings screen, so it looks like one: grouped rows and hairlines,
/// not a page of cards. The one thing worth explaining — how to put Sava on the
/// Action Button — gets a sentence, and everything else is a row.
struct ProfileView: View {
    @AppStorage(AppTheme.storageKey) private var themeRaw = AppTheme.dark.rawValue
    let user: User

    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var library: LibraryViewModel
    @EnvironmentObject private var subscriptions: SubscriptionManager
    @Environment(\.openURL) private var openURL
    @Environment(\.dismiss) private var dismiss

    @State private var confirmSignOut = false
    @State private var exporting = false
    @State private var exportPayload: SharePayload?
    @State private var showPaywall = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xxl) {
                identity
                planSection
                usageSection
                appearance
                libraryStats
                captureSection
                #if DEBUG
                debugSection
                #endif
                signOut
                accountSection
            }
            .screenPadding()
            .padding(.top, Space.l)
            .padding(.bottom, Space.xxl)
        }
        .devScrollAnchor()
        .background(SavaColor.ground)
        .navigationTitle("Profile")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Done") { dismiss() }
            }
        }
        .tint(SavaColor.primary)
        .sheet(item: $exportPayload) { payload in
            ShareSheet(items: [payload.url])
        }
        .sheet(isPresented: $showPaywall) {
            NavigationStack { PaywallView(context: "profile") }
        }
        .confirmationDialog("Sign out of Sava?", isPresented: $confirmSignOut) {
            Button("Sign out", role: .destructive) {
                Haptics.press()
                session.signOut()
            }
        }
    }

    /// Sava Pro. First thing under the identity, because "what am I paying
    /// for" is the question a settings screen most often gets opened to answer.
    private var planSection: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "Plan")
            SubscriptionRow { showPaywall = true }
            // Apple requires a way to reach subscription management, and a
            // subscriber should not have to open the *paywall* to cancel.
            if subscriptions.isPro {
                SavaRow(title: "Manage Subscription", symbol: "creditcard") {
                    openURL(AppConfig.Links.manageSubscription)
                }
            }
        }
    }

    private var usageSection: some View {
        UsageSection()
    }

    private var appearance: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "Appearance")
            AppearancePicker(selection: Binding(
                get: { AppTheme(rawValue: themeRaw) ?? .dark },
                set: { themeRaw = $0.rawValue }))
        }
    }

    private var identity: some View {
        HStack(alignment: .top, spacing: Space.l) {
            SavaMark(size: 52)
            identityText
        }
    }

    private var identityText: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(user.email)
                .font(SavaType.title)
                .foregroundStyle(SavaColor.primary)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            if let joined = user.createdAt, joined > .distantPast {
                Text("Saving since \(joined.formatted(.dateTime.month(.wide).year()))")
                    .font(SavaType.meta)
                    .foregroundStyle(SavaColor.tertiary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    /// A single honest line about the library, drawn from what is already
    /// loaded. Not a dashboard — one sentence, no tiles, no charts.
    @ViewBuilder private var libraryStats: some View {
        if !library.all.isEmpty {
            VStack(alignment: .leading, spacing: Space.m) {
                SectionHeader(text: "Library")
                VStack(spacing: 0) {
                    SavaRow(title: "Library", detail: "\(library.all.count)",
                            showsChevron: false)
                    ForEach(library.availablePlatforms.prefix(4)) { platform in
                        SavaRow(title: platform.displayName,
                                detail: "\(library.count(for: platform))",
                                showsChevron: false)
                    }
                }
            }
        }
    }

    /// One row into the setup screen, rather than the instructions inline.
    ///
    /// Installing the Shortcut and assigning the Action Button is a one-time
    /// job with its own links and its own copy. Leaving it half-explained on
    /// the settings screen meant a sentence and a dead row that told you where
    /// to go without taking you there.
    private var captureSection: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "Saving")
            VStack(spacing: 0) {
                NavigationLink {
                    SaveAnywhereView()
                } label: {
                    HStack(spacing: Space.m) {
                        Image(systemName: "button.horizontal.top.press")
                            .font(.system(size: 15))
                            .foregroundStyle(SavaColor.secondary)
                            .frame(width: 22)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Save from anywhere")
                                .font(SavaType.body)
                                .foregroundStyle(SavaColor.primary)
                            Text("Add the Shortcut, use the Action Button")
                                .font(SavaType.meta)
                                .foregroundStyle(SavaColor.tertiary)
                        }
                        Spacer(minLength: Space.s)
                        Image(systemName: "chevron.right")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(SavaColor.tertiary)
                    }
                    .frame(minHeight: 56)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .hairline()

                NavigationLink {
                    SaveAnywhereView()
                } label: {
                    HStack(spacing: Space.m) {
                        Image(systemName: "sparkles.rectangle.stack")
                            .font(.system(size: 15))
                            .foregroundStyle(SavaColor.secondary)
                            .frame(width: 22)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Learn Sava")
                                .font(SavaType.body)
                                .foregroundStyle(SavaColor.primary)
                            Text("The tour, and every way to save")
                                .font(SavaType.meta)
                                .foregroundStyle(SavaColor.tertiary)
                        }
                        Spacer(minLength: Space.s)
                        Image(systemName: "chevron.right")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(SavaColor.tertiary)
                    }
                    .frame(minHeight: 56)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .hairline()

                SavaRow(title: "Open Shortcuts", symbol: "square.stack.3d.up") {
                    if let url = URL(string: "shortcuts://") { openURL(url) }
                }
            }
        }
    }

    #if DEBUG
    /// Capture diagnostics. Debug builds only — never compiled into Release.
    private var debugSection: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "Capture debug")
            let traces = CaptureDiagnostics.recent()
            if traces.isEmpty {
                Text("No Action Button presses recorded yet.")
                    .font(SavaType.callout)
                    .foregroundStyle(SavaColor.secondary)
            } else {
                VStack(spacing: Space.s) {
                    ForEach(traces.prefix(6)) { trace in
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Text(trace.path)
                                    .font(.system(size: 11, weight: .bold, design: .monospaced))
                                    .foregroundStyle(trace.outcome == "failed"
                                                     ? SavaColor.danger : SavaColor.success)
                                Spacer()
                                Text("\(trace.durationMs)ms")
                                    .font(.system(size: 10, design: .monospaced))
                                    .foregroundStyle(SavaColor.tertiary)
                            }
                            Text("urls=\(trace.onScreenURLCount) selected=\(trace.selectedURL ?? "nil")")
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundStyle(SavaColor.tertiary)
                                .lineLimit(2)
                            if let message = trace.message {
                                Text(message)
                                    .font(.system(size: 10))
                                    .foregroundStyle(SavaColor.tertiary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .padding(Space.s)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(SavaColor.fill,
                                    in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    }
                }
                Button("Clear") { CaptureDiagnostics.clear() }
                    .font(SavaType.caption)
                    .foregroundStyle(SavaColor.accent)
            }
        }
    }
    #endif

    private var signOut: some View {
        SavaButton(title: "Sign out", role: .secondary) {
            confirmSignOut = true
        }
    }

    /// Export and deletion.
    ///
    /// Placed last and styled quietly: these are the two things nobody wants to
    /// tap by accident. Deletion is nonetheless a plain, visible control rather
    /// than something buried — App Store Guideline 5.1.1(v) requires it to be
    /// findable, and hiding it would be hostile regardless of the rule.
    private var accountSection: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "Your data")

            Button {
                Haptics.tap()
                Task { await exportData() }
            } label: {
                HStack(spacing: Space.s) {
                    Image(systemName: exporting
                          ? "arrow.down.circle" : "square.and.arrow.up")
                        .font(.system(size: 13, weight: .semibold))
                    Text(exporting ? "Preparing…" : "Export my data")
                        .font(SavaType.callout)
                    Spacer(minLength: 0)
                }
                .foregroundStyle(SavaColor.secondary)
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(exporting)

            NavigationLink {
                DeleteAccountView()
            } label: {
                HStack(spacing: Space.s) {
                    Image(systemName: "trash")
                        .font(.system(size: 13, weight: .semibold))
                    Text("Delete account")
                        .font(SavaType.callout)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(SavaColor.tertiary)
                }
                .foregroundStyle(SavaColor.danger)
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    private func exportData() async {
        exporting = true
        defer { exporting = false }
        guard let data = try? await AccountService(client: session.api).exportData(),
              !data.isEmpty else { return }
        // Written to a temp file and handed to the share sheet: a JSON export is
        // something people send to themselves, not something to read on a phone.
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("sava-export.json")
        try? data.write(to: url)
        exportPayload = SharePayload(url: url)
    }
}
