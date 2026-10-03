import Foundation

/// `GET /study`, from `host/study_service.py`.
///
/// Decoded strictly on purpose. Every field the window draws is required, so a
/// host that answers with a different shape fails here with a name attached
/// instead of drawing a chart of zeros. `CodingKeys` are spelled out rather
/// than using `.convertFromSnakeCase`, because that strategy also rewrites the
/// keys of dictionaries, and the share maps are keyed by bins such as
/// `night_00_05` that must arrive exactly as sent.
///
/// `friends` and `machines` are decoded even though this window does not draw
/// them yet: the host sends one document, and a shape change in either should
/// fail in the same place.
struct StudySnapshot: Decodable, Sendable {
    var ok: Bool
    /// `scanning` (first pass over the logs, a few seconds), `ready`, `empty`.
    var status: String
    var asOf: String?
    var error: String?
    var handle: String
    var friends: [StudyFriend]
    var machines: [StudyMachine]
    var insights: StudyInsights?
    var card: StudyCard?
    var cardText: String?

    var isScanning: Bool { status == "scanning" }

    enum CodingKeys: String, CodingKey {
        case ok, status, error, handle, friends, machines, insights, card
        case asOf = "as_of"
        case cardText = "card_text"
    }
}

/// The person's own numbers, with real counts. Never leaves the Mac.
struct StudyInsights: Decodable, Sendable {
    var machines: Int
    var files: Int
    var turns: Int
    var sessions: Int
    var activeDays: Int
    var firstDay: String
    var lastDay: String
    var tokens: Tokens
    var cacheHitPct: Double?
    var outputPer1kInput: Double?
    /// An estimate, always. It is the API price of the tokens, not a bill.
    var costUsdEstimate: Double
    var ratesChecked: String
    /// Families with output but no rates, left out of the cost. Optional
    /// because hosts before Codex support never sent it.
    var unpricedModels: [String]?
    var models: [Model]
    var monthly: [Month]
    var weekly: [Week]
    var dailyTokens: DailyTokens
    var hours: [Int]
    var weekdays: [Int]
    var promptLen: [PromptBin]
    var session: SessionStats
    var subagentShare: Double
    var tools: [Tool]

    struct Tokens: Decodable, Sendable {
        var input: Int
        var output: Int
        var cacheRead: Int
        var cacheWrite: Int

        enum CodingKeys: String, CodingKey {
            case input, output
            case cacheRead = "cache_read"
            case cacheWrite = "cache_write"
        }
    }

    struct Model: Decodable, Sendable, Identifiable {
        var family: String
        /// `claude`, `codex` or `other`. Nil from hosts before Codex support,
        /// which only ever sent Claude families.
        var provider: String?
        var outputShare: Double
        var totalShare: Double
        var id: String { family }

        enum CodingKeys: String, CodingKey {
            case family, provider
            case outputShare = "output_share"
            case totalShare = "total_share"
        }
    }

    struct Month: Decodable, Sendable, Identifiable {
        var month: String
        /// Output tokens by model family.
        var output: [String: Int]
        var id: String { month }
    }

    struct Week: Decodable, Sendable, Identifiable {
        var week: String
        var output: Int
        var id: String { week }
    }

    struct DailyTokens: Decodable, Sendable {
        var median: Int
        var p90: Int
        var max: Int
    }

    struct PromptBin: Decodable, Sendable, Identifiable {
        var bin: String
        var count: Int
        var id: String { bin }
    }

    struct SessionStats: Decodable, Sendable {
        var medianMin: Double
        var p90Min: Double
        var longestMin: Double
        var medianTurns: Int
        var p90Turns: Int

        enum CodingKeys: String, CodingKey {
            case medianMin = "median_min"
            case p90Min = "p90_min"
            case longestMin = "longest_min"
            case medianTurns = "median_turns"
            case p90Turns = "p90_turns"
        }
    }

    struct Tool: Decodable, Sendable, Identifiable {
        var name: String
        var count: Int
        var id: String { name }
    }

    enum CodingKeys: String, CodingKey {
        case machines, files, turns, sessions, tokens, models, monthly, weekly
        case hours, weekdays, session, tools
        case activeDays = "active_days"
        case firstDay = "first_day"
        case lastDay = "last_day"
        case cacheHitPct = "cache_hit_pct"
        case outputPer1kInput = "output_per_1k_input"
        case costUsdEstimate = "cost_usd_estimate"
        case ratesChecked = "rates_checked"
        case unpricedModels = "unpriced_models"
        case dailyTokens = "daily_tokens"
        case promptLen = "prompt_len"
        case subagentShare = "subagent_share"
    }
}

/// What a friend receives. Shares in 5% steps and wide bins, never counts.
struct StudyCard: Decodable, Sendable {
    var id: String
    var handle: String
    var week: String
    var data: StudyCardData
}

struct StudyCardData: Decodable, Sendable {
    var promptLenShare: [String: Double]
    var outPerTurnShare: [String: Double]
    var sessionActiveMinShare: [String: Double]
    var timeOfDayShare: [String: Double]
    var cacheHitBucketPct: Int?
    var weeklyModelShare: [String: [String: Double]]

    enum CodingKeys: String, CodingKey {
        case promptLenShare = "prompt_len_share"
        case outPerTurnShare = "out_per_turn_share"
        case sessionActiveMinShare = "session_active_min_share"
        case timeOfDayShare = "time_of_day_share"
        case cacheHitBucketPct = "cache_hit_bucket_pct"
        case weeklyModelShare = "weekly_model_share"
    }
}

struct StudyFriend: Decodable, Sendable, Identifiable {
    var id: String
    var alias: String?
    var handle: String
    /// The alias if you gave one, else the handle they chose.
    var name: String
    var week: String
    var data: StudyCardData
    var added: String?
    var updated: String?
}

struct StudyMachine: Decodable, Sendable, Identifiable {
    var id: String
    var name: String
    var thisMac: Bool
    var hasUsage: Bool
    var generated: String?
    /// Arrived in a multi-Mac sync record, not a file. Optional because older
    /// hosts never sent it.
    var synced: Bool?

    var isSynced: Bool { synced ?? false }

    enum CodingKeys: String, CodingKey {
        case id, name, generated, synced
        case thisMac = "this_mac"
        case hasUsage = "has_usage"
    }
}

/// `POST /study/handle`.
struct StudyHandleResult: Decodable, Sendable {
    var ok: Bool
    var handle: String
}

/// `POST /study/friends`.
struct StudyFriendResult: Decodable, Sendable {
    var ok: Bool
    /// True when the card replaced one you already had from the same sender.
    var updated: Bool
    var friend: StudyFriend?
}

/// `POST /study/shard`.
struct StudyShardResult: Decodable, Sendable {
    var ok: Bool
    var machine: String
}
