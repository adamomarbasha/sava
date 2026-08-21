import SwiftUI
import UIKit

// Sava's design system.
//
// Dark-first, media-first, typography-led. The library is the colour; the
// interface is ink, paper-white type, and two accents used only where they mean
// something.
//
// The identity in one line: **ink black, citron, electric blue.** Citron is the
// signature — it appears on exactly one thing per screen, the thing you are
// meant to act on. Blue is structural: selection, focus, links, the AI voice.
// Everything else is neutral, and neutrals do the majority of the work.
//
// Rules the whole app follows:
//   * Media is the loudest thing on screen. Chrome is not.
//   * Structure comes from spacing and a hairline, not from boxes and borders.
//   * Accent means action or state. Never decoration.
//   * Geometry is fixed by us, never by whatever a CDN returned.
//   * No gradients, no glow, no glass. Depth comes from three flat surfaces.
//
// Every colour below was checked against its background before it was chosen;
// the ratios are recorded inline so a later edit cannot quietly break contrast.

// MARK: - Colour

enum SavaColor {

    // ── Surfaces ─────────────────────────────────────────────────────────
    // Three levels, and only three. Depth is communicated by which surface a
    // thing sits on, not by shadows — shadows on a near-black ground read as
    // smudges rather than as elevation.

    /// Page ground. Ink black, very slightly warm so it does not look like a
    /// dead pixel field next to real media.
    static let ground = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xFBFAF8), dark: UIColor(hex: 0x0A0A0B)))

    /// Raised: sheets, the tab bar, the few filled blocks that exist.
    static let surface = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xFFFFFF), dark: UIColor(hex: 0x151515)))

    /// Recessed fills: fields, chips, media placeholders, skeletons.
    static let fill = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xF1EFEB), dark: UIColor(hex: 0x1A1A1A)))

    // ── Text ─────────────────────────────────────────────────────────────
    // Three weights of voice. Ratios are against `ground`.

    /// Paper white. 18.4:1 — AAA.
    static let primary = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x111113), dark: UIColor(hex: 0xF7F7F4)))

    /// Supporting copy. 7.9:1 — AAA.
    static let secondary = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x55555C), dark: UIColor(hex: 0xA3A3A8)))

    /// Metadata, timestamps, counts. 5.5:1 — AA at any size.
    ///
    /// Deliberately not dimmer. The obvious "premium dark" move is a very low
    /// contrast tertiary, and it is the single most common way a dark interface
    /// becomes unreadable in daylight.
    /// Dark 5.5:1, light 5.1:1 — AA in both.
    ///
    /// These are different values on purpose. The same grey cannot serve both
    /// grounds: #86868F is comfortable on ink and only 3.5:1 on paper, which is
    /// below AA and is exactly how a light theme ends up looking washed out and
    /// failing an audit.
    static let tertiary = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x6B6B75), dark: UIColor(hex: 0x86868F)))

    /// The only divider in the system. Structure otherwise comes from spacing.
    static let hairline = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xE6E3DE), dark: UIColor(hex: 0x2B2B2B)))

    // ── Accents ──────────────────────────────────────────────────────────

    /// **Citron. The signature.** In dark it is the fill; in light it is the
    /// label. The pair inverts, and it has to.
    ///
    /// Citron against paper is 1.11:1 — a citron button on a white page has no
    /// edge and simply dissolves. The obvious workaround is to darken it, and
    /// that was tried: it lands on a muddy olive that reads as a different
    /// brand. So in light mode the primary action becomes an ink fill carrying
    /// a *citron label* (16.3:1), which keeps the signature colour on screen,
    /// keeps the button unmistakably the primary one, and is the shape a light
    /// interface expects anyway.
    ///
    /// Read `accent` as "the primary action's fill" and `onAccent` as "what
    /// sits on it" — the two simply trade places between appearances.
    static let accent = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x111113), dark: UIColor(hex: 0xD6FF00)))

    /// 16.3:1 in both directions — the same two colours, swapped.
    static let onAccent = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xD6FF00), dark: UIColor(hex: 0x0A0A0B)))

    /// **Electric blue.** Structural accent: selection, focus rings, the AI
    /// voice. Used as a *fill*, with white on it (4.7:1 — AA).
    static let accentBlue = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x2B45E0), dark: UIColor(hex: 0x2563FF)))

    /// Blue as small text or an icon on ink. A separate token because the fill
    /// blue is only 4.1:1 against the ground — no single blue can do both jobs
    /// on a dark base, and pretending otherwise is how dark themes fail audits.
    /// This is a 35% tint of the same blue, so it is the same hue: 7.3:1, AAA.
    static let accentBlueText = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x2B45E0), dark: UIColor(hex: 0x719AFF)))

    /// Warm support tone, and the third of the three rotating accents. 7.1:1.
    /// Citron **as a mark**, for glyphs and text.
    ///
    /// Distinct from `accent`, which is the primary action's *fill* and
    /// therefore inverts to ink in light mode. A small glyph tinted with the
    /// fill token would come out black on paper and lose the family entirely,
    /// so this is the citron that stays citron: 17.1:1 on ink, and a darkened
    /// 4.57:1 on paper — the olive is muddy at button size but reads correctly
    /// as lime at 13pt.
    static let accentTint = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x6B7A00), dark: UIColor(hex: 0xD6FF00)))

    /// 6.8:1 on paper, 8.7:1 on ink. Exists so that categorical tints have
    /// enough distinct hues not to collapse into each other.
    static let violet = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x6D28D9), dark: UIColor(hex: 0xB79CFF)))

    static let coral = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xC2410C), dark: UIColor(hex: 0xFF6B6B)))

    static let danger = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xC0392B), dark: UIColor(hex: 0xFF6B5A)))

    static let success = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x2E7D32), dark: UIColor(hex: 0x8FE388)))

    // ── The rotation ─────────────────────────────────────────────────────
    //
    // Citron, blue, coral, in that order, cycling across an indexed set —
    // numbered steps, key points, structured sections.
    //
    // This is what stops a list of accent tiles becoming a bag of sweets: the
    // sequence is fixed, so the colour of the third item is a consequence of
    // its position rather than a choice someone made. It also means colour
    // carries no meaning here — nobody has to learn that blue means anything —
    // it exists purely to help the eye separate one row from the next.

    // ── Platform identity ────────────────────────────────────────────────
    //
    // A selected filter takes the colour of the thing it filters to. "All" is
    // Sava's own blue; each platform is its own brand.
    //
    // The exact brand hexes are adjusted where they had to be. YouTube's
    // #FF0000 carries white at only 4.0:1, and Instagram's #E1306C at 4.3:1 —
    // both below AA for 13.5pt text — so each is darkened to the nearest value
    // that passes while still reading unmistakably as that platform. TikTok is
    // black, which is invisible against Sava's ground, so it is the one chip
    // that carries a border.

    static let platformYouTube  = Color(uiColor: UIColor(hex: 0xE60000))  // white 4.8:1
    static let platformTikTok   = Color(uiColor: UIColor(hex: 0x000000))  // needs a border
    static let platformInstagram = Color(uiColor: UIColor(hex: 0xD9276A)) // white 4.7:1

    /// The fill for a selected chip.
    static func platformFill(_ platform: Platform?) -> Color {
        switch platform {
        case .youtube:   return platformYouTube
        case .tiktok:    return platformTikTok
        case .instagram: return platformInstagram
        default:         return accentBlue
        }
    }

    /// Whether that fill needs an outline to be visible on the ground.
    /// Only TikTok's black does.
    static func platformNeedsOutline(_ platform: Platform?) -> Bool {
        platform == .tiktok
    }

    /// The three accents, in rotation order.
    static let rotation: [Color] = [accent, accentBlue, coral]

    /// The accent for an item at this position in a list.
    static func rotating(_ index: Int) -> Color {
        rotation[abs(index) % rotation.count]
    }

    /// What text or an icon must be to sit on `rotating(index)`.
    /// Blue is the only one of the three that takes white.
    static func onRotating(_ index: Int) -> Color {
        abs(index) % rotation.count == 1 ? .white : onAccent
    }
}

// MARK: - Type

enum SavaType {
    /// The wordmark. Heavy and tightly tracked — the negative tracking is what
    /// makes a system font read as a set logotype rather than as a heading.
    static let wordmark = Font.system(size: 38, weight: .heavy)

    /// Editorial display. For the one statement at the top of a screen that is
    /// meant to be *seen* rather than read. Oversized and tight; this is where
    /// the "exaggerated minimalism" of the direction actually lives.
    static let display = Font.system(size: 30, weight: .bold)

    static let title = Font.system(size: 21, weight: .semibold)
    static let lede = Font.system(size: 20, weight: .regular)

    /// Section labels. Small, heavy, wide-tracked, upper-cased at the call
    /// site — the one place tracking goes positive.
    static let section = Font.system(size: 11, weight: .bold)

    static let mediaTitle = Font.system(size: 14.5, weight: .semibold)
    static let meta = Font.system(size: 12, weight: .regular)
    static let body = Font.system(size: 16, weight: .regular)

    /// **The AI voice.** A serif, and the only serif in the product.
    ///
    /// This is the strongest identity decision in the type system: everything
    /// Sava *says* is set in serif, everything the interface says is set in
    /// sans. It reads as considered rather than generated, and it costs
    /// nothing — New York ships with the OS.
    static let prose = Font.system(size: 17, weight: .regular, design: .serif)

    /// Serif for headings that belong to Sava's voice rather than the UI's.
    static let proseTitle = Font.system(size: 22, weight: .semibold, design: .serif)

    static let callout = Font.system(size: 15, weight: .regular)
    static let caption = Font.system(size: 12, weight: .medium)
    static let button = Font.system(size: 16, weight: .semibold)
    static let numeric = Font.system(size: 12, weight: .medium).monospacedDigit()
}

/// Letter-spacing, as a named scale rather than magic numbers at call sites.
///
/// Tracking is most of the difference between "system font" and "designed".
/// Large type needs it negative or it looks loose; small caps need it positive
/// or it looks cramped.
enum Tracking {
    /// Display and wordmark.
    static let tight: CGFloat = -0.9
    /// Titles.
    static let snug: CGFloat = -0.4
    /// Body and everything unremarkable.
    static let normal: CGFloat = 0
    /// Small upper-cased section labels.
    static let wide: CGFloat = 0.8
}

enum Space {
    static let xs: CGFloat = 4
    static let s: CGFloat = 8
    static let m: CGFloat = 12
    static let l: CGFloat = 16
    static let xl: CGFloat = 24
    static let xxl: CGFloat = 40

    /// The single horizontal margin every screen uses.
    static let screen: CGFloat = 20
    /// The gutter between grid columns, and the rhythm between grid rows.
    static let gutter: CGFloat = 12
    static let row: CGFloat = 26
}

enum Radius {
    /// Media. Small — a big radius on a photograph reads as a sticker.
    static let media: CGFloat = 10
    /// Controls: fields, buttons, chips that are not pills.
    static let control: CGFloat = 12
    /// Sheets, and the few real cards that exist.
    static let card: CGFloat = 18
    /// Fully rounded. Chips, badges, the capsule buttons.
    static let pill: CGFloat = 999
}

enum MediaRatio {
    /// Vertical short-form: TikTok, Reels, Shorts.
    static let portrait: CGFloat = 4.0 / 5.0
    /// Landscape: YouTube.
    static let landscape: CGFloat = 16.0 / 9.0
    /// The detail hero's stage for vertical short-form — TikTok, Reels, vertical
    /// Instagram. Square, and the same for every one of them.
    ///
    /// A hero that takes its height from the source means a 9:16 TikTok becomes
    /// 700pt tall and a phone-screenshot Instagram post becomes taller than the
    /// screen: the whole thumbnail is visible, but it is the only thing visible.
    /// Landscape media does not have that problem, so it keeps its natural
    /// shape; vertical media gets a fixed square stage and sits inside it
    /// complete. Fixing the ratio also means these heroes never resize as the
    /// picture loads.
    static let verticalHero: CGFloat = 1.0

    /// A card's shape, decided by what the media *is* rather than where it came
    /// from.
    ///
    /// Two presentation classes, and only two:
    ///
    ///   * **vertical** — TikTok, Instagram, and YouTube Shorts. All 4:5, so a
    ///     Short and a TikTok are indistinguishable in the grid, which is what
    ///     they are to the person looking at them.
    ///   * **landscape** — ordinary YouTube. 16:9, its natural shape.
    ///
    /// Keying on platform was the subtle bug in the original: `forPlatform`
    /// answered "landscape" for anything on YouTube, so a Short — vertical
    /// video, identical in every way to a TikTok — was laid out in a 16:9 box
    /// and cropped to a letterbox strip. `isShortForm` is the server's own
    /// classification (see api/content/shortform.py), so the client and the
    /// feed agree on what counts as vertical.
    static func forItem(_ bookmark: Bookmark) -> CGFloat {
        if bookmark.isShortForm { return portrait }
        return bookmark.platform.prefersPortrait ? portrait : landscape
    }

    static func forPlatform(_ platform: Platform) -> CGFloat {
        platform.prefersPortrait ? portrait : landscape
    }
}

// MARK: - Motion

enum Motion {
    /// Press feedback. Fast enough to feel like the surface responded, not
    /// like it animated.
    static let tap = Animation.spring(response: 0.26, dampingFraction: 0.8)
    /// Navigation and state changes with weight.
    static let standard = Animation.spring(response: 0.38, dampingFraction: 0.88)
    /// Opacity and small reveals. Never used on anything that changes height
    /// inside a scroll view — see the note in `LibraryView`.
    static let gentle = Animation.easeOut(duration: 0.22)

    static func respecting(_ animation: Animation, _ reduce: Bool) -> Animation {
        reduce ? .easeInOut(duration: 0.1) : animation
    }
}

// MARK: - Shared modifiers

extension View {
    /// The standard screen margin.
    func screenPadding() -> some View {
        padding(.horizontal, Space.screen)
    }

    /// A hairline separator inset to the text column.
    func hairline(leading: CGFloat = 0) -> some View {
        overlay(alignment: .bottom) {
            Rectangle()
                .fill(SavaColor.hairline)
                .frame(height: 0.5)
                .padding(.leading, leading)
        }
    }
}

// MARK: - Primitives

/// A quiet section heading. Uppercase, tracked, tertiary — it labels without
/// shouting, so the content below stays dominant.
struct SectionHeader: View {
    let text: String
    var trailing: String? = nil

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(text.uppercased())
                .font(SavaType.section)
                .tracking(Tracking.wide)
                .foregroundStyle(SavaColor.tertiary)
            Spacer(minLength: Space.s)
            if let trailing {
                Text(trailing)
                    .font(SavaType.numeric)
                    .foregroundStyle(SavaColor.tertiary)
                    // Counts tick rather than snap when a filter changes.
                    .contentTransition(.numericText())
                    .animation(Motion.gentle, value: trailing)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// Primary action. Solid ink, full width, no gradient, no glow.
struct SavaButton: View {
    let title: String
    var isLoading: Bool = false
    var isEnabled: Bool = true
    var role: Role = .primary
    let action: () -> Void

    enum Role { case primary, secondary, destructive }

    var body: some View {
        Button {
            Haptics.tap()
            action()
        } label: {
            ZStack {
                Text(title)
                    .font(SavaType.button)
                    .opacity(isLoading ? 0 : 1)
                if isLoading {
                    ProgressView().tint(foreground)
                }
            }
            .foregroundStyle(foreground)
            .frame(maxWidth: .infinity)
            .frame(height: 50)
            .background(background, in: RoundedRectangle(cornerRadius: Radius.control,
                                                        style: .continuous))
        }
        .buttonStyle(.pressable)
        .disabled(!isEnabled || isLoading)
        .animation(Motion.gentle, value: isLoading)
        .animation(Motion.gentle, value: isEnabled)
        .accessibilityLabel(title)
        .accessibilityAddTraits(isLoading ? [.updatesFrequently] : [])
    }

    /// The primary action is the citron moment.
    ///
    /// There is at most one of these on a screen, which is the entire reason it
    /// works: a single acid-yellow slab against ink is unmistakably the thing to
    /// press. Ink sits on it at 17.2:1, so it is also the most legible control
    /// in the product — the signature colour and the accessible choice happen to
    /// be the same decision.
    ///
    /// A disabled action recedes to a plain fill rather than a faded accent.
    /// Fading citron to 40% produces a sickly olive that still shouts.
    private var foreground: Color {
        guard isEnabled else { return SavaColor.tertiary }
        switch role {
        case .primary: return SavaColor.onAccent
        case .secondary: return SavaColor.primary
        case .destructive: return SavaColor.danger
        }
    }

    private var background: Color {
        guard isEnabled else { return SavaColor.fill }
        switch role {
        case .primary: return SavaColor.accent
        case .secondary, .destructive: return SavaColor.fill
        }
    }
}

/// A secondary, full-width action that sits inside content — "Ask this
/// collection", "Ask about this". A fill, a label, a chevron. No card.
struct SavaInlineAction: View {
    let title: String
    var symbol: String? = nil
    let action: () -> Void

    var body: some View {
        Button {
            Haptics.tap()
            action()
        } label: {
            HStack(spacing: Space.m) {
                if let symbol {
                    Image(systemName: symbol)
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(SavaColor.secondary)
                }
                Text(title).font(SavaType.button)
                Spacer(minLength: Space.s)
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(SavaColor.tertiary)
            }
            .foregroundStyle(SavaColor.primary)
            .padding(.horizontal, Space.l)
            .frame(height: 52)
            .background(SavaColor.fill,
                        in: RoundedRectangle(cornerRadius: Radius.control, style: .continuous))
        }
        .buttonStyle(.pressable)
    }
}

/// Text input. A recessed fill and a focus ring — no border in the resting state.
struct SavaField: View {
    let placeholder: String
    @Binding var text: String
    var isSecure: Bool = false
    var keyboard: UIKeyboardType = .default
    var contentType: UITextContentType? = nil
    var submitLabel: SubmitLabel = .next
    var isInvalid: Bool = false
    var onSubmit: () -> Void = {}

    @FocusState private var focused: Bool

    var body: some View {
        Group {
            if isSecure {
                SecureField(placeholder, text: $text)
            } else {
                TextField(placeholder, text: $text)
            }
        }
        .font(SavaType.body)
        .foregroundStyle(SavaColor.primary)
        .tint(SavaColor.accent)
        .keyboardType(keyboard)
        .textContentType(contentType)
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .submitLabel(submitLabel)
        .onSubmit(onSubmit)
        .focused($focused)
        .padding(.horizontal, Space.l)
        .frame(height: 52)
        .background(SavaColor.fill,
                    in: RoundedRectangle(cornerRadius: Radius.control, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                .strokeBorder(isInvalid ? SavaColor.danger : SavaColor.accent,
                              lineWidth: (focused || isInvalid) ? 1.5 : 0)
        )
        .animation(Motion.gentle, value: focused)
        .animation(Motion.gentle, value: isInvalid)
    }
}

/// A compact filter chip. Filled when active — no border in either state.
struct SavaChip: View {
    let title: String
    var count: Int? = nil
    let selected: Bool
    /// The platform this chip filters to, if any. Decides the selected fill.
    /// Nil means "All", which uses Sava's own blue.
    var platform: Platform? = nil
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Text(title)
                if let count {
                    Text("\(count)")
                        .monospacedDigit()
                        .contentTransition(.numericText())
                        .foregroundStyle(selected ? Color.white.opacity(0.65)
                                                  : SavaColor.tertiary)
                }
            }
            .font(.system(size: 13.5, weight: .medium))
            // Selection is blue, deliberately not citron. Citron marks the one
            // thing to *do* on a screen; a row of filters is state, not action,
            // and colouring five chips with the signature would spend it.
            .foregroundStyle(selected ? Color.white : SavaColor.primary)
            .padding(.horizontal, Space.m)
            .frame(height: 34)
            .background(selected ? SavaColor.platformFill(platform) : SavaColor.fill,
                        in: Capsule())
            .overlay {
                // Only TikTok's black needs this; every other fill separates
                // from the ground on its own.
                if selected && SavaColor.platformNeedsOutline(platform) {
                    Capsule().stroke(Color.white.opacity(0.55), lineWidth: 1)
                }
            }
            .contentShape(Capsule())
        }
        .buttonStyle(.pressable)
        .accessibilityLabel(count.map { "\(title), \($0)" } ?? title)
        .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
    }
}

/// Empty and error states. A line of type, a line of explanation, one action.
/// No illustration — an icon here would be decoration, not information.
/// A filled action chip that sits among the filter chips.
///
/// Deliberately the same size and rhythm as `SavaChip` so the row reads as one
/// control strip rather than as a button someone dropped next to some filters.
/// Filled rather than outlined because it acts instead of filtering, and that
/// is the only signal it needs — no icon badge, no gradient, no pill floating
/// over the content.
struct ScrollEntryChip: View {
    let title: String
    var action: () -> Void

    var body: some View {
        Button {
            Haptics.tap()
            action()
        } label: {
            HStack(spacing: 5) {
                Image(systemName: "play.fill")
                    .font(.system(size: 9, weight: .black))
                Text(title)
                    .font(SavaType.caption)
            }
            // Citron. This is the Library's one primary action, so it is the
            // one place the signature colour appears on that screen.
            .foregroundStyle(SavaColor.onAccent)
            .padding(.horizontal, Space.m)
            .padding(.vertical, 8)
            .background(SavaColor.accent, in: Capsule())
        }
        .buttonStyle(.pressable)
        .accessibilityLabel(title)
        .accessibilityHint("Opens a full-screen feed")
    }
}

/// A small filled rounded square carrying a glyph or a number.
///
/// The one decorative shape in the system, and it earns its place by doing a
/// job: in a list of four points, four tiles in rotating accents let the eye
/// find its place again after looking away. Colour here is positional, not
/// semantic — nobody has to learn what blue means.
///
/// Sized and cornered to echo the app icon, so the shape reads as Sava's rather
/// than as a generic chip.
struct AccentTile: View {
    /// Position in the list. Decides the colour.
    let index: Int
    /// An SF Symbol. When nil the tile shows its number instead.
    var symbol: String? = nil
    var size: CGFloat = 34

    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.28, style: .continuous)
            .fill(SavaColor.rotating(index))
            .frame(width: size, height: size)
            .overlay {
                Group {
                    if let symbol {
                        Image(systemName: symbol)
                            .font(.system(size: size * 0.42, weight: .semibold))
                    } else {
                        Text("\(index + 1)")
                            .font(.system(size: size * 0.40, weight: .bold))
                            .monospacedDigit()
                    }
                }
                .foregroundStyle(SavaColor.onRotating(index))
            }
            .accessibilityHidden(true)
    }
}

struct SavaEmptyState: View {
    let title: String
    let message: String
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: Space.s) {
            Text(title)
                .font(SavaType.title)
                .foregroundStyle(SavaColor.primary)
                .multilineTextAlignment(.center)
            Text(message)
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: 320)
            if let actionTitle, let action {
                Button(actionTitle) {
                    Haptics.tap()
                    action()
                }
                .font(SavaType.button)
                .foregroundStyle(SavaColor.accent)
                .padding(.top, Space.s)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, Space.xl)
        .padding(.vertical, Space.xxl)
    }
}

/// Skeleton block for loading. A restrained shimmer, and none at all when the
/// user has asked for reduced motion.
struct Skeleton: View {
    var cornerRadius: CGFloat = Radius.media
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase: CGFloat = -1.2

    var body: some View {
        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
            .fill(SavaColor.fill)
            .overlay {
                if !reduceMotion {
                    GeometryReader { geo in
                        LinearGradient(
                            colors: [.clear, SavaColor.primary.opacity(0.05), .clear],
                            startPoint: .leading, endPoint: .trailing
                        )
                        .frame(width: geo.size.width * 0.7)
                        .offset(x: phase * geo.size.width * 1.4)
                    }
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.linear(duration: 1.3).repeatForever(autoreverses: false)) {
                    phase = 1.2
                }
            }
            .accessibilityHidden(true)
    }
}

/// A settings/list row: label, optional detail, hairline. No card.
struct SavaRow: View {
    let title: String
    var detail: String? = nil
    var symbol: String? = nil
    var showsChevron: Bool = true
    var action: (() -> Void)? = nil

    var body: some View {
        Group {
            if let action {
                Button {
                    Haptics.tap()
                    action()
                } label: { content }
                .buttonStyle(.plain)
            } else {
                content
            }
        }
        .hairline()
    }

    private var content: some View {
        HStack(spacing: Space.m) {
            if let symbol {
                Image(systemName: symbol)
                    .font(.system(size: 15))
                    .foregroundStyle(SavaColor.secondary)
                    .frame(width: 22)
            }
            Text(title)
                .font(SavaType.body)
                .foregroundStyle(SavaColor.primary)
            Spacer(minLength: Space.s)
            if let detail {
                Text(detail)
                    .font(SavaType.callout)
                    .foregroundStyle(SavaColor.tertiary)
                    .lineLimit(1)
            }
            if showsChevron && action != nil {
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(SavaColor.tertiary)
            }
        }
        .frame(minHeight: 50)
        .contentShape(Rectangle())
    }
}
