import SwiftUI

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

    /// Nil hands the decision back to the system.
    var colorScheme: ColorScheme? {
        switch self {
        case .automatic: return nil
        case .light:     return .light
        case .dark:      return .dark
        }
    }

    /// The stored preference. Read wherever it is needed; written only by the
    /// picker in Profile.
    static let storageKey = "sava.appearance"
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
