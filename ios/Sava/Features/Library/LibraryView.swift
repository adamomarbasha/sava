import SwiftUI

/// The home library — the user's real saved content, content-first, in a
/// balanced two-column masonry with instant platform filtering.
struct LibraryView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var model: LibraryViewModel

    private var service: BookmarkService { BookmarkService(client: session.api) }

    var body: some View {
        ScrollView {
            LazyVStack(spacing: Spacing.lg, pinnedViews: [.sectionHeaders]) {
                Section {
                    content
                        .padding(.horizontal, Spacing.md)
                        .padding(.bottom, 120)
                } header: {
                    header
                }
            }
        }
        .background(SavaColors.background)
        .refreshable { await model.refresh(service) }
        .task { await model.loadIfNeeded(service) }
    }

    // MARK: Header (title + filter pills)

    private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            HStack(alignment: .firstTextBaseline) {
                Text("Library")
                    .font(SavaFont.largeTitle)
                    .foregroundStyle(SavaColors.textPrimary)
                Spacer()
                if !model.all.isEmpty {
                    Text("\(model.all.count)")
                        .font(SavaFont.subheadline)
                        .foregroundStyle(SavaColors.textTertiary)
                        .monospacedDigit()
                }
            }
            .padding(.horizontal, Spacing.md)
            .padding(.top, Spacing.sm)

            if !model.all.isEmpty {
                filterBar
            }
        }
        .padding(.bottom, Spacing.sm)
        .background(SavaColors.background.opacity(0.96))
    }

    private var filterBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Spacing.xs) {
                FilterPill(title: "All", count: model.count(for: nil),
                           isSelected: model.selectedPlatform == nil,
                           tint: SavaColors.accent) {
                    model.setFilter(nil)
                }
                ForEach(model.availablePlatforms) { platform in
                    FilterPill(title: platform.displayName,
                               count: model.count(for: platform),
                               isSelected: model.selectedPlatform == platform,
                               tint: platform.tint) {
                        model.setFilter(platform)
                    }
                }
            }
            .padding(.horizontal, Spacing.md)
        }
    }

    // MARK: Content states

    @ViewBuilder private var content: some View {
        switch model.state {
        case .idle:
            LibrarySkeleton()
        case .loading where model.all.isEmpty:
            LibrarySkeleton()
        case .failed(let message):
            StatusView(icon: "wifi.exclamationmark",
                       title: "Can't reach Sava",
                       message: message,
                       tint: SavaColors.danger,
                       actionTitle: "Try again") {
                Task { await model.load(service) }
            }
            .frame(maxWidth: .infinity)
            .padding(.top, Spacing.xxl)
        case .empty:
            StatusView(icon: "bookmark",
                       title: "Your library is empty",
                       message: "Save a video from TikTok, YouTube, or Instagram and it lands here — understood and searchable.")
                .frame(maxWidth: .infinity)
                .padding(.top, Spacing.xxl)
        default:
            feed
        }
    }

    private var feed: some View {
        BookmarkGrid(bookmarks: model.visible,
                     processingIDs: model.processingIDs,
                     onDelete: { bookmark in
                         Task { await model.delete(bookmark, using: service) }
                     })
    }
}

/// A filter pill with a live count. Selected state fills with the platform tint.
private struct FilterPill: View {
    let title: String
    let count: Int
    let isSelected: Bool
    let tint: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Text(title)
                Text("\(count)")
                    .monospacedDigit()
                    .foregroundStyle(isSelected ? SavaColors.background.opacity(0.7) : SavaColors.textTertiary)
            }
            .font(SavaFont.subheadline)
            .foregroundStyle(isSelected ? SavaColors.background : SavaColors.textPrimary)
            .padding(.horizontal, Spacing.sm)
            .frame(height: 34)
            .background(
                Capsule().fill(isSelected ? SavaColors.textPrimary : SavaColors.surfaceMuted)
            )
        }
        .buttonStyle(.pressable)
    }
}

/// Scale-down press feedback for cards.
struct CardPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(SavaMotion.tap, value: configuration.isPressed)
    }
}
