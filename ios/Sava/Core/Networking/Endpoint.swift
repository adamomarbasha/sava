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
