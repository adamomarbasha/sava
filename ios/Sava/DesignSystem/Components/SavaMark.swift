import SwiftUI
import UIKit

/// Sava's mark, as it appears inside the app.
///
/// The artwork is the one already in the repository (`web/public/icon.png`),
/// trimmed of its drop shadow and nothing else — no redraw, no reinterpretation.
///
/// It ships as a **tile** rather than as a bare glyph, and that is a constraint
/// of the artwork rather than a preference: the mark is a black silhouette with
/// a white counter, so on Sava's ink ground it would be a black shape on a
/// near-black field. Setting it on citron is what makes it legible, and it has
/// the useful side effect that the mark inside the app is identical to the one
/// on the home screen — the same object, at different sizes.
///
/// Used sparingly. A logo repeated on every screen stops being a mark and
/// becomes wallpaper; this appears at the three moments where the app is
/// introducing itself rather than getting out of the way.
struct SavaMark: View {
    var size: CGFloat = 56

    /// iOS's superellipse, matched so the in-app tile and the home-screen icon
    /// read as the same shape. A plain rounded rectangle at this size looks
    /// subtly wrong beside the real thing.
    private var corner: CGFloat { size * 0.2237 }

    /// Taken from `AppIcon.png`, which is the canonical artwork.
    private static let tile = Color(uiColor: UIColor(hex: 0xD6FF00))

    var body: some View {
        RoundedRectangle(cornerRadius: corner, style: .continuous)
            .fill(Self.tile)
            .frame(width: size, height: size)
            .overlay {
                // The artwork as drawn, not as a template.
                //
                // `AppIcon.png` is the canonical lockup: citron #D6FF00 with
                // the mark on it. The mark itself is **two-tone** — a near-black
                // body with a white detail inside it — and
                // `.renderingMode(.template)` flattens colour to a single tint,
                // which erases that detail and leaves a plain silhouette. That
                // is what made the logo read as a generic symbol rather than as
                // Sava.
                //
                // (The earlier check for interior *alpha* knockouts found none
                // and wrongly concluded the glyph was solid; the detail is
                // carried in colour, not transparency. Measured luminance
                // across opaque pixels spans 3 to 255.)
                //
                // So: original rendering, on a fixed citron tile. A logo is a
                // constant — it does not invert with the theme, and both of its
                // colours are baked into the asset exactly as the icon has them.
                Image("SavaMark")
                    .renderingMode(.original)
                    .resizable()
                    .scaledToFit()
                    // Matches the optical inset used when generating the app
                    // icon, so the two are the same composition.
                    .frame(width: size * 0.56)
                    .offset(y: -size * 0.014)
            }
            .accessibilityHidden(true)
    }
}

/// The mark beside the wordmark — the lockup used where Sava signs its name.
struct SavaLockup: View {
    var markSize: CGFloat = 44
    var font: Font = SavaType.wordmark

    var body: some View {
        HStack(spacing: Space.m) {
            SavaMark(size: markSize)
            Text("Sava")
                .font(font)
                .tracking(Tracking.tight)
                .foregroundStyle(SavaColor.primary)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Sava")
    }
}
