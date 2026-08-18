import SwiftUI

/// Typography scale. Uses the system font (SF Pro) with intentional weights and
/// tracking. Rounded design on display sizes gives Sava a warmer, more
/// consumer feel while staying native and Dynamic-Type friendly.
enum SavaFont {
    static func display(_ size: CGFloat = 34, weight: Font.Weight = .bold) -> Font {
        .system(size: size, weight: weight, design: .rounded)
    }

    static let largeTitle = Font.system(size: 34, weight: .bold, design: .rounded)
    static let title = Font.system(size: 26, weight: .bold, design: .rounded)
    static let title2 = Font.system(size: 21, weight: .semibold, design: .rounded)
    static let headline = Font.system(size: 17, weight: .semibold)
    static let body = Font.system(size: 16, weight: .regular)
    static let callout = Font.system(size: 15, weight: .regular)
    static let subheadline = Font.system(size: 14, weight: .medium)
    static let footnote = Font.system(size: 13, weight: .regular)
    static let caption = Font.system(size: 12, weight: .medium)
    static let mono = Font.system(size: 13, weight: .medium, design: .monospaced)
}

extension Text {
    /// Wordmark styling for "Sava".
    func savaWordmark(_ size: CGFloat = 28) -> some View {
        self.font(.system(size: size, weight: .heavy, design: .rounded))
            .tracking(-0.5)
    }
}
