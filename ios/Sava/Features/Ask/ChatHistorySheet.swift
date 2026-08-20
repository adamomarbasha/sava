import SwiftUI

/// Past conversations.
///
/// A chat that vanishes when you close it is a demo, not a feature — the whole
/// value of asking Sava something is that the answer stays findable. This is the
/// list of everything asked in a given scope: across the library on the Ask tab,
/// or about one specific item when opened from its detail screen.
struct ChatHistorySheet: View {
    let scope: String
    let bookmarkID: Int?
    let onOpen: (ChatThreadSummary) -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss

    @State private var threads: [ChatThreadSummary] = []
    @State private var loading = true

    private var intelligence: IntelligenceService {
        IntelligenceService(client: session.api)
    }

    var body: some View {
        NavigationStack {
            Group {
                if loading {
                    skeleton
                } else if threads.isEmpty {
                    SavaEmptyState(
                        title: "No conversations yet",
                        message: bookmarkID == nil
                            ? "Anything you ask Sava will show up here."
                            : "Anything you ask about this will show up here.")
                } else {
                    list
                }
            }
            .background(SavaColor.ground)
            .navigationTitle("History")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Done") { dismiss() }
                }
            }
            .tint(SavaColor.primary)
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .task { await load() }
    }

    private var list: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                ForEach(threads) { thread in
                    Button {
                        Haptics.select()
                        onOpen(thread)
                        dismiss()
                    } label: {
                        row(thread)
                    }
                    .buttonStyle(.pressable)
                    .hairline()
                }
            }
            .screenPadding()
            .padding(.vertical, Space.s)
        }
    }

    private func row(_ thread: ChatThreadSummary) -> some View {
        HStack(alignment: .top, spacing: Space.m) {
            VStack(alignment: .leading, spacing: 3) {
                Text(thread.title)
                    .font(SavaType.body)
                    .foregroundStyle(SavaColor.primary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)

                Text(subtitle(thread))
                    .font(SavaType.meta)
                    .foregroundStyle(SavaColor.tertiary)
            }
            Spacer(minLength: Space.s)
            Image(systemName: "chevron.right")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(SavaColor.tertiary)
                .padding(.top, 3)
        }
        .padding(.vertical, Space.m)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(thread.title). \(subtitle(thread))")
    }

    private func subtitle(_ thread: ChatThreadSummary) -> String {
        let count = "\(thread.exchangeCount) message\(thread.exchangeCount == 1 ? "" : "s")"
        guard let age = Format.relativeAge(thread.updatedAt) else { return count }
        return "\(count) · \(age)"
    }

    private var skeleton: some View {
        VStack(spacing: Space.l) {
            ForEach(0..<5, id: \.self) { _ in
                VStack(alignment: .leading, spacing: Space.s) {
                    Skeleton(cornerRadius: 4).frame(height: 15)
                    Skeleton(cornerRadius: 4).frame(width: 110, height: 11)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Spacer()
        }
        .screenPadding()
        .padding(.top, Space.l)
        .accessibilityHidden(true)
    }

    private func load() async {
        threads = (try? await intelligence.threads(scope: scope, bookmarkID: bookmarkID)) ?? []
        withAnimation(Motion.gentle) { loading = false }
    }
}
