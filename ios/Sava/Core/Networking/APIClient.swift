import Foundation

/// Supplies the current bearer token (if any) and reacts to auth failures.
/// The session store conforms to this so the client stays decoupled from it.
protocol AuthTokenProviding: AnyObject {
    var currentToken: String? { get }
    func handleUnauthorized() async
}

/// The single entry point for talking to the Sava backend. Handles URL
/// assembly, auth injection, decoding, cancellation, and error normalization.
///
/// Raw `URLSession` calls never appear in views or view models — everything
/// flows through here via typed service methods.
final class APIClient {
    private let baseURL: URL
    private let session: URLSession
    weak var tokenProvider: AuthTokenProviding?

    private lazy var decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            // Dates are non-critical for the client; never fail a whole payload
            // because a timestamp is in an unexpected shape. Naive datetimes
            // (no timezone, common with SQLite) fall back to a plain formatter.
            return SavaDateParsing.parse(raw) ?? .distantPast
        }
        return d
    }()

    init(baseURL: URL = AppConfig.apiBaseURL) {
        self.baseURL = baseURL
        let config = URLSessionConfiguration.default
        // Tuned for a remote backend rather than a Mac on the same desk.
        //
        // 20s with `waitsForConnectivity = false` is fine over LAN and wrong
        // over the internet: a managed host that has scaled to zero can take
        // 20-40s to answer its first request, and a phone moving between cell
        // and Wi-Fi is briefly offline rather than permanently so. Both showed
        // up as "login times out" with no way for the user to tell a cold start
        // apart from a real outage.
        config.timeoutIntervalForRequest = 45
        // Queue the request through a short connectivity gap instead of failing
        // it. Bounded by the resource timeout so nothing hangs indefinitely.
        config.waitsForConnectivity = true
        config.timeoutIntervalForResource = 90
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        self.session = URLSession(configuration: config)
    }

    /// Perform a request and decode the JSON response into `T`.
    func send<T: Decodable>(_ endpoint: Endpoint, as type: T.Type = T.self) async throws -> T {
        let data = try await perform(endpoint)
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decoding(underlying: error)
        }
    }

    /// Perform a request that returns no meaningful body.
    @discardableResult
    func send(_ endpoint: Endpoint) async throws -> Data {
        try await perform(endpoint)
    }

    // MARK: - Core

    private func perform(_ endpoint: Endpoint) async throws -> Data {
        let request = try buildRequest(endpoint)

        #if DEBUG
        let hasAuth = request.value(forHTTPHeaderField: "Authorization") != nil
        NSLog("[Sava http] -> %@ %@ auth=%@ body=%dB",
              endpoint.method.rawValue,
              request.url?.absoluteString ?? endpoint.path,
              hasAuth ? "yes" : "NO",
              request.httpBody?.count ?? 0)
        #endif

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let urlError as URLError {
            #if DEBUG
            NSLog("[Sava http] <- %@ TRANSPORT FAILURE %@ (code %d)",
                  endpoint.path, urlError.localizedDescription, urlError.code.rawValue)
            #endif
            switch urlError.code {
            case .notConnectedToInternet, .dataNotAllowed:
                throw APIError.offline
            case .timedOut:
                throw APIError.timedOut
            case .cancelled:
                throw CancellationError()
            default:
                throw APIError.transport(underlying: urlError)
            }
        }

        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }

        #if DEBUG
        NSLog("[Sava http] <- %@ HTTP %d  %dB  %@",
              endpoint.path, http.statusCode, data.count,
              String(data: data.prefix(300), encoding: .utf8) ?? "<binary>")
        #endif

        switch http.statusCode {
        case 200...299:
            return data
        case 401:
            await tokenProvider?.handleUnauthorized()
            throw APIError.unauthorized
        case 403:
            // FastAPI's HTTPBearer returns 403 "Not authenticated" when the
            // Authorization header is missing entirely. Reporting that as a
            // server fault ("Sava is having a moment") hid a real auth bug.
            throw APIError.unauthorized
        case 404:
            throw APIError.notFound(Self.detail(from: data))
        case 409:
            throw APIError.conflict(Self.detail(from: data))
        case 402:
            throw APIError.upgradeRequired(Self.detail(from: data),
                                           capability: Self.capability(from: data))
        case 400, 422:
            throw APIError.badRequest(Self.detail(from: data))
        default:
            throw APIError.server(status: http.statusCode, detail: Self.detail(from: data))
        }
    }

    // MARK: Streaming

    /// Consume a Server-Sent Events response as an async sequence of frames.
    ///
    /// ── Why `bytes(for:)` ───────────────────────────────────────────────
    ///
    /// `URLSession.data(for:)` buffers the entire body before returning, which
    /// is exactly the behaviour streaming exists to avoid — it would deliver
    /// every token at once, at the end, and be indistinguishable from the
    /// synchronous endpoint it replaces. `bytes(for:)` hands back an
    /// `AsyncSequence` of lines as they arrive off the socket.
    ///
    /// Cancellation is inherited: cancelling the enclosing `Task` tears down
    /// the URLSession task, which closes the connection, which lands on the
    /// server as a `GeneratorExit` and stops generation. Nothing needs to be
    /// sent to say "stop".
    ///
    /// Only `data:` lines are parsed. SSE also carries `event:` and `id:`
    /// lines, but the event name is duplicated inside the JSON payload, so one
    /// parser handles both and a frame is never split across two shapes.
    func stream(_ endpoint: Endpoint) -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var request = try buildRequest(endpoint)
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    // A generated answer legitimately outlives an ordinary JSON
                    // call, and the timeout here is per *byte received* rather
                    // than for the whole body, so a streaming response resets it
                    // with every token.
                    request.timeoutInterval = endpoint.timeout ?? 120

                    let (bytes, response) = try await session.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        throw APIError.invalidResponse
                    }
                    guard (200..<300).contains(http.statusCode) else {
                        // The error body is small and not a stream, so it can be
                        // collected and reported through the normal path.
                        var body = Data()
                        for try await byte in bytes { body.append(byte) }
                        throw Self.error(status: http.statusCode, data: body)
                    }

                    for try await line in bytes.lines {
                        guard !Task.isCancelled else { break }
                        guard line.hasPrefix("data:") else { continue }
                        let payload = line.dropFirst("data:".count)
                            .trimmingCharacters(in: .whitespaces)
                        guard !payload.isEmpty else { continue }
                        continuation.yield(Data(payload.utf8))
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func buildRequest(_ endpoint: Endpoint) throws -> URLRequest {
        guard var components = URLComponents(
            url: baseURL.appendingPathComponent(endpoint.path),
            resolvingAgainstBaseURL: false
        ) else {
            throw APIError.invalidResponse
        }
        if !endpoint.query.isEmpty {
            components.queryItems = endpoint.query
        }
        guard let url = components.url else { throw APIError.invalidResponse }

        var request = URLRequest(url: url)
        request.httpMethod = endpoint.method.rawValue
        if let timeout = endpoint.timeout {
            request.timeoutInterval = timeout
        }
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        if let body = endpoint.body {
            request.httpBody = body
            request.setValue(endpoint.contentTypeOverride ?? "application/json",
                             forHTTPHeaderField: "Content-Type")
        }

        if endpoint.requiresAuth, let token = tokenProvider?.currentToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        #if DEBUG
        if endpoint.requiresAuth, request.value(forHTTPHeaderField: "Authorization") == nil {
            // Either there is genuinely no token, or the weakly-held provider
            // was released. The second case is a bug, so name it explicitly.
            let hasToken = KeychainStore().read(.authToken)?.isEmpty == false
            NSLog("[Sava http] WARNING sending %@ unauthenticated — provider=%@ keychainToken=%@",
                  endpoint.path,
                  tokenProvider == nil ? "RELEASED" : "present",
                  hasToken ? "present" : "absent")
        }
        #endif

        return request
    }

    /// The `APIError` for a non-2xx response.
    ///
    /// Shared with `send`, so a 402 on the streaming endpoint produces the same
    /// upgrade prompt as a 402 anywhere else rather than a generic failure.
    /// `handleUnauthorized` is not called here: the streaming path is always
    /// user-initiated and a sign-out mid-answer is handled by the next ordinary
    /// request, whereas tearing the session down from inside a stream races the
    /// view that is rendering it.
    static func error(status: Int, data: Data) -> APIError {
        switch status {
        case 401, 403: return .unauthorized
        case 404:      return .notFound(detail(from: data))
        case 409:      return .conflict(detail(from: data))
        case 402:      return .upgradeRequired(detail(from: data),
                                               capability: capability(from: data))
        case 400, 422: return .badRequest(detail(from: data))
        default:       return .server(status: status, detail: detail(from: data))
        }
    }

    /// FastAPI encodes errors as `{"detail": ...}`. The value may be a string or,
    /// for validation errors, a list of objects — extract a readable message.
    private static func detail(from data: Data) -> String? {
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let dict = object as? [String: Any],
            let detail = dict["detail"]
        else { return nil }

        if let string = detail as? String { return string }
        if let array = detail as? [[String: Any]],
           let first = array.first,
           let msg = first["msg"] as? String {
            return msg
        }
        // A structured detail — what `entitlements.UpgradeRequired` sends, so
        // the response can carry the capability alongside the message. Without
        // this branch a 402 arrives with no text at all and the paywall opens
        // with a blank reason.
        if let dict = detail as? [String: Any],
           let message = dict["message"] as? String {
            return message
        }
        return nil
    }

    /// The `capability` field of a structured error detail, when present.
    private static func capability(from data: Data) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let dict = object as? [String: Any],
              let detail = dict["detail"] as? [String: Any]
        else { return nil }
        return detail["capability"] as? String
    }
}

/// Tolerant date parsing for the assorted timestamp shapes the backend can emit
/// (ISO8601 with/without fractional seconds and timezone, plus naive datetimes).
enum SavaDateParsing {
    private static let iso8601Fractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let iso8601Plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static let naiveFormatters: [DateFormatter] = {
        [
            "yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss.SSS", "yyyy-MM-dd'T'HH:mm:ss",
            "yyyy-MM-dd HH:mm:ss.SSSSSS", "yyyy-MM-dd HH:mm:ss.SSS", "yyyy-MM-dd HH:mm:ss"
        ]
            .map { format in
                let f = DateFormatter()
                f.locale = Locale(identifier: "en_US_POSIX")
                f.timeZone = TimeZone(identifier: "UTC")
                f.dateFormat = format
                return f
            }
    }()

    static func parse(_ raw: String) -> Date? {
        if let d = iso8601Fractional.date(from: raw) { return d }
        if let d = iso8601Plain.date(from: raw) { return d }
        for f in naiveFormatters {
            if let d = f.date(from: raw) { return d }
        }
        return nil
    }
}
