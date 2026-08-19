import SwiftUI

/// Presents the transcript — the substantive, real understanding of a video the
/// backend produces. Framed in plain product language ("Transcript",
/// "Auto-generated"), never pipeline jargon. Loads on appear; a full reader
/// with search opens in a sheet to keep the detail scroll light.
struct TranscriptSection: View {
    @ObservedObject var model: DetailViewModel
    let service: ContentService

    @State private var showReader = false

    var body: some View {
        Group {
            switch model.transcriptState {
            case .unsupported:
                EmptyView()
            case .idle, .loading:
                DetailSection(title: "Transcript", subtitle: "Auto-generated", systemImage: "text.alignleft") {
                    VStack(alignment: .leading, spacing: Spacing.xs) {
                        ForEach(0..<3, id: \.self) { _ in
                            ShimmerPlaceholder().frame(height: 12).clipShape(Capsule())
                        }
                        ShimmerPlaceholder().frame(width: 120, height: 12).clipShape(Capsule())
                    }
                }
            case .loaded(let segments, let language):
                loaded(segments, language: language)
            case .unavailable(let reason):
                DetailSection(title: "Transcript", systemImage: "text.alignleft") {
                    Text(reason)
                        .font(SavaFont.footnote)
                        .foregroundStyle(SavaColors.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            case .failed(let message):
                DetailSection(title: "Transcript", systemImage: "text.alignleft") {
                    Button {
                        Task { await reload() }
                    } label: {
                        Label(message + " Tap to retry.", systemImage: "arrow.clockwise")
                            .font(SavaFont.footnote)
                            .foregroundStyle(SavaColors.accent)
                    }.buttonStyle(.plain)
                }
            }
        }
        .task { await model.loadTranscript(service) }
    }

    private func reload() async {
        // Reset by loading again (state guards on .idle, so nudge through).
        await model.loadTranscript(service)
    }

    private func loaded(_ segments: [TranscriptSegment], language: String?) -> some View {
        let preview = segments.prefix(6).map(\.text).joined(separator: " ")
        return DetailSection(title: "Transcript",
                             subtitle: language.map { $0.uppercased() } ?? "Auto-generated",
                             systemImage: "text.alignleft") {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text(preview)
                    .font(SavaFont.callout)
                    .foregroundStyle(SavaColors.textSecondary)
                    .lineLimit(4)
                    .lineSpacing(3)

                Button {
                    Haptics.tap()
                    showReader = true
                } label: {
                    HStack(spacing: 6) {
                        Text("Read full transcript")
                        Text("·").foregroundStyle(SavaColors.textTertiary)
                        Text("\(segments.count) lines").foregroundStyle(SavaColors.textTertiary)
                        Image(systemName: "chevron.right").font(.system(size: 11, weight: .bold))
                    }
                    .font(SavaFont.subheadline)
                    .foregroundStyle(SavaColors.accent)
                }
                .buttonStyle(.pressable)
            }
            .padding(Spacing.md)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                    .fill(SavaColors.surface)
                    .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                        .strokeBorder(SavaColors.hairline, lineWidth: 1))
            )
            .sheet(isPresented: $showReader) {
                TranscriptReaderView(bookmark: model.bookmark, segments: segments)
            }
        }
    }
}

/// Full transcript reader with search and tap-to-open-at-timestamp.
struct TranscriptReaderView: View {
    let bookmark: Bookmark
    let segments: [TranscriptSegment]

    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var query = ""

    private var filtered: [TranscriptSegment] {
        guard !query.trimmingCharacters(in: .whitespaces).isEmpty else { return segments }
        return segments.filter { $0.text.localizedCaseInsensitiveContains(query) }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Spacing.sm) {
                    ForEach(filtered) { segment in
                        Button {
                            open(at: segment.start)
                        } label: {
                            HStack(alignment: .top, spacing: Spacing.sm) {
                                Text(Format.timestamp(segment.start))
                                    .font(SavaFont.mono)
                                    .foregroundStyle(SavaColors.accent)
                                    .frame(width: 46, alignment: .leading)
                                Text(segment.text)
                                    .font(SavaFont.callout)
                                    .foregroundStyle(SavaColors.textPrimary)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(Spacing.md)
            }
            .background(SavaColors.background)
            .navigationTitle("Transcript")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $query, prompt: "Search transcript")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    /// YouTube supports jumping to a timestamp via the `t` query param.
    private func open(at seconds: Double) {
        Haptics.tap()
        guard bookmark.platform == .youtube, var comps = URLComponents(string: bookmark.url) else {
            if let url = URL(string: bookmark.url) { openURL(url) }
            return
        }
        var items = comps.queryItems ?? []
        items.removeAll { $0.name == "t" }
        items.append(URLQueryItem(name: "t", value: "\(Int(seconds))"))
        comps.queryItems = items
        if let url = comps.url { openURL(url) }
    }
}
