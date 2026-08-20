import SwiftUI

/// Account and capture setup.
///
/// This is a settings screen, so it looks like one: grouped rows and hairlines,
/// not a page of cards. The one thing worth explaining — how to put Sava on the
/// Action Button — gets a sentence, and everything else is a row.
struct ProfileView: View {
    let user: User

    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var library: LibraryViewModel
    @Environment(\.openURL) private var openURL
    @Environment(\.dismiss) private var dismiss

    @State private var confirmSignOut = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xxl) {
                identity
                libraryStats
                captureSection
                #if DEBUG
                debugSection
                #endif
                signOut
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
        .confirmationDialog("Sign out of Sava?", isPresented: $confirmSignOut) {
            Button("Sign out", role: .destructive) {
                Haptics.press()
                session.signOut()
            }
        }
    }

    private var identity: some View {
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
                    SavaRow(title: "Saves", detail: "\(library.all.count)",
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

    private var captureSection: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            SectionHeader(text: "One-press save")
            Text("Assign Sava to your Action Button to save whatever you're watching without opening the app.")
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(spacing: 0) {
                SavaRow(title: "Open Shortcuts", symbol: "square.stack.3d.up") {
                    if let url = URL(string: "shortcuts://") { openURL(url) }
                }
                SavaRow(title: "Action Button", detail: "Settings → Action Button",
                        symbol: "button.horizontal.top.press", showsChevron: false)
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
        SavaButton(title: "Sign out", role: .destructive) {
            confirmSignOut = true
        }
    }
}
