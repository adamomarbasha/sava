import SwiftUI
import UIKit

/// Light, dark, or whatever the phone is doing.
///
/// Three options, which is what every well-behaved iOS app offers — Automatic,
/// Light, Dark. Automatic means *follow the system*, and that is the whole
/// implementation: iOS already knows the time of day, already runs the
/// sunrise/sunset schedule if the user has turned it on, and already respects
/// Focus modes and accessibility settings that force one appearance.
///
/// An app that ran its own clock would fight all of that — it would go dark at
/// 6pm for someone who had deliberately pinned their phone to light, and it
/// would ignore a scheduled change the user configured in Settings. Following
/// the system is not the lazy option here; it is the correct one.
enum AppTheme: String, CaseIterable, Identifiable {
    case automatic, light, dark

    var id: String { rawValue }

    var label: String {
        switch self {
        case .automatic: return "Automatic"
        case .light:     return "Light"
        case .dark:      return "Dark"
        }
    }

    var symbol: String {
        switch self {
        case .automatic: return "circle.lefthalf.filled"
        case .light:     return "sun.max"
        case .dark:      return "moon"
        }
    }

    /// The shared representation. `AppTheme` is the app's UI-facing enum —
    /// labels, symbols, the picker — while the *preference* itself lives in
    /// `SavaShared` so the share extension can read the same value.
    var preference: AppearancePreference {
        AppearancePreference(rawValue: rawValue) ?? .dark
    }

    /// `.unspecified` hands the decision back to the system.
    var interfaceStyle: UIUserInterfaceStyle { preference.interfaceStyle }

    /// The stored preference. Read wherever it is needed; written only by the
    /// picker in Profile.
    static let storageKey = AppearancePreference.storageKey

    /// The app-group store, so the app and the share extension read one value.
    ///
    /// This was `UserDefaults.standard`, which is per-process — the extension
    /// could not see the preference and rendered in the system appearance
    /// regardless of what the user had chosen.
    static var store: UserDefaults { AppearancePreference.store }

    /// Apply the appearance to the whole app, not just the SwiftUI tree.
    ///
    /// This used to be `.preferredColorScheme` on the root view, and that was
    /// the bug behind "it doesn't switch all of the UI". That modifier sets a
    /// SwiftUI *environment* value, so it reaches SwiftUI views and nothing
    /// else: the navigation and tab bars, the keyboard, share sheets, context
    /// menus, alerts, scroll indicators and anything presented in its own
    /// hosting controller all kept the old appearance until the app was
    /// relaunched.
    ///
    /// `overrideUserInterfaceStyle` on the window sets the *trait collection*
    /// instead. Every one of those surfaces derives from it, and so does
    /// SwiftUI's `\.colorScheme` — so this is strictly more coverage through one
    /// mechanism rather than two mechanisms that can disagree.
    ///
    /// The crossfade is the other half of the complaint. Re-resolving every
    /// dynamic colour in a live tree is not free, and switching without a
    /// transition shows that work as a visible stutter. Handing the change to
    /// Core Animation renders both states into one snapshot and dissolves
    /// between them, which is both smoother and cheaper than animating each
    /// view.
    @MainActor
    static func apply(_ theme: AppTheme, animated: Bool = true) {
        let style = theme.interfaceStyle
        let windows = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)

        for window in windows where window.overrideUserInterfaceStyle != style {
            guard animated, !UIAccessibility.isReduceMotionEnabled else {
                window.overrideUserInterfaceStyle = style
                continue
            }
            UIView.transition(with: window, duration: 0.28,
                              options: [.transitionCrossDissolve, .allowAnimatedContent],
                              animations: { window.overrideUserInterfaceStyle = style })
        }
    }
}

/// The appearance picker.
///
/// A segmented control rather than a list of rows: the three options are
/// mutually exclusive and comparable, and seeing them side by side is the
/// entire decision. Built rather than borrowed from `Picker(.segmented)` so it
/// carries Sava's radii and its accent instead of the system's tint.
struct AppearancePicker: View {
    @Binding var selection: AppTheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var indicator

    var body: some View {
        HStack(spacing: 0) {
            ForEach(AppTheme.allCases) { theme in
                Button {
                    Haptics.select()
                    withAnimation(Motion.respecting(Motion.tap, reduceMotion)) {
                        selection = theme
                    }
                } label: {
                    HStack(spacing: 5) {
                        Image(systemName: theme.symbol)
                            .font(.system(size: 12, weight: .semibold))
                        Text(theme.label)
                            .font(.system(size: 13.5, weight: .medium))
                    }
                    .foregroundStyle(selection == theme ? SavaColor.onAccent
                                                        : SavaColor.secondary)
                    .frame(maxWidth: .infinity)
                    .frame(height: 34)
                    .background {
                        if selection == theme {
                            Capsule()
                                .fill(SavaColor.accent)
                                .matchedGeometryEffect(id: "appearance", in: indicator)
                        }
                    }
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(theme.label)
                .accessibilityAddTraits(selection == theme ? [.isSelected, .isButton] : .isButton)
            }
        }
        .padding(3)
        .background(SavaColor.fill, in: Capsule())
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Appearance")
    }
}
