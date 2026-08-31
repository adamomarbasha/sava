import SwiftUI

/// Stage 4 — activation, not another page of information.
///
/// Three rows with a *status* each, so the screen answers "what is set up and
/// what is not" at a glance. Share Sheet is already Ready and says so: seeing
/// one row green is what makes the other two feel like optional polish rather
/// than a wall of setup between the user and the app.
struct SetupChecklist: View {
    let shortcutOpened: Bool
    let settingsOpened: Bool
    let onAddShortcut: () -> Void
    let onOpenSettings: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            SetupRow(
                title: "Share sheet",
                detail: "In TikTok, Instagram, YouTube or Safari: Share, then Sava.",
                status: .ready,
                action: nil)

            Divider().background(SavaColor.hairline)

            SetupRow(
                title: "Sava Shortcut",
                detail: shortcutOpened
                    ? "Finish adding it in Shortcuts, then come back."
                    : "Optional. Lets the Action Button grab a link without "
                      + "copying it first.",
                status: shortcutOpened ? .inProgress : .todo,
                actionTitle: shortcutOpened ? "Open again" : "Add",
                action: onAddShortcut)

            if ActionButtonSupport.isAvailable {
                Divider().background(SavaColor.hairline)
                // Laid out vertically, unlike the rows above it, because the
                // path has to be *visible with the button* rather than revealed
                // by pressing it. iOS opens Sava's own Settings page and no
                // further — see `ActionButtonSupport` — so the four remaining
                // taps are the user's to make, and a button that drops somebody
                // into Settings with no map is where setup is abandoned.
                VStack(alignment: .leading, spacing: Space.m) {
                    SetupRow(
                        title: "Action Button",
                        detail: "Copy a link, press the button. No Shortcut needed.",
                        status: settingsOpened ? .inProgress : .todo,
                        action: nil)
                        .padding(.bottom, -Space.s)

                    VStack(alignment: .leading, spacing: Space.s) {
                        Text("Settings opens on Sava's own page. From there:")
                            .font(SavaType.meta)
                            .foregroundStyle(SavaColor.tertiary)
                            .fixedSize(horizontal: false, vertical: true)
                        SettingsPathTrail()
                        Button("Open Settings", action: onOpenSettings)
                            .font(SavaType.caption)
                            .foregroundStyle(SavaColor.onAccentTint)
                            .frame(maxWidth: .infinity)
                            .frame(minHeight: 44)
                            .background(SavaColor.accentTint, in: Capsule())
                            .buttonStyle(.plain)
                    }
                    .padding(.horizontal, Space.l)
                    .padding(.bottom, Space.l)
                }
            } else {
                // No Action Button on this iPhone. Saying so is better than
                // showing setup steps for hardware the user does not have.
                Divider().background(SavaColor.hairline)
                HStack(alignment: .top, spacing: Space.m) {
                    Image(systemName: "info.circle")
                        .font(.system(size: 15))
                        .foregroundStyle(SavaColor.tertiary)
                    Text("The Action Button arrived with iPhone 15 Pro. On this "
                         + "iPhone, the share sheet is the fast way to save.")
                        .font(SavaType.meta)
                        .foregroundStyle(SavaColor.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .padding(Space.l)
            }
        }
        .background(SavaColor.surface,
                    in: RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
            .strokeBorder(SavaColor.hairline, lineWidth: 0.5))
    }
}

private struct SetupRow: View {
    enum Status { case ready, inProgress, todo }

    let title: String
    let detail: String
    let status: Status
    var actionTitle: String = ""
    let action: (() -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: Space.m) {
            statusMark
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: Space.s) {
                    Text(title)
                        .font(SavaType.title)
                        .foregroundStyle(SavaColor.primary)
                    if status == .ready {
                        Text("READY")
                            .font(.system(size: 9, weight: .black))
                            .tracking(0.7)
                            .foregroundStyle(SavaColor.onAccent)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(SavaColor.accent, in: Capsule())
                    }
                }
                Text(detail)
                    .font(SavaType.meta)
                    .foregroundStyle(SavaColor.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: Space.s)

            if let action {
                Button(actionTitle, action: action)
                    .font(SavaType.caption)
                    .foregroundStyle(SavaColor.onAccentTint)
                    .padding(.horizontal, Space.m)
                    .frame(minHeight: 34)
                    .background(SavaColor.accentTint, in: Capsule())
                    .buttonStyle(.plain)
            }
        }
        .padding(Space.l)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var statusMark: some View {
        switch status {
        case .ready:
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 17))
                .foregroundStyle(SavaColor.accent)
        case .inProgress:
            Image(systemName: "circle.dotted")
                .font(.system(size: 17))
                .foregroundStyle(SavaColor.accent)
        case .todo:
            Image(systemName: "circle")
                .font(.system(size: 17))
                .foregroundStyle(SavaColor.tertiary)
        }
    }
}

// MARK: - Why the Shortcut exists

/// The Action Button chain, drawn.
///
/// This used to teach "Add the Shortcut → assign it → copy a link → press",
/// which overstated the setup. `SavaShortcuts` is an `AppShortcutsProvider`, so
/// **"Save to Sava" is already in Settings → Action Button → Shortcut** — there
/// is nothing to install. The real chain is one assignment and then a copy.
///
/// The published iCloud Shortcut still exists and does more (it can read a URL
/// off the screen without copying), but it is an upgrade, not a prerequisite,
/// and onboarding is the wrong place to sell it.
struct ActionButtonChain: View {
    private let links: [(String, String)] = [
        ("gear", "Assign it\nin Settings"),
        ("link", "Copy any\nlink"),
        ("button.horizontal.top.press", "Press the\nbutton"),
        ("bookmark.fill", "Saved"),
    ]

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(Array(links.enumerated()), id: \.offset) { index, link in
                VStack(spacing: 6) {
                    Image(systemName: link.0)
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(index == links.count - 1
                                         ? SavaColor.accentTint : SavaColor.secondary)
                        .frame(width: 38, height: 38)
                        .background(SavaColor.fill, in: Circle())
                    Text(link.1)
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(SavaColor.tertiary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity)
                if index < links.count - 1 {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(SavaColor.hairline)
                        .padding(.top, 14)
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Assign Save to Sava in Settings, copy any link, "
                            + "press the button, and it is saved.")
    }
}

// MARK: - The Settings path

/// Where to go once Settings opens.
///
/// iOS has no supported URL that lands on the Action Button screen — see
/// `ActionButtonSupport` — so the honest design is to open Settings and show
/// the remaining four taps as a trail. Written as a breadcrumb rather than a
/// sentence because that is the shape people match against a screen.
struct SettingsPathTrail: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            ForEach(Array(ActionButtonSupport.settingsPath.enumerated()), id: \.offset) { i, part in
                HStack(spacing: Space.s) {
                    if i > 0 {
                        Image(systemName: "arrow.turn.down.right")
                            .font(.system(size: 9, weight: .bold))
                            .foregroundStyle(SavaColor.tertiary)
                            .padding(.leading, CGFloat(i - 1) * 12)
                    }
                    Text(part)
                        .font(.system(size: 13, weight: i == ActionButtonSupport.settingsPath.count - 1
                                      ? .semibold : .regular))
                        .foregroundStyle(i == ActionButtonSupport.settingsPath.count - 1
                                         ? SavaColor.accent : SavaColor.secondary)
                }
            }
        }
        .padding(Space.m)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SavaColor.fill,
                    in: RoundedRectangle(cornerRadius: Radius.control, style: .continuous))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("In Settings, go to "
            + ActionButtonSupport.settingsPath.dropFirst().joined(separator: ", then "))
    }
}

// MARK: - Where the share sheet works

/// The apps people actually save from, as chips.
///
/// Named rather than drawn with their logos: shipping other companies' marks
/// means shipping their trademarks, and Sava's own type on its own chip says
/// the same thing without borrowing anything. The final chip is the honest
/// generalisation — the share sheet is a system feature, not an integration
/// Sava had to build per app.
struct ShareTargets: View {
    private let names = ["TikTok", "Instagram", "YouTube", "Safari", "Anywhere else"]

    var body: some View {
        // A wrapping flow, because at accessibility text sizes five chips do
        // not fit on two lines and a horizontal scroller hides the last one.
        FlowLayout(spacing: Space.s) {
            ForEach(names, id: \.self) { name in
                Text(name)
                    .font(SavaType.caption)
                    .foregroundStyle(name == names.last ? SavaColor.tertiary
                                                        : SavaColor.secondary)
                    .padding(.horizontal, Space.m)
                    .frame(height: 32)
                    .background(SavaColor.fill, in: Capsule())
                    .overlay {
                        if name == names.last {
                            Capsule().strokeBorder(SavaColor.hairline,
                                                   style: .init(lineWidth: 0.5, dash: [3, 3]))
                        }
                    }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Works in " + names.dropLast().joined(separator: ", ")
                            + ", and anywhere else with a share button.")
    }
}

/// Left-to-right wrapping stack.
///
/// SwiftUI has no wrapping stack, and the alternatives are worse: a fixed grid
/// leaves ragged holes because these chips are different widths, and an
/// `HStack` in a `ScrollView` hides content off the edge — which at large text
/// sizes would hide most of the list.
struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews,
                      cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        let rows = arrange(subviews: subviews, width: width)
        let height = rows.reduce(0) { $0 + $1.height } +
            spacing * CGFloat(max(0, rows.count - 1))
        return CGSize(width: proposal.width ?? rows.map(\.width).max() ?? 0,
                      height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize,
                       subviews: Subviews, cache: inout ()) {
        let rows = arrange(subviews: subviews, width: bounds.width)
        var y = bounds.minY
        for row in rows {
            var x = bounds.minX
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y),
                                      anchor: .topLeading,
                                      proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row {
        var indices: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(subviews: Subviews, width: CGFloat) -> [Row] {
        var rows: [Row] = []
        var current = Row()
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            let needed = current.indices.isEmpty ? size.width
                                                 : current.width + spacing + size.width
            if needed > width && !current.indices.isEmpty {
                rows.append(current)
                current = Row()
            }
            if current.indices.isEmpty {
                current.width = size.width
            } else {
                current.width += spacing + size.width
            }
            current.indices.append(index)
            current.height = max(current.height, size.height)
        }
        if !current.indices.isEmpty { rows.append(current) }
        return rows
    }
}

/// The last screen's evidence.
///
/// Three facts about what the user now has, each tied to something they saw
/// happen in the tour rather than to a feature list. The first counts the cards
/// they actually dragged in during Stage 1 — the tour ends by handing their own
/// actions back to them.
struct ReadyRecap: View {
    let savedCount: Int

    private var rows: [(String, String, String)] {
        [("tray.full", savedCount > 0 ? "\(savedCount) saved already"
                                      : "Save from any app",
          "Share sheet, or one press"),
         ("text.magnifyingglass", "Findable by anything",
          "A word, a phrase, or the gist"),
         ("bubble.left.and.text.bubble.right", "Ask about any of it",
          "Sava has read what you saved")]
    }

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                if index > 0 {
                    Rectangle().fill(SavaColor.hairline).frame(height: 0.5)
                }
                HStack(spacing: Space.m) {
                    Image(systemName: row.0)
                        .font(.system(size: 15, weight: .medium))
                        // `accentTint`, not `accent`: at glyph size the fill
                        // token inverts to ink on paper.
                        .foregroundStyle(SavaColor.accentTint)
                        .frame(width: 24)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(row.1)
                            .font(SavaType.title)
                            .foregroundStyle(SavaColor.primary)
                        Text(row.2)
                            .font(SavaType.meta)
                            .foregroundStyle(SavaColor.tertiary)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.vertical, Space.m)
                .padding(.horizontal, Space.l)
                .accessibilityElement(children: .combine)
            }
        }
        .background(SavaColor.surface,
                    in: RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
            .strokeBorder(SavaColor.hairline, lineWidth: 0.5))
    }
}
