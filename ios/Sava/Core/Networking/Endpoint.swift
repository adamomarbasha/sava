import Foundation

enum HTTPMethod: String {
    case get = "GET"
    case post = "POST"
    case put = "PUT"
    case delete = "DELETE"
}

/// Describes a single request against the Sava API. Views never build URLs;
/// they call typed service methods that construct `Endpoint`s.
struct Endpoint {
    var path: String
    var method: HTTPMethod = .get
    var query: [URLQueryItem] = []
    var body: Data? = nil
    var requiresAuth: Bool = true

    /// Multipart upload — used by screenshot resolution, which posts raw image
    /// bytes rather than JSON.
    static func multipart(
        _ path: String,
        fileField: String,
        fileName: String,
        mimeType: String,
        fileData: Data,
        query: [URLQueryItem] = [],
        requiresAuth: Bool = true
    ) -> (endpoint: Endpoint, contentType: String) {
        let boundary = "SavaBoundary-\(UUID().uuidString)"
        var body = Data()
        func append(_ string: String) { body.append(Data(string.utf8)) }

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"\(fileField)\"; filename=\"\(fileName)\"\r\n")
        append("Content-Type: \(mimeType)\r\n\r\n")
        body.append(fileData)
        append("\r\n--\(boundary)--\r\n")

        let endpoint = Endpoint(path: path, method: .post, query: query,
                                body: body, requiresAuth: requiresAuth)
        return (endpoint, "multipart/form-data; boundary=\(boundary)")
    }

    /// Overrides the default `application/json` Content-Type.
    var contentTypeOverride: String? = nil

    /// Per-request timeout. Screenshot resolution runs a vision model and a
    /// search server-side, so it legitimately needs longer than the 20s
    /// default that suits ordinary JSON calls.
    var timeout: TimeInterval? = nil

    static func json<Body: Encodable>(
        _ path: String,
        method: HTTPMethod,
        body: Body,
        requiresAuth: Bool = true
    ) -> Endpoint {
        let data = try? JSONEncoder().encode(body)
        return Endpoint(path: path, method: method, body: data, requiresAuth: requiresAuth)
    }
}
