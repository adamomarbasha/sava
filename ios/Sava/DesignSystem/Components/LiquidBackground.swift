import SwiftUI

/// A slow, ambient "liquid" field — soft blurred light blobs that drift.
/// Inspired by Canvas UI's Liquid/Displacement feel, translated into a cheap,
/// GPU-friendly effect (a handful of blurred radial gradients).
///
/// Honors Reduce Motion by holding a calm static composition.
struct LiquidBackground: View {
    var animated: Bool = true

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase: CGFloat = 0

    private struct Blob: Identifiable {
        let id = UUID()
        let color: Color
        let size: CGFloat
        let base: CGPoint
        let drift: CGSize
    }

    private var blobs: [Blob] {
        [
            Blob(color: SavaColors.meshA, size: 460, base: CGPoint(x: 0.18, y: 0.16), drift: CGSize(width: 26, height: 34)),
            Blob(color: SavaColors.meshB, size: 400, base: CGPoint(x: 0.86, y: 0.30), drift: CGSize(width: -30, height: 22)),
            Blob(color: SavaColors.meshC, size: 520, base: CGPoint(x: 0.70, y: 0.88), drift: CGSize(width: 24, height: -28))
        ]
    }

    var body: some View {
        GeometryReader { geo in
            ZStack {
                SavaColors.background

                ForEach(blobs) { blob in
                    let dx = animated && !reduceMotion ? blob.drift.width * phase : 0
                    let dy = animated && !reduceMotion ? blob.drift.height * phase : 0
                    Circle()
                        .fill(blob.color)
                        .frame(width: blob.size, height: blob.size)
                        .position(
                            x: blob.base.x * geo.size.width + dx,
                            y: blob.base.y * geo.size.height + dy
                        )
                        .blur(radius: 90)
                }
            }
            .ignoresSafeArea()
            .onAppear {
                guard animated, !reduceMotion else { return }
                withAnimation(SavaMotion.ambient) { phase = 1 }
            }
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}
