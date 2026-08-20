import SwiftUI

/// The full transcript of a piece of media.
///
/// Reading a transcript is a different job from watching the thing, so this is
/// its own surface rather than a section buried under the summary: a search
/// field that filters as you type, timestamps you can scan down, and one action
/// to take the whole thing away with you.
///
/// The transcript is already stored against canonical content, so opening this
/// costs a single cached read — no fetch, no transcription, no inference.
struct TranscriptView: View {
    let bookmark: Bookmark

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var transcript: Transcript?
    @State private var loading = true
    @State private var query = ""
    @State private var copied = false
    @FocusState private var searchFocused: Bool

    private var intelligence: IntelligenceService {
        IntelligenceService(client: session.api)
    }

    private var matches: [Transcript.Segment] {
        guard let transcript else { return [] }
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !needle.isEmpty else { return transcript.segments }
        return transcript.segments.filter { $0.text.lowercased().contains(needle) }
    }

    var body: some View {
        NavigationStack {
            Group {
                if loading {
                    loadingState
                } else if let transcript, !transcript.segments.isEmpty {
                    content(transcript)
                } else {
                    SavaEmptyState(
                        title: "No transcript",
                        message: unavailableMessage)
                }
            }
            .background(SavaColor.ground)
            .navigationTitle("Transcript")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Done") { dismiss() }
                }
                if transcript?.segments.isEmpty == false {
                    ToolbarItem(placement: .topBarTrailing) { copyButton }
                }
            }
            .tint(SavaColor.primary)
        }
        .presentationDragIndicator(.visible)
        .task { await load() }
    }

    // MARK: Content

    private func content(_ transcript: Transcript) -> some View {
        VStack(spacing: 0) {
            searchField

            if matches.isEmpty {
                SavaEmptyState(title: "No matches",
                               message: "Nothing in this transcript mentions “\(query)”.")
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(matches) { segment in
                            row(segment)
                        }
                    }
                    .screenPadding()
                    .padding(.vertical, Space.s)
                }
                .scrollDismissesKeyboard(.interactively)
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { footer(transcript) }
    }

    /// A timestamp column and the line beside it. Reads like a script, scans
    /// like an index.
    private func row(_ segment: Transcript.Segment) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.m) {
            Text(segment.timestamp)
                .font(SavaType.numeric)
                .foregroundStyle(SavaColor.tertiary)
                .frame(width: 44, alignment: .leading)

            highlighted(segment.text)
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.primary)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, Space.s)
        .textSelection(.enabled)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(segment.timestamp). \(segment.text)")
    }

    /// Marks the search term inside the line, so a match is visible without
    /// having to re-read the sentence looking for it.
    private func highlighted(_ text: String) -> Text {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty,
              let range = text.range(of: needle, options: .caseInsensitive)
        else { return Text(text) }

        return Text(text[text.startIndex..<range.lowerBound])
            + Text(text[range]).foregroundColor(SavaColor.accent).bold()
            + Text(text[range.upperBound...])
    }

    private var searchField: some View {
        HStack(spacing: Space.s) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(SavaColor.tertiary)

            TextField("Search transcript", text: $query)
                .font(SavaType.body)
                .foregroundStyle(SavaColor.primary)
                .tint(SavaColor.accent)
                .autocorrectionDisabled()
                .focused($searchFocused)
                .submitLabel(.search)

            if !query.isEmpty {
                Button {
                    withAnimation(Motion.gentle) { query = "" }
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 15))
                        .foregroundStyle(SavaColor.tertiary)
                }
                .buttonStyle(.plain)
                .transition(.opacity)
                .accessibilityLabel("Clear search")
            }
        }
        .padding(.horizontal, Space.m)
        .frame(height: 40)
        .background(SavaColor.fill, in: Capsule())
        .screenPadding()
        .padding(.vertical, Space.m)
        .background(alignment: .bottom) {
            Rectangle().fill(SavaColor.hairline).frame(height: 0.5)
        }
    }

    /// How many lines, and where they came from. Captions are the platform's own
    /// text; anything else was heard by a model and may be imperfect — worth
    /// saying once, quietly, rather than pretending both are the same thing.
    private func footer(_ transcript: Transcript) -> some View {
        HStack {
            Text(query.isEmpty
                 ? "\(transcript.segments.count) lines"
                 : "\(matches.count) of \(transcript.segments.count) lines")
                .contentTransition(.numericText())
            Spacer()
            Text(transcript.sourceLabel)
        }
        .font(SavaType.meta)
        .foregroundStyle(SavaColor.tertiary)
        .screenPadding()
        .padding(.vertical, Space.m)
        .background(alignment: .top) {
            Rectangle().fill(SavaColor.hairline).frame(height: 0.5)
        }
        .background(.bar)
        .animation(Motion.gentle, value: matches.count)
    }

    private var copyButton: some View {
        Button {
            UIPasteboard.general.string = transcript?.plainText ?? ""
            Haptics.success()
            withAnimation(Motion.tap) { copied = true }
            Task {
                try? await Task.sleep(for: .seconds(1.6))
                withAnimation(Motion.gentle) { copied = false }
            }
        } label: {
            Image(systemName: copied ? "checkmark" : "doc.on.doc")
                .font(.system(size: 15, weight: .medium))
                .contentTransition(.symbolEffect(.replace))
        }
        .accessibilityLabel(copied ? "Copied" : "Copy transcript")
    }

    private var loadingState: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            ForEach(0..<10, id: \.self) { index in
                HStack(alignment: .top, spacing: Space.m) {
                    Skeleton(cornerRadius: 4).frame(width: 34, height: 12)
                    Skeleton(cornerRadius: 4)
                        .frame(height: 12)
                        .frame(maxWidth: index.isMultiple(of: 3) ? 200 : .infinity)
                }
            }
            Spacer()
        }
        .screenPadding()
        .padding(.top, Space.l)
    }

    private var unavailableMessage: String {
        bookmark.isProcessing
            ? "Sava is still reading this one."
            : "There's no transcript for this — it may have no speech, or the platform didn't provide captions."
    }

    private func load() async {
        transcript = try? await intelligence.transcript(bookmarkID: bookmark.id)
        withAnimation(Motion.respecting(Motion.gentle, reduceMotion)) { loading = false }
    }
}
