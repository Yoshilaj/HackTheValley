import Foundation
import Security

/// The API base URL and device token received from the website's QR code.
struct PairingInfo {
    let apiBase: URL
    let token: String
}

enum PairingError: LocalizedError {
    case notAPairingLink
    case missingDetails
    case insecureServer

    var errorDescription: String? {
        switch self {
        case .notAPairingLink:
            return "This isn't a pairing code. Scan the QR code shown on the website."
        case .missingDetails:
            return "This pairing code is incomplete. Create a new one on the website and scan it again."
        case .insecureServer:
            return "This pairing code points to a server that isn't secure, so it can't be used."
        }
    }
}

enum Pairing {
    private static let apiBaseKey = "pairing.apiBase"

    /// Parses `pdcompanion://pair?api=<url-encoded base URL>&token=<device token>`.
    static func parse(_ url: URL) throws -> PairingInfo {
        guard url.scheme?.lowercased() == "pdcompanion",
              url.host()?.lowercased() == "pair",
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        else { throw PairingError.notAPairingLink }

        // URLComponents already percent-decodes query item values.
        let items = components.queryItems ?? []
        let apiString = items.first { $0.name == "api" }?.value?.trimmingCharacters(in: .whitespaces) ?? ""
        let token = items.first { $0.name == "token" }?.value?.trimmingCharacters(in: .whitespaces) ?? ""

        // The token goes into an HTTP header, so reject anything with whitespace or control characters.
        let tokenIsClean = token.unicodeScalars.allSatisfy {
            !CharacterSet.whitespacesAndNewlines.contains($0) && !CharacterSet.controlCharacters.contains($0)
        }
        guard !token.isEmpty, tokenIsClean, let api = URL(string: apiString), api.host() != nil else {
            throw PairingError.missingDetails
        }
        guard isAllowedServer(api) else { throw PairingError.insecureServer }
        return PairingInfo(apiBase: api, token: token)
    }

    /// Only https, plus plain http for localhost and 192.168.* during development.
    static func isAllowedServer(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(), let host = url.host()?.lowercased() else { return false }
        switch scheme {
        case "https":
            return true
        case "http":
            return host == "localhost" || host.hasPrefix("192.168.")
        default:
            return false
        }
    }

    // MARK: Storage

    static func load() -> PairingInfo? {
        guard let string = UserDefaults.standard.string(forKey: apiBaseKey),
              let api = URL(string: string),
              let token = Keychain.readToken()
        else { return nil }
        return PairingInfo(apiBase: api, token: token)
    }

    static func save(_ info: PairingInfo) {
        UserDefaults.standard.set(info.apiBase.absoluteString, forKey: apiBaseKey)
        Keychain.saveToken(info.token)
    }

    static func clear() {
        UserDefaults.standard.removeObject(forKey: apiBaseKey)
        Keychain.deleteToken()
    }
}

/// Stores the device token as a generic password in the Keychain.
enum Keychain {
    private static let service = (Bundle.main.bundleIdentifier ?? "pdcompanion") + ".device-token"
    private static let account = "device-token"

    private static var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    static func saveToken(_ token: String) {
        deleteToken()
        var query = baseQuery
        query[kSecValueData as String] = Data(token.utf8)
        // Background deliveries can wake the app while the phone is locked, so the token
        // must be readable after the first unlock. It never leaves this device.
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(query as CFDictionary, nil)
    }

    static func readToken() -> String? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data
        else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func deleteToken() {
        SecItemDelete(baseQuery as CFDictionary)
    }
}
