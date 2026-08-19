import SwiftUI
import UIKit

/// Async, cached image with a graceful shimmer placeholder and failure state.
/// Cancels its load when it leaves the screen, keeping scrolling smooth.
struct RemoteImage<Placeholder: View, Failure: View>: View {
    let url: URL?
    var contentMode: ContentMode = .fill
    @ViewBuilder var placeholder: () -> Placeholder
    @ViewBuilder var failure: () -> Failure

    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: contentMode)
                    .transition(.opacity)
            } else if failed {
                failure()
            } else {
                placeholder()
            }
        }
        .task(id: url) { await load() }
    }

    private func load() async {
        image = nil
        failed = false
        guard let url else { failed = true; return }
        if let hit = await ImagePipeline.shared.cached(url) {
            image = hit
            return
        }
        do {
            let loaded = try await ImagePipeline.shared.image(for: url)
            guard !Task.isCancelled else { return }
            withAnimation(.easeOut(duration: 0.25)) { image = loaded }
        } catch is CancellationError {
            // Scrolled away — leave placeholder.
        } catch {
            if !Task.isCancelled { failed = true }
        }
    }
}

extension RemoteImage where Placeholder == ShimmerPlaceholder, Failure == ThumbnailFallback {
    /// Convenience for the common case: shimmer while loading, glyph on failure.
    init(url: URL?, platform: Platform, contentMode: ContentMode = .fill) {
        self.url = url
        self.contentMode = contentMode
        self.placeholder = { ShimmerPlaceholder() }
        self.failure = { ThumbnailFallback(platform: platform) }
    }
}

/// A subtle shimmer used as an image/skeleton placeholder.
struct ShimmerPlaceholder: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase: CGFloat = -1

    var body: some View {
        SavaColors.surfaceMuted
            .overlay {
                if !reduceMotion {
                    GeometryReader { geo in
                        LinearGradient(
                            colors: [.clear, SavaColors.hairline, .clear],
                            startPoint: .leading, endPoint: .trailing
                        )
                        .frame(width: geo.size.width * 0.6)
                        .offset(x: phase * geo.size.width * 1.4)
                    }
                }
            }
            .clipped()
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.linear(duration: 1.2).repeatForever(autoreverses: false)) {
                    phase = 1
                }
            }
    }
}

/// Shown when a thumbnail can't be loaded — a calm platform-tinted glyph.
struct ThumbnailFallback: View {
    let platform: Platform

    var body: some View {
        ZStack {
            SavaColors.surfaceMuted
            Image(systemName: platform.symbol)
                .font(.system(size: 26, weight: .semibold))
                .foregroundStyle(platform.tint.opacity(0.55))
        }
    }
}
