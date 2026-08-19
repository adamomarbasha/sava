import Foundation

/// Small, reusable display formatters. Kept out of views so formatting stays
/// consistent and testable.
enum Format {
    /// 1490 -> "24:50", 65 -> "1:05".
    static func duration(_ seconds: Int?) -> String? {
        guard let s = seconds, s > 0 else { return nil }
        let h = s / 3600, m = (s % 3600) / 60, sec = s % 60
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, sec) }
        return String(format: "%d:%02d", m, sec)
    }

    /// 86813 -> "86.8K", 6638 -> "6.6K", 1200000 -> "1.2M".
    static func compactCount(_ value: Int?) -> String? {
        guard let v = value, v >= 0 else { return nil }
        switch v {
        case 0..<1000: return "\(v)"
        case 1000..<1_000_000:
            return trimmed(Double(v) / 1000) + "K"
        default:
            return trimmed(Double(v) / 1_000_000) + "M"
        }
    }

    private static func trimmed(_ d: Double) -> String {
        let s = String(format: "%.1f", d)
        return s.hasSuffix(".0") ? String(s.dropLast(2)) : s
    }

    /// "3d", "2w", "5mo" — compact relative age.
    static func relativeAge(_ date: Date?) -> String? {
        guard let date else { return nil }
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    /// mm:ss timestamp from a seconds offset (for transcript rows).
    static func timestamp(_ seconds: Double) -> String {
        let total = Int(seconds.rounded())
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}
