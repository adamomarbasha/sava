import SwiftUI

/// Illustrated thumbnails for the onboarding demo library.
///
/// Each scene is a small composition of gradients and shapes — a bowl seen from
/// above, a skyline at dusk, a lit face against night neon — drawn at whatever
/// size it is asked for. No file, no network, no photograph of anyone's actual
/// content. See `DemoLibrary` for why they are drawn rather than shipped.
///
/// ── Rules they follow ───────────────────────────────────────────────────
///
///   * **Legible at 90pt.** These are mostly seen as small cards in a drifting
///     constellation, so each scene has one dominant shape that survives being
///     shrunk. Fine detail that turns to mush at thumbnail size is not drawn.
///   * **Dark-first, but not black.** They sit on Sava's near-black ground and
///     have to read as *images* rather than as holes in it, so every scene has
///     its own colour temperature.
///   * **Nothing borrowed.** No platform chrome, no imitation of another app's
///     interface, no logo shapes. A Reel-shaped card is 4:5 and says "Reel" in
///     Sava's own type; that is the whole of the resemblance.
///   * **No text inside the art.** Titles are drawn by the card, in Sava's
///     type, so they scale with Dynamic Type. Baking words into the artwork
///     would freeze them at one size and one language.
enum PosterArt {

    enum Scene: String, CaseIterable {
        case pasta, nightCreator, cityDusk, torii, codeDesk, espresso, editorial
    }

    /// The scene's dominant colour, for chrome that has to sit next to it —
    /// the glow under a card, a selected border, the save chip.
    static func keyColor(_ scene: Scene) -> Color {
        switch scene {
        case .pasta:        return Color(red: 0.85, green: 0.42, blue: 0.20)
        case .nightCreator: return Color(red: 0.51, green: 0.35, blue: 0.92)
        case .cityDusk:     return Color(red: 0.94, green: 0.51, blue: 0.33)
        case .torii:        return Color(red: 0.83, green: 0.29, blue: 0.31)
        case .codeDesk:     return Color(red: 0.29, green: 0.62, blue: 0.94)
        case .espresso:     return Color(red: 0.62, green: 0.38, blue: 0.22)
        case .editorial:    return Color(red: 0.78, green: 0.74, blue: 0.66)
        }
    }
}

// MARK: - The view

struct PosterView: View {
    let scene: PosterArt.Scene

    var body: some View {
        GeometryReader { geo in
            let s = min(geo.size.width, geo.size.height)
            ZStack {
                switch scene {
                case .pasta:        Pasta(unit: s)
                case .nightCreator: NightCreator(unit: s)
                case .cityDusk:     CityDusk(unit: s)
                case .torii:        Torii(unit: s)
                case .codeDesk:     CodeDesk(unit: s)
                case .espresso:     Espresso(unit: s)
                case .editorial:    Editorial(unit: s)
                }
            }
            .frame(width: geo.size.width, height: geo.size.height)
            .clipped()
        }
        .accessibilityHidden(true)   // the card announces its title instead
    }
}

// MARK: - Scenes
//
// Each is a plain `View` rather than a `Canvas`: SwiftUI shapes are cheap,
// composite correctly with the card's corner radius, and — unlike a Canvas —
// cost nothing to redraw when only the parent's opacity changes, which is what
// the drifting constellation does constantly.

/// A bowl of pasta seen from directly above. Warm terracotta on a dark board.
private struct Pasta: View {
    let unit: CGFloat
    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(red: 0.20, green: 0.10, blue: 0.07),
                                    Color(red: 0.36, green: 0.16, blue: 0.09)],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            // The bowl.
            Circle()
                .fill(RadialGradient(
                    colors: [Color(red: 0.96, green: 0.78, blue: 0.52),
                             Color(red: 0.87, green: 0.50, blue: 0.22)],
                    center: .init(x: 0.4, y: 0.35), startRadius: 0, endRadius: unit * 0.5))
                .frame(width: unit * 0.72, height: unit * 0.72)
            // Nests of pasta: three arcs, not thirty strands.
            ForEach(0..<3, id: \.self) { i in
                Circle()
                    .trim(from: 0.05, to: 0.72)
                    .stroke(Color(red: 0.99, green: 0.87, blue: 0.62).opacity(0.85),
                            style: .init(lineWidth: unit * 0.035, lineCap: .round))
                    .frame(width: unit * (0.30 + CGFloat(i) * 0.13))
                    .rotationEffect(.degrees(Double(i) * 47 - 20))
            }
            // Basil.
            ForEach(0..<3, id: \.self) { i in
                Ellipse()
                    .fill(Color(red: 0.28, green: 0.55, blue: 0.28))
                    .frame(width: unit * 0.10, height: unit * 0.062)
                    .rotationEffect(.degrees(Double(i) * 55))
                    .offset(x: unit * [0.13, -0.16, 0.02][i],
                            y: unit * [-0.16, 0.06, 0.19][i])
            }
        }
    }
}

/// A lit face against night neon — a creator talking to camera. The shape is a
/// silhouette, deliberately: a drawn *person* would be a drawn likeness.
private struct NightCreator: View {
    let unit: CGFloat
    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(red: 0.08, green: 0.06, blue: 0.18),
                                    Color(red: 0.22, green: 0.09, blue: 0.30)],
                           startPoint: .top, endPoint: .bottom)
            // Bokeh behind the subject.
            ForEach(0..<7, id: \.self) { i in
                Circle()
                    .fill([Color(red: 0.45, green: 0.35, blue: 0.98),
                           Color(red: 0.95, green: 0.30, blue: 0.55),
                           Color(red: 0.30, green: 0.80, blue: 0.90)][i % 3]
                        .opacity(0.42))
                    .frame(width: unit * [0.16, 0.09, 0.13, 0.07, 0.11, 0.06, 0.14][i])
                    .blur(radius: unit * 0.035)
                    .offset(x: unit * [-0.34, 0.31, -0.18, 0.38, 0.12, -0.40, 0.26][i],
                            y: unit * [-0.30, -0.36, 0.30, 0.10, -0.42, 0.06, 0.36][i])
            }
            // Head and shoulders, rim-lit.
            VStack(spacing: -unit * 0.035) {
                Circle()
                    .fill(Color(red: 0.10, green: 0.07, blue: 0.16))
                    .frame(width: unit * 0.30)
                    .overlay(Circle().strokeBorder(
                        Color(red: 0.70, green: 0.55, blue: 1.0).opacity(0.9),
                        lineWidth: unit * 0.016))
                Capsule()
                    .fill(Color(red: 0.10, green: 0.07, blue: 0.16))
                    .frame(width: unit * 0.52, height: unit * 0.30)
                    .overlay(Capsule().strokeBorder(
                        Color(red: 0.70, green: 0.55, blue: 1.0).opacity(0.7),
                        lineWidth: unit * 0.014))
            }
            .offset(y: unit * 0.10)
        }
    }
}

/// A skyline at dusk — the travel Reel.
private struct CityDusk: View {
    let unit: CGFloat
    private let heights: [CGFloat] = [0.30, 0.46, 0.22, 0.55, 0.34, 0.62, 0.26, 0.42]
    var body: some View {
        ZStack(alignment: .bottom) {
            LinearGradient(colors: [Color(red: 0.16, green: 0.13, blue: 0.32),
                                    Color(red: 0.86, green: 0.45, blue: 0.31),
                                    Color(red: 0.97, green: 0.72, blue: 0.42)],
                           startPoint: .top, endPoint: .bottom)
            Circle()
                .fill(Color(red: 1.0, green: 0.88, blue: 0.62).opacity(0.95))
                .frame(width: unit * 0.20)
                .offset(x: unit * 0.16, y: -unit * 0.20)
                .blur(radius: unit * 0.006)
            HStack(alignment: .bottom, spacing: unit * 0.022) {
                ForEach(heights.indices, id: \.self) { i in
                    RoundedRectangle(cornerRadius: unit * 0.012)
                        .fill(Color(red: 0.09, green: 0.07, blue: 0.14).opacity(0.94))
                        .frame(height: unit * heights[i])
                        .overlay(alignment: .top) {
                            // Lit windows, a couple per tower.
                            VStack(spacing: unit * 0.03) {
                                ForEach(0..<2, id: \.self) { _ in
                                    Rectangle()
                                        .fill(Color(red: 1.0, green: 0.85, blue: 0.55)
                                            .opacity(i.isMultiple(of: 2) ? 0.75 : 0.35))
                                        .frame(width: unit * 0.018, height: unit * 0.018)
                                }
                            }
                            .padding(.top, unit * 0.05)
                        }
                }
            }
            .frame(height: unit * 0.62, alignment: .bottom)
        }
    }
}

/// A torii gate in mist — the Kyoto Reel.
private struct Torii: View {
    let unit: CGFloat
    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(red: 0.13, green: 0.16, blue: 0.17),
                                    Color(red: 0.36, green: 0.40, blue: 0.38)],
                           startPoint: .top, endPoint: .bottom)
            // Hills behind.
            Ellipse()
                .fill(Color(red: 0.17, green: 0.22, blue: 0.20).opacity(0.9))
                .frame(width: unit * 1.5, height: unit * 0.6)
                .offset(x: -unit * 0.3, y: unit * 0.38)
            let vermilion = Color(red: 0.85, green: 0.27, blue: 0.24)
            ZStack {
                // Lintels.
                Capsule().fill(vermilion)
                    .frame(width: unit * 0.66, height: unit * 0.055)
                    .offset(y: -unit * 0.20)
                Capsule().fill(vermilion)
                    .frame(width: unit * 0.52, height: unit * 0.040)
                    .offset(y: -unit * 0.11)
                // Posts.
                ForEach([-1.0, 1.0], id: \.self) { side in
                    Capsule().fill(vermilion)
                        .frame(width: unit * 0.050, height: unit * 0.44)
                        .offset(x: unit * 0.19 * side, y: unit * 0.045)
                }
            }
            // Mist.
            LinearGradient(colors: [.clear, Color(red: 0.80, green: 0.84, blue: 0.83).opacity(0.5)],
                           startPoint: .center, endPoint: .bottom)
        }
    }
}

/// A desk at night with code on screen — the long YouTube explainer.
private struct CodeDesk: View {
    let unit: CGFloat
    private let widths: [CGFloat] = [0.42, 0.30, 0.50, 0.22, 0.38, 0.46]
    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(red: 0.07, green: 0.09, blue: 0.13),
                                    Color(red: 0.11, green: 0.15, blue: 0.22)],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            // The monitor.
            RoundedRectangle(cornerRadius: unit * 0.035)
                .fill(Color(red: 0.05, green: 0.07, blue: 0.11))
                .overlay(RoundedRectangle(cornerRadius: unit * 0.035)
                    .strokeBorder(Color.white.opacity(0.10), lineWidth: unit * 0.008))
                .frame(width: unit * 0.74, height: unit * 0.52)
                .overlay(alignment: .topLeading) {
                    VStack(alignment: .leading, spacing: unit * 0.035) {
                        ForEach(widths.indices, id: \.self) { i in
                            Capsule()
                                .fill(i == 2 ? Color(red: 0.79, green: 0.94, blue: 0.20).opacity(0.9)
                                             : Color(red: 0.35, green: 0.66, blue: 0.96)
                                                 .opacity(i.isMultiple(of: 2) ? 0.75 : 0.45))
                                .frame(width: unit * widths[i], height: unit * 0.022)
                        }
                    }
                    .padding(unit * 0.075)
                }
                .offset(y: -unit * 0.04)
            // Desk edge.
            Rectangle()
                .fill(Color(red: 0.16, green: 0.12, blue: 0.10))
                .frame(height: unit * 0.16)
                .frame(maxHeight: .infinity, alignment: .bottom)
        }
    }
}

/// An espresso from above — crema rings in a white cup.
private struct Espresso: View {
    let unit: CGFloat
    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(red: 0.15, green: 0.12, blue: 0.11),
                                    Color(red: 0.28, green: 0.21, blue: 0.17)],
                           startPoint: .top, endPoint: .bottom)
            Circle()
                .fill(Color(red: 0.94, green: 0.93, blue: 0.91))
                .frame(width: unit * 0.62)
            Circle()
                .fill(RadialGradient(
                    colors: [Color(red: 0.74, green: 0.50, blue: 0.28),
                             Color(red: 0.35, green: 0.19, blue: 0.10)],
                    center: .init(x: 0.42, y: 0.40), startRadius: 0, endRadius: unit * 0.28))
                .frame(width: unit * 0.46)
            // Crema swirl.
            Circle()
                .trim(from: 0.1, to: 0.85)
                .stroke(Color(red: 0.87, green: 0.68, blue: 0.42).opacity(0.75),
                        style: .init(lineWidth: unit * 0.022, lineCap: .round))
                .frame(width: unit * 0.28)
                .rotationEffect(.degrees(30))
            // Saucer shadow.
            Circle()
                .strokeBorder(Color.black.opacity(0.22), lineWidth: unit * 0.03)
                .frame(width: unit * 0.70)
        }
    }
}

/// A page of set type — the article.
private struct Editorial: View {
    let unit: CGFloat
    private let widths: [CGFloat] = [0.52, 0.46, 0.54, 0.40, 0.52, 0.30]
    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(red: 0.93, green: 0.91, blue: 0.86),
                                    Color(red: 0.82, green: 0.79, blue: 0.72)],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            VStack(alignment: .leading, spacing: unit * 0.030) {
                // Drop cap and headline.
                HStack(alignment: .top, spacing: unit * 0.035) {
                    RoundedRectangle(cornerRadius: unit * 0.012)
                        .fill(Color(red: 0.13, green: 0.12, blue: 0.11))
                        .frame(width: unit * 0.12, height: unit * 0.15)
                    VStack(alignment: .leading, spacing: unit * 0.028) {
                        Capsule().fill(Color(red: 0.13, green: 0.12, blue: 0.11))
                            .frame(width: unit * 0.40, height: unit * 0.030)
                        Capsule().fill(Color(red: 0.13, green: 0.12, blue: 0.11).opacity(0.7))
                            .frame(width: unit * 0.30, height: unit * 0.030)
                    }
                }
                .padding(.bottom, unit * 0.02)
                ForEach(widths.indices, id: \.self) { i in
                    Capsule()
                        .fill(Color(red: 0.24, green: 0.22, blue: 0.20).opacity(0.55))
                        .frame(width: unit * widths[i], height: unit * 0.016)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .padding(unit * 0.11)
        }
    }
}
