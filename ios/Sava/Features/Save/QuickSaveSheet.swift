import SwiftUI

/// A focused sheet for saving a link by hand — paste a URL, Sava ingests it.
/// Mirrors the Action Button pipeline and celebrates success with a ripple.
struct QuickSaveSheet: View {
    let onSaved: (Bookmark) -> Void

    @EnvironmentObject private var session: SessionStore
    @StateObject private var model = QuickSaveViewModel()
    @Environment(\.dismiss) private var dismiss
    @FocusState private var focused: Bool

    private var service: BookmarkService { BookmarkService(client: session.api) }

    var body: some View {
        NavigationStack {
            ZStack {
                SavaColors.background.ignoresSafeArea()
                content
            }
            .navigationTitle("Save a link")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .presentationDetents([.height(360), .medium])
        .presentationDragIndicator(.visible)
        .onAppear {
            model.prefillFromClipboardIfEmpty()
            focused = true
        }
    }

    @ViewBuilder private var content: some View {
        switch model.phase {
        case .saved(let bookmark):
            SaveSuccessView(bookmark: bookmark) { dismiss() }
        default:
            editor
        }
    }

    private var editor: some View {
        VStack(spacing: Spacing.md) {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                HStack {
                    Text("Paste a link")
                        .font(SavaFont.caption)
                        .foregroundStyle(SavaColors.textSecondary)
                        .textCase(.uppercase).tracking(0.6)
                    Spacer()
                    if let platform = model.detectedPlatform, platform != .other {
                        HStack(spacing: 4) {
                            PlatformBadge(platform: platform, size: 18)
                            Text(platform.displayName).font(SavaFont.caption)
                        }
                        .foregroundStyle(SavaColors.textSecondary)
                        .transition(.opacity)
                    }
                }

                HStack(spacing: Spacing.sm) {
                    Image(systemName: "link").foregroundStyle(SavaColors.textTertiary)
                    TextField("https://…", text: $model.urlText)
                        .font(SavaFont.body)
                        .foregroundStyle(SavaColors.textPrimary)
                        .tint(SavaColors.accent)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .focused($focused)
                        .submitLabel(.done)
                        .onSubmit(save)
                    if !model.urlText.isEmpty {
                        Button { model.urlText = "" } label: {
                            Image(systemName: "xmark.circle.fill").foregroundStyle(SavaColors.textTertiary)
                        }.buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, Spacing.md)
                .frame(height: 54)
                .background(SavaColors.surface, in: RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .strokeBorder(focused ? SavaColors.accent : SavaColors.separator, lineWidth: focused ? 2 : 1))
                .animation(SavaMotion.tap, value: focused)
                .animation(SavaMotion.tap, value: model.detectedPlatform)
            }

            if case .failed(let message) = model.phase {
                InlineBanner(text: message)
            }

            SavaPrimaryButton(title: "Save to Sava",
                              isLoading: model.phase == .saving,
                              isEnabled: model.canSave,
                              action: save)

            Text("Sava saves it instantly and analyzes it in the background.")
                .font(SavaFont.footnote)
                .foregroundStyle(SavaColors.textTertiary)
                .multilineTextAlignment(.center)

            Spacer(minLength: 0)
        }
        .padding(Spacing.lg)
    }

    private func save() {
        focused = false
        Task { await model.save(service: service, onSaved: onSaved) }
    }
}

/// Celebratory confirmation after a save, with a particle-reveal-inspired burst.
private struct SaveSuccessView: View {
    let bookmark: Bookmark
    let onDone: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false

    var body: some View {
        VStack(spacing: Spacing.md) {
            ZStack {
                Circle().fill(SavaColors.success.opacity(0.14)).frame(width: 92, height: 92)
                Image(systemName: "checkmark")
                    .font(.system(size: 38, weight: .bold))
                    .foregroundStyle(SavaColors.success)
                    .scaleEffect(appeared || reduceMotion ? 1 : 0.4)
            }
            Text("Saved to Sava")
                .font(SavaFont.title2)
                .foregroundStyle(SavaColors.textPrimary)
            Text(bookmark.displayTitle)
                .font(SavaFont.callout)
                .foregroundStyle(SavaColors.textSecondary)
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .padding(.horizontal, Spacing.lg)

            Button(action: onDone) {
                Text("Done")
                    .font(SavaFont.headline)
                    .foregroundStyle(SavaColors.background)
                    .frame(maxWidth: .infinity).frame(height: 52)
                    .background(SavaColors.textPrimary, in: RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            }
            .buttonStyle(.pressable)
            .padding(.top, Spacing.xs)
        }
        .padding(Spacing.xl)
        .onAppear { withAnimation(SavaMotion.bounce) { appeared = true } }
    }
}
