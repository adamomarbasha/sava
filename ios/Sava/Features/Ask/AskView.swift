import SwiftUI

/// Ask — one conversational surface, three scopes.
///
/// Ask Sava (the whole library), Ask this collection, and Ask this save are the
/// same interaction with a different amount of evidence behind it, so they are
/// one view rather than three near-identical ones. What changes is the scope
/// header, the suggested questions, and which endpoint the question goes to.
///
/// The differentiator is that answers are **visual**. Sava knows this person's
/// media, so when an answer draws on saves they are rendered as real thumbnails
/// above the prose, and each one opens. That is the difference between Sava
/// answering and a chatbot answering: the answer hands you back your own media.
struct AskView: View {
    enum Scope: Equatable {
        case library
        case collection(SavaCollection)
        case save(Bookmark)

        var title: String {
            switch self {
            case .library:           return "Ask Sava"
            case .collection:        return "Ask this collection"
            case .save:              return "Ask this"
            }
        }

        var prompt: String {
            switch self {
            case .library:            return "Ask anything about your library."
            case .collection(let c):  return "Ask about \(c.name)."
            case .save:               return "Answers come from what Sava read in it."
            }
        }

        var placeholder: String {
            switch self {
            case .library:    return "Ask your library…"
            case .collection: return "Ask this collection…"
            case .save:       return "Ask a question…"
            }
        }

        /// Only the library scope is a tab root. The others arrive as sheets.
        var isRoot: Bool { self == .library }

        var offersModePicker: Bool {
            if case .save = self { return false }
            return true
        }

        /// Matches the scope the server files threads under.
        var threadScope: String {
            switch self {
            case .library:    return "library"
            case .collection: return "collection"
            case .save:       return "save"
            }
        }

        /// Threads about one item are listed per item; library and collection
        /// conversations are listed together for their scope.
        var threadBookmarkID: Int? {
            if case .save(let bookmark) = self { return bookmark.id }
            return nil
        }
    }

    var scope: Scope = .library
    /// Supplied by the detail screen so suggestions can be about *this* content
    /// rather than generic prompts.
    var understanding: SaveUnderstanding? = nil

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var question = ""
    @State private var turns: [Turn] = []
    @State private var asking = false
    @State private var mode: AskMode = .auto
    @State private var threadID: Int?
    @State private var errorMessage: String?
    @FocusState private var focused: Bool
    @State private var askTask: Task<Void, Never>?
    /// Held so a cancelled question can be handed back to the field.
    @State private var inFlightQuestion = ""
    /// The last thing asked, so a failed turn can be retried in one tap.
    @State private var lastQuestion = ""
    /// The question already asked and still waiting for its reply. Shown as a
    /// bubble immediately so the conversation never appears to swallow input.
    @State private var pending: String?
    @State private var showHistory = false
    @State private var showPaywall = false
    /// Set when the last failure was "out of Ask messages", so the error line
    /// can offer an upgrade instead of a "Try again" that cannot work.
    @State private var errorNeedsUpgrade = false
    /// Something worth saying while the answer is being written — currently
    /// only visual escalation ("Looking through the video…").
    @State private var statusMessage: String?
    @State private var suggestions: [AskSuggestion] = []
    @State private var loadingSuggestions = false
    /// A stable id for the pending block so the scroll view can follow it.
    private let pendingAnchor = "pending"

    private var intelligence: IntelligenceService {
        IntelligenceService(client: session.api)
    }

    struct Turn: Identifiable, Equatable {
        let id = UUID()
        let question: String
        /// Grows as deltas arrive.
        var answer: String
        var sources: [RelatedSave]
        var citations: [AskAnswer.Citation]
        /// True until its answer has finished arriving. Scrolling back to an
        /// older turn must not replay it.
        var isNew: Bool = false
        /// Tokens are still arriving, so the turn shows a caret rather than the
        /// finished treatment.
        var isStreaming: Bool = false
    }

    // MARK: Body

    var body: some View {
        if scope.isRoot {
            conversation
        } else {
            NavigationStack {
                conversation.savaDestinations()
            }
            .presentationDragIndicator(.visible)
        }
    }

    private var conversation: some View {
        transcript
            // Insets rather than a VStack: the scope header and the composer are
            // chrome around a scrolling transcript, and expressing them as safe
            // areas is what makes the scroll view inset itself correctly, keeps
            // the composer clear of the tab bar, and lets the keyboard push the
            // composer without shoving the whole screen upward.
            .safeAreaInset(edge: .top, spacing: 0) { scopeHeader }
            .safeAreaInset(edge: .bottom, spacing: 0) { composer }
            .background(SavaColor.ground)
        .navigationTitle(scope.title)
        .navigationBarTitleDisplayMode(scope.isRoot ? .large : .inline)
        .toolbar {
            if !scope.isRoot {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Done") { dismiss() }
                }
            }
            ToolbarItem(placement: .topBarTrailing) { overflowMenu }
            if scope.offersModePicker {
                ToolbarItem(placement: .topBarTrailing) { modePicker }
            }
        }
        .tint(SavaColor.primary)
        .sheet(isPresented: $showHistory) {
            ChatHistorySheet(scope: scope.threadScope,
                             bookmarkID: scope.threadBookmarkID) { thread in
                Task { await open(thread) }
            }
            .environmentObject(session)
        }
        .sheet(isPresented: $showPaywall) {
            NavigationStack {
                PaywallView(context: "quota_ask",
                            reason: "You've used all your Ask messages this month.")
            }
        }
        .task {
            await loadSuggestions()
            guard turns.isEmpty, let seed = DevFlags.askQuestion else { return }
            ask(seed)
        }
    }

    /// The thing under discussion, pinned. Without it a scoped Ask degrades into
    /// a generic chat window and the user loses track of what they are asking
    /// about — which is the failure mode this whole feature has to avoid.
    @ViewBuilder private var scopeHeader: some View {
        switch scope {
        case .library:
            EmptyView()

        case .save(let bookmark):
            HStack(spacing: Space.m) {
                MediaImage(url: bookmark.imageURL,
                           fallback: .save(bookmark.platform, title: nil),
                           cornerRadius: 6)
                    .frame(width: 46, height: 46)

                VStack(alignment: .leading, spacing: 2) {
                    Text(bookmark.displayTitle)
                        .font(SavaType.mediaTitle)
                        .foregroundStyle(SavaColor.primary)
                        .lineLimit(1)
                    Text(bookmark.metaLine)
                        .font(SavaType.meta)
                        .foregroundStyle(SavaColor.tertiary)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            .headerSurface()
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Asking about \(bookmark.displayTitle)")

        case .collection(let collection):
            HStack(spacing: Space.m) {
                CollectionCover(name: collection.name,
                                thumbnails: collection.coverURLs)
                    .frame(width: 68)

                VStack(alignment: .leading, spacing: 2) {
                    Text(collection.name)
                        .font(SavaType.mediaTitle)
                        .foregroundStyle(SavaColor.primary)
                        .lineLimit(1)
                    Text(collection.countLabel)
                        .font(SavaType.meta)
                        .foregroundStyle(SavaColor.tertiary)
                }
                Spacer(minLength: 0)
            }
            .headerSurface()
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Asking about \(collection.name), \(collection.countLabel)")
        }
    }

    private var overflowMenu: some View {
        Menu {
            Button {
                Haptics.tap()
                startNewConversation()
            } label: { Label("New conversation", systemImage: "square.and.pencil") }
                .disabled(turns.isEmpty && pending == nil)

            Button {
                Haptics.tap()
                showHistory = true
            } label: { Label("History", systemImage: "clock.arrow.circlepath") }
        } label: {
            Image(systemName: "ellipsis")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(SavaColor.secondary)
        }
        .accessibilityLabel("Conversation options")
    }

    /// Auto / Fast / Advanced. Sava's own vocabulary — no provider or model name
    /// is ever shown, because which model ran is an implementation detail the
    /// product should be free to change.
    private var modePicker: some View {
        Menu {
            Picker("Answer depth", selection: $mode) {
                ForEach(AskMode.allCases) { option in
                    Text("\(option.title) — \(option.subtitle)").tag(option)
                }
            }
        } label: {
            HStack(spacing: 3) {
                Text(mode.title).font(.system(size: 13, weight: .medium))
                Image(systemName: "chevron.down").font(.system(size: 9, weight: .bold))
            }
            .foregroundStyle(SavaColor.secondary)
        }
        .onChange(of: mode) { _, _ in Haptics.select() }
        .accessibilityLabel("Answer depth, \(mode.title). \(mode.subtitle)")
    }

    // MARK: Transcript

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xxl) {
                    if turns.isEmpty && !asking { opening }

                    ForEach(turns) { turn in
                        answerBlock(turn).id(turn.id)
                    }

                    if let pending {
                        VStack(alignment: .leading, spacing: Space.l) {
                            questionBubble(pending)
                            workingLine
                        }
                        .id(pendingAnchor)
                        .transition(.opacity)
                    }
                    // The turn exists but its first token has not landed. Same
                    // affordance as `pending`, so the transition from "asked" to
                    // "answering" does not blink an indicator out and back in.
                    if asking, pending == nil,
                       let last = turns.last, last.isStreaming, last.answer.isEmpty {
                        workingLine
                            .id(pendingAnchor)
                            .transition(.opacity)
                    }
                    if let errorMessage { errorLine(errorMessage) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .animation(Motion.gentle, value: asking)
                .animation(Motion.gentle, value: errorMessage)
                .screenPadding()
                .padding(.vertical, Space.l)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: pending) { _, value in
                guard value != nil else { return }
                withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
                    proxy.scrollTo(pendingAnchor, anchor: .top)
                }
            }
            .onChange(of: turns.count) { _, _ in
                guard let last = turns.last else { return }
                withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
                    proxy.scrollTo(last.id, anchor: .top)
                }
            }
            // Follow the answer as it is written.
            //
            // `turns.count` alone was enough when an answer arrived whole: one
            // append, one scroll. A streamed turn is appended *empty* and then
            // grows for several seconds, so without this the text writes itself
            // off the bottom of the screen while the view sits still.
            //
            // Anchored to the *bottom* rather than the top: the question is
            // already read and the newest words are what matter. Unanimated,
            // because animating a scroll on every token fights the next one and
            // produces a visible stutter.
            .onChange(of: turns.last?.answer) { _, _ in
                guard let last = turns.last, last.isStreaming else { return }
                proxy.scrollTo(last.id, anchor: .bottom)
            }
        }
    }

    /// One exchange: what they asked, then what Sava said.
    ///
    /// The question sits in a restrained bubble on the trailing edge and the
    /// answer runs full width as plain text — the shape every good conversational
    /// interface has settled on, because it lets the answer breathe while still
    /// making it obvious who said what. Deliberately *not* the serif used for
    /// summaries: a summary is something Sava wrote about an item, a reply is
    /// something Sava said to you, and they should not sound the same.
    private func answerBlock(_ turn: Turn) -> some View {
        VStack(alignment: .leading, spacing: Space.l) {
            questionBubble(turn.question)

            // Evidence before prose. Seeing the items an answer rests on is the
            // point of asking Sava rather than asking a search engine, and the
            // numbering makes the superscripts in the prose resolve to media.
            let evidence = Self.evidence(for: turn)
            if !evidence.isEmpty {
                VStack(spacing: 0) {
                    ForEach(evidence, id: \.save.id) { item in
                        NavigationLink(value: item.save) {
                            InlineMediaReference(save: item.save, index: item.number)
                        }
                        .buttonStyle(.pressable)
                        .hairline()
                    }
                }
            }

            // `animates: false` — the answer is now genuinely streamed.
            //
            // `StreamingText` reveals a *finished* string word by word on a
            // timer, which was the right illusion when the whole answer landed
            // at once. It is the wrong thing now: the deltas already arrive
            // progressively, so leaving it on would animate an animation —
            // re-revealing text the user has already watched appear, and
            // holding the last words back behind a timer that has nothing to
            // do with the model. The real tokens are the motion.
            HStack(alignment: .bottom, spacing: 2) {
                StreamingText(text: turn.answer, font: SavaType.body,
                              animates: false) {
                    markSettled(turn.id)
                }
                .foregroundStyle(SavaColor.primary)
                if turn.isStreaming && !turn.answer.isEmpty {
                    StreamingCaret()
                }
            }
        }
    }

    /// What the person said. Appears the instant they send it — waiting for the
    /// reply before showing the question makes the app look like it dropped the
    /// input.
    private func questionBubble(_ text: String) -> some View {
        HStack {
            Spacer(minLength: Space.xxl)
            Text(text)
                .font(SavaType.body)
                .foregroundStyle(SavaColor.primary)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, Space.l)
                .padding(.vertical, Space.m)
                .background(SavaColor.fill,
                            in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("You asked: \(text)")
    }

    /// The items worth showing above an answer.
    ///
    /// Retrieval always hands back its top matches, but an answer usually only
    /// draws on a couple of them — so listing all ten under a reply that says
    /// "you saved one recipe" would contradict the reply. When the answer cites
    /// sources by number, only those are shown, and they keep their original
    /// numbering so the superscripts still resolve. When it cites none, a short
    /// unnumbered head of the list stands in as "what I looked at".
    private static func evidence(for turn: Turn) -> [(number: Int?, save: RelatedSave)] {
        let numbered = turn.sources.enumerated().map { (number: $0.offset + 1, save: $0.element) }
        let cited = citedNumbers(in: turn.answer)
        if cited.isEmpty {
            return numbered.prefix(3).map { (number: nil, save: $0.save) }
        }
        return numbered.filter { cited.contains($0.number) }
    }

    private static func citedNumbers(in answer: String) -> Set<Int> {
        guard let regex = try? NSRegularExpression(pattern: "\\[(\\d{1,2})\\]") else { return [] }
        let range = NSRange(answer.startIndex..., in: answer)
        return Set(regex.matches(in: answer, range: range).compactMap { match in
            Range(match.range(at: 1), in: answer).flatMap { Int(answer[$0]) }
        })
    }

    /// Marks a turn as settled so its answer never streams a second time.
    private func markSettled(_ id: UUID) {
        guard let index = turns.firstIndex(where: { $0.id == id }), turns[index].isNew
        else { return }
        turns[index].isNew = false
    }

    /// Three breathing dots. A spinner beside "Looking through your library…"
    /// narrates the implementation; this just says something is happening, which
    /// is all anyone needs while they wait a second and a half.
    /// The gap between asking and the first token.
    ///
    /// It is short now — retrieval finishes in tens of milliseconds and the
    /// first token follows — but it is not nothing, and a silent gap is what
    /// made Ask feel broken. When the server has something specific to say
    /// (currently only "Looking through the video…") it says it; otherwise the
    /// dots carry it.
    private var workingLine: some View {
        HStack(spacing: Space.s) {
            ThinkingDots()
            if let statusMessage {
                Text(statusMessage)
                    .font(SavaType.meta)
                    .foregroundStyle(SavaColor.tertiary)
                    .transition(.opacity)
            }
        }
        .padding(.top, Space.xs)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(statusMessage
            ?? (scopeIsSave ? "Reading it" : "Looking through your library"))
        .accessibilityAddTraits(.updatesFrequently)
    }

    /// A failed turn, stated quietly with a way forward.
    ///
    /// Most of what lands here is a rate limit or a dropped connection — both
    /// temporary, neither the user's fault. A red block would frame an ordinary
    /// "try that again in a second" as breakage.
    private func errorLine(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Text(message)
                .font(SavaType.callout)
                .foregroundStyle(SavaColor.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if errorNeedsUpgrade {
                // "Try again" would fail identically until the month resets, so
                // the only honest control is the one that actually changes the
                // outcome.
                Button("Upgrade to Sava Pro") { showPaywall = true }
                    .font(SavaType.caption)
                    .foregroundStyle(SavaColor.accent)
            } else if !lastQuestion.isEmpty {
                Button("Try again") { ask(lastQuestion) }
                    .font(SavaType.caption)
                    .foregroundStyle(SavaColor.accent)
            }
        }
        .transition(.opacity)
    }

    // MARK: Opening state

    private var opening: some View {
        AskOpeningView(lede: scope.prompt,
                       suggestions: suggestions,
                       isLoading: loadingSuggestions,
                       onPick: { ask($0) },
                       onShuffle: { Task { await loadSuggestions() } })
    }

    /// Opening questions, fetched per open from the server.
    ///
    /// They are generated from this user's actual saves — creators they return
    /// to, topics that recur, collections they named — so a suggestion is a
    /// question the library can genuinely answer, and a different set is offered
    /// each time. See `api/services/ask_suggestions.py` for how the candidates
    /// are built and why an empty library correctly yields none.
    private func loadSuggestions() async {
        guard turns.isEmpty, pending == nil else { return }
        loadingSuggestions = true
        defer { loadingSuggestions = false }
        do {
            suggestions = try await intelligence.askSuggestions(
                scope: scope.threadScope,
                collectionID: { if case .collection(let c) = scope { return c.id }; return nil }(),
                bookmarkID: scope.threadBookmarkID)
        } catch {
            // Silent by design: suggestions are an optional way in, and an
            // error where an invitation should be is worse than a bare field.
            suggestions = []
        }
    }

    // MARK: Composer

    private var composer: some View {
        HStack(alignment: .bottom, spacing: Space.s) {
            TextField(scope.placeholder, text: $question, axis: .vertical)
                .font(SavaType.body)
                .foregroundStyle(SavaColor.primary)
                .tint(SavaColor.accent)
                .lineLimit(1...6)
                .focused($focused)
                .submitLabel(.send)
                .onSubmit { ask(question) }
                .padding(.horizontal, Space.l)
                .padding(.vertical, 12)
                .frame(minHeight: 44)
                .background(SavaColor.fill,
                            in: RoundedRectangle(cornerRadius: 22, style: .continuous))

            sendButton
        }
        .screenPadding()
        .padding(.top, Space.s)
        .padding(.bottom, Space.m)
        .background(alignment: .top) {
            Rectangle().fill(SavaColor.hairline).frame(height: 0.5)
        }
        .background(.bar)
        .animation(Motion.tap, value: asking)
    }

    /// One control with two jobs. While an answer is coming it becomes a stop
    /// button, because the useful thing to offer someone who has changed their
    /// mind mid-answer is a way out, not a disabled arrow.
    private var sendButton: some View {
        Button {
            if asking { cancel() } else { ask(question) }
        } label: {
            Image(systemName: asking ? "stop.fill" : "arrow.up")
                .font(.system(size: asking ? 13 : 15, weight: .bold))
                .foregroundStyle(SavaColor.ground)
                .frame(width: 40, height: 40)
                .background(asking || canAsk ? SavaColor.primary : SavaColor.tertiary,
                            in: Circle())
                .contentTransition(.symbolEffect(.replace))
        }
        .buttonStyle(.pressable)
        .disabled(!asking && !canAsk)
        .animation(Motion.gentle, value: canAsk)
        .accessibilityLabel(asking ? "Stop" : "Send")
    }

    private var canAsk: Bool {
        !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !asking
    }

    private var scopeIsSave: Bool {
        if case .save = scope { return true }
        return false
    }

    // MARK: Asking

    private func ask(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !asking else { return }
        focused = false
        asking = true
        errorMessage = nil
        errorNeedsUpgrade = false
        statusMessage = nil
        question = ""
        inFlightQuestion = trimmed
        lastQuestion = trimmed
        withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
            pending = trimmed
        }
        Haptics.tap()

        askTask = Task { await stream(trimmed) }
    }

    /// Consume the answer as it is generated.
    ///
    /// ── The shape this is trying to produce ─────────────────────────────
    ///
    ///   * the question is already on screen (`pending`, set by `ask`)
    ///   * `sources` arrives in tens of milliseconds and opens a live turn, so
    ///     the reply block appears long before the sentence does
    ///   * every `token` is appended to that turn
    ///   * `done` closes it
    ///
    /// The turn is opened on the *first* event that proves work is happening
    /// rather than on the first token, because retrieval finishing is itself
    /// worth showing — it is the difference between "nothing is happening" and
    /// "it is reading these four things".
    @MainActor
    private func stream(_ trimmed: String) async {
        var live: UUID?
        var produced = false

        func openTurn(sources: [RelatedSave]) {
            guard live == nil else { return }
            let turn = Turn(question: trimmed, answer: "", sources: sources,
                            citations: [], isNew: true, isStreaming: true)
            live = turn.id
            withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
                pending = nil
                turns.append(turn)
            }
        }

        do {
            for try await event in events(trimmed) {
                if Task.isCancelled { break }
                switch event {
                case .meta(let id):
                    // Captured before anything can fail, so a retry after an
                    // error continues this conversation rather than orphaning it.
                    if let id { threadID = id }

                case .sources(let sources, _):
                    openTurn(sources: sources)

                case .status(let message, _):
                    withAnimation(Motion.gentle) { statusMessage = message }

                case .token(let delta):
                    guard !delta.isEmpty else { continue }
                    openTurn(sources: [])
                    produced = true
                    statusMessage = nil
                    // Appended, never assigned: the server sends deltas.
                    if let live, let i = turns.firstIndex(where: { $0.id == live }) {
                        turns[i].answer += delta
                    }

                case .done(let answer):
                    produced = produced || !(answer.answer ?? "").isEmpty
                    if let live, let i = turns.firstIndex(where: { $0.id == live }) {
                        // Trust the final text over the accumulation: the server
                        // cleans it up, and any frame dropped by a flaky
                        // connection is repaired here rather than left as a gap.
                        if let final = answer.answer, !final.isEmpty {
                            turns[i].answer = final
                        }
                        turns[i].sources = answer.sources
                        turns[i].citations = answer.citations
                        turns[i].isStreaming = false
                    }
                    if let id = answer.threadID { threadID = id }

                case .failed(let message, _):
                    throw AskStreamFailure(message: message)
                }
            }

            asking = false
            statusMessage = nil
            if produced {
                Haptics.success()
            } else {
                // Nothing was generated at all. Be honest rather than leaving an
                // empty bubble that looks like the answer.
                discard(live)
                pending = nil
                errorMessage = fallbackMessage
                Haptics.error()
            }
        } catch is CancellationError {
            finishCancelled(live, produced: produced)
        } catch {
            guard !Task.isCancelled else {
                finishCancelled(live, produced: produced)
                return
            }
            asking = false
            statusMessage = nil
            // A partial answer is kept. It is real output the user already read,
            // and deleting it on failure is more disorienting than leaving it
            // with an error underneath.
            if !produced {
                discard(live)
                pending = nil
            } else if let live, let i = turns.firstIndex(where: { $0.id == live }) {
                turns[i].isStreaming = false
            }
            let api = error as? APIError
            errorNeedsUpgrade = api?.needsUpgrade ?? false
            errorMessage = (error as? AskStreamFailure)?.message
                ?? api?.userMessage
                ?? "Couldn't get an answer. Try again."
            // No error haptic for a quota: it is not a fault, and buzzing at
            // somebody for reaching a documented limit reads as blame.
            if errorNeedsUpgrade { Haptics.tap() } else { Haptics.error() }
        }
    }

    /// Cancelled mid-answer. Whatever arrived is kept — the user watched it
    /// appear, so removing it would look like a bug rather than a cancellation.
    @MainActor
    private func finishCancelled(_ live: UUID?, produced: Bool) {
        asking = false
        statusMessage = nil
        pending = nil
        if produced, let live, let i = turns.firstIndex(where: { $0.id == live }) {
            turns[i].isStreaming = false
        } else {
            discard(live)
        }
    }

    @MainActor
    private func discard(_ live: UUID?) {
        guard let live else { return }
        turns.removeAll { $0.id == live }
    }

    /// One error type, so a server-sent `error` frame and a transport failure
    /// land in the same place.
    private struct AskStreamFailure: Error { let message: String }

    /// Stop waiting for an answer and give the question back, so it can be
    /// edited rather than retyped.
    private func cancel() {
        askTask?.cancel()
        askTask = nil
        asking = false
        withAnimation(Motion.gentle) { pending = nil }
        if question.isEmpty { question = inFlightQuestion }
        inFlightQuestion = ""
        Haptics.tap()
    }

    /// Clears the board and forgets the thread, so the next question starts a
    /// genuinely new conversation rather than continuing the old one invisibly.
    private func startNewConversation() {
        askTask?.cancel()
        askTask = nil
        withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
            turns = []
            pending = nil
            errorMessage = nil
        errorNeedsUpgrade = false
        }
        asking = false
        threadID = nil
        question = ""
    }

    /// Rebuild a stored conversation. Nothing streams — this is history being
    /// reopened, not something being written now.
    private func open(_ thread: ChatThreadSummary) async {
        guard let messages = try? await intelligence.messages(threadID: thread.id)
        else { return }

        var restored: [Turn] = []
        var question: String?
        for message in messages {
            if message.isUser {
                question = message.content
            } else {
                restored.append(Turn(question: question ?? "",
                                     answer: message.content,
                                     sources: message.sources,
                                     citations: message.citations,
                                     isNew: false))
                question = nil
            }
        }

        withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
            turns = restored
            pending = nil
            errorMessage = nil
        errorNeedsUpgrade = false
        }
        threadID = thread.id
        Haptics.success()
    }

    /// The right stream for this scope. Library, collection and single-item Ask
    /// all use the same architecture — one event protocol, one renderer.
    private func events(_ text: String) -> AsyncThrowingStream<AskEvent, Error> {
        switch scope {
        case .library:
            return intelligence.askSavaStream(question: text, mode: mode,
                                              threadID: threadID)
        case .collection(let collection):
            return intelligence.askSavaStream(question: text, mode: mode,
                                              threadID: threadID,
                                              collectionID: collection.id)
        case .save(let bookmark):
            return intelligence.askThisStream(bookmarkID: bookmark.id, question: text,
                                              mode: mode, threadID: threadID)
        }
    }

    private var fallbackMessage: String {
        switch scope {
        case .library:    return "Nothing in your library covers that yet."
        case .collection: return "Nothing in this collection covers that yet."
        case .save:       return "Sava hasn't finished reading this save yet."
        }
    }
}

/// The pinned media header's surface.
///
/// It sits in a `safeAreaInset`, and a safe-area inset does not clip what
/// scrolls beneath it — the transcript passes *under* the header rather than
/// stopping at it. Without an opaque fill the conversation was legible straight
/// through the thumbnail and title, which read as a rendering fault.
///
/// Deliberately a solid page fill rather than a material: a material would blur
/// the passing text rather than hide it, and moving grey shapes behind a title
/// look just as broken as readable ones. The hairline below carries the
/// separation that the translucency was implying.
private extension View {
    /// The pinned context strip at the top of a scoped Ask.
    ///
    /// Messages scroll *underneath* this, so it has to be a solid surface. It
    /// was not: the background covered the strip's own bounds but stopped at
    /// the safe-area boundary, leaving the band between it and the top of the
    /// screen unpainted — so message text slid up into that band and appeared
    /// to pass through the header.
    ///
    /// Extending the same opaque colour past the top edge closes it. No
    /// material and no blur: this sits over arbitrary chat text, and legibility
    /// of the title beats any translucency effect.
    func headerSurface() -> some View {
        self
            .screenPadding()
            .padding(.vertical, Space.m)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                SavaColor.ground
                    .ignoresSafeArea(edges: .top)
            }
            .hairline()
            // Above the scroll content in the same stacking context, so nothing
            // can composite on top of it.
            .zIndex(10)
    }
}
