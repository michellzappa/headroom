import Foundation

struct UsageSnapshot: Decodable, Sendable {
    var updated: String?
    var plan: String?
    var quotaOK: Bool?
    var quotaError: String?
    var sessionPct: Double?
    var sessionPacePct: Double?
    var sessionResetsIn: String?
    var weekPct: Double?
    var weekPacePct: Double?
    var weekResetsIn: String?
    var today: TokenBucket?
    var byDay: [DailyBurnDay]?
    var codex: CodexUsage?
    var cursor: CursorUsage?
    /// Normalized quota providers from the host registry (additive).
    var providers: [QuotaProviderInfo]?
    var vercel: VercelUsage?
    var git: GitUsage?
    var github: GitHubUsage?
    var activity: [ActivityItem]?
    var local: LocalUsage?
    var supabase: SupabaseUsage?
    var plausible: PlausibleUsage?
    var claudeStatus: ClaudeStatus?
    var sources: [SyncSource]?
    var attention: Attention?
    /// Provider ids the compact surfaces show, picked host-side so the menu
    /// bar, the widget, and the board never disagree about which three.
    var focus: [String]?
    /// Per-provider, per-pool burndown keyed as ["claude": ["week": …]].
    var burndown: [String: [String: Burndown]]?
    var burndownPrimary: Burndown?
    /// Every Mac signed into the same shared folder, this one first. Always at
    /// least one row, so a single-Mac install has the same shape as a synced
    /// one and no surface needs a special case for "sync is off".
    var machines: [MachineSummary]?

    static let empty = UsageSnapshot()

    init(
        updated: String? = nil,
        plan: String? = nil,
        quotaOK: Bool? = nil,
        quotaError: String? = nil,
        sessionPct: Double? = nil,
        sessionPacePct: Double? = nil,
        sessionResetsIn: String? = nil,
        weekPct: Double? = nil,
        weekPacePct: Double? = nil,
        weekResetsIn: String? = nil,
        today: TokenBucket? = nil,
        byDay: [DailyBurnDay]? = nil,
        codex: CodexUsage? = nil,
        cursor: CursorUsage? = nil,
        providers: [QuotaProviderInfo]? = nil,
        vercel: VercelUsage? = nil,
        git: GitUsage? = nil,
        github: GitHubUsage? = nil,
        activity: [ActivityItem]? = nil,
        local: LocalUsage? = nil,
        supabase: SupabaseUsage? = nil,
        plausible: PlausibleUsage? = nil,
        claudeStatus: ClaudeStatus? = nil,
        sources: [SyncSource]? = nil,
        attention: Attention? = nil,
        burndown: [String: [String: Burndown]]? = nil,
        burndownPrimary: Burndown? = nil,
        machines: [MachineSummary]? = nil
    ) {
        self.updated = updated
        self.plan = plan
        self.quotaOK = quotaOK
        self.quotaError = quotaError
        self.sessionPct = sessionPct
        self.sessionPacePct = sessionPacePct
        self.sessionResetsIn = sessionResetsIn
        self.weekPct = weekPct
        self.weekPacePct = weekPacePct
        self.weekResetsIn = weekResetsIn
        self.today = today
        self.byDay = byDay
        self.codex = codex
        self.cursor = cursor
        self.providers = providers
        self.vercel = vercel
        self.git = git
        self.github = github
        self.activity = activity
        self.local = local
        self.supabase = supabase
        self.plausible = plausible
        self.claudeStatus = claudeStatus
        self.sources = sources
        self.attention = attention
        self.burndown = burndown
        self.burndownPrimary = burndownPrimary
        self.machines = machines
    }

    enum CodingKeys: String, CodingKey {
        case updated, plan, today, codex, cursor, providers, vercel, git, github, activity, local
        case supabase, plausible, sources, attention, focus, burndown, machines
        case claudeStatus = "claude_status"
        case burndownPrimary = "burndown_primary"
        case byDay = "by_day"
        case quotaOK = "quota_ok"
        case quotaError = "quota_error"
        case sessionPct = "session_pct"
        case sessionPacePct = "session_pace_pct"
        case sessionResetsIn = "session_resets_in"
        case weekPct = "week_pct"
        case weekPacePct = "week_pace_pct"
        case weekResetsIn = "week_resets_in"
    }

    /// Enabled coding-quota providers from the host registry (string ids).
    ///
    /// CodexBar-style: Settings → Sources is the subset. Prefer `providers[]`
    /// intersected with enabled quota `sources[]`. Empty when the host has not
    /// advertised any — never invent Claude/Codex/Cursor.
    var visibleQuotaProviders: [QuotaProviderInfo] {
        let sourcesList = sources ?? []
        let hasKind = sourcesList.contains { $0.kind != nil }
        let known = Set(UsageProvider.allCases.map(\.rawValue))

        let quotaSourceRows = sourcesList.filter { row in
            if hasKind { return row.kind == "quota" }
            return known.contains(row.id)
        }
        let enabledQuotaIDs = Set(
            quotaSourceRows.filter { $0.enabled != false }.map(\.id)
        )
        // Sources listed quota rows but the user turned them all off.
        if !quotaSourceRows.isEmpty && enabledQuotaIDs.isEmpty {
            return []
        }

        let rows = providers ?? []
        if !rows.isEmpty {
            return rows.filter {
                $0.enabled != false
                    && (enabledQuotaIDs.isEmpty || enabledQuotaIDs.contains($0.id))
            }
        }

        // Older payloads without providers[]: synthesize from enabled sources.
        guard !enabledQuotaIDs.isEmpty else { return [] }
        var seen = Set<String>()
        return quotaSourceRows.compactMap { row in
            guard enabledQuotaIDs.contains(row.id),
                  seen.insert(row.id).inserted
            else { return nil }
            return QuotaProviderInfo(
                id: row.id,
                title: row.title,
                label: row.label,
                kind: row.kind ?? "quota",
                enabled: true
            )
        }
    }

    /// The providers a compact surface shows: menu-bar tanks and the iOS
    /// widget.
    ///
    /// The host picks them (pinned order, enabled only) and ships the ids in
    /// `focus`, so every surface shows the same providers even when one of
    /// them is a poll behind. Falls back to the first `limit` visible
    /// providers when talking to a host that predates the field.
    func focusProviders(limit: Int = 3) -> [QuotaProviderInfo] {
        let visible = visibleQuotaProviders
        guard let focus, !focus.isEmpty else {
            return Array(visible.prefix(limit))
        }
        let byID = Dictionary(visible.map { ($0.id, $0) }) { first, _ in first }
        let picked = focus.compactMap { byID[$0] }
        // A focus id the client can't resolve (disabled between polls, or a
        // provider this build doesn't know) must not shrink the row.
        return picked.isEmpty ? Array(visible.prefix(limit))
                              : Array(picked.prefix(limit))
    }

    /// Known-enum view of `visibleQuotaProviders` for Mac chrome still typed
    /// on `UsageProvider`. Unknown registry ids are skipped until those
    /// surfaces take string ids.
    var activeQuotaProviders: [UsageProvider] {
        var seen = Set<String>()
        var out: [UsageProvider] = []
        for row in visibleQuotaProviders {
            guard let provider = UsageProvider(rawValue: row.id),
                  seen.insert(row.id).inserted
            else { continue }
            out.append(provider)
        }
        return out
    }


    func burndownRings(for provider: UsageProvider) -> [Burndown] {
        burndownRings(forProviderID: provider.rawValue)
    }

    /// Pools for one provider in the app-wide pool order: the same selection
    /// and sequence as the progress bars, so rings, bars and burndown charts
    /// can never disagree. Prefer host `ring` flags when the provider
    /// advertised pools; otherwise fall back to known Cursor filters.
    func burndownRings(forProviderID providerID: String) -> [Burndown] {
        let pools = burndown?[providerID] ?? [:]
        if let info = providers?.first(where: { $0.id == providerID }),
           !(info.pools ?? [:]).isEmpty {
            return info.orderedBurndown(from: pools)
        }
        let all = Array(pools.values)
        let visible = providerID == UsageProvider.cursor.rawValue
            ? all.filter { $0.pool == "total" || $0.pool == "api" }
            : all
        return visible.sorted { lhs, rhs in
            let lw = lhs.windowS ?? .greatestFiniteMagnitude
            let rw = rhs.windowS ?? .greatestFiniteMagnitude
            if lw != rw { return lw < rw }
            let li = QuotaProviderInfo.poolPrecedence.firstIndex(
                of: lhs.pool ?? "") ?? QuotaProviderInfo.poolPrecedence.count
            let ri = QuotaProviderInfo.poolPrecedence.firstIndex(
                of: rhs.pool ?? "") ?? QuotaProviderInfo.poolPrecedence.count
            return li < ri
        }
    }

    /// The single pool the combined burndown draws for a provider.
    ///
    /// The longest window, because that is the one a week-wide chart is about
    /// — except Cursor, whose Total is the billing cycle its API pool nests
    /// inside. Cursor's API pool keeps to the Cursor detail chart; four lines
    /// is more than an overview can usefully carry.
    func overviewBurndown(forProviderID providerID: String) -> Burndown? {
        let pools = burndownRings(forProviderID: providerID)
        if providerID == UsageProvider.cursor.rawValue,
           let total = pools.first(where: { $0.pool == "total" }) {
            return total
        }
        return pools.max { ($0.windowS ?? 0) < ($1.windowS ?? 0) }
    }

    func meter(for provider: UsageProvider) -> ProviderMeter {
        meter(forProviderID: provider.rawValue)
    }

    func meter(for info: QuotaProviderInfo) -> ProviderMeter {
        if !(info.pools ?? [:]).isEmpty {
            return meter(fromRegistry: info)
        }
        if let known = UsageProvider(rawValue: info.id) {
            return legacyMeter(for: known)
        }
        return ProviderMeter(
            id: info.id,
            title: info.markTitle,
            ok: info.ok ?? false,
            plan: info.plan,
            error: info.error,
            primary: MeterWindow(title: "—", percent: nil),
            secondary: MeterWindow(title: "—", percent: nil),
            headlinePoolID: info.headline
        )
    }

    func meter(forProviderID providerID: String) -> ProviderMeter {
        if let info = providers?.first(where: { $0.id == providerID }) {
            return meter(for: info)
        }
        if let known = UsageProvider(rawValue: providerID) {
            return legacyMeter(for: known)
        }
        return ProviderMeter(
            id: providerID,
            title: providerID.capitalized,
            ok: false,
            primary: MeterWindow(title: "—", percent: nil),
            secondary: MeterWindow(title: "—", percent: nil)
        )
    }

    /// Schema-driven meter from `/usage` → `providers[]`. Cost / reset-credit
    /// extras still come from the legacy nested objects until the host folds
    /// them into the registry payload.
    private func meter(fromRegistry info: QuotaProviderInfo) -> ProviderMeter {
        let windows = info.visiblePools.map { entry in
            MeterWindow(
                id: entry.id,
                title: entry.pool.title ?? entry.id.capitalized,
                percent: entry.pool.pct,
                pacePercent: entry.pool.pacePct,
                reset: entry.pool.resetsIn
            )
        }
        let primary = windows.first ?? MeterWindow(title: "—", percent: nil)
        let secondary = windows.count > 1
            ? windows[1]
            : MeterWindow(title: "—", percent: nil)
        let tertiary = windows.count > 2 ? windows[2] : nil

        var paceLabel: String?
        var runsOutIn: String?
        var resetCreditsLabel: String?
        var resetCreditsExpiryLabel: String?
        var costLabel: String?
        switch info.id {
        case UsageProvider.claude.rawValue:
            costLabel = today?.costUSD.map { $0.dollarLabel + " today" }
        case UsageProvider.codex.rawValue:
            paceLabel = codex?.paceLabel
            runsOutIn = codex?.runsOutIn
            resetCreditsLabel = codex?.resetCreditsLabel
            resetCreditsExpiryLabel = codex?.resetCreditsExpiryLabel
            costLabel = codex?.costLabel
        case UsageProvider.cursor.rawValue:
            paceLabel = cursor?.paceLabel
            costLabel = cursorCostLabel
        default:
            break
        }

        return ProviderMeter(
            id: info.id,
            title: info.markTitle,
            ok: info.ok ?? false,
            plan: info.plan,
            error: info.error,
            primary: primary,
            secondary: secondary,
            tertiary: tertiary,
            paceLabel: paceLabel,
            runsOutIn: runsOutIn,
            resetCreditsLabel: resetCreditsLabel,
            resetCreditsExpiryLabel: resetCreditsExpiryLabel,
            costLabel: costLabel,
            headlinePoolID: info.headline,
            statusNote: info.statusNote,
            needsSignIn: info.needsSignIn
        )
    }

    private func legacyMeter(for provider: UsageProvider) -> ProviderMeter {
        switch provider {
        case .claude:
            ProviderMeter(
                provider: provider,
                ok: quotaOK ?? false,
                plan: plan,
                error: quotaError,
                primary: MeterWindow(
                    id: "session",
                    title: "Session",
                    percent: sessionPct,
                    pacePercent: sessionPacePct,
                    reset: sessionResetsIn
                ),
                secondary: MeterWindow(
                    id: "week",
                    title: "Weekly",
                    percent: weekPct,
                    pacePercent: weekPacePct,
                    reset: weekResetsIn
                ),
                costLabel: today?.costUSD.map {
                    $0.dollarLabel + " today"
                },
                headlinePoolID: "week"
            )
        case .codex:
            ProviderMeter(
                provider: provider,
                ok: codex?.ok ?? false,
                plan: codex?.plan,
                error: codex?.error,
                primary: MeterWindow(
                    id: "session",
                    title: "Session",
                    percent: codex?.sessionPct,
                    pacePercent: codex?.sessionPacePct,
                    reset: codex?.sessionResetsIn
                ),
                secondary: MeterWindow(
                    id: "week",
                    title: "Weekly",
                    percent: codex?.weekPct,
                    pacePercent: codex?.weekPacePct,
                    reset: codex?.weekResetsIn
                ),
                paceLabel: codex?.paceLabel,
                runsOutIn: codex?.runsOutIn,
                resetCreditsLabel: codex?.resetCreditsLabel,
                resetCreditsExpiryLabel: codex?.resetCreditsExpiryLabel,
                costLabel: codex?.costLabel,
                headlinePoolID: "week"
            )
        case .cursor:
            // Total (included) and API (on-demand) are independent pools that
            // share a billing cycle. Auto is omitted — it sits at 0% for most
            // plans and used to steal the second ring from API.
            ProviderMeter(
                provider: provider,
                ok: cursor?.ok ?? false,
                plan: cursor?.plan,
                primary: MeterWindow(
                    id: "total",
                    title: "Total",
                    percent: cursor?.totalPct,
                    pacePercent: cursor?.totalPacePct,
                    reset: cursor?.resetsIn
                ),
                secondary: MeterWindow(
                    id: "api",
                    title: "API",
                    percent: cursor?.apiPct,
                    pacePercent: cursor?.apiPacePct,
                    reset: cursor?.resetsIn
                ),
                paceLabel: cursor?.paceLabel,
                costLabel: cursorCostLabel,
                headlinePoolID: "total"
            )
        }
    }

    private var cursorCostLabel: String? {
        let plan = cursor?.costLabel
        let onDemand = cursor?.onDemandLabel
        switch (plan, onDemand) {
        case let (plan?, onDemand?):
            return "\(plan) · \(onDemand)"
        case let (plan?, nil):
            return plan
        case let (nil, onDemand?):
            return onDemand
        default:
            return nil
        }
    }
}

/// One pool's burndown. Series arrive as compact [[epoch, remainingPct], …]
/// pairs rather than objects, because this rides the same document the board
/// pulls over USB CDC.
struct Burndown: Decodable, Sendable, Identifiable {
    var provider: String?
    var pool: String?
    var windowStart: Double?
    var windowEnd: Double?
    var windowS: Double?
    var remainingPct: Double?
    var usedPct: Double?
    var idealRemainingPct: Double?
    var deltaPct: Double?
    var inDeficit: Bool?
    var exhausted: Bool?
    var status: String?
    var resetsIn: String?
    var ideal: [[Double]]?
    var actual: [[Double]]?
    var projected: [[Double]]?
    var rateUnit: String?
    /// Resets the provider granted out of band, oldest first — the reason a
    /// pool can jump back to full days before its window was due to roll.
    /// Scheduled rolls are not in here; the axis already ends on those.
    var resets: [BurndownReset]?
    /// The burn a grant wiped out, [[epoch, remaining], …], drawn faint behind
    /// the live curve. Empty unless this window began with one — a window that
    /// simply ran out needs no explaining.
    var forgiven: [[Double]]?
    /// "measured" from real samples, "estimated" from token history, nil when
    /// there is nothing to go on yet.
    var rateSource: String?
    var burnRatePct: Double?
    var allowancePct: Double?
    var exhaustsAt: Double?
    var exhaustsIn: String?
    var exhaustsBeforeReset: Bool?
    var samples: Int?
    /// Prose, for VoiceOver and for surfaces with room for only one line.
    var headline: String?
    /// The same situation as a short phrase, for a card that shows the numbers
    /// in a stat row beside it rather than inside the sentence.
    var verdict: String?

    var id: String { "\(provider ?? "?").\(pool ?? "?")" }

    /// Rings and bars elsewhere in the app grow with consumption, so the ring
    /// draws used percent even though the chart itself is a burndown.
    var pacePercent: Double? { idealRemainingPct.map { 100 - $0 } }

    var kind: BurndownStatus {
        BurndownStatus(rawValue: status ?? "") ?? .ok
    }

    /// A fit needs history; until then every forecast field is nil by design.
    var hasForecast: Bool { burnRatePct != nil }

    /// Forecast rests on the token-history prior, not on measured samples.
    var isEstimated: Bool { rateSource == "estimated" }

    var poolTitle: String {
        switch pool {
        case "session": "Session"
        case "week": "Weekly"
        case "total": "Total"
        case "auto": "Auto"
        case "api": "API"
        default: pool?.capitalized ?? "—"
        }
    }

    /// Projected [[t, remaining], …] stopped at the held reset and at empty.
    ///
    /// The host already crops this way; clients re-apply so a stale or demo
    /// payload cannot draw a forecast through a renewal (or under the floor).
    var croppedProjected: [[Double]] {
        Self.cropProjection(projected, windowEnd: windowEnd)
    }

    /// Crop a pool's forecast at its reset and at empty. The implementation
    /// lives with the rest of the chart geometry, in `BurndownChartMath`.
    static func cropProjection(
        _ pairs: [[Double]]?,
        windowEnd: Double?
    ) -> [[Double]] {
        OverallBurndownChartMath.cropProjection(pairs, windowEnd: windowEnd)
    }

    enum CodingKeys: String, CodingKey {
        case provider, pool, status, ideal, actual, projected, samples, headline
        case exhausted, verdict, resets, forgiven
        case windowStart = "window_start"
        case windowEnd = "window_end"
        case windowS = "window_s"
        case remainingPct = "remaining_pct"
        case usedPct = "used_pct"
        case idealRemainingPct = "ideal_remaining_pct"
        case deltaPct = "delta_pct"
        case inDeficit = "in_deficit"
        case resetsIn = "resets_in"
        case rateUnit = "rate_unit"
        case rateSource = "rate_source"
        case burnRatePct = "burn_rate_pct"
        case allowancePct = "allowance_pct"
        case exhaustsAt = "exhausts_at"
        case exhaustsIn = "exhausts_in"
        case exhaustsBeforeReset = "exhausts_before_reset"
    }
}

enum BurndownStatus: String, Sendable {
    case ok
    case ahead
    case critical
    case exhausted
}

/// One reset a provider handed out early, as detected in the sample log.
///
/// Without this the grant is invisible: the burn curve simply restarts, which
/// reads as the chart having forgotten the week rather than the week having
/// been forgiven.
struct BurndownReset: Decodable, Sendable, Identifiable {
    /// When the pool came back, epoch seconds.
    var t: Double?
    /// "granted" today. Present so scheduled rolls could join later without
    /// changing the shape.
    var kind: String?
    /// Percentage points the grant handed back.
    var forgivenPct: Double?

    var id: Double { t ?? 0 }

    var date: Date? { t.map { Date(timeIntervalSince1970: $0) } }

    enum CodingKeys: String, CodingKey {
        case t, kind
        case forgivenPct = "forgiven_pct"
    }
}

enum UsageProvider: String, CaseIterable, Sendable {
    case claude
    case codex
    case cursor

    var title: String {
        switch self {
        case .claude: "Claude"
        case .codex: "Codex"
        case .cursor: "Cursor"
        }
    }
}

struct ProviderMeter: Sendable {
    var id: String
    var title: String
    var ok: Bool
    var plan: String?
    var error: String?
    var primary: MeterWindow
    var secondary: MeterWindow
    var tertiary: MeterWindow?
    var paceLabel: String?
    var runsOutIn: String?
    /// Codex limit-reset credit inventory, e.g. "2 reset credits".
    var resetCreditsLabel: String?
    /// Joined expiry countdowns for those credits, e.g. "6d 5h · 18d 3h".
    var resetCreditsExpiryLabel: String?
    var costLabel: String?
    /// Host registry headline pool id (`week`, `total`, …) for menu-bar tanks.
    var headlinePoolID: String?
    /// Set when the host cannot refresh this meter — a dead login or frozen
    /// numbers. See `QuotaProviderInfo.statusNote`, which is where the wording
    /// is decided. Nil on a provider that is fetching normally.
    var statusNote: String?
    /// Whether `statusNote` is about a credential rather than a slow fetch.
    /// Carried alongside the note so a card can lead with it without having
    /// to parse prose back into a state.
    var needsSignIn: Bool

    var knownProvider: UsageProvider? { UsageProvider(rawValue: id) }

    init(
        id: String,
        title: String,
        ok: Bool,
        plan: String? = nil,
        error: String? = nil,
        primary: MeterWindow,
        secondary: MeterWindow,
        tertiary: MeterWindow? = nil,
        paceLabel: String? = nil,
        runsOutIn: String? = nil,
        resetCreditsLabel: String? = nil,
        resetCreditsExpiryLabel: String? = nil,
        costLabel: String? = nil,
        headlinePoolID: String? = nil,
        statusNote: String? = nil,
        needsSignIn: Bool = false
    ) {
        self.id = id
        self.title = title
        self.ok = ok
        self.plan = plan
        self.error = error
        self.primary = primary
        self.secondary = secondary
        self.tertiary = tertiary
        self.paceLabel = paceLabel
        self.runsOutIn = runsOutIn
        self.resetCreditsLabel = resetCreditsLabel
        self.resetCreditsExpiryLabel = resetCreditsExpiryLabel
        self.costLabel = costLabel
        self.headlinePoolID = headlinePoolID
        self.statusNote = statusNote
        self.needsSignIn = needsSignIn
    }

    /// Compatibility for call sites still typed on the known-provider enum.
    init(
        provider: UsageProvider,
        ok: Bool,
        plan: String? = nil,
        error: String? = nil,
        primary: MeterWindow,
        secondary: MeterWindow,
        tertiary: MeterWindow? = nil,
        paceLabel: String? = nil,
        runsOutIn: String? = nil,
        resetCreditsLabel: String? = nil,
        resetCreditsExpiryLabel: String? = nil,
        costLabel: String? = nil,
        headlinePoolID: String? = nil
    ) {
        self.init(
            id: provider.rawValue,
            title: provider.title,
            ok: ok,
            plan: plan,
            error: error,
            primary: primary,
            secondary: secondary,
            tertiary: tertiary,
            paceLabel: paceLabel,
            runsOutIn: runsOutIn,
            resetCreditsLabel: resetCreditsLabel,
            resetCreditsExpiryLabel: resetCreditsExpiryLabel,
            costLabel: costLabel,
            headlinePoolID: headlinePoolID
        )
    }

    private var allWindows: [MeterWindow] {
        [primary, secondary, tertiary].compactMap { $0 }
    }

    /// Window shown as the provider's headline signal (menu bar + overview rings).
    var headline: MeterWindow {
        if let headlinePoolID,
           let match = allWindows.first(where: { $0.id == headlinePoolID }) {
            return match
        }
        if id == UsageProvider.cursor.rawValue {
            return primary
        }
        return allWindows.max { ($0.percent ?? -1) < ($1.percent ?? -1) }
            ?? primary
    }

    /// Long-window tank for the menu-bar icon (Weekly / Total / host headline).
    var menuBarWindow: MeterWindow {
        if let headlinePoolID,
           let match = allWindows.first(where: { $0.id == headlinePoolID }) {
            return match
        }
        return id == UsageProvider.cursor.rawValue ? primary : secondary
    }
}

struct MeterWindow: Sendable {
    var id: String?
    var title: String
    var percent: Double?
    var pacePercent: Double?
    var reset: String?

    init(
        id: String? = nil,
        title: String,
        percent: Double?,
        pacePercent: Double? = nil,
        reset: String? = nil
    ) {
        self.id = id
        self.title = title
        self.percent = percent
        self.pacePercent = pacePercent
        self.reset = reset
    }
}

struct TokenBucket: Decodable, Sendable {
    var total: Int?
    var costUSD: Double?

    enum CodingKeys: String, CodingKey {
        case total
        case costUSD = "cost_usd"
    }
}

struct DailyBurnDay: Decodable, Sendable, Identifiable {
    var date: String
    var claude: Double?
    var codex: Double?
    var cursor: Double?
    var total: Double?
    /// Dynamic map mirroring host `by_day[].burns` (preferred when present).
    var burns: [String: Double]?

    var id: String { date }

    func burn(for provider: UsageProvider) -> Double {
        burn(forProviderID: provider.rawValue)
    }

    func burn(forProviderID providerID: String) -> Double {
        if let burns, let value = burns[providerID] {
            return value
        }
        switch providerID {
        case UsageProvider.claude.rawValue: return claude ?? 0
        case UsageProvider.codex.rawValue: return codex ?? 0
        case UsageProvider.cursor.rawValue: return cursor ?? 0
        default: return 0
        }
    }

    /// Total across the given providers (enabled set), not every column.
    func total(for providers: [UsageProvider]) -> Double {
        total(forProviderIDs: providers.map(\.rawValue))
    }

    func total(forProviderIDs providerIDs: [String]) -> Double {
        if providerIDs.isEmpty { return total ?? 0 }
        return providerIDs.reduce(0) { $0 + burn(forProviderID: $1) }
    }
}

/// One coding-quota provider as advertised by the host registry.
struct QuotaProviderInfo: Decodable, Identifiable, Sendable {
    var id: String
    var title: String?
    /// User-defined name for an extra login (`claude:work` → "Work"). Nil on
    /// the default provider row. Drawn next to the brand mark so the mark
    /// names the tool and this names the account.
    var label: String?
    var kind: String?
    /// Position in the user's pinned order. The host already sorted
    /// `providers[]`; this is here so a client that re-sorts can't drift.
    var rank: Int?
    var enabled: Bool?
    var ok: Bool?
    /// The host is replaying its last good numbers because the live fetch is
    /// failing. `ok` stays true — these percentages were real once — so a
    /// surface that only checks `ok` will draw a frozen meter as a live one.
    var stale: Bool?
    /// Seconds since the numbers were actually fetched. Nil when the host
    /// predates the field or never managed a good fetch to date from.
    var staleForS: Double?
    /// The credential behind this provider is missing or was rejected. Always
    /// arrives with `stale`, and is the more useful of the two: staleness is
    /// a symptom shared by rate limits and dropped networks, and only this one
    /// names something the reader can go and do.
    var authRequired: Bool?
    var plan: String?
    var error: String?
    var accent: String?
    /// The registry's own color, before any Settings override. Settings marks
    /// this swatch "Default"; everything else just paints `accent`.
    var accentDefault: String?
    var headline: String?
    /// Where this provider's granted resets get explained, when it explains
    /// them anywhere. A permalink the app only ever opens — nothing fetches
    /// it, so no part of your account leaves the Mac to render a reset.
    var resetNoteURL: String?
    var pools: [String: QuotaPoolInfo]?

    init(
        id: String,
        title: String? = nil,
        label: String? = nil,
        kind: String? = nil,
        rank: Int? = nil,
        enabled: Bool? = nil,
        ok: Bool? = nil,
        stale: Bool? = nil,
        staleForS: Double? = nil,
        authRequired: Bool? = nil,
        plan: String? = nil,
        error: String? = nil,
        accent: String? = nil,
        accentDefault: String? = nil,
        headline: String? = nil,
        resetNoteURL: String? = nil,
        pools: [String: QuotaPoolInfo]? = nil
    ) {
        self.id = id
        self.title = title
        self.label = label
        self.kind = kind
        self.rank = rank
        self.enabled = enabled
        self.ok = ok
        self.stale = stale
        self.staleForS = staleForS
        self.authRequired = authRequired
        self.plan = plan
        self.error = error
        self.accent = accent
        self.accentDefault = accentDefault
        self.headline = headline
        self.resetNoteURL = resetNoteURL
        self.pools = pools
    }

    enum CodingKeys: String, CodingKey {
        case id, title, label, kind, rank, enabled, ok, plan, error, accent, stale
        case headline, pools
        case staleForS = "stale_for_s"
        case authRequired = "auth_required"
        case accentDefault = "accent_default"
        case resetNoteURL = "reset_note_url"
    }

    /// Full name for text-only surfaces: "Claude · Work", or "Claude".
    var displayTitle: String { title ?? id.capitalized }

    /// Name drawn next to a brand mark (logo or accent-colored ring).
    ///
    /// The mark already says which tool this is. Repeating "Claude" beside it
    /// is how a row of account tabs all truncate to "Claude…" — the one word
    /// that was already obvious. Prefer the user label; fall back to parsing
    /// an older host's "Brand · Label" title; otherwise the full title.
    var markTitle: String {
        if let label = Self.normalized(label) { return label }
        if id.contains(":"),
           let title,
           let sep = title.range(of: " · ") {
            let suffix = String(title[sep.upperBound...])
            if let label = Self.normalized(suffix) { return label }
        }
        return displayTitle
    }

    private static func normalized(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// Frozen numbers the host is replaying. Worth saying out loud on any
    /// surface that draws them: `ok` is still true, so nothing else about the
    /// provider looks wrong.
    var isStale: Bool { stale == true }

    /// The credential is gone or refused, and the meter will not recover on
    /// its own. Checked ahead of `isStale` everywhere, because it is true of
    /// a provider that never managed a first fetch either — where there are
    /// no frozen numbers to be stale, only an empty card that owes the reader
    /// a reason.
    var needsSignIn: Bool { authRequired == true }

    /// The numbers on this card cannot be read as current — frozen, or behind
    /// a login that is no longer working. What drains a ring and ambers a
    /// caption, since both failures make the arc a claim about the past.
    var readingSuspect: Bool { needsSignIn || isStale }

    /// "Needs sign-in · 2 hours ago", "Not updating · 2 hours ago", or nil
    /// when the provider is fetching normally. One place decides what a meter
    /// in trouble says, so the Mac and the phone cannot word it differently.
    var statusNote: String? {
        if needsSignIn {
            guard let age = staleForS else { return HeadroomCopy.needsSignIn }
            return HeadroomCopy.needsSignIn(age: age)
        }
        guard isStale else { return nil }
        guard let age = staleForS else { return HeadroomCopy.notUpdating }
        return HeadroomCopy.notUpdating(age: age)
    }

    /// Fallback pool order for hosts that predate `pools[].rank`. It only
    /// names the pools those hosts could serve — Copilot, Gemini, JetBrains
    /// and Zed all arrived with the rank field, so they never land here.
    static let poolPrecedence = ["session", "total", "api", "auto", "week"]

    /// Rank a pool for sorting: the host's declared order when it sent one,
    /// otherwise the legacy precedence list, otherwise last.
    static func poolRank(id: String, pool: QuotaPoolInfo) -> Int {
        if let rank = pool.rank { return rank }
        return poolPrecedence.firstIndex(of: id) ?? poolPrecedence.count
    }

    /// Ring pools in the host's declared order — the single sequence rings,
    /// progress bars and burndown charts all draw in.
    var visiblePools: [(id: String, pool: QuotaPoolInfo)] {
        (pools ?? [:])
            .filter { $0.value.ring != false }
            .sorted {
                let lhs = Self.poolRank(id: $0.key, pool: $0.value)
                let rhs = Self.poolRank(id: $1.key, pool: $1.value)
                // Ids break ties so an unranked pair can't shuffle between
                // refreshes — Swift's sort is not stable.
                if lhs != rhs { return lhs < rhs }
                return $0.key < $1.key
            }
            .map { (id: $0.key, pool: $0.value) }
    }

    /// This provider's burndown pools in exactly the selection and order of
    /// `visiblePools`, so a provider's charts line up one-for-one with the
    /// progress bars above them. Pools the host hid from the rings get no
    /// chart, and a pool with no history yet simply drops out.
    ///
    /// - Parameter burndown: the provider's slice of `snapshot.burndown`,
    ///   keyed by pool id.
    func orderedBurndown(from burndown: [String: Burndown]?) -> [Burndown] {
        let byPool = burndown ?? [:]
        guard !(pools ?? [:]).isEmpty else {
            return byPool.values.sorted {
                let lhs = Self.poolPrecedence.firstIndex(of: $0.pool ?? "")
                    ?? Self.poolPrecedence.count
                let rhs = Self.poolPrecedence.firstIndex(of: $1.pool ?? "")
                    ?? Self.poolPrecedence.count
                if lhs != rhs { return lhs < rhs }
                return ($0.pool ?? "") < ($1.pool ?? "")
            }
        }
        return visiblePools.compactMap { byPool[$0.id] }
    }
}

struct QuotaPoolInfo: Decodable, Sendable {
    var title: String?
    /// Position in the host's declared pool order. Nil from hosts older than
    /// the field, which is why `poolPrecedence` survives as the fallback.
    var rank: Int?
    var pct: Double?
    var pacePct: Double?
    var windowS: Double?
    var resetsInS: Double?
    var resetsIn: String?
    var ring: Bool?

    enum CodingKeys: String, CodingKey {
        case title, rank, pct, ring
        case pacePct = "pace_pct"
        case windowS = "window_s"
        case resetsInS = "resets_in_s"
        case resetsIn = "resets_in"
    }
}

struct CodexUsage: Decodable, Sendable {
    var ok: Bool?
    var plan: String?
    var error: String?
    var sessionPct: Double?
    var sessionPacePct: Double?
    var sessionResetsIn: String?
    var weekPct: Double?
    var weekPacePct: Double?
    var weekResetsIn: String?
    var paceLabel: String?
    var runsOutIn: String?
    var resetCreditsAvailable: Int?
    var resetCreditsExpiries: [String]?
    /// Exact expiry instants for banked reset credits. Countdown strings are
    /// still used on meters; dates let the overview place an expiry in time.
    var resetCreditsExpireAt: [Double]?
    var costUSD: Double?
    var costLimitUSD: Double?
    var costLabel: String?
    var costReached: Bool?

    /// Matches the ESP32 line: "N reset credits". Nil when the host omitted the field.
    var resetCreditsLabel: String? {
        guard let available = resetCreditsAvailable else { return nil }
        return "\(available) reset credits"
    }

    /// Expiry countdowns joined the same way the board does (" · ").
    var resetCreditsExpiryLabel: String? {
        let parts = (resetCreditsExpiries ?? []).filter { !$0.isEmpty }
        guard !parts.isEmpty else { return nil }
        return parts.joined(separator: " · ")
    }

    enum CodingKeys: String, CodingKey {
        case ok, plan, error
        case sessionPct = "session_pct"
        case sessionPacePct = "session_pace_pct"
        case sessionResetsIn = "session_resets_in"
        case weekPct = "week_pct"
        case weekPacePct = "week_pace_pct"
        case weekResetsIn = "week_resets_in"
        case paceLabel = "pace_label"
        case runsOutIn = "runs_out_in"
        case resetCreditsAvailable = "reset_credits_available"
        case resetCreditsExpiries = "reset_credits_expiries"
        case resetCreditsExpireAt = "reset_credits_expire_at"
        case costUSD = "cost_usd"
        case costLimitUSD = "cost_limit_usd"
        case costLabel = "cost_label"
        case costReached = "cost_reached"
    }
}

struct CursorUsage: Decodable, Sendable {
    var ok: Bool?
    var plan: String?
    var totalPct: Double?
    var totalPacePct: Double?
    var autoPct: Double?
    var autoPacePct: Double?
    var apiPct: Double?
    var apiPacePct: Double?
    var resetsIn: String?
    var paceLabel: String?
    var costUSD: Double?
    var costLimitUSD: Double?
    var costLabel: String?
    var onDemandLabel: String?
    var onDemandRemainingUSD: Double?
    var onDemandLimitUSD: Double?
    var onDemandUsedUSD: Double?

    enum CodingKeys: String, CodingKey {
        case ok, plan
        case totalPct = "total_pct"
        case totalPacePct = "total_pace_pct"
        case autoPct = "auto_pct"
        case autoPacePct = "auto_pace_pct"
        case apiPct = "api_pct"
        case apiPacePct = "api_pace_pct"
        case resetsIn = "resets_in"
        case paceLabel = "pace_label"
        case costUSD = "cost_usd"
        case costLimitUSD = "cost_limit_usd"
        case costLabel = "cost_label"
        case onDemandLabel = "on_demand_label"
        case onDemandRemainingUSD = "on_demand_remaining_usd"
        case onDemandLimitUSD = "on_demand_limit_usd"
        case onDemandUsedUSD = "on_demand_used_usd"
    }
}

struct Attention: Decodable, Sendable {
    var level: String?
    var score: Int?
    var summary: String?
    var reasons: [AttentionReason]?
    var acknowledged: Bool?

    var isWarning: Bool {
        switch level {
        case "warn", "critical": true
        default: false
        }
    }

    var isCritical: Bool {
        level == "critical"
    }

    /// Stable identity for acknowledge-until-new. Changes when reasons change.
    var fingerprint: String {
        let parts = (reasons ?? []).map(\.id).sorted()
        return parts.isEmpty ? (level ?? "ok") : parts.joined(separator: "\n")
    }
}

struct AttentionReason: Decodable, Sendable, Identifiable {
    var level: String?
    var kind: String?
    var summary: String?

    var id: String {
        [level, kind, summary].compactMap { $0 }.joined(separator: "|")
    }
}

/// Persists a cleared attention fingerprint so the menu-bar pip stays off
/// until a different (new) attention set appears.
enum AttentionAck {
    static let defaultsKey = "dismissedAttentionFingerprint"

    static var dismissedFingerprint: String? {
        get { UserDefaults.standard.string(forKey: defaultsKey) }
        set {
            if let newValue {
                UserDefaults.standard.set(newValue, forKey: defaultsKey)
            } else {
                UserDefaults.standard.removeObject(forKey: defaultsKey)
            }
        }
    }

    static func acknowledge(_ attention: Attention) {
        dismissedFingerprint = attention.fingerprint
    }

    static func shouldShowPip(
        for attention: Attention?,
        dismissedFingerprint: String? = AttentionAck.dismissedFingerprint
    ) -> Bool {
        guard let attention, attention.isWarning else { return false }
        return attention.fingerprint != dismissedFingerprint
    }
}

struct VercelUsage: Decodable, Sendable {
    var ok: Bool?
    var team: String?
    var error: String?
    var stale: Bool?
    var deployments: [Deployment]?
}

struct Deployment: Decodable, Identifiable, Sendable {
    var deploymentID: String?
    var project: String?
    var state: String?
    var status: String?
    var target: String?
    var ago: String?
    var branch: String?
    var sha: String?
    var shortSHA: String?
    var repo: String?
    var commitMessage: String?
    var errorMessage: String?
    var inspectorURL: String?
    var url: String?

    var id: String {
        deploymentID ?? [project, branch, ago, url]
            .compactMap { $0 }.joined(separator: "|")
    }

    enum CodingKeys: String, CodingKey {
        case project, state, status, target, ago, branch, sha, repo, url
        case deploymentID = "id"
        case shortSHA = "short_sha"
        case commitMessage = "commit_message"
        case errorMessage = "error_message"
        case inspectorURL = "inspector_url"
    }
}

struct GitUsage: Decodable, Sendable {
    var ok: Bool?
    var error: String?
    var stale: Bool?
    var commits: [Commit]?
}

struct GitHubUsage: Decodable, Sendable {
    var ok: Bool?
    var configured: Bool?
    var error: String?
    var stale: Bool?
    var failCount: Int?
    var runningCount: Int?
    var runs: [GitHubRun]?
    var repos: [String]?

    enum CodingKeys: String, CodingKey {
        case ok, configured, error, stale, runs, repos
        case failCount = "fail_count"
        case runningCount = "running_count"
    }
}

struct GitHubRun: Decodable, Identifiable, Sendable {
    var id: String
    var repo: String?
    var name: String?
    var displayTitle: String?
    var status: String?
    var conclusion: String?
    var branch: String?
    var sha: String?
    var shortSHA: String?
    var url: String?
    var ago: String?

    enum CodingKeys: String, CodingKey {
        case id, repo, name, status, conclusion, branch, sha, url, ago
        case displayTitle = "display_title"
        case shortSHA = "short_sha"
    }
}

struct Commit: Decodable, Identifiable, Sendable {
    var repo: String?
    var subject: String?
    var ago: String?
    var branch: String?
    var sha: String?
    var shortSHA: String?
    var pushed: Bool?
    var path: String?
    var repoURL: String?

    var id: String {
        sha ?? [repo, subject, ago].compactMap { $0 }.joined(separator: "|")
    }

    enum CodingKeys: String, CodingKey {
        case repo, subject, ago, branch, sha, pushed, path
        case shortSHA = "short_sha"
        case repoURL = "repo_url"
    }
}

struct ActivityItem: Decodable, Identifiable, Sendable {
    var id: String
    var kind: String?
    var status: String?
    var subject: String?
    var repo: String?
    var project: String?
    var branch: String?
    var sha: String?
    var shortSHA: String?
    var target: String?
    var ago: String?
    var errorMessage: String?
    var url: String?
    var inspectorURL: String?

    enum CodingKeys: String, CodingKey {
        case id, kind, status, subject, repo, project, branch, sha, target, ago, url
        case shortSHA = "short_sha"
        case errorMessage = "error_message"
        case inspectorURL = "inspector_url"
    }
}

struct SupabaseUsage: Decodable, Sendable {
    var ok: Bool?
    var configured: Bool?
    var error: String?
    var stale: Bool?
    var projects: [SupabaseProject]?
    var projectCount: Int?
    var healthyCount: Int?
    var alertCount: Int?
    /// Portfolio-wide advisor totals. Health and lints are separate signals:
    /// `alertCount` is "something is down", these are "something is unsafe".
    var lintErrorCount: Int?
    var lintWarnCount: Int?
    var lintTotal: Int?

    enum CodingKeys: String, CodingKey {
        case ok, configured, error, projects, stale
        case projectCount = "project_count"
        case healthyCount = "healthy_count"
        case alertCount = "alert_count"
        case lintErrorCount = "lint_error_count"
        case lintWarnCount = "lint_warn_count"
        case lintTotal = "lint_total"
    }
}

struct SyncSource: Decodable, Identifiable, Sendable {
    var id: String
    var title: String?
    /// User-defined name for an extra login. Nil on the default provider row.
    /// Settings keeps the full `title` ("Claude · Work"); surfaces that draw
    /// a brand mark use this instead — see `QuotaProviderInfo.markTitle`.
    var label: String?
    var hint: String?
    /// "quota" or "activity" — from the host registry.
    var kind: String?
    /// "ai" or "devtools" — which Settings section this row belongs to.
    var group: String?
    /// Brand accent `#RRGGBB` — the Settings override when one is set,
    /// otherwise the registry's. Nil for rows with no brand.
    var accent: String?
    /// The registry's own color, so the picker can offer "Default" and tell
    /// an overridden row from a shipped one.
    var accentDefault: String?
    var enabled: Bool?
    /// Settings' Library vs Active membership. Off-but-not-dismissed is
    /// paused: the row stays in Active, dimmed, and nothing polls it. Nil
    /// from hosts predating the flag — read through `isDismissed`, which
    /// falls back to the old rule (off meant Library).
    var dismissed: Bool?
    var ok: Bool?
    var stale: Bool?
    /// This row's credential needs re-authenticating. Settings sorts on it and
    /// says so in words: a row that reads "not connected" invites you to check
    /// a network, which is the wrong half of the problem.
    var authRequired: Bool?
    var configured: Bool?
    var error: String?
    var detail: String?
    var ageS: Int?

    var needsSignIn: Bool { authRequired == true }

    var isDismissed: Bool { dismissed ?? !(enabled ?? true) }

    enum CodingKeys: String, CodingKey {
        case id, title, label, hint, kind, group, accent, enabled, ok, stale
        case configured, error, detail, dismissed
        case authRequired = "auth_required"
        case accentDefault = "accent_default"
        case ageS = "age_s"
    }

    var sourceGroup: SourceGroup { SourceGroup(group: group, kind: kind) }
}

/// AI coding tools vs. dev tools: two different jobs, so onboarding and
/// Settings list them apart instead of one undifferentiated pile of toggles.
///
/// Membership comes from the host registry (`sources[].group`); titles are
/// chrome and live in `HeadroomCopy`.
enum SourceGroup: String, CaseIterable, Sendable {
    case ai
    case devtools

    /// Hosts predating `group` only sent `kind`, where quota == a coding tool.
    init(group: String?, kind: String?) {
        if let group, let known = SourceGroup(rawValue: group) {
            self = known
        } else {
            self = kind == "quota" ? .ai : .devtools
        }
    }

    var title: String {
        switch self {
        case .ai: return HeadroomCopy.aiTools
        case .devtools: return HeadroomCopy.devTools
        }
    }

    var subtitle: String {
        switch self {
        case .ai: return HeadroomCopy.aiToolsHint
        case .devtools: return HeadroomCopy.devToolsHint
        }
    }
}

extension Array where Element == SyncSource {
    /// Rows split into `SourceGroup` order, dropping groups with no rows.
    /// Registry order is preserved inside each group.
    func groupedBySourceGroup() -> [(group: SourceGroup, sources: [SyncSource])] {
        SourceGroup.allCases.compactMap { group in
            let rows = filter { $0.sourceGroup == group }
            return rows.isEmpty ? nil : (group, rows)
        }
    }
}

struct SupabaseProject: Decodable, Identifiable, Sendable {
    var ref: String
    var name: String?
    var organizationID: String?
    var region: String?
    var status: String?
    var healthy: Bool?
    var services: [SupabaseService]?
    var unhealthyServices: [String]?
    var healthError: String?
    var dashboardURL: String?
    /// Security advisor findings, ERROR first. Capped host-side; `lintTotal`
    /// is the real count.
    var lints: [SupabaseLint]?
    var lintTruncated: Bool?
    var lintErrorCount: Int?
    var lintWarnCount: Int?
    var lintInfoCount: Int?
    var lintTotal: Int?
    /// Set when the advisors endpoint failed; health is still trustworthy.
    var advisorError: String?

    var id: String { ref }

    /// Deep link to the project's advisor page, where these get fixed.
    var advisorsURL: String {
        "https://supabase.com/dashboard/project/\(ref)/advisors/security"
    }

    enum CodingKeys: String, CodingKey {
        case ref, name, region, status, healthy, services, lints
        case organizationID = "organization_id"
        case unhealthyServices = "unhealthy_services"
        case healthError = "health_error"
        case dashboardURL = "dashboard_url"
        case lintTruncated = "lint_truncated"
        case lintErrorCount = "lint_error_count"
        case lintWarnCount = "lint_warn_count"
        case lintInfoCount = "lint_info_count"
        case lintTotal = "lint_total"
        case advisorError = "advisor_error"
    }
}

/// One security advisor finding — `rls_disabled_in_public` and friends.
struct SupabaseLint: Decodable, Identifiable, Sendable {
    var name: String
    var title: String?
    /// "ERROR", "WARN", or "INFO". Unknown severities arrive as "WARN".
    var level: String?
    var categories: [String]?
    var description: String?
    var detail: String?
    /// Docs URL for the fix, when Supabase supplies one.
    var remediation: String?
    /// The table or view the finding is about, e.g. "public.posts".
    var entity: String?

    var id: String { "\(name)|\(entity ?? "")" }

    var isError: Bool { (level ?? "").uppercased() == "ERROR" }
}

struct SupabaseService: Decodable, Identifiable, Sendable {
    var name: String
    var status: String?
    var healthy: Bool?

    var id: String { name }
}

struct PlausibleUsage: Decodable, Sendable {
    var ok: Bool?
    var configured: Bool?
    var error: String?
    var stale: Bool?
    var sites: [PlausibleSite]?
    var siteCount: Int?
    var visitorsToday: Int?
    var realtime: Int?
    /// Primary window id from the host (`day`, `24h`, `7d`, `30d`).
    var range: String?
    var rangeLabel: String?

    var windowLabel: String {
        rangeLabel ?? range ?? "today"
    }

    enum CodingKeys: String, CodingKey {
        case ok, configured, error, sites, stale, realtime, range
        case siteCount = "site_count"
        case visitorsToday = "visitors_today"
        case rangeLabel = "range_label"
    }
}

/// status.claude.com rollup — Attention reads `alerting`; Settings uses sources[].
struct ClaudeStatus: Decodable, Sendable {
    var ok: Bool?
    var configured: Bool?
    var error: String?
    var stale: Bool?
    var indicator: String?
    var description: String?
    var alerting: Bool?
    var incidentName: String?
    var incidentImpact: String?
    var url: String?
    var updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case ok, configured, error, stale, indicator, description, alerting, url
        case incidentName = "incident_name"
        case incidentImpact = "incident_impact"
        case updatedAt = "updated_at"
    }
}

struct PlausibleSite: Decodable, Identifiable, Sendable {
    var domain: String
    var visitorsToday: Int?
    var pageviewsToday: Int?
    var visitors7d: Int?
    var pageviews7d: Int?
    var bounceRate7d: Double?
    var visitDuration7d: Int?
    var realtime: Int?
    var dashboardURL: String?
    var error: String?
    var range: String?
    var rangeLabel: String?

    var id: String { domain }

    var windowLabel: String {
        rangeLabel ?? range ?? "today"
    }

    enum CodingKeys: String, CodingKey {
        case domain, realtime, error, range
        case visitorsToday = "visitors_today"
        case pageviewsToday = "pageviews_today"
        case visitors7d = "visitors_7d"
        case pageviews7d = "pageviews_7d"
        case bounceRate7d = "bounce_rate_7d"
        case visitDuration7d = "visit_duration_7d"
        case dashboardURL = "dashboard_url"
        case rangeLabel = "range_label"
    }
}

struct LocalUsage: Decodable, Sendable {
    var ok: Bool?
    var host: String?
    var error: String?
    var stale: Bool?
    var servers: [LocalServer]?
}

struct LocalServer: Decodable, Identifiable, Sendable {
    var name: String?
    var port: Int?
    var pid: Int?
    var cmd: String?
    var cwd: String?
    var bind: String?
    var reachable: Bool?
    var latencyMS: Int?

    var id: String { "\(name ?? "server"):\(port ?? 0)" }

    enum CodingKeys: String, CodingKey {
        case name, port, pid, cmd, cwd, bind, reachable
        case latencyMS = "latency_ms"
    }
}

/// One Mac signed into the same shared folder.
///
/// A summary, not a slice of that Mac's document: what it is burning, whether
/// something is waiting on you there, and how long ago it said so. Nothing
/// here is merged with the local numbers — two Macs are allowed to disagree,
/// and each row carries its own age so a reader can tell which is which.
struct MachineSummary: Decodable, Identifiable, Sendable {
    var id: String?
    var name: String?
    var isSelf: Bool?
    var ageS: Int?
    var stale: Bool?
    var hostVersion: String?
    var providers: [MachineProvider]?
    var servers: Int?
    var attentionOpen: Int?
    var attentionTop: String?
    /// Coding agents on that Mac waiting for an answer.
    var agent: Int?
    /// True on the one Mac with the ESP32 on its desk.
    var board: Bool?

    enum CodingKeys: String, CodingKey {
        case id, name, stale, providers, servers, agent, board
        case isSelf = "self"
        case ageS = "age_s"
        case hostVersion = "host_version"
        case attentionOpen = "attention_open"
        case attentionTop = "attention_top"
    }

    var title: String { name ?? "Mac" }
    var isCurrent: Bool { isSelf == true }
    var needsYou: Bool { (agent ?? 0) > 0 || (attentionOpen ?? 0) > 0 }

    /// "just now" / "4m ago" / "2h ago" / "3d ago".
    ///
    /// Coarse on purpose. The question a reader has is whether the other Mac
    /// is awake, and to the minute is already more precision than that needs.
    var lastSeenLabel: String {
        let seconds = ageS ?? 0
        if isCurrent || seconds < 90 { return "just now" }
        if seconds < 3600 { return "\(seconds / 60)m ago" }
        if seconds < 86_400 { return "\(seconds / 3600)h ago" }
        return "\(seconds / 86_400)d ago"
    }

    /// What that Mac is doing, in one line, or nil when there is nothing to say.
    var activityLabel: String? {
        var parts: [String] = []
        if let agent, agent > 0 {
            parts.append("\(agent) waiting")
        }
        if let servers, servers > 0 {
            parts.append("\(servers) server" + (servers == 1 ? "" : "s"))
        }
        if let top = attentionTop, !top.isEmpty, (agent ?? 0) == 0 {
            parts.append(top)
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// A provider meter as another Mac reported it.
struct MachineProvider: Decodable, Identifiable, Sendable {
    var id: String?
    var title: String?
    var pct: Double?
    var accent: String?
}

extension UsageSnapshot {
    /// Other Macs, freshest first. Empty when sync is off or this is the only one.
    var peerMachines: [MachineSummary] {
        (machines ?? []).filter { !$0.isCurrent }
    }

    /// This Mac's own row, when the host published one.
    var currentMachine: MachineSummary? {
        (machines ?? []).first { $0.isCurrent }
    }
}

extension Double {
    /// Always whole dollars with `$`, never cents or locale currency codes.
    var dollarLabel: String {
        String(format: "$%.0f", rounded())
    }
}

enum MobilePermission: String, CaseIterable, Codable, Sendable {
    case read
    case refresh
    case sources
    case servers
    case agents

    var title: String {
        switch self {
        case .read: "Read dashboard"
        case .refresh: "Refresh data"
        case .sources: "Manage sources"
        case .servers: "Stop local servers"
        case .agents: HeadroomCopy.answerCodingAgents
        }
    }
}

struct MobilePermissions: Codable, Sendable, Equatable {
    var read = false
    var refresh = false
    var sources = false
    var servers = false
    var agents = false

    static let allEnabled = MobilePermissions(
        read: true, refresh: true, sources: true, servers: true, agents: true)
    static let allDisabled = MobilePermissions()

    init(
        read: Bool = false,
        refresh: Bool = false,
        sources: Bool = false,
        servers: Bool = false,
        agents: Bool = false
    ) {
        self.read = read
        self.refresh = refresh
        self.sources = sources
        self.servers = servers
        self.agents = agents
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        read = try values.decodeIfPresent(Bool.self, forKey: .read) ?? false
        refresh = try values.decodeIfPresent(Bool.self, forKey: .refresh) ?? false
        sources = try values.decodeIfPresent(Bool.self, forKey: .sources) ?? false
        servers = try values.decodeIfPresent(Bool.self, forKey: .servers) ?? false
        agents = try values.decodeIfPresent(Bool.self, forKey: .agents) ?? false
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(read, forKey: .read)
        try values.encode(refresh, forKey: .refresh)
        try values.encode(sources, forKey: .sources)
        try values.encode(servers, forKey: .servers)
        try values.encode(agents, forKey: .agents)
    }

    private enum CodingKeys: String, CodingKey {
        case read, refresh, sources, servers, agents
    }

    subscript(_ permission: MobilePermission) -> Bool {
        get {
            switch permission {
            case .read: read
            case .refresh: refresh
            case .sources: sources
            case .servers: servers
            case .agents: agents
            }
        }
        set {
            switch permission {
            case .read: read = newValue
            case .refresh: refresh = newValue
            case .sources: sources = newValue
            case .servers: servers = newValue
            case .agents: agents = newValue
            }
        }
    }

    var dictionary: [String: Bool] {
        Dictionary(uniqueKeysWithValues: MobilePermission.allCases.map {
            ($0.rawValue, self[$0])
        })
    }
}

struct MobilePermissionsResponse: Codable, Sendable {
    var ok: Bool
    var permissions: MobilePermissions
}

struct AgentAttentionAction: Codable, Sendable, Equatable, Identifiable {
    var id: String
    var label: String
    var risk: String
    /// Why you would pick this one. Named `subtitle` rather than
    /// `description` because that name already means something else on every
    /// Swift type; the wire key stays the provider's.
    var subtitle: String?
    /// This answer is carried by words you type, not by the button alone.
    var acceptsText: Bool?
    var requiresForeground: Bool?
    var requiresBiometric: Bool?

    enum CodingKeys: String, CodingKey {
        case id, label, risk
        case subtitle = "description"
        case acceptsText = "accepts_text"
        case requiresForeground = "requires_foreground"
        case requiresBiometric = "requires_biometric"
    }
}

/// One field of the agent's actual request, as the provider sent it.
///
/// The host decides ordering, labelling and bounds (`host/agent_request.py`);
/// a client only decides how to draw a `kind` it recognises. A kind it does
/// not recognise still renders as text, so a new tool never needs an app
/// update to be readable.
struct AgentRequestField: Codable, Sendable, Equatable, Identifiable {
    var key: String
    var label: String
    var kind: String
    var value: String
    var truncated: Bool?
    var fullChars: Int?
    var omittedFields: Int?

    var id: String { key }

    /// True when the host clipped this value, so the row can say so rather
    /// than silently showing a prefix of a command you are approving.
    var wasTruncated: Bool { truncated == true }

    enum CodingKeys: String, CodingKey {
        case key, label, kind, value, truncated
        case fullChars = "full_chars"
        case omittedFields = "omitted_fields"
    }
}

struct AgentAttentionDetail: Codable, Sendable, Equatable {
    var toolName: String?
    var request: [AgentRequestField]?
    var reasons: [String]?
    var reason: String?
    var command: String?
    var cwd: String?
    var grantRoot: String?
    var permissionMode: String?
    var transcriptPath: String?
    /// The exact rule an `approve_always` answer would save. Shown before the
    /// tap, because a durable grant made from a phone should never be blind.
    var permissionRule: String?

    /// Codex still sends a bare `command`; Claude sends structured fields.
    /// One accessor so views never branch on which provider they came from.
    var requestFields: [AgentRequestField] {
        if let request, !request.isEmpty { return request }
        guard let command, !command.isEmpty else { return [] }
        return [AgentRequestField(
            key: "command", label: "Command", kind: "command", value: command
        )]
    }

    enum CodingKeys: String, CodingKey {
        case request, reasons, reason, command, cwd
        case toolName = "tool_name"
        case grantRoot = "grant_root"
        case permissionMode = "permission_mode"
        case transcriptPath = "transcript_path"
        case permissionRule = "permission_rule"
    }
}

struct AgentAttentionEvent: Codable, Sendable, Equatable, Identifiable {
    var id: String
    var provider: String
    var adapter: String
    var sessionID: String
    var turnID: String?
    var itemID: String?
    var kind: String
    var state: String
    var revision: Int
    var title: String
    var summary: String
    var detail: AgentAttentionDetail
    var actions: [AgentAttentionAction]
    var createdAtMS: Int64
    var updatedAtMS: Int64
    var expiresAtMS: Int64?

    /// How long the agent has been waiting. A request that has sat for six
    /// minutes reads very differently from one that just arrived — and a
    /// permission hook gives up at ~285s, so the age is also how close this
    /// one is to answering itself.
    var age: TimeInterval {
        max(0, Date().timeIntervalSince1970 - Double(createdAtMS) / 1000)
    }

    /// Gateway providers name the adapter surface (`claude-code`); the icon
    /// and palette registries are keyed by the tool (`claude`). Mapped once
    /// here so a new adapter does not need a second copy in every view.
    var providerIconID: String {
        provider == "claude-code" ? "claude" : provider
    }

    /// A row nobody can answer — a finished/idle notice — only offers
    /// dismissal, and that is what makes it safe to swipe away.
    var isDismissOnly: Bool {
        !actions.isEmpty && actions.allSatisfy { $0.id == "dismiss" }
    }

    enum CodingKeys: String, CodingKey {
        case id, provider, adapter, kind, state, revision, title, summary
        case detail, actions
        case sessionID = "session_id"
        case turnID = "turn_id"
        case itemID = "item_id"
        case createdAtMS = "created_at_ms"
        case updatedAtMS = "updated_at_ms"
        case expiresAtMS = "expires_at_ms"
    }
}

struct AgentAttentionEventsResponse: Codable, Sendable {
    var ok: Bool
    var events: [AgentAttentionEvent]
    var nextAfterMS: Int64?

    enum CodingKeys: String, CodingKey {
        case ok, events
        case nextAfterMS = "next_after_ms"
    }
}

struct AgentAttentionResponseRequest: Codable, Sendable {
    var revision: Int
    var action: String
    var idempotencyKey: String
    /// Words typed on the phone. The adapter decides what they mean — a reply
    /// to a permission request is Claude's "tell it what to do differently",
    /// and a reply to a question is the answer itself.
    var text: String?

    enum CodingKeys: String, CodingKey {
        case revision, action, text
        case idempotencyKey = "idempotency_key"
    }
}

struct AgentAttentionResponse: Codable, Sendable {
    var ok: Bool
    var duplicate: Bool
    var event: AgentAttentionEvent
}
