import Foundation

/// One daily value, encoded exactly as the backend expects.
struct MetricSample: Encodable {
    let type: String
    let value: Double
    let recordedAt: String

    enum CodingKeys: String, CodingKey {
        case type, value
        case recordedAt = "recorded_at"
    }
}

struct MetricsResponse: Decodable {
    struct Rejection: Decodable {
        let index: Int
        let reason: String
    }

    let inserted: Int
    let updated: Int
    let duplicates: Int
    let rejected: [Rejection]
}

enum APIError: LocalizedError {
    /// 401: the device token is invalid or was revoked on the website.
    case unauthorized
    case server(status: Int)
    case badResponse
    case network(URLError)

    var errorDescription: String? {
        switch self {
        case .unauthorized:
            return "Disconnected, scan the QR code again."
        case .server(let status):
            return "The server had a problem (error \(status)). It will try again later."
        case .badResponse:
            return "The server sent an unexpected answer. It will try again later."
        case .network(let error):
            switch error.code {
            case .notConnectedToInternet, .networkConnectionLost, .dataNotAllowed:
                return "No internet connection. It will try again later."
            case .timedOut:
                return "The server took too long to respond. Try again in a minute."
            case .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed:
                return "Couldn't reach the server. Check your connection and try again."
            case .secureConnectionFailed, .serverCertificateUntrusted, .serverCertificateHasBadDate,
                 .serverCertificateNotYetValid, .serverCertificateHasUnknownRoot:
                return "Couldn't make a secure connection to the server."
            default:
                return "Couldn't reach the server. It will try again later."
            }
        }
    }

    /// Worth one retry: the free-tier server may still be waking up.
    var isRetryable: Bool {
        switch self {
        case .unauthorized, .badResponse:
            return false
        case .server(let status):
            return status >= 500 || status == 429
        case .network(let error):
            return error.code != .cancelled
        }
    }
}

enum APIClient {
    /// The free-tier server sleeps; the first request can take up to ~60 s.
    private static let timeout: TimeInterval = 90

    private static let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = timeout
        config.timeoutIntervalForResource = timeout
        return URLSession(configuration: config)
    }()

    /// POST {api}/api/metrics, retrying once on network errors and 5xx responses.
    static func sendMetrics(_ samples: [MetricSample], to pairing: PairingInfo) async throws -> MetricsResponse {
        var request = URLRequest(url: pairing.apiBase.appending(path: "api/metrics"), timeoutInterval: timeout)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(pairing.token, forHTTPHeaderField: "X-Device-Token")
        request.httpBody = try JSONEncoder().encode(Body(source: "apple_health", samples: samples))

        do {
            return try await send(request)
        } catch let error as APIError where error.isRetryable {
            return try await send(request)
        }
    }

    private struct Body: Encodable {
        let source: String
        let samples: [MetricSample]
    }

    private static func send(_ request: URLRequest) async throws -> MetricsResponse {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let error as URLError {
            throw APIError.network(error)
        }

        guard let http = response as? HTTPURLResponse else { throw APIError.badResponse }
        switch http.statusCode {
        case 200..<300:
            guard let decoded = try? JSONDecoder().decode(MetricsResponse.self, from: data) else {
                throw APIError.badResponse
            }
            return decoded
        case 401:
            throw APIError.unauthorized
        default:
            throw APIError.server(status: http.statusCode)
        }
    }
}
