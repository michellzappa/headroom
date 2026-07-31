import Foundation

/// The one way this app talks to the host. Previously the popover and the
/// Settings pane each had their own client, each deriving base URLs and
/// decoding snapshots slightly differently — so they could disagree about
/// what the host said.
struct HeadroomClient: Sendable {
    enum ClientError: LocalizedError {
        case invalidEndpoint
        case unauthorized
        case badResponse(Int)
        case backend(String)

        var errorDescription: String? {
            switch self {
            case .invalidEndpoint:
                "The headroom endpoint is invalid."
            case .unauthorized:
                "The host rejected the token. Set it in Settings → Backend."
            case let .badResponse(code):
                "The backend returned HTTP \(code)."
            case let .backend(message):
                message
            }
        }
    }

    static let defaultEndpoint = "http://127.0.0.1:8737/usage"

    var endpoint: String
    var token: String?

    init(endpoint: String? = nil, token: String? = nil) {
        let resolved = endpoint
            ?? UserDefaults.standard.string(forKey: "usageEndpoint")
            ?? Self.defaultEndpoint
        self.endpoint = resolved
        // Host waves loopback through — don't touch Keychain on the hot path.
        // A wedged securityd (or unlock prompt) would otherwise freeze the
        // MainActor and the menu-bar icon never paints.
        if let token {
            self.token = token
        } else if Self.isLoopback(resolved) {
            self.token = nil
        } else {
            self.token = TokenStore.host.read()
        }
    }

    /// Same rule as Settings: token only matters off-machine.
    static func isLoopback(_ endpoint: String) -> Bool {
        guard let host = URL(string: endpoint)?.host()?.lowercased() else {
            return false
        }
        return host == "127.0.0.1" || host == "localhost" || host == "::1"
    }

    /// The endpoint as configured, for callers that need to build a client
    /// rather than print one. `displayEndpoint` is the human-readable form and
    /// is not a URL.
    static var currentEndpoint: String {
        UserDefaults.standard.string(forKey: "usageEndpoint") ?? defaultEndpoint
    }

    static var displayEndpoint: String {
        let raw = UserDefaults.standard.string(forKey: "usageEndpoint")
            ?? defaultEndpoint
        guard let url = URL(string: raw), let host = url.host() else { return raw }
        return url.port.map { "\(host):\($0)" } ?? host
    }

    private var usageURL: URL? { URL(string: endpoint) }

    /// Everything but /usage hangs off the same parent path.
    private func base() throws -> URL {
        guard let usageURL else { throw ClientError.invalidEndpoint }
        return usageURL.lastPathComponent == "usage"
            ? usageURL.deletingLastPathComponent()
            : usageURL
    }

    private func request(
        _ url: URL, method: String = "GET", body: Data? = nil,
        timeout: TimeInterval = 10
    ) -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.timeoutInterval = timeout
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let token, !token.isEmpty {
            request.setValue(token, forHTTPHeaderField: "X-Headroom-Token")
        }
        return request
    }

    @discardableResult
    private func send(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw ClientError.badResponse(0)
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
            throw ClientError.badResponse(http.statusCode)
        }
        return data
    }

    func fetchUsage() async throws -> UsageSnapshot {
        guard let usageURL else { throw ClientError.invalidEndpoint }
        let data = try await send(request(usageURL, timeout: 8))
        return try JSONDecoder().decode(UsageSnapshot.self, from: data)
    }

    func health() async throws -> HealthReport {
        let url = try base().appendingPathComponent("health")
        let data = try await send(request(url, timeout: 5))
        return try JSONDecoder().decode(HealthReport.self, from: data)
    }

    func fetchSetup() async throws -> SetupPayload {
        let url = try base().appendingPathComponent("setup")
        let data = try await send(request(url, timeout: 5))
        return try JSONDecoder().decode(SetupPayload.self, from: data)
    }

    func refresh(sources: [String]?) async throws {
        let url = try base()
            .appendingPathComponent("sync")
            .appendingPathComponent("refresh")
        let body: [String: Any] = (sources?.isEmpty == false)
            ? ["sources": sources as Any]
            : [:]
        try await send(request(
            url, method: "POST",
            body: try JSONSerialization.data(withJSONObject: body),
            timeout: 8))
    }

    func acknowledgeAttention(_ fingerprint: String) async throws {
        let url = try base()
            .appendingPathComponent("attention")
            .appendingPathComponent("ack")
        _ = try await send(request(
            url,
            method: "POST",
            body: try JSONSerialization.data(
                withJSONObject: ["fingerprint": fingerprint]),
            timeout: 8
        ))
    }

    /// Persist the Plausible primary window and force a fresh poll.
    @discardableResult
    func setPlausibleRange(_ range: String) async throws -> String {
        let url = try base()
            .appendingPathComponent("plausible")
            .appendingPathComponent("refresh")
        let data = try await send(request(
            url, method: "POST",
            body: try JSONSerialization.data(withJSONObject: ["range": range]),
            timeout: 8))
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (object?["range"] as? String) ?? range
    }

    /// The host answers a refresh with 202 and does the work in the background,
    /// so poll /health until the requested sources report a fresh age instead
    /// of sleeping a guessed interval and hoping.
    func waitForRefresh(
        sources: [String]?,
        timeout: TimeInterval = 6,
        freshWithin: Int = 3
    ) async {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            try? await Task.sleep(for: .milliseconds(400))
            guard let report = try? await health() else { continue }
            let wanted = sources ?? Array(report.sources.keys)
            let settled = wanted.allSatisfy { id in
                guard let row = report.sources[id] else { return true }
                guard row.enabled != false else { return true }
                guard let age = row.ageS else { return false }
                return age <= freshWithin
            }
            if settled { return }
        }
    }

    /// Write source flags. `enabled` pauses/resumes; `dismissed` moves rows
    /// between Settings' Active list and its Library. One POST for both, so
    /// a Library chip tap (un-dismiss + enable) can't land half-applied.
    func setSources(
        _ enabled: [String: Bool], dismissed: [String: Bool]? = nil
    ) async throws -> [String: Bool] {
        let url = try base().appendingPathComponent("sources")
        var body: [String: Any] = [:]
        if !enabled.isEmpty { body["enabled"] = enabled }
        if let dismissed { body["dismissed"] = dismissed }
        let data = try await send(request(
            url, method: "POST",
            body: try JSONSerialization.data(withJSONObject: body),
            timeout: 8))
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (object?["enabled"] as? [String: Bool]) ?? enabled
    }

    /// Pin the provider order. The host normalizes (unknown ids dropped, new
    /// providers appended) and answers with the list it actually stored.
    @discardableResult
    func setSourceOrder(_ order: [String]) async throws -> [String] {
        let url = try base().appendingPathComponent("sources")
        let data = try await send(request(
            url, method: "POST",
            body: try JSONSerialization.data(withJSONObject: ["order": order]),
            timeout: 8))
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (object?["order"] as? [String]) ?? order
    }

    /// Extra logins per provider, and which providers can hold them.
    func fetchAccounts() async throws -> ProviderAccounts {
        let url = try base().appendingPathComponent("accounts")
        let data = try await send(request(url, timeout: 5))
        return try JSONDecoder().decode(ProviderAccounts.self, from: data)
    }

    /// Register a second login. The host answers with the stored list and
    /// then restarts to rebuild its source registry around it, so callers
    /// must wait for it to come back before trusting `/usage` again.
    @discardableResult
    func addAccount(
        provider: String, label: String, root: String
    ) async throws -> ProviderAccounts {
        try await postAccounts([
            "provider": provider, "label": label, "root": root,
        ])
    }

    @discardableResult
    func removeAccount(_ id: String) async throws -> ProviderAccounts {
        try await postAccounts(["remove": id])
    }

    private func postAccounts(
        _ body: [String: String]
    ) async throws -> ProviderAccounts {
        let url = try base().appendingPathComponent("accounts")
        let data = try await send(request(
            url, method: "POST",
            body: try JSONSerialization.data(withJSONObject: body),
            timeout: 10))
        return try JSONDecoder().decode(ProviderAccounts.self, from: data)
    }

    /// Override the color a source is painted in, everywhere. Pass nil to
    /// restore the registry's own. The host resolves it into `accent` on the
    /// next document, so rings, meters and the phone follow without each
    /// client keeping its own copy.
    @discardableResult
    func setSourceAccent(_ id: String, hex: String?) async throws -> [String: String] {
        let url = try base().appendingPathComponent("sources")
        let value: Any = hex ?? NSNull()
        let data = try await send(request(
            url, method: "POST",
            body: try JSONSerialization.data(
                withJSONObject: ["accents": [id: value]]),
            timeout: 8))
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (object?["accents"] as? [String: String]) ?? [:]
    }

    func fetchMobilePermissions() async throws -> MobilePermissions {
        let url = try base()
            .appendingPathComponent("mobile")
            .appendingPathComponent("permissions")
        let data = try await send(request(url, timeout: 5))
        return try JSONDecoder()
            .decode(MobilePermissionsResponse.self, from: data)
            .permissions
    }

    func setMobilePermissions(
        _ permissions: MobilePermissions
    ) async throws -> MobilePermissions {
        let url = try base()
            .appendingPathComponent("mobile")
            .appendingPathComponent("permissions")
        let body = try JSONSerialization.data(
            withJSONObject: ["permissions": permissions.dictionary])
        let data = try await send(request(
            url, method: "POST", body: body, timeout: 5))
        return try JSONDecoder()
            .decode(MobilePermissionsResponse.self, from: data)
            .permissions
    }

    func fetchAgentGatewayConfiguration() async throws
        -> AgentGatewayConfiguration {
        let url = try base()
            .appendingPathComponent("agents")
            .appendingPathComponent("config")
        let data = try await send(request(url, timeout: 5))
        return try JSONDecoder().decode(
            AgentGatewayConfiguration.self, from: data)
    }

    func setAgentGatewayConfiguration(
        enabled: Bool,
        codexBinary: String
    ) async throws -> AgentGatewayConfiguration {
        let url = try base()
            .appendingPathComponent("agents")
            .appendingPathComponent("config")
        let body = try JSONSerialization.data(withJSONObject: [
            "enabled": enabled,
            "codex_binary": codexBinary,
        ])
        let data = try await send(request(
            url, method: "POST", body: body, timeout: 8))
        return try JSONDecoder().decode(
            AgentGatewayConfiguration.self, from: data)
    }

    func fetchMultiMacConfiguration() async throws -> MultiMacConfiguration {
        let url = try base()
            .appendingPathComponent("machines")
            .appendingPathComponent("config")
        let data = try await send(request(url, timeout: 5))
        return try JSONDecoder().decode(MultiMacConfiguration.self, from: data)
    }

    /// Turning sync on runs a round host-side before answering, so the peers
    /// in the response are real rather than a promise about the next minute.
    func setMultiMacConfiguration(enabled: Bool) async throws
        -> MultiMacConfiguration {
        let url = try base()
            .appendingPathComponent("machines")
            .appendingPathComponent("config")
        let body = try JSONSerialization.data(
            withJSONObject: ["enabled": enabled])
        let data = try await send(request(
            url, method: "POST", body: body, timeout: 10))
        return try JSONDecoder().decode(MultiMacConfiguration.self, from: data)
    }

    /// Hand the host the peer payloads fetched from CloudKit; get back the one
    /// this Mac should publish. The whole CloudKit contract in one call.
    ///
    /// `records` are the raw payload strings as stored, parsed here only far
    /// enough to nest them in the request body.
    func syncMachines(records: [String]) async throws -> MachineRound {
        let url = try base()
            .appendingPathComponent("machines")
            .appendingPathComponent("sync")
        let parsed = records.compactMap { blob -> Any? in
            guard let data = blob.data(using: .utf8) else { return nil }
            return try? JSONSerialization.jsonObject(with: data)
        }
        let body = try JSONSerialization.data(
            withJSONObject: ["records": parsed])
        let data = try await send(request(
            url, method: "POST", body: body, timeout: 15))
        guard let object = try JSONSerialization.jsonObject(with: data)
                as? [String: Any],
              let record = object["record"] as? [String: Any],
              let recordID = record["id"] as? String, !recordID.isEmpty,
              let encoded = try? JSONSerialization.data(
                withJSONObject: record, options: [.sortedKeys]),
              let json = String(data: encoded, encoding: .utf8)
        else { throw ClientError.badResponse(0) }
        return MachineRound(
            recordID: recordID,
            recordJSON: json,
            adopted: object["adopted"] as? [String] ?? [],
            peerCount: (object["peers"] as? [Any])?.count ?? 0
        )
    }

    func fetchClaudeHookConfiguration() async throws
        -> ClaudeHookConfiguration {
        let url = try base()
            .appendingPathComponent("agents")
            .appendingPathComponent("claude")
            .appendingPathComponent("config")
        let data = try await send(request(url, timeout: 5))
        return try JSONDecoder().decode(
            ClaudeHookConfiguration.self, from: data)
    }

    func changeClaudeHooks(_ action: String) async throws
        -> ClaudeHookConfiguration {
        let url = try base()
            .appendingPathComponent("agents")
            .appendingPathComponent("claude")
            .appendingPathComponent("config")
        let body = try JSONSerialization.data(
            withJSONObject: ["action": action])
        let data = try await send(request(
            url, method: "POST", body: body, timeout: 8))
        return try JSONDecoder().decode(
            ClaudeHookConfiguration.self, from: data)
    }

    func fetchGitHubWatch() async throws -> GitHubWatch {
        let url = try base()
            .appendingPathComponent("github")
            .appendingPathComponent("watch")
        let data = try await send(request(url, timeout: 8))
        return try JSONDecoder().decode(GitHubWatch.self, from: data)
    }

    /// Persist which repos Actions watches and answer with the resolved list.
    @discardableResult
    func setGitHubWatch(
        owners: [String], alwaysRepos: [String], maxDiscovered: Int
    ) async throws -> GitHubWatch {
        let url = try base()
            .appendingPathComponent("github")
            .appendingPathComponent("watch")
        let body = try JSONSerialization.data(withJSONObject: [
            "owners": owners,
            "always_repos": alwaysRepos,
            "max_discovered": maxDiscovered,
        ])
        let data = try await send(request(
            url, method: "POST", body: body, timeout: 10))
        return try JSONDecoder().decode(GitHubWatch.self, from: data)
    }

    func stopServer(pid: Int, port: Int) async throws {
        let url = try base()
            .appendingPathComponent("local")
            .appendingPathComponent("stop")
        let body = try JSONEncoder().encode(StopServerRequest(pid: pid, port: port))
        var request = request(url, method: "POST", body: body)
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw ClientError.badResponse(0)
        }
        if http.statusCode == 401 { throw ClientError.unauthorized }
        let result = try? JSONDecoder().decode(StopServerResponse.self, from: data)
        guard (200..<300).contains(http.statusCode) else {
            throw ClientError.backend(
                result?.error ?? "The backend returned HTTP \(http.statusCode).")
        }
        guard result?.ok == true else {
            throw ClientError.backend(result?.error ?? "Could not stop the server.")
        }
    }
}

/// Extra logins per provider, from `/accounts`. Mac-local like the GitHub
/// watch list: these name folders holding live credentials, and the host
/// serves the endpoint to loopback only.
struct ProviderAccounts: Decodable, Sendable {
    var providers: [AccountProvider] = []
    /// True on the response to a write — the host is re-execing to rebuild
    /// its source registry, so the caller should wait before re-reading.
    var restarting: Bool?

    var isEmpty: Bool { providers.isEmpty }
}

struct AccountProvider: Decodable, Identifiable, Sendable {
    var id: String
    var title: String
    /// "dir" when a second login is a second credential folder, "file" when
    /// it is the credential store itself. Drives which panel Add opens.
    var kind: String
    var hint: String?
    var accent: String?
    var max: Int?
    var accounts: [ProviderAccount] = []

    var wantsFolder: Bool { kind == "dir" }
    var isFull: Bool { accounts.count >= (max ?? 8) }
}

struct ProviderAccount: Decodable, Identifiable, Sendable {
    var id: String
    var provider: String
    var slug: String
    var label: String
    /// The credential location as the user typed it.
    var root: String
}

/// Mac-local Actions configuration. Not in Shared: iOS never edits the config
/// that lives next to the Mac's GitHub token.
struct GitHubWatch: Decodable, Sendable {
    var owners: [String] = []
    var alwaysRepos: [String] = []
    var maxDiscovered: Int = 6
    var devRoot: String?
    /// What the owners and always-repos resolved to on this machine.
    var watching: [String] = []

    enum CodingKeys: String, CodingKey {
        case owners, watching
        case alwaysRepos = "always_repos"
        case maxDiscovered = "max_discovered"
        case devRoot = "dev_root"
    }
}

struct AgentGatewayConfiguration: Decodable, Sendable {
    var ok: Bool
    var enabled: Bool
    var codexBinary: String
    var provider: AgentProviderStatus

    enum CodingKeys: String, CodingKey {
        case ok, enabled, provider
        case codexBinary = "codex_binary"
    }
}

struct MultiMacConfiguration: Decodable, Sendable {
    var ok: Bool
    var enabled: Bool
    /// "cloudkit", "folder", or "off". Which transport carries this Mac.
    var mode: String?
    var directory: String
    /// False when the folder's parent is missing — iCloud Drive switched off,
    /// or a configured directory that no longer exists. The toggle still
    /// works; it just will not find anyone, which is worth saying out loud.
    var available: Bool
    var machine: MultiMacIdentity
    var peers: [MachineSummary]
    /// Why the folder cannot be read, when it cannot. Writing keeps working
    /// when this is set, so without it "syncing fine, nobody there" and
    /// "macOS is blocking us" look identical.
    var trouble: String?
    var troubleDetail: String?

    enum CodingKeys: String, CodingKey {
        case ok, enabled, mode, directory, available, machine, peers, trouble
        case troubleDetail = "trouble_detail"
    }

    static let unknown = MultiMacConfiguration(
        ok: false, enabled: false, mode: "off", directory: "",
        available: false,
        machine: MultiMacIdentity(id: "", name: "This Mac"), peers: [])
}

struct MultiMacIdentity: Decodable, Sendable {
    var id: String
    var name: String
}

/// One `/machines/sync` round.
///
/// The record stays as raw JSON rather than a dictionary. That is not laziness
/// about typing it: Swift is transport here and must never read the contents,
/// so `Data` states the contract the type system can actually enforce — and it
/// is `Sendable`, which `[String: Any]` is not.
struct MachineRound: Sendable {
    /// CloudKit record name for this Mac's own record.
    var recordID: String
    /// Exactly the bytes to store in the record's payload field.
    var recordJSON: String
    var adopted: [String]
    var peerCount: Int
}

struct AgentProviderStatus: Decodable, Sendable {
    var provider: String
    var available: Bool
    var connection: String
    var error: String?
    var version: String?
    var resolvedBinary: String?

    enum CodingKeys: String, CodingKey {
        case provider, available, connection, error, version
        case resolvedBinary = "resolved_binary"
    }
}

struct ClaudeHookConfiguration: Decodable, Sendable {
    var ok: Bool
    var provider: String
    var settingsPath: String?
    var state: String
    var installed: Bool
    var installedEvents: [String]?
    var version: Int?
    var error: String?

    enum CodingKeys: String, CodingKey {
        case ok, provider, state, installed, version, error
        case settingsPath = "settings_path"
        case installedEvents = "installed_events"
    }
}

struct HealthReport: Decodable, Sendable {
    var ok: Bool?
    var uptimeS: Int?
    var updated: String?
    var sources: [String: SourceHealth]
    /// Absent on hosts older than the version handshake — see HostVersion.
    var version: String?
    var build: String?

    enum CodingKeys: String, CodingKey {
        case ok, updated, sources, version, build
        case uptimeS = "uptime_s"
    }
}

struct SourceHealth: Decodable, Sendable {
    var ok: Bool?
    var stale: Bool?
    var enabled: Bool?
    var ageS: Int?
    var error: String?
    var detail: String?

    enum CodingKeys: String, CodingKey {
        case ok, stale, enabled, error, detail
        case ageS = "age_s"
    }
}

private struct StopServerRequest: Encodable {
    let pid: Int
    let port: Int
}

private struct StopServerResponse: Decodable {
    let ok: Bool
    let error: String?
}
