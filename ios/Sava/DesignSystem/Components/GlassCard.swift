import SwiftUI

/// A layered glass surface used for floating auth/AI surfaces.
/// Real material blur + a fine hairline + a soft top-light gradient give it
/// depth without the "random glassmorphism" look.
struct GlassCard<Content: View>: View {
    var cornerRadius: CGFloat = Radius.xl
    @ViewBuilder var content: () -> Content

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        content()
            .background {
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .fill(.ultraThinMaterial)
                    .overlay {
                        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                            .fill(
                                LinearGradient(
                                    colors: [
                                        Color.white.opacity(scheme == .dark ? 0.06 : 0.5),
                                        Color.white.opacity(0.0)
                                    ],
                                    startPoint: .top,
                                    endPoint: .center
                                )
                            )
                    }
            }
            .overlay {
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .strokeBorder(SavaColors.hairline, lineWidth: 1)
            }
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .shadow(
                color: Elevation.card(scheme).color,
                radius: Elevation.card(scheme).radius,
                x: 0,
                y: Elevation.card(scheme).y
            )
    }
}
