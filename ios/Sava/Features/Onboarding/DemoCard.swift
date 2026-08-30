import SwiftUI

/// One piece of content, as Sava would hold it.
///
/// Poster art fills the card and the text sits on a scrim over the bottom of
/// it, which is the shape every short-form app has trained people to read. The
/// scrim is drawn only where the text is, not over the whole image: a full-card
/// darkening is what makes onboarding illustrations look like stock photos with
/// a filter on them.
struct DemoCard: View {
    let item: DemoItem
    /// Card width. Height follows from `aspect`.
    var width: CGFloat = 132
    var aspect: CGFloat = 4.0 / 5.0
    /// Drawn with a citron edge and a "Saved" chip.
    var isSaved: Bool = false
    /// Dimmed and desaturated — used while the search demo filters.
    var isMuted: Bool = false
    /// Poster and platform dot only, no title.
    ///
    /// Below about 90pt the title cannot be set at a readable size *and* fit:
    /// at 54pt the flow demo was rendering "TIK / TO / K" down three lines with
    /// the title ellipsed after two words, which looks like a bug rather than a
    /// thumbnail. A small card that shows only the artwork is honest about how
    /// much room it has.
    var isCompact: Bool = false

    private var height: CGFloat { width / aspect }

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            PosterView(scene: item.poster)

            // Legibility scrim: bottom 55%, and no further.
            LinearGradient(
                colors: [.clear, .black.opacity(0.55), .black.opacity(0.86)],
                startPoint: .init(x: 0.5, y: 0.45), endPoint: .bottom)

            if isCompact {
                HStack(spacing: 4) {
                    PlatformDot(platform: item.platform, size: width * 0.075)
                    Spacer(minLength: 0)
                }
                .padding(width * 0.085)
            } else {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 5) {
                    PlatformDot(platform: item.platform, size: width * 0.045)
                    Text(item.kindLabel.uppercased())
                        .font(.system(size: max(8, width * 0.062), weight: .bold))
                        .tracking(0.7)
                        .foregroundStyle(.white.opacity(0.82))
                    Spacer(minLength: 0)
                    Text(item.duration)
                        .font(.system(size: max(7.5, width * 0.058), weight: .medium))
                        .foregroundStyle(.white.opacity(0.62))
                }
                Text(item.title)
                    .font(.system(size: max(10, width * 0.088), weight: .semibold))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, width * 0.072)
            .padding(.bottom, width * 0.072)
            }

            if isSaved && !isCompact { savedChip.padding(width * 0.06) }
        }
        .frame(width: width, height: height)
        .clipShape(RoundedRectangle(cornerRadius: width * 0.10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: width * 0.10, style: .continuous)
                .strokeBorder(isSaved ? SavaColor.accent : Color.white.opacity(0.10),
                              lineWidth: isSaved ? 1.5 : 0.5))
        .saturation(isMuted ? 0.18 : 1)
        .opacity(isMuted ? 0.30 : 1)
        .shadow(color: .black.opacity(0.45), radius: width * 0.07, y: width * 0.035)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(item.kindLabel) by \(item.creator). \(item.title)"
                            + (isSaved ? ". Saved" : ""))
    }

    private var savedChip: some View {
        HStack(spacing: 3) {
            Image(systemName: "checkmark")
                .font(.system(size: max(7, width * 0.055), weight: .black))
            Text("SAVED")
                .font(.system(size: max(7, width * 0.055), weight: .black))
                .tracking(0.6)
        }
        .foregroundStyle(SavaColor.onAccent)
        .padding(.horizontal, width * 0.055)
        .padding(.vertical, width * 0.028)
        .background(SavaColor.accent, in: Capsule())
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
    }
}

/// The platform's colour as a dot.
///
/// TikTok's brand colour is black, which is invisible on Sava's near-black
/// ground, so the design system already carries `platformNeedsOutline` for
/// exactly this case. Using it here rather than inventing a substitute colour
/// keeps the dot consistent with every other platform marker in the app.
struct PlatformDot: View {
    let platform: Platform
    var size: CGFloat = 6

    var body: some View {
        Circle()
            .fill(SavaColor.platformFill(platform))
            .frame(width: size, height: size)
            .overlay {
                if SavaColor.platformNeedsOutline(platform) {
                    Circle().strokeBorder(Color.white.opacity(0.55), lineWidth: 0.8)
                }
            }
    }
}
