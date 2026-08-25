import Foundation
import Security

/// Minimal, focused Keychain wrapper for storing the auth token securely.
/// Values live in the keychain (not UserDefaults) so the token is protected at
/// rest and excluded from unencrypted backups where appropriate.
struct KeychainStore {
    enum Key: String {
        case authToken = "com.sava.mobile.authToken"
    }

    private let service = "com.sava.mobile"

    /// The shared Keychain access group.
    ///
    /// A share extension runs in its own process with its own sandbox, so it
    /// cannot read the containing app's Keychain items unless both declare the
    /// same access group. Without this the extension would be unable to
    /// authenticate and every share would fail — which is the single most
    /// surprising part of building one.
    ///
    /// Nil until the entitlement is provisioned: passing an access group the
    /// app is not entitled to makes every Keychain call fail with
    /// `errSecMissingEntitlement`, which is far worse than not sharing. The
    /// value is read from the entitlement itself rather than hardcoded, so the
    /// app and the extension cannot disagree about it.
    private var accessGroup: String? {
        Bundle.main.object(forInfoDictionaryKey: "SavaKeychainAccessGroup") as? String
    }

    /// Query attributes common to every call, including the access group when
    /// one is configured.
    private func baseQuery(_ key: Key) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key.rawValue,
        ]
        if let accessGroup, !accessGroup.isEmpty {
            query[kSecAttrAccessGroup as String] = accessGroup
        }
        return query
    }

    func save(_ value: String, for key: Key) {
        guard let data = value.data(using: .utf8) else { return }
        let query = baseQuery(key)
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]
        // Update if present, otherwise add.
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var insert = query
            insert.merge(attributes) { _, new in new }
            SecItemAdd(insert as CFDictionary, nil)
        }
    }

    func read(_ key: Key) -> String? {
        var query = baseQuery(key)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess,
              let data = result as? Data,
              let value = String(data: data, encoding: .utf8) else {
            return nil
        }
        return value
    }

    func delete(_ key: Key) {
        SecItemDelete(baseQuery(key) as CFDictionary)
    }
}
