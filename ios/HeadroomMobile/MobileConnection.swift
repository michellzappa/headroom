import Foundation
import Security

enum MobileConnection {
    static let endpointKey = "mobileUsageEndpoint"
    static let configuredKey = "mobileConnectionConfigured"
    static let defaultEndpoint = "http://headroom.local:8737/usage"

    static var endpoint: String {
        UserDefaults.standard.string(forKey: endpointKey) ?? defaultEndpoint
    }

    static var isConfigured: Bool {
        UserDefaults.standard.bool(forKey: configuredKey)
    }

    /// Host from the saved endpoint — `.local` name, MagicDNS, or IP.
    static var hostLabel: String {
        hostLabel(for: endpoint)
    }

    static func hostLabel(for endpoint: String) -> String {
        URL(string: endpoint)?.host() ?? endpoint
    }

    /// One-line Mac identity for the Usage status tile.
    ///
    /// Prefer the Computer Name from `/usage` when it adds something the host
    /// does not already say; otherwise just the address you paired with.
    static func identityLabel(machineName: String?) -> String {
        let host = hostLabel
        guard let name = machineName?
            .trimmingCharacters(in: .whitespacesAndNewlines),
              !name.isEmpty else { return host }
        let hostBase = host.split(separator: ".").first.map(String.init) ?? host
        if name.caseInsensitiveCompare(host) == .orderedSame
            || name.caseInsensitiveCompare(hostBase) == .orderedSame {
            return host
        }
        return "\(name) · \(host)"
    }

    static func normalize(_ input: String) -> String? {
        var value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return nil }
        if !value.contains("://") {
            value = "http://\(value)"
        }
        guard var components = URLComponents(string: value),
              components.host != nil else { return nil }
        if components.port == nil {
            components.port = 8737
        }
        if components.path.isEmpty || components.path == "/" {
            components.path = "/usage"
        }
        return components.url?.absoluteString
    }
}

enum MobileTokenStore {
    private static let service = "com.centaur-labs.headroom.mobile"
    private static let legacyAccount = "host-token"

    static func read(for endpoint: String = MobileConnection.endpoint) -> String? {
        var query = baseQuery(account: account(for: endpoint))
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        var status = SecItemCopyMatching(query as CFDictionary, &item)
        // Migrate the original one-computer token lazily. It remains in the
        // Keychain only as a compatibility fallback for existing installs.
        if status != errSecSuccess, endpoint == MobileConnection.endpoint {
            query = baseQuery(account: legacyAccount)
            query[kSecReturnData as String] = true
            query[kSecMatchLimit as String] = kSecMatchLimitOne
            status = SecItemCopyMatching(query as CFDictionary, &item)
        }
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func save(_ token: String, for endpoint: String = MobileConnection.endpoint) throws {
        let data = Data(token.utf8)
        let query = baseQuery(account: account(for: endpoint))
        let status: OSStatus
        // `read(for:)` also falls back to the pre-multi-computer account for
        // the current endpoint. Do not use that fallback to choose Update:
        // an existing legacy token still needs to be copied into this new
        // endpoint-specific Keychain item.
        var lookup = query
        lookup[kSecReturnData as String] = false
        let hasEndpointToken = SecItemCopyMatching(
            lookup as CFDictionary, nil
        ) == errSecSuccess
        if !hasEndpointToken {
            var attributes = query
            attributes[kSecValueData as String] = data
            attributes[kSecAttrAccessible as String] =
                kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            status = SecItemAdd(attributes as CFDictionary, nil)
        } else {
            status = SecItemUpdate(
                query as CFDictionary,
                [kSecValueData as String: data] as CFDictionary
            )
        }
        guard status == errSecSuccess else {
            throw NSError(
                domain: NSOSStatusErrorDomain,
                code: Int(status),
                userInfo: [NSLocalizedDescriptionKey: "Could not save the mobile token."]
            )
        }
    }

    private static func account(for endpoint: String) -> String {
        "host-token:\(endpoint)"
    }

    private static func baseQuery(account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}

/// Non-secret metadata for Macs this iPhone has paired with. Tokens stay in
/// the Keychain under the matching endpoint; this list is safe to keep in
/// UserDefaults and makes switching computers explicit rather than silently
/// replacing the current connection.
struct PairedComputer: Codable, Hashable, Identifiable, Sendable {
    var id: String
    var name: String
    var endpoint: String
}

enum PairedComputerStore {
    private static let key = "pairedComputers"

    static var all: [PairedComputer] {
        guard let data = UserDefaults.standard.data(forKey: key),
              let computers = try? JSONDecoder().decode(
                  [PairedComputer].self, from: data)
        else { return [] }
        return computers
    }

    static func upsert(
        endpoint: String,
        machineID: String?,
        machineName: String?
    ) {
        let fallback = MobileConnection.hostLabel(for: endpoint)
        let id = machineID?.isEmpty == false ? machineID! : endpoint
        let name = machineName?.isEmpty == false ? machineName! : fallback
        var computers = all.filter { $0.id != id && $0.endpoint != endpoint }
        computers.insert(
            PairedComputer(id: id, name: name, endpoint: endpoint), at: 0)
        guard let data = try? JSONEncoder().encode(computers) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }
}

struct MobileHeadroomClient: Sendable {
    enum ClientError: LocalizedError {
        case invalidEndpoint
        case unauthorized
        case response(Int)
        case backend(String)

        var errorDescription: String? {
            switch self {
            case .invalidEndpoint:
                "The Mac address is invalid."
            case .unauthorized:
                "The Mac rejected the mobile token."
            case let .response(code):
                "The Headroom server returned HTTP \(code)."
            case let .backend(message):
                message
            }
        }
    }

    let endpoint: String
    let token: String

    func fetchUsage() async throws -> UsageSnapshot {
        try await fetchUsagePayload().snapshot
    }

    /// The decoded snapshot plus the bytes it came from, so callers can archive
    /// the payload verbatim instead of re-encoding a decode-only model.
    func fetchUsagePayload() async throws -> (snapshot: UsageSnapshot, payload: Data) {
        let data = try await send(url: try usageURL)
        return (try JSONDecoder().decode(UsageSnapshot.self, from: data), data)
    }

    func requestRefresh() async throws {
        let url = try base().appending(path: "sync/refresh")
        _ = try await send(
            url: url,
            method: "POST",
            body: Data(#"{}"#.utf8)
        )
    }

    /// Host answers /sync/refresh with 202 and works in the background — poll
    /// /health until sources look fresh instead of sleeping a guessed interval.
    func waitForRefresh(
        timeout: TimeInterval = 6,
        freshWithin: Int = 3
    ) async {
        guard let healthURL = try? base().appending(path: "health") else {
            return
        }
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            try? await Task.sleep(for: .milliseconds(400))
            guard let data = try? await send(url: healthURL),
                  let report = try? JSONDecoder().decode(
                      HealthReport.self, from: data)
            else { continue }
            let settled = report.sources.values.allSatisfy { row in
                if row.enabled == false { return true }
                guard let age = row.ageS else { return false }
                return age <= freshWithin
            }
            if settled { return }
        }
    }

    func acknowledgeAttention(_ fingerprint: String) async throws {
        let url = try base()
            .appending(path: "attention")
            .appending(path: "ack")
        let body = try JSONEncoder().encode(
            AttentionAcknowledgementRequest(fingerprint: fingerprint)
        )
        _ = try await send(url: url, method: "POST", body: body)
    }

    func fetchMobilePermissions() async throws -> MobilePermissions {
        let url = try base()
            .appending(path: "mobile")
            .appending(path: "permissions")
        let data = try await send(url: url)
        return try JSONDecoder()
            .decode(MobilePermissionsResponse.self, from: data)
            .permissions
    }

    func fetchAgentAttentionEvents() async throws -> [AgentAttentionEvent] {
        let url = try base()
            .appending(path: "attention")
            .appending(path: "events")
        let data = try await send(url: url)
        return try JSONDecoder()
            .decode(AgentAttentionEventsResponse.self, from: data)
            .events
    }

    func respond(
        to event: AgentAttentionEvent,
        action: AgentAttentionAction,
        idempotencyKey: String
    ) async throws -> AgentAttentionEvent {
        let url = try base()
            .appending(path: "attention")
            .appending(path: "events")
            .appending(path: event.id)
            .appending(path: "respond")
        let body = try JSONEncoder().encode(AgentAttentionResponseRequest(
            revision: event.revision,
            action: action.id,
            idempotencyKey: idempotencyKey,
            text: nil
        ))
        let data = try await send(url: url, method: "POST", body: body)
        return try JSONDecoder()
            .decode(AgentAttentionResponse.self, from: data)
            .event
    }

    func setSources(_ enabled: [String: Bool]) async throws -> [String: Bool] {
        let url = try base()
            .appending(path: "sources")
        let body = try JSONEncoder().encode(SourceControlRequest(enabled: enabled))
        let data = try await send(url: url, method: "POST", body: body)
        return try JSONDecoder().decode(SourceControlResponse.self, from: data).enabled
    }

    @discardableResult
    func setServicesOrder(_ order: [String]) async throws -> [String] {
        let url = try base()
            .appending(path: "sources")
        let body = try JSONEncoder().encode(
            ServicesOrderRequest(servicesOrder: order))
        let data = try await send(url: url, method: "POST", body: body)
        return try JSONDecoder().decode(
            ServicesOrderResponse.self, from: data).servicesOrder ?? order
    }

    func stopServer(pid: Int, port: Int) async throws {
        let url = try base()
            .appending(path: "local")
            .appending(path: "stop")
        let body = try JSONEncoder().encode(
            StopServerControlRequest(pid: pid, port: port)
        )
        _ = try await send(url: url, method: "POST", body: body)
    }

    private var usageURL: URL {
        get throws {
            guard let url = URL(string: endpoint) else {
                throw ClientError.invalidEndpoint
            }
            return url
        }
    }

    /// Everything but /usage hangs off the same parent path. Mirrors the
    /// macOS client: strip the last component only when it is the `usage`
    /// leaf, so a nonstandard saved endpoint doesn't point every sibling
    /// route at the wrong path.
    private func base() throws -> URL {
        let url = try usageURL
        return url.lastPathComponent == "usage"
            ? url.deletingLastPathComponent()
            : url
    }

    private func send(
        url: URL,
        method: String = "GET",
        body: Data? = nil
    ) async throws -> Data {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 10
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.setValue(token, forHTTPHeaderField: "X-Headroom-Token")
        request.setValue("ios", forHTTPHeaderField: "X-Headroom-Client")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw ClientError.response(0)
        }
        if http.statusCode == 401 { throw ClientError.unauthorized }
        guard (200..<300).contains(http.statusCode) else {
            // The host explains rejections in the body ("'acme' is not
            // owner/name"); an HTTP number alone sends people to the logs.
            if let object = try? JSONSerialization.jsonObject(with: data)
                as? [String: Any],
               let message = object["error"] as? String, !message.isEmpty {
                throw ClientError.backend(message)
            }
            throw ClientError.response(http.statusCode)
        }
        return data
    }
}

private struct SourceControlRequest: Encodable {
    let enabled: [String: Bool]
}

private struct SourceControlResponse: Decodable {
    let enabled: [String: Bool]
}

private struct ServicesOrderRequest: Encodable {
    let servicesOrder: [String]

    enum CodingKeys: String, CodingKey {
        case servicesOrder = "integrations_order"
    }
}

private struct ServicesOrderResponse: Decodable {
    let servicesOrder: [String]?

    enum CodingKeys: String, CodingKey {
        case servicesOrder = "integrations_order"
    }
}

private struct StopServerControlRequest: Encodable {
    let pid: Int
    let port: Int
}

private struct AttentionAcknowledgementRequest: Encodable {
    let fingerprint: String
}
