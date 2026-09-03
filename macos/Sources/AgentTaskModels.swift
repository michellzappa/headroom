import Foundation

/// Mac-local task-launching models. They are intentionally outside `Shared`:
/// the iPhone answers existing attention events and never creates agent work.
struct AgentTaskSurface: Codable, Sendable {
    var ok: Bool
    var providers: [AgentTaskProvider]
    var folders: [String]

    var startable: [AgentTaskProvider] {
        providers.filter { $0.canStart && $0.connection == "ready" }
    }
}

struct AgentTaskProvider: Codable, Sendable, Identifiable, Equatable {
    var provider: String
    var canStart: Bool
    var connection: String?

    var id: String { provider }

    /// Gateway providers name the adapter; the palette and marks are keyed by
    /// the tool. Same mapping an event row uses.
    var iconID: String { provider == "claude-code" ? "claude" : provider }

    var title: String { provider == "claude-code" ? "Claude Code" : "Codex" }

    enum CodingKeys: String, CodingKey {
        case provider, connection
        case canStart = "can_start"
    }
}

/// What the host says came of a start. Both providers return `ok`, so a
/// silent success was indistinguishable from nothing happening at all —
/// which is what it looked like.
struct AgentStartTaskResponse: Codable, Sendable {
    var ok: Bool
    var provider: String
    var task: AgentStartedTask
}

struct AgentStartedTask: Codable, Sendable {
    var cwd: String?
    var pid: Int?
    var threadID: String?
    var turnID: String?

    enum CodingKeys: String, CodingKey {
        case cwd, pid
        case threadID = "thread_id"
        case turnID = "turn_id"
    }
}

/// The result of asking an agent to start, in words a person can read.
struct AgentTaskOutcome: Sendable, Equatable {
    var ok: Bool
    var message: String
}

struct AgentStartTaskRequest: Codable, Sendable {
    var provider: String
    var cwd: String
    var prompt: String
}
