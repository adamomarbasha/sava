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

    /// Vertical formats display better in a 9:16-ish frame.
    var prefersPortrait: Bool {
        self == .tiktok || self == .instagram || self == .snapchat
    }
}
