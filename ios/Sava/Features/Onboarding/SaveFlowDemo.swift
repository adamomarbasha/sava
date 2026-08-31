import SwiftUI

/// Stage 2 — the two ways a link gets into Sava, shown rather than described.
///
/// ── Why this replaced three bullet points ───────────────────────────────
///
/// The previous screen listed "The share sheet / The Sava Shortcut / The Action
/// Button" as three cards of prose. It named the machinery without ever showing
/// the user what to *do*, and it implied the three were alternatives when in
/// fact the Shortcut is not a way to save at all — it is the thing that makes
/// the Action Button work.
///
/// So there are two methods here, not three, and each is a short film:
///
///   SHARE SHEET     open the post → Share → Sava            (nothing to set up)
///   ACTION BUTTON   Copy Link → press the button → saved    (Shortcut required)
///
/// and the platform picker changes what the film is *of*, because "Share" is a
/// different-looking gesture in TikTok than it is in Safari and people
/// recognise their own app.
///
/// ── What it deliberately does not claim ─────────────────────────────────
///
/// Every path shown ends in a **link**. Nothing here suggests Sava can watch
/// the screen, intercept playback, or save something it was not handed a URL
/// for — because it cannot, and an onboarding promise the product then fails to
/// keep is worse than no onboarding.
struct SaveFlowDemo: View {
    @Binding var platform: DemoPlatform
    @Binding var method: SaveMethod

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// How far through the sequence we are: 0 = start, `steps.count` = saved.
    @State private var step = 0
    @State private var runID = 0

    private var steps: [FlowStep] { method.steps(for: platform) }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            platformPicker
            methodPicker
            stage
            caption
        }
        .onChange(of: platform) { _, _ in restart() }
        .onChange(of: method) { _, _ in restart() }
        .onAppear { restart() }
        .onDisappear { runID &+= 1 }   // cancels any in-flight sequence
    }

    // MARK: Pickers

    private var platformPicker: some View {
        HStack(spacing: Space.s) {
            ForEach(DemoPlatform.allCases) { option in
                let selected = option == platform
                Button {
                    Haptics.tap()
                    withAnimation(Motion.respecting(Motion.tap, reduceMotion)) {
                        platform = option
                    }
                } label: {
                    HStack(spacing: 7) {
                        PlatformMark(platform: option.mark, size: 15,
                                     monochrome: selected ? SavaColor.onAccent : nil)
                        Text(option.title)
                            .font(SavaType.caption)
                    }
                    .foregroundStyle(selected ? SavaColor.onAccent : SavaColor.secondary)
                    .padding(.horizontal, Space.m)
                    .frame(height: 34)
                    .background(selected ? SavaColor.accent : SavaColor.fill, in: Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(option.title)
                .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
            }
            Spacer(minLength: 0)
        }
    }

    private var methodPicker: some View {
        HStack(spacing: 0) {
            ForEach(SaveMethod.allCases) { option in
                let selected = option == method
                Button {
                    Haptics.tap()
                    withAnimation(Motion.respecting(Motion.tap, reduceMotion)) {
                        method = option
                    }
                } label: {
                    VStack(spacing: 2) {
                        Text(option.title)
                            .font(SavaType.caption)
                            .foregroundStyle(selected ? SavaColor.primary : SavaColor.tertiary)
                        Text(option.requirement)
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(selected ? SavaColor.accent : SavaColor.tertiary)
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 46)
                    .background {
                        if selected {
                            RoundedRectangle(cornerRadius: Radius.control - 2,
                                             style: .continuous)
                                .fill(SavaColor.surface)
                                .overlay(RoundedRectangle(cornerRadius: Radius.control - 2,
                                                          style: .continuous)
                                    .strokeBorder(SavaColor.hairline, lineWidth: 0.5))
                        }
                    }
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(option.title). \(option.requirement)")
                .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
            }
        }
        .padding(3)
        .background(SavaColor.fill, in: RoundedRectangle(cornerRadius: Radius.control,
                                                         style: .continuous))
    }

    // MARK: The film

    private var stage: some View {
        HStack(alignment: .center, spacing: 0) {
            ForEach(Array(steps.enumerated()), id: \.element.id) { index, item in
                FlowStepView(step: item,
                             state: state(for: index),
                             platform: platform)
                if index < steps.count - 1 {
                    FlowArrow(active: step > index, reduceMotion: reduceMotion)
                }
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Space.l)
        .padding(.horizontal, Space.m)
        .background(SavaColor.surface,
                    in: RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
            .strokeBorder(SavaColor.hairline, lineWidth: 0.5))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spokenSummary)
    }

    private func state(for index: Int) -> FlowStepView.State {
        if step > index { return .done }
        if step == index { return .active }
        return .waiting
    }

    private var caption: some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.s) {
            Text(method.explanation(for: platform))
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: Space.s)
            Button {
                Haptics.tap()
                restart()
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "arrow.counterclockwise")
                        .font(.system(size: 11, weight: .semibold))
                    Text("Replay").font(SavaType.caption)
                }
                .foregroundStyle(SavaColor.accentBlueText)
                .frame(minHeight: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Replay this demonstration")
        }
    }

    private var spokenSummary: String {
        let names = steps.map(\.spoken).joined(separator: ", then ")
        return "\(platform.title), \(method.title): \(names)."
    }

    // MARK: Sequencing

    /// Advances one step at a time on a detached task, rather than with a chain
    /// of `withAnimation(_:completion:)` closures.
    ///
    /// `runID` invalidates the sequence when the user switches platform, method
    /// or screen mid-run — otherwise a demo that was cancelled two seconds ago
    /// keeps ticking and drives the *new* demo's steps.
    private func restart() {
        runID &+= 1
        let id = runID
        step = 0
        guard !reduceMotion else {
            // Reduce Motion gets the finished state, which is the whole story
            // in one frame: every step done, the save complete.
            step = steps.count
            return
        }
        Task { @MainActor in
            for index in 0...steps.count {
                try? await Task.sleep(for: .milliseconds(index == 0 ? 420 : 760))
                guard id == runID else { return }
                withAnimation(Motion.standard) { step = index + 1 }
            }
        }
    }
}

// MARK: - One step

struct FlowStep: Identifiable, Equatable {
    let id: String
    let symbol: String
    let label: String
    /// Read aloud instead of the symbol.
    let spoken: String
    /// Draws Sava's mark rather than an SF Symbol.
    var isSava: Bool = false
    /// Draws the physical Action Button rather than an SF Symbol.
    var isActionButton: Bool = false
    /// Draws the demo content card rather than an icon.
    var isContent: Bool = false
}

private struct FlowStepView: View {
    enum State { case waiting, active, done }

    let step: FlowStep
    let state: State
    let platform: DemoPlatform

    var body: some View {
        VStack(spacing: 7) {
            ZStack {
                if step.isContent {
                    DemoCard(item: platform.sample, width: 52, isCompact: true)
                } else {
                    RoundedRectangle(cornerRadius: 15, style: .continuous)
                        .fill(fill)
                        .frame(width: 52, height: 52)
                        .overlay(RoundedRectangle(cornerRadius: 15, style: .continuous)
                            .strokeBorder(border, lineWidth: 1))
                        .overlay(glyph)
                }
                if state == .done && !step.isContent {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(SavaColor.accent)
                        .background(Circle().fill(SavaColor.ground).padding(1.5))
                        .offset(x: 20, y: -18)
                        .transition(.scale.combined(with: .opacity))
                }
            }
            .scaleEffect(state == .active ? 1.07 : 1)
            .frame(height: 68)

            Text(step.label)
                .font(.system(size: 10.5, weight: .semibold))
                .foregroundStyle(state == .waiting ? SavaColor.tertiary : SavaColor.primary)
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .frame(width: 66)
        }
        .opacity(state == .waiting ? 0.42 : 1)
    }

    @ViewBuilder private var glyph: some View {
        if step.isSava {
            SavaMark(size: 24)
        } else if step.isActionButton {
            ActionButtonGlyph(pressed: state != .waiting)
        } else {
            Image(systemName: step.symbol)
                .font(.system(size: 20, weight: .medium))
                .foregroundStyle(state == .waiting ? SavaColor.tertiary : SavaColor.primary)
        }
    }

    private var fill: Color {
        state == .waiting ? SavaColor.fill : SavaColor.fill.opacity(0.9)
    }
    private var border: Color {
        state == .waiting ? SavaColor.hairline : SavaColor.accent.opacity(0.55)
    }
}

/// The physical button on the side of the phone, drawn.
///
/// A capsule on an edge, not an SF Symbol: there is no system glyph for the
/// Action Button, and the nearest ones (a bell, a switch) would teach the wrong
/// hardware. Showing the actual silhouette is what makes someone look at the
/// side of their phone.
private struct ActionButtonGlyph: View {
    let pressed: Bool
    var body: some View {
        HStack(spacing: 0) {
            Capsule()
                .fill(pressed ? SavaColor.accent : SavaColor.secondary)
                .frame(width: 4, height: 15)
                .offset(x: pressed ? 1.5 : 0)
            RoundedRectangle(cornerRadius: 5, style: .continuous)
                .strokeBorder(SavaColor.secondary, lineWidth: 1.6)
                .frame(width: 21, height: 32)
        }
        .animation(.spring(response: 0.2, dampingFraction: 0.6), value: pressed)
    }
}

/// The connector. Fills with citron as the step it follows completes.
private struct FlowArrow: View {
    let active: Bool
    let reduceMotion: Bool

    var body: some View {
        Image(systemName: "arrow.right")
            .font(.system(size: 11, weight: .bold))
            .foregroundStyle(active ? SavaColor.accent : SavaColor.hairline)
            .frame(maxWidth: .infinity)
            .offset(y: -8)
            .animation(Motion.respecting(Motion.standard, reduceMotion), value: active)
            .accessibilityHidden(true)
    }
}

// MARK: - What is being demonstrated

enum DemoPlatform: String, CaseIterable, Identifiable {
    case tiktok, instagram, youtube

    var id: String { rawValue }

    var platform: Platform {
        switch self {
        case .tiktok: return .tiktok
        case .instagram: return .instagram
        case .youtube: return .youtube
        }
    }

    /// The drawn brand mark.
    var mark: SavaPlatform {
        switch self {
        case .tiktok: return .tiktok
        case .instagram: return .instagram
        case .youtube: return .youtube
        }
    }

    /// What the share control is called in that app.
    var shareVerb: String { mark.shareVerb }

    var title: String {
        switch self {
        case .tiktok: return "TikTok"
        case .instagram: return "Instagram"
        case .youtube: return "YouTube"
        }
    }

    /// The card shown as the first frame of the demo.
    var sample: DemoItem {
        DemoLibrary.all.first { $0.platform == platform } ?? DemoLibrary.all[0]
    }

    /// What the share control is actually called in that app. Getting this
    /// wrong is how instructions stop matching the screen the user is looking
    /// at, which is the fastest way to lose them.

}

enum SaveMethod: String, CaseIterable, Identifiable {
    case shareSheet, actionButton

    var id: String { rawValue }

    var title: String {
        switch self {
        case .shareSheet:   return "Share sheet"
        case .actionButton: return "Action Button"
        }
    }

    /// Shown under the title in the picker. This is the distinction the old
    /// screen never made, and the single most useful sentence on it.
    /// Shown under the title in the picker.
    ///
    /// The Action Button line used to read "Needs the Shortcut", which is
    /// wrong. `SavaShortcuts` is an `AppShortcutsProvider`, so "Save to Sava"
    /// appears in Settings → Action Button → Shortcut on its own — nothing has
    /// to be installed first. What it does need is the one-time assignment,
    /// and a link on the clipboard when pressed.
    var requirement: String {
        switch self {
        case .shareSheet:   return "Nothing to set up"
        case .actionButton: return "One-time setup"
        }
    }

    func steps(for platform: DemoPlatform) -> [FlowStep] {
        switch self {
        case .shareSheet:
            return [
                FlowStep(id: "content", symbol: "", label: platform.title,
                         spoken: "the post in \(platform.title)", isContent: true),
                FlowStep(id: "share", symbol: "square.and.arrow.up",
                         label: platform.shareVerb, spoken: "tap Share"),
                FlowStep(id: "sava", symbol: "", label: "Sava",
                         spoken: "choose Sava", isSava: true),
            ]
        case .actionButton:
            return [
                FlowStep(id: "content", symbol: "", label: platform.title,
                         spoken: "the post in \(platform.title)", isContent: true),
                FlowStep(id: "copy", symbol: "link", label: "Copy link",
                         spoken: "tap Copy Link"),
                FlowStep(id: "press", symbol: "", label: "Press button",
                         spoken: "press the Action Button", isActionButton: true),
                FlowStep(id: "sava", symbol: "", label: "Saved",
                         spoken: "Sava saves it", isSava: true),
            ]
        }
    }

    func explanation(for platform: DemoPlatform) -> String {
        switch self {
        case .shareSheet:
            return "In \(platform.title): \(platform.shareVerb), then Sava. "
                + "Works the moment you install the app."
        case .actionButton:
            return platform == .instagram
                ? "Instagram never puts the link on screen, so Copy Link is the "
                    + "way in — then one press, without leaving the app."
                : "Copy the link, then one press. You never open Sava."
        }
    }
}
