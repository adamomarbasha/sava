import SwiftUI

/// Sava's semantic color system — "ink & paper" with a single restrained
/// signal accent. Every token adapts to light and dark appearance.
///
/// Identity notes:
/// - Light mode is warm paper, not sterile white.
/// - Dark mode is a designed deep ink, not an inverted white.
/// - The accent (Signal) is used sparingly: focus, primary action, "AI ready".
enum SavaColors {
    // MARK: Canvas
    static let background = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xFAFAF7),
        dark: UIColor(hex: 0x0A0A0C)
    ))

    static let backgroundElevated = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xFFFFFF),
        dark: UIColor(hex: 0x121216)
    ))

    static let surface = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xFFFFFF),
        dark: UIColor(hex: 0x17171C)
    ))

    static let surfaceMuted = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xF2F1EC),
        dark: UIColor(hex: 0x1E1E24)
    ))

    // MARK: Ink (text)
    static let textPrimary = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x111318),
        dark: UIColor(hex: 0xF4F4F1)
    ))

    static let textSecondary = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x585C64),
        dark: UIColor(hex: 0xA6A9B0)
    ))

    static let textTertiary = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x8B8F98),
        dark: UIColor(hex: 0x6C6F78)
    ))

    static let textOnAccent = Color.white

    // MARK: Lines
    static let separator = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xE8E6DF),
        dark: UIColor(hex: 0x2A2A31)
    ))

    static let hairline = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x000000, alpha: 0.06),
        dark: UIColor(hex: 0xFFFFFF, alpha: 0.08)
    ))

    // MARK: Signal accent
    static let accent = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x5457FF),
        dark: UIColor(hex: 0x7C7EFF)
    ))

    static let accentDeep = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x3A3DE0),
        dark: UIColor(hex: 0x5457FF)
    ))

    static let accentSoft = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x5457FF, alpha: 0.10),
        dark: UIColor(hex: 0x7C7EFF, alpha: 0.16)
    ))

    // MARK: Status
    static let danger = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xD1453B),
        dark: UIColor(hex: 0xFF6B60)
    ))

    static let dangerSoft = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xD1453B, alpha: 0.10),
        dark: UIColor(hex: 0xFF6B60, alpha: 0.14)
    ))

    static let success = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x1F9E6A),
        dark: UIColor(hex: 0x35C98B)
    ))

    // MARK: Auth-background mesh stops (subtle liquid gradient)
    static let meshA = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xEDEBFF),
        dark: UIColor(hex: 0x191A2E)
    ))

    static let meshB = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xFDECF3),
        dark: UIColor(hex: 0x201826)
    ))

    static let meshC = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xE7F0FF),
        dark: UIColor(hex: 0x141A26)
    ))
}

/// Platform brand colors, matched to the existing web client.
enum PlatformColor {
    static func tint(for platform: String) -> Color {
        switch platform.lowercased() {
        case "youtube":   return Color(hex: 0xFF0033)
        case "tiktok":    return Color(hex: 0x111318)
        case "instagram": return Color(hex: 0xE1306C)
        case "twitter":   return Color(hex: 0x1D9BF0)
        case "linkedin":  return Color(hex: 0x0A66C2)
        case "reddit":    return Color(hex: 0xFF4500)
        case "pinterest": return Color(hex: 0xE60023)
        case "facebook":  return Color(hex: 0x1877F2)
        case "snapchat":  return Color(hex: 0xFFD400)
        default:          return SavaColors.textSecondary
        }
    }
}
