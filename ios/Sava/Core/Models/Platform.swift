import SwiftUI

/// The platforms the backend recognizes (mirrors the server's platform enum).
/// Carries display metadata so the UI never hard-codes per-platform styling.
enum Platform: String, CaseIterable, Identifiable {
    case youtube, tiktok, instagram, twitter, linkedin, reddit, pinterest, snapchat, facebook, other

    var id: String { rawValue }

    init(rawValue: String) {
        self = Platform.allCases.first { $0.rawValue == rawValue.lowercased() } ?? .other
    }

    var displayName: String {
        switch self {
        case .youtube: return "YouTube"
        case .tiktok: return "TikTok"
        case .instagram: return "Instagram"
        case .twitter: return "X"
        case .linkedin: return "LinkedIn"
        case .reddit: return "Reddit"
        case .pinterest: return "Pinterest"
        case .snapchat: return "Snapchat"
        case .facebook: return "Facebook"
        case .other: return "Link"
        }
    }

    var tint: Color { PlatformColor.tint(for: rawValue) }

    /// Two-letter mark used on the designed no-image plate.
    var shortMark: String {
        switch self {
        case .youtube: return "YT"
        case .tiktok: return "TT"
        case .instagram: return "IG"
        case .twitter: return "X"
        case .linkedin: return "IN"
        case .reddit: return "RD"
        case .pinterest: return "PIN"
        case .snapchat: return "SC"
        case .facebook: return "FB"
        case .other: return "LINK"
        }
    }

    /// SF Symbol used as a compact platform glyph.
    var symbol: String {
        switch self {
        case .youtube: return "play.rectangle.fill"
        case .tiktok: return "music.note"
        case .instagram: return "camera.fill"
        case .twitter: return "bird.fill"
        case .linkedin: return "briefcase.fill"
        case .reddit: return "antenna.radiowaves.left.and.right"
        case .pinterest: return "pin.fill"
        case .snapchat: return "bolt.fill"
        case .facebook: return "person.2.fill"
        case .other: return "link"
        }
    }

    /// Vertical formats display better in a taller frame.
    var prefersPortrait: Bool {
        self == .tiktok || self == .instagram || self == .snapchat
    }

    /// The platforms Sava actually extracts from today.
    ///
    /// Everything else still saves, still shows in the library and still opens —
    /// the ingestion layer just has no handler for it, so there is no metadata,
    /// no transcript and nothing to understand. Offering a filter for a platform
    /// that can only ever return bare links promises capability the product does
    /// not have yet, so the filter row lists these three and nothing else.
    static let supported: [Platform] = [.tiktok, .instagram, .youtube]

    var isSupported: Bool { Self.supported.contains(self) }
}


/// Platform brand tints, matched to the web client. Used only for the small
/// glyph on a no-image plate — never as a surface colour.
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
        case "snapchat":  return Color(hex: 0xE5B800)
        default:          return Color(hex: 0x7A7C83)
        }
    }
}
