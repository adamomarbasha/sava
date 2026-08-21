import SwiftUI

/// Paste a link.
///
/// One field, one button, no note field, no confirmation screen. Saving should
/// feel almost invisible: the sheet dismisses itself the moment the save exists
/// and the item appears at the top of the library, enriching in place. There is
/// nothing here to read and nothing to decide.
struct QuickSaveSheet: View {
    let onSaved: (Bookmark) -> Void

    @EnvironmentObject private var session: SessionStore
    @StateObject private var model = QuickSaveViewModel()
    @Environment(\.dismiss) private var dismiss
    @FocusState private var focused: Bool

    private var service: BookmarkService { BookmarkService(client: session.api) }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: Space.m) {
                SavaField(placeholder: "Paste a link", text: $model.urlText,
                          keyboard: .URL, contentType: .URL, submitLabel: .go,
                          onSubmit: save)
                    .focused($focused)

                HStack(spacing: Space.m) {
                    // A quiet acknowledgement that Sava recognised the link.
                    // Enough feedback to feel understood, not enough to be a step.
                    Text(model.detectedPlatform?.displayName ?? " ")
                        .font(SavaType.meta)
                        .foregroundStyle(SavaColor.tertiary)
                        .opacity(model.detectedPlatform == nil ? 0 : 1)

                    Spacer(minLength: 0)

                    if model.urlText.isEmpty && model.clipboardMayHoldLink {
                        PasteButton(payloadType: String.self) { model.acceptPasted($0) }
                            .buttonBorderShape(.capsule)
                            .labelStyle(.titleAndIcon)
                            .tint(SavaColor.primary)
                            .transition(.opacity)
                    }
                }
                .frame(height: 30)

                if case .failed(let message) = model.phase {
                    Text(message)
                        .font(SavaType.callout)
                        .foregroundStyle(SavaColor.danger)
                        .fixedSize(horizontal: false, vertical: true)
                }

                SavaButton(title: "Add", isLoading: model.phase == .saving,
                           isEnabled: model.canSave, action: save)

                Spacer(minLength: 0)
            }
            .screenPadding()
            .padding(.top, Space.l)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(SavaColor.ground)
            .navigationTitle("Save a link")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                }
            }
            .tint(SavaColor.primary)
        }
        .presentationDetents([.height(280)])
        .presentationDragIndicator(.visible)
        .onAppear {
            focused = true
            model.refreshClipboardHint()
        }
        .animation(Motion.gentle, value: model.detectedPlatform)
        .animation(Motion.gentle, value: model.phase)
        .animation(Motion.gentle, value: model.clipboardMayHoldLink)
    }

    private func save() {
        focused = false
        Task {
            await model.save(service: service) { bookmark in
                onSaved(bookmark)
                dismiss()
            }
        }
    }
}
