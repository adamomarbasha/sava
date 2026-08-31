import SwiftUI

/// A single saved item.
///
/// Reading order: the media, who made it, what it says, what Sava understood,
/// what it extracted, then the ability to ask. The AI understanding is set as
/// editorial body copy — a serif paragraph under a plain "Summary" heading —
/// because the fact that a model wrote it is not the interesting part. What it
/// says is. There is no sparkle, no gradient card, and no badge announcing that
/// intelligence happened.
///
/// The hierarchy varies with content type: a recipe leads with its ingredients
/// and method, a trip with its places, a review with its verdict. That ordering
/// lives in `SaveUnderstanding.sections`, so this view stays a renderer.
struct SaveDetailView: View {
    let bookmark: Bookmark

    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var shortForm: ShortFormContext
    @Environment(\.openURL) private var openURL
    @State private var understanding: SaveUnderstanding?
    @State private var loadingSummary = true
    @State private var showAsk = false
    @State private var showTranscript = false
    @State private var hasTranscript = false
    @State private var retrying = false
    /// Set once the server has accepted the reprocess — not when it was asked.
    @State private var queued = false
    @State private var retryError: String?
    @State private var showPaywall = false
    @State private var summaryRevealed = false
    /// True only when this summary was generated on this request and has never
    /// been watched appearing before.
    @State private var revealsSummary = false
    @State private var showAddToCollection = false

    private var intelligence: IntelligenceService {
        IntelligenceService(client: session.api)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                // The media is the control. Tapping a thumbnail to watch the
                // thing it is a thumbnail of needs no separate button, and a
                // small corner glyph is enough to say that it will.
                MediaHero(bookmark: bookmark)
                    .overlay(alignment: .bottomTrailing) {
                        if bookmark.isShortForm {
                            PlayAffordance { shortForm.open(bookmark) }
                                .padding(Space.m)
                        }
                    }
                    .contentShape(Rectangle())
                    .onTapGesture {
                        guard bookmark.isShortForm else { return }
                        shortForm.open(bookmark)
                    }

                VStack(alignment: .leading, spacing: Space.xl) {
                    header
                    summarySection
                    structuredSections
                    actions
                }
                .screenPadding()
                .padding(.top, Space.l)
            }
            .padding(.bottom, Space.xxl)
        }
        .devScrollAnchor()
        .background(SavaColor.ground)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    if hasTranscript {
                        Button {
                            Haptics.tap()
                            showTranscript = true
                        } label: {
                            Label("View transcript", systemImage: "text.alignleft")
                        }
                    }
                    Button {
                        Haptics.tap()
                        showAddToCollection = true
                    } label: {
                        Label("Add to collection", systemImage: "rectangle.stack.badge.plus")
                    }
                    Button {
                        guard let url = URL(string: bookmark.url) else { return }
                        openURL(url)
                    } label: {
                        Label("Open in \(bookmark.platform.displayName)",
                              systemImage: "arrow.up.forward.app")
                    }
                    if let url = URL(string: bookmark.url) {
                        ShareLink(item: url) { Label("Share", systemImage: "square.and.arrow.up") }
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 15, weight: .semibold))
                }
                .accessibilityLabel("More actions")
            }
        }
        .task { await load() }
        .sheet(isPresented: $showAsk) {
            AskView(scope: .save(bookmark), understanding: understanding)
                .environmentObject(session)
        }
        .sheet(isPresented: $showTranscript) {
            TranscriptView(bookmark: bookmark)
                .environmentObject(session)
        }
        .sheet(isPresented: $showAddToCollection) {
            AddToCollectionSheet(bookmark: bookmark)
                .environmentObject(session)
        }
        .sheet(isPresented: $showPaywall) {
            NavigationStack {
                PaywallView(context: "quota_processing",
                            reason: "You've used all your AI units this month.")
            }
        }
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Text(bookmark.attributionLine)
                .font(SavaType.meta)
                .foregroundStyle(SavaColor.tertiary)
                .lineLimit(2)

            Text(bookmark.displayTitle)
                .font(SavaType.title)
                .foregroundStyle(SavaColor.primary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: Summary — editorial, not a robot card

    @ViewBuilder private var summarySection: some View {
        if loadingSummary {
            VStack(alignment: .leading, spacing: Space.m) {
                SectionHeader(text: "Summary")
                Skeleton(cornerRadius: 4).frame(height: 14)
                Skeleton(cornerRadius: 4).frame(height: 14)
                Skeleton(cornerRadius: 4).frame(width: 200, height: 14)
            }
        } else if let understanding, understanding.hasContent {
            VStack(alignment: .leading, spacing: Space.m) {
                SectionHeader(text: "Summary")

                StreamingText(text: understanding.tlDr ?? "",
                              font: SavaType.prose,
                              animates: revealsSummary) {
                    if let id = bookmark.canonicalID { SummaryRevealLog.markSeen(id) }
                    withAnimation(Motion.gentle) { summaryRevealed = true }
                }
                .foregroundStyle(SavaColor.primary)

                // Key points wait for the summary to finish arriving. Two blocks
                // of text growing at once reads as a page loading badly.
                if !understanding.displayKeyPoints.isEmpty && (summaryRevealed || !revealsSummary) {
                    VStack(alignment: .leading, spacing: Space.m) {
                        SectionHeader(text: "Key points")
                        VStack(alignment: .leading, spacing: Space.m) {
                            ForEach(Array(understanding.displayKeyPoints.enumerated()),
                                    id: \.element) { position, point in
                                BulletLine(text: point, index: position)
                            }
                        }
                    }
                    .padding(.top, Space.s)
                    .transition(.opacity)
                }
            }
        } else {
            summaryUnavailable
        }
    }

    /// Being honest here matters more than filling the space. A save Sava has
    /// not read yet says so, in one line, and the screen moves on. A save that
    /// genuinely failed gets the one thing that can help: a way to try again.
    @ViewBuilder private var summaryUnavailable: some View {
        if bookmark.isProcessing {
            noticeLine("Sava is still reading this one.")
        } else if bookmark.processingState == .limitReached {
            // Not an error state, and it deliberately does not read like one.
            // The save is fine; the allowance ran out. The remedy is an
            // upgrade, so that is what is offered — not "Try again", which
            // would fail identically every time until the month turned over.
            VStack(alignment: .leading, spacing: Space.m) {
                noticeLine("You've used this month's AI processing. This save is "
                           + "safe in your library — Sava will read it when your "
                           + "units reset, or as soon as you upgrade.")
                Button("Upgrade to Sava Pro") { showPaywall = true }
                    .font(SavaType.button)
                    .foregroundStyle(SavaColor.accent)
            }
        } else if bookmark.processingState == .failed {
            VStack(alignment: .leading, spacing: Space.m) {
                noticeLine(queued ? "Sava will read this again shortly."
                                  : "Sava couldn't read this one.")
                if let retryError {
                    Text(retryError)
                        .font(SavaType.meta)
                        .foregroundStyle(SavaColor.danger)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Button(queued ? "Queued" : (retrying ? "Starting…" : "Try again")) {
                    retryProcessing()
                }
                    .font(SavaType.button)
                    .foregroundStyle(SavaColor.accent)
                    .disabled(retrying || queued)
            }
        } else if let message = understanding?.message {
            noticeLine(message)
        } else if understanding?.available == false {
            noticeLine("Sava hasn't read this one yet.")
        }
    }

    /// Ask the server to read this save again.
    ///
    /// Two faults here, both invisible. `retrying` was set and never cleared,
    /// so a single tap disabled the button permanently — including when the
    /// request had failed and retrying was exactly what the user needed. And
    /// the error was swallowed by `try?`, so a refused reprocess looked
    /// identical to an accepted one: the label changed to "Queued" and nothing
    /// ever happened.
    ///
    /// "Queued" is now the truth rather than a guess: it is shown only after
    /// the server has accepted the job.
    private func retryProcessing() {
        guard !retrying else { return }
        retrying = true
        Haptics.tap()
        Task {
            do {
                try await intelligence.reprocess(bookmarkID: bookmark.id, force: true)
                queued = true
                Haptics.success()
            } catch {
                retryError = (error as? APIError)?.userMessage
                    ?? "Couldn't start reading this again."
                Haptics.error()
            }
            retrying = false
        }
    }

    private func noticeLine(_ text: String) -> some View {
        VStack(alignment: .leading, spacing: Space.s) {
            SectionHeader(text: "Summary")
            Text(text)
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Typed extraction, rendered as content — never as JSON

    @ViewBuilder private var structuredSections: some View {
        if let understanding {
            let sections = understanding.sections
            if !sections.isEmpty {
                VStack(alignment: .leading, spacing: Space.xl) {
                    ForEach(sections) { StructuredSectionView(section: $0) }
                }
            }
        }
    }

    /// The one action that belongs in the reading flow. Everything else —
    /// transcript, collection, open, share — lives in the toolbar menu, where a
    /// secondary action belongs.
    private var actions: some View {
        SavaInlineAction(title: "Ask this", symbol: "text.bubble") {
            showAsk = true
        }
    }

    private func load() async {
        // Both reads are cached server-side, so they run together and neither
        // holds up the other's section.
        async let summary = try? intelligence.summary(bookmarkID: bookmark.id)
        async let transcript = try? intelligence.transcript(bookmarkID: bookmark.id)
        let (loadedSummary, loadedTranscript) = await (summary, transcript)

        if let loadedSummary, loadedSummary.hasContent {
            let isFreshlyGenerated = loadedSummary.cached == false
            let alreadyWatched = bookmark.canonicalID.map(SummaryRevealLog.hasSeen) ?? true
            revealsSummary = DevFlags.forceStreamReveal
                || (isFreshlyGenerated && !alreadyWatched)
        }
        understanding = loadedSummary
        hasTranscript = loadedTranscript?.segments.isEmpty == false
        withAnimation(Motion.gentle) { loadingSummary = false }
    }
}
