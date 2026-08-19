import SwiftUI

/// "Ask this save" — grounded Q&A about one piece of content.
///
/// The backend answers from persisted transcript/OCR chunks, so asking never
/// re-downloads or re-transcribes the media. Answers carry timestamps back to
/// the moment they came from.
///
/// The picker offers Sava Auto / Fast / Advanced. Those are Sava's own routing
/// intents, not model names — the product never surfaces a vendor.
struct AskSavaSection: View {
    let bookmark: Bookmark

    @EnvironmentObject private var session: SessionStore
    @State private var question = ""
    @State private var mode: AskMode = .auto
    @State private var turns: [Turn] = []
    @State private var isAsking = false
    @State private var errorMessage: String?
    @State private var threadID: Int?
    @FocusState private var focused: Bool

    private var service: IntelligenceService { IntelligenceService(client: session.api) }

    struct Turn: Identifiable, Equatable {
        let id = UUID()
        let question: String
        let answer: String
        let citations: [AskAnswer.Citation]
    }

    private var examples: [String] {
        switch bookmark.platform {
        case .youtube:
            return ["What are the main points?", "What's said about pricing?"]
        case .tiktok, .instagram:
            return ["What products are shown?", "Where is this?"]
        default:
            return ["What is this about?", "Give me the key takeaways"]
        }
    }

    var body: some View {
        DetailSection(title: "Ask this save", systemImage: "text.bubble") {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                modePicker

                if turns.isEmpty && !isAsking {
                    Text("Ask anything about this save. Sava answers from what it read — transcript, captions, and on-screen text.")
                        .font(SavaFont.footnote)
                        .foregroundStyle(SavaColors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)

                    FlexibleWrap(spacing: Spacing.xs, lineSpacing: Spacing.xs) {
                        ForEach(examples, id: \.self) { example in
                            Button {
                                question = example
                                ask()
                            } label: {
                                Text(example)
                                    .font(SavaFont.footnote)
                                    .foregroundStyle(SavaColors.textPrimary)
                                    .padding(.horizontal, Spacing.sm)
                                    .frame(height: 32)
                                    .background(SavaColors.surfaceMuted, in: Capsule())
                            }
                            .buttonStyle(.pressable)
                        }
                    }
                }

                ForEach(turns) { turn in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(turn.question)
                            .font(SavaFont.subheadline)
                            .foregroundStyle(SavaColors.textSecondary)
                        Text(turn.answer)
                            .font(SavaFont.callout)
                            .foregroundStyle(SavaColors.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                            .textSelection(.enabled)

                        if !turn.citations.isEmpty {
                            HStack(spacing: 6) {
                                Image(systemName: "clock")
                                    .font(.system(size: 10, weight: .semibold))
                                ForEach(turn.citations.prefix(4)) { citation in
                                    Text(citation.timestamp ?? "—")
                                        .font(.system(size: 11, weight: .semibold, design: .rounded))
                                        .monospacedDigit()
                                }
                            }
                            .foregroundStyle(SavaColors.textTertiary)
                            .padding(.top, 2)
                        }
                    }
                    .padding(.vertical, Spacing.xxs)
                }

                if isAsking {
                    HStack(spacing: Spacing.xs) {
                        ProgressView().controlSize(.small)
                        Text("Reading this save…")
                            .font(SavaFont.footnote)
                            .foregroundStyle(SavaColors.textSecondary)
                    }
                }

                if let errorMessage {
                    InlineBanner(text: errorMessage)
                }

                inputRow
            }
            .padding(Spacing.md)
            .background(
                RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                    .fill(SavaColors.surface)
                    .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                        .strokeBorder(SavaColors.hairline, lineWidth: 1))
            )
        }
    }

    // MARK: Mode picker

    private var modePicker: some View {
        Menu {
            Picker("Mode", selection: $mode) {
                ForEach(AskMode.allCases) { option in
                    VStack(alignment: .leading) {
                        Text(option.title)
                        Text(option.subtitle)
                    }
                    .tag(option)
                }
            }
        } label: {
            HStack(spacing: 4) {
                Text(mode.title)
                    .font(.system(size: 13, weight: .semibold))
                Image(systemName: "chevron.down")
                    .font(.system(size: 9, weight: .bold))
            }
            .foregroundStyle(SavaColors.textPrimary)
            .padding(.horizontal, Spacing.sm)
            .frame(height: 30)
            .background(SavaColors.surfaceMuted, in: Capsule())
        }
        .accessibilityLabel("Answer mode, currently \(mode.title). \(mode.subtitle)")
    }

    // MARK: Input

    private var inputRow: some View {
        HStack(spacing: Spacing.xs) {
            TextField("Ask a question…", text: $question, axis: .vertical)
                .font(SavaFont.callout)
                .foregroundStyle(SavaColors.textPrimary)
                .tint(SavaColors.accent)
                .lineLimit(1...4)
                .focused($focused)
                .submitLabel(.send)
                .onSubmit(ask)
                .padding(.horizontal, Spacing.md)
                .padding(.vertical, 12)
                .background(SavaColors.surfaceMuted,
                            in: RoundedRectangle(cornerRadius: Radius.md, style: .continuous))

            Button(action: ask) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 30))
                    .foregroundStyle(canAsk ? SavaColors.accent : SavaColors.textTertiary)
            }
            .buttonStyle(.plain)
            .disabled(!canAsk)
            .accessibilityLabel("Send question")
        }
    }

    private var canAsk: Bool {
        !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isAsking
    }

    private func ask() {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isAsking else { return }
        focused = false
        isAsking = true
        errorMessage = nil
        question = ""
        Haptics.tap()

        Task {
            do {
                let result = try await service.askThis(
                    bookmarkID: bookmark.id, question: text, mode: mode, threadID: threadID)
                await MainActor.run {
                    isAsking = false
                    if result.ok, let answer = result.answer, !answer.isEmpty {
                        threadID = result.threadID ?? threadID
                        withAnimation(SavaMotion.standard) {
                            turns.append(Turn(question: text, answer: answer,
                                              citations: result.citations))
                        }
                        Haptics.success()
                    } else {
                        // Honest state: say what's missing rather than inventing an answer.
                        errorMessage = result.message
                            ?? "Sava hasn't finished reading this save yet."
                    }
                }
            } catch {
                await MainActor.run {
                    isAsking = false
                    errorMessage = (error as? APIError)?.userMessage
                        ?? "Couldn't get an answer. Try again."
                    Haptics.error()
                }
            }
        }
    }
}

/// Simple wrapping row of non-interactive suggestion chips.
struct FlowChips: View {
    let items: [String]

    var body: some View {
        FlexibleWrap(spacing: Spacing.xs, lineSpacing: Spacing.xs) {
            ForEach(items, id: \.self) { item in
                Text(item)
                    .font(SavaFont.footnote)
                    .foregroundStyle(SavaColors.textSecondary)
                    .padding(.horizontal, Spacing.sm)
                    .frame(height: 32)
                    .background(SavaColors.surfaceMuted, in: Capsule())
            }
        }
    }
}
