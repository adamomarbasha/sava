import Foundation

/// One frame of a streamed Ask.
///
/// The server emits Server-Sent Events; this is the decoded shape of the JSON
/// on each `data:` line. The event name is carried *inside* the payload as well
/// as on the SSE `event:` line, so a single decoder handles every frame and no
/// frame can be misread as another kind.
///
/// ── The important one is `token` ────────────────────────────────────────
///
/// `token.text` is a **delta** — the characters produced since the last frame,
/// never the running total. Appending is correct; assigning would work by
/// accident on the first frame and then show a growing echo of the answer.
enum AskEvent: Decodable {
    /// Arrives first, before any work. Carries the thread so a retry after a
    /// failure continues the same conversation instead of starting a new one.
    case meta(threadID: Int?)
    /// What Sava is answering from. Sent before the first token, because
    /// retrieval takes tens of milliseconds and generation takes seconds.
    case sources(sources: [RelatedSave], groundedIn: Int)
    /// Something worth saying out loud while the user waits — currently only
    /// visual escalation ("Looking through the video…").
    case status(message: String, state: String)
    /// A delta of the answer.
    case token(text: String)
    /// The finished answer, plus everything the non-streaming endpoint returns.
    case done(AskAnswer)
    /// Generation failed. The user's message stays on screen so Retry has
    /// something to retry.
    case failed(message: String, reason: String)

    private enum Keys: String, CodingKey {
        case event, text, message, reason, state, threadID = "thread_id"
        case sources, groundedIn = "grounded_in"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        switch try c.decodeIfPresent(String.self, forKey: .event) ?? "" {
        case "meta":
            self = .meta(threadID: try c.decodeIfPresent(Int.self, forKey: .threadID))
        case "sources":
            self = .sources(
                sources: (try? c.decode([RelatedSave].self, forKey: .sources)) ?? [],
                groundedIn: (try? c.decode(Int.self, forKey: .groundedIn)) ?? 0)
        case "status":
            self = .status(
                message: (try? c.decode(String.self, forKey: .message)) ?? "",
                state: (try? c.decode(String.self, forKey: .state)) ?? "")
        case "token":
            self = .token(text: (try? c.decode(String.self, forKey: .text)) ?? "")
        case "done":
            // The `done` frame is a superset of the non-streaming response, so
            // it decodes with the existing model rather than a parallel one —
            // sources, citations and the visual flags all keep working.
            self = .done(try AskAnswer(from: decoder))
        case "error":
            self = .failed(
                message: (try? c.decode(String.self, forKey: .message))
                    ?? "Sava couldn't finish that answer.",
                reason: (try? c.decode(String.self, forKey: .reason)) ?? "unknown")
        case let other:
            throw DecodingError.dataCorruptedError(
                forKey: .event, in: c, debugDescription: "unknown event \(other)")
        }
    }
}

extension IntelligenceService {

    /// `POST /api/ask/stream` — the library, streamed.
    func askSavaStream(question: String, mode: AskMode = .auto,
                       threadID: Int? = nil,
                       collectionID: Int? = nil) -> AsyncThrowingStream<AskEvent, Error> {
        struct Body: Encodable {
            let question: String
            let mode: String
            let thread_id: Int?
            let collection_id: Int?
        }
        let body = Body(question: question, mode: mode.rawValue,
                        thread_id: threadID, collection_id: collectionID)
        return withFallback(
            Endpoint.json("api/ask/stream", method: .post, body: body),
            legacy: { try await askSava(question: question, mode: mode,
                                        threadID: threadID,
                                        collectionID: collectionID) })
    }

    /// `POST /api/bookmarks/{id}/ask/stream` — one item, streamed.
    func askThisStream(bookmarkID: Int, question: String, mode: AskMode = .auto,
                       threadID: Int? = nil) -> AsyncThrowingStream<AskEvent, Error> {
        struct Body: Encodable {
            let question: String
            let mode: String
            let thread_id: Int?
        }
        let body = Body(question: question, mode: mode.rawValue, thread_id: threadID)
        return withFallback(
            Endpoint.json("api/bookmarks/\(bookmarkID)/ask/stream",
                          method: .post, body: body),
            legacy: { try await askThis(bookmarkID: bookmarkID, question: question,
                                        mode: mode, threadID: threadID) })
    }

    /// Fall back to the non-streaming endpoint when streaming is not there.
    ///
    /// ── Why a client must not assume the server matches it ──────────────
    ///
    /// Observed against production: `POST /api/ask/stream` returned **404**
    /// while `POST /api/ask` returned 403 (auth required, i.e. the route
    /// exists). The backend had not been deployed since streaming landed, so
    /// every Ask on the shipped app hit a 404, which `APIClient` correctly
    /// mapped to `.notFound` — and the user saw "We couldn't find that."
    ///
    /// A client and a server do not deploy in lockstep, and an app already on
    /// a phone cannot wait for one. So a missing stream degrades to the older
    /// contract instead of failing: the answer arrives whole rather than
    /// progressively, which is the honest consequence of talking to a server
    /// that cannot stream.
    ///
    /// Only `.notFound` triggers it. A 401, a 402 or a 500 mean something the
    /// user needs to hear, and retrying those against a second endpoint would
    /// double the work and bury the real error.
    private func withFallback(
        _ endpoint: Endpoint,
        legacy: @escaping () async throws -> AskAnswer
    ) -> AsyncThrowingStream<AskEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                var sawAnything = false
                do {
                    for try await event in events(endpoint) {
                        sawAnything = true
                        continuation.yield(event)
                    }
                    continuation.finish()
                } catch {
                    // Mid-stream failures are real; only a refusal to start
                    // can be a missing route.
                    guard !sawAnything,
                          case APIError.notFound = error else {
                        continuation.finish(throwing: error)
                        return
                    }
                    do {
                        let answer = try await legacy()
                        if let id = answer.threadID {
                            continuation.yield(.meta(threadID: id))
                        }
                        continuation.yield(.sources(sources: answer.sources,
                                                    groundedIn: answer.groundedIn))
                        if let text = answer.answer, !text.isEmpty {
                            continuation.yield(.token(text: text))
                            continuation.yield(.done(answer))
                        } else {
                            continuation.yield(.failed(
                                message: answer.message ?? "Sava couldn't answer that.",
                                reason: "no_answer"))
                        }
                        continuation.finish()
                    } catch {
                        continuation.finish(throwing: error)
                    }
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// Decode each SSE payload into an `AskEvent`.
    ///
    /// An undecodable frame is skipped rather than fatal: a future server may
    /// add an event this build has never heard of, and dropping the answer
    /// half-way through because of an unrecognised progress message would be a
    /// worse failure than ignoring it.
    private func events(_ endpoint: Endpoint) -> AsyncThrowingStream<AskEvent, Error> {
        let raw = client.stream(endpoint)
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let decoder = JSONDecoder()
                    for try await payload in raw {
                        guard let event = try? decoder.decode(AskEvent.self, from: payload)
                        else { continue }
                        continuation.yield(event)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
