import SwiftUI

/// The three platforms Sava is for, drawn as their recognisable marks.
///
/// ── Why drawn, and how far the resemblance goes ─────────────────────────
///
/// A coloured dot is not a brand — people recognise TikTok by its note and
/// YouTube by its red play tile, and an onboarding screen that says "TikTok"
/// beside a grey circle reads as a placeholder. These are the marks, built from
/// primitives at draw time: no downloaded artwork, no bundled trademark files,
/// nothing copied out of another app's binary.
///
/// This is nominative use — showing a platform's mark to say "this is the one
/// we mean". Each is a simplified geometric construction rather than a traced
/// reproduction: the TikTok note is two circles and a stem, the Instagram mark
/// is a rounded square with a ring, the YouTube mark is a rounded rectangle
/// with a triangle. They read correctly at 16pt and carry no claim of
/// endorsement.
///
/// Every mark also carries its name in an accessibility label, so the meaning
/// survives for somebody who cannot see the glyph — colour and shape are never
/// the only signal.
enum SavaPlatform: String, CaseIterable, Identifiable {
    case tiktok, instagram, youtube

    var id: String { rawValue }

    var title: String {
        switch self {
        case .tiktok:    return "TikTok"
        case .instagram: return "Instagram"
        case .youtube:   return "YouTube"
        }
    }

    /// What the share control is actually called in that app. Getting this
    /// wrong is how instructions stop matching the screen the user is on.
    var shareVerb: String {
        switch self {
        case .tiktok:    return "Share"
        case .instagram: return "Share to…"
        case .youtube:   return "Share"
        }
    }

    /// The platform's own colour, for a selected chip. TikTok's is black, which
    /// is invisible on Sava's ground, so it borrows the citron the rest of the
    /// app uses for "selected".
    var tint: Color {
        switch self {
        case .tiktok:    return SavaColor.accent
        case .instagram: return Color(red: 0.85, green: 0.18, blue: 0.44)
        case .youtube:   return Color(red: 0.90, green: 0.00, blue: 0.00)
        }
    }
}

/// One platform's mark at a given size.
struct PlatformMark: View {
    let platform: SavaPlatform
    var size: CGFloat = 22
    /// Draw in a single colour instead of the brand palette — used where the
    /// mark sits on an accent fill and must not fight it.
    var monochrome: Color? = nil

    var body: some View {
        Group {
            switch platform {
            case .tiktok:    TikTokNote(size: size, mono: monochrome)
            case .instagram: InstagramGlyph(size: size, mono: monochrome)
            case .youtube:   YouTubeGlyph(size: size, mono: monochrome)
            }
        }
        .frame(width: size, height: size)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(platform.title)
    }
}

// MARK: - The marks

/// TikTok's note: a bowl, a stem, and the offset cyan/magenta shadow that makes
/// it read as TikTok rather than as a generic music glyph.
private struct TikTokNote: View {
    let size: CGFloat
    let mono: Color?

    var body: some View {
        ZStack {
            if mono == nil {
                note.foregroundStyle(Color(red: 0.02, green: 0.94, blue: 0.92))
                    .offset(x: -size * 0.055, y: size * 0.03)
                note.foregroundStyle(Color(red: 0.99, green: 0.09, blue: 0.35))
                    .offset(x: size * 0.055, y: -size * 0.03)
            }
            note.foregroundStyle(mono ?? SavaColor.primary)
        }
    }

    private var note: some View {
        ZStack(alignment: .bottomLeading) {
            // The bowl.
            Circle()
                .strokeBorder(.white, lineWidth: size * 0.13)
                .frame(width: size * 0.42, height: size * 0.42)
            // The stem, with the flag curving off the top.
            Path { p in
                let x = size * 0.36
                p.move(to: CGPoint(x: x, y: size * 0.78))
                p.addLine(to: CGPoint(x: x, y: size * 0.20))
                p.addQuadCurve(to: CGPoint(x: size * 0.78, y: size * 0.34),
                               control: CGPoint(x: size * 0.72, y: size * 0.16))
            }
            .stroke(.white, style: .init(lineWidth: size * 0.13,
                                         lineCap: .round, lineJoin: .round))
            .frame(width: size, height: size)
        }
        .frame(width: size, height: size, alignment: .bottomLeading)
    }
}

/// Instagram's rounded square with the lens ring and the corner dot.
private struct InstagramGlyph: View {
    let size: CGFloat
    let mono: Color?

    var body: some View {
        let stroke = size * 0.11
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.28, style: .continuous)
                .strokeBorder(style: StrokeStyle(lineWidth: stroke))
            Circle()
                .strokeBorder(style: StrokeStyle(lineWidth: stroke))
                .frame(width: size * 0.42, height: size * 0.42)
            Circle()
                .frame(width: stroke * 1.15, height: stroke * 1.15)
                .offset(x: size * 0.21, y: -size * 0.21)
        }
        .foregroundStyle(mono.map(AnyShapeStyle.init)
                         ?? AnyShapeStyle(instagramGradient))
        .frame(width: size, height: size)
    }

    /// Instagram's own warm sunset ramp — amber through magenta — which is what
    /// makes the outline read as Instagram rather than as a camera icon.
    private var instagramGradient: LinearGradient {
        LinearGradient(colors: [Color(red: 0.99, green: 0.69, blue: 0.31),
                                Color(red: 0.85, green: 0.18, blue: 0.44),
                                Color(red: 0.51, green: 0.23, blue: 0.75)],
                       startPoint: .bottomLeading, endPoint: .topTrailing)
    }
}

/// YouTube's play tile.
private struct YouTubeGlyph: View {
    let size: CGFloat
    let mono: Color?

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.23, style: .continuous)
                .fill(mono ?? Color(red: 0.90, green: 0.00, blue: 0.00))
                .frame(width: size, height: size * 0.70)
            Triangle()
                .fill(mono == nil ? Color.white : SavaColor.ground)
                .frame(width: size * 0.20, height: size * 0.24)
                .offset(x: size * 0.02)
        }
        .frame(width: size, height: size)
    }
}

/// The play triangle. A `Path` rather than an SF Symbol so it sits optically
/// centred in the tile at any size.
private struct Triangle: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        p.closeSubpath()
        return p
    }
}
