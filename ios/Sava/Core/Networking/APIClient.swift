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
        config.timeoutIntervalForRequest = 20
        config.waitsForConnectivity = false
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

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let urlError as URLError {
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

        switch http.statusCode {
        case 200...299:
            return data
        case 401:
            await tokenProvider?.handleUnauthorized()
            throw APIError.unauthorized
        case 404:
            throw APIError.notFound(Self.detail(from: data))
        case 409:
            throw APIError.conflict(Self.detail(from: data))
        case 400, 422:
            throw APIError.badRequest(Self.detail(from: data))
        default:
            throw APIError.server(status: http.statusCode, detail: Self.detail(from: data))
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
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        if let body = endpoint.body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        if endpoint.requiresAuth, let token = tokenProvider?.currentToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        return request
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
        return nil
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
