import SwiftUI
import UIKit

// Sava's design system.
//
// Deliberately small. The saved media supplies the colour and the personality;
// the interface stays quiet so it does not compete. Every token here earns its
// place — there is no palette of forty greys, no shadow scale, no gradient set.
//
// Rules the whole app follows:
//   * Media is the loudest thing on screen. Chrome is not.
//   * Structure comes from spacing and a hairline, not from boxes and borders.
//   * One accent, used for state and action only. Never for decoration.
//   * Geometry is fixed by us, never by whatever a CDN returned.

// MARK: - Colour

enum SavaColor {
    /// Page ground. Warm off-white; true near-black in dark so media pops.
    static let ground = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xFBFAF8), dark: UIColor(hex: 0x0A0A0B)))

    /// Raised surfaces: sheets, the bar, the few filled blocks that exist.
    static let surface = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xFFFFFF), dark: UIColor(hex: 0x151517)))

    /// Recessed fills: search fields, chips, media placeholders.
    static let fill = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xF1EFEB), dark: UIColor(hex: 0x1C1C1F)))

    static let primary = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x111113), dark: UIColor(hex: 0xF5F4F1)))
    static let secondary = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x5E5F66), dark: UIColor(hex: 0x9D9EA5)))
    static let tertiary = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x92939A), dark: UIColor(hex: 0x6C6D74)))

    /// The only structural line in the app. 0.5pt, inset to the text column.
    static let hairline = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x000000, alpha: 0.10),
        dark: UIColor(hex: 0xFFFFFF, alpha: 0.12)))

    /// One accent. Focus, active state, primary action, live processing.
    static let accent = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x1F3FE0), dark: UIColor(hex: 0x8098FF)))

    static let danger = Color(uiColor: .dynamic(
        light: UIColor(hex: 0xB3341F), dark: UIColor(hex: 0xFF7A63)))
    static let success = Color(uiColor: .dynamic(
        light: UIColor(hex: 0x0E6E4F), dark: UIColor(hex: 0x45C795)))
}

// MARK: - Type

enum SavaType {
    /// The wordmark. The one place Sava sets its own name.
    static let wordmark = Font.system(size: 38, weight: .bold)
    static let title = Font.system(size: 21, weight: .semibold)
    /// A statement line — the promise on the sign-in screen, an Ask question.
    static let lede = Font.system(size: 20, weight: .regular)
    /// Section headers within a screen. Small, tracked, quiet.
    static let section = Font.system(size: 12, weight: .semibold)
    /// A media item's title in a card or row.
    static let mediaTitle = Font.system(size: 14.5, weight: .semibold)
    /// Creator, platform, duration — the quiet line under a title.
    static let meta = Font.system(size: 12, weight: .regular)
    /// Reading text.
    static let body = Font.system(size: 16, weight: .regular)
    /// AI prose. A serif keeps a summary editorial rather than machine-printed —
    /// the single strongest signal that this is writing, not output.
    static let prose = Font.system(size: 17, weight: .regular, design: .serif)
    static let callout = Font.system(size: 15, weight: .regular)
    static let caption = Font.system(size: 12, weight: .medium)
    static let button = Font.system(size: 16, weight: .semibold)
    /// Numerals that must not jitter as they change.
    static let numeric = Font.system(size: 12, weight: .medium).monospacedDigit()
}

// MARK: - Space & geometry

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
    /// Media plates. The only rounded geometry that matters.
    static let media: CGFloat = 10
    static let control: CGFloat = 12
}

/// Deterministic media geometry.
///
/// Source dimensions never decide layout. A vertical TikTok and a tiny
/// Instagram thumbnail land in exactly the same box, so a scrolling grid stays
/// on a rhythm instead of jittering per item.
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

    static func forPlatform(_ platform: Platform) -> CGFloat {
        platform.prefersPortrait ? portrait : landscape
    }
}

// MARK: - Motion

enum Motion {
    static let tap = Animation.spring(response: 0.26, dampingFraction: 0.8)
    static let standard = Animation.spring(response: 0.38, dampingFraction: 0.88)
    static let gentle = Animation.easeOut(duration: 0.22)

    /// Collapses to a near-instant fade when Reduce Motion is on.
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
                .tracking(0.8)
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

    /// A disabled primary action recedes to a plain fill rather than a faded
    /// slab of ink. Fading a near-black button to 40% produces a heavy grey
    /// block that still dominates the screen — the opposite of "unavailable".
    private var foreground: Color {
        guard isEnabled else { return SavaColor.tertiary }
        switch role {
        case .primary: return SavaColor.ground
        case .secondary: return SavaColor.primary
        case .destructive: return SavaColor.danger
        }
    }

    private var background: Color {
        guard isEnabled else { return SavaColor.fill }
        switch role {
        case .primary: return SavaColor.primary
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
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Text(title)
                if let count {
                    Text("\(count)")
                        .monospacedDigit()
                        .contentTransition(.numericText())
                        .foregroundStyle(selected ? SavaColor.ground.opacity(0.6)
                                                  : SavaColor.tertiary)
                }
            }
            .font(.system(size: 13.5, weight: .medium))
            .foregroundStyle(selected ? SavaColor.ground : SavaColor.primary)
            .padding(.horizontal, Space.m)
            .frame(height: 32)
            .background(selected ? SavaColor.primary : SavaColor.fill, in: Capsule())
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
            .foregroundStyle(SavaColor.ground)
            .padding(.horizontal, Space.m)
            .padding(.vertical, 7)
            .background(SavaColor.primary, in: Capsule())
        }
        .buttonStyle(.pressable)
        .accessibilityLabel(title)
        .accessibilityHint("Opens a full-screen feed")
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
