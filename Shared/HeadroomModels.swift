import Foundation

// `Failable` and `decodeLossyArrayIfPresent` live in Shared/LossyDecode.swift
// so the widget and watch targets — which deliberately do not compile this
// file — can decode their own cache the same tolerant way.

struct UsageSnapshot: Decodable, Sendable {
    var updated: String?
    /// Shape of the document this host speaks, compared against
    /// ``UsageSnapshot/minimumContract``. Absent from any host older than the
    /// release that introduced it, which is itself the answer: nil means old.
    var contract: Int?
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
    /// Long-range Claude token history the host aggregates from session logs.
    /// Emitted since 1.0 and decoded by nobody until the Spend card; a host
    /// too old to send it leaves this nil and the card does not draw.
    var history: UsageHistory?
    /// Mixed-source daily activity: Claude session evidence plus daily burn
    /// evidence from every quota source. Empty days are absent from `days`;
    /// `levels` is the compact full-window series for the board.
    var activityHistory: ActivityHistory?
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
    var posthog: PostHogUsage?
    var sentry: SentryUsage?
    var datadog: DatadogUsage?
    var axiom: AxiomUsage?
    var claudeStatus: ClaudeStatus?
    var sources: [SyncSource]?
    var attention: Attention?
    /// Provider ids the compact surfaces show, picked host-side so the menu
    /// bar, the widget, and the board never disagree about which three.
    var focus: [String]?
    /// Activity service panel order (Supabase / local servers / …).
    var servicesOrder: [String]?
    /// Integrations catalog order (Settings list + Activity blocks).
    var integrationsOrder: [String]?
    /// Per-provider, per-pool burndown keyed as ["claude": ["week": …]].
    var burndown: [String: [String: Burndown]]?
    var burndownPrimary: Burndown?
    /// Every Mac signed into the same shared folder, this one first. Always at
    /// least one row, so a single-Mac install has the same shape as a synced
    /// one and no surface needs a special case for "sync is off".
    var machines: [MachineSummary]?

    static let empty = UsageSnapshot()

    /// Oldest `/usage` shape this build can draw correctly.
    ///
    /// Raise this only alongside a `host_version.CONTRACT` bump, and only when
    /// this build would show something *wrong or empty* against the older
    /// shape. Raising it costs every user on an older host their data until
    /// they update the Mac, so the bar is "they would be misled", not "they
    /// would miss out". See docs/contract.md.
    static let minimumContract = 1

    /// Whether the host that produced this snapshot is new enough to trust.
    ///
    /// A host predating the field reports nil. That is treated as satisfied,
    /// not as failure: those hosts speak contract 1 by definition, and the
    /// alternative would blank a working desk the day this shipped.
    var contractSatisfied: Bool {
        (contract ?? Self.minimumContract) >= Self.minimumContract
    }

    init(
        updated: String? = nil,
        contract: Int? = nil,
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
        activityHistory: ActivityHistory? = nil,
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
        posthog: PostHogUsage? = nil,
        sentry: SentryUsage? = nil,
        datadog: DatadogUsage? = nil,
        axiom: AxiomUsage? = nil,
        claudeStatus: ClaudeStatus? = nil,
        sources: [SyncSource]? = nil,
        attention: Attention? = nil,
        focus: [String]? = nil,
        servicesOrder: [String]? = nil,
        integrationsOrder: [String]? = nil,
        burndown: [String: [String: Burndown]]? = nil,
        burndownPrimary: Burndown? = nil,
        machines: [MachineSummary]? = nil
    ) {
        self.updated = updated
        self.contract = contract
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
        self.activityHistory = activityHistory
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
        self.posthog = posthog
        self.sentry = sentry
        self.datadog = datadog
        self.axiom = axiom
        self.claudeStatus = claudeStatus
        self.sources = sources
        self.attention = attention
        self.focus = focus
        self.servicesOrder = servicesOrder
        self.integrationsOrder = integrationsOrder
        self.burndown = burndown
        self.burndownPrimary = burndownPrimary
        self.machines = machines
    }

    enum CodingKeys: String, CodingKey {
        case updated, contract, plan, today, codex, cursor, providers, vercel, git, github, activity, local
        case supabase, plausible, posthog, sentry, datadog, axiom, sources, attention, focus, burndown, machines
        case servicesOrder = "services_order"
        case integrationsOrder = "integrations_order"
        case claudeStatus = "claude_status"
        case burndownPrimary = "burndown_primary"
        case byDay = "by_day"
        case history
        case activityHistory = "activity_history"
        case quotaOK = "quota_ok"
        case quotaError = "quota_error"
        case sessionPct = "session_pct"
        case sessionPacePct = "session_pace_pct"
        case sessionResetsIn = "session_resets_in"
        case weekPct = "week_pct"
        case weekPacePct = "week_pace_pct"
        case weekResetsIn = "week_resets_in"
    }

    /// Hand-written so the list-valued fields can decode lossily. Everything
    /// else is what the compiler would have synthesized.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        func value<T: Decodable>(_ key: CodingKeys) throws -> T? {
            try container.decodeIfPresent(T.self, forKey: key)
        }

        updated = try value(.updated)
        contract = try value(.contract)
        plan = try value(.plan)
        quotaOK = try value(.quotaOK)
        quotaError = try value(.quotaError)
        sessionPct = try value(.sessionPct)
        sessionPacePct = try value(.sessionPacePct)
        sessionResetsIn = try value(.sessionResetsIn)
        weekPct = try value(.weekPct)
        weekPacePct = try value(.weekPacePct)
        weekResetsIn = try value(.weekResetsIn)
        today = try value(.today)
        history = try value(.history)
        activityHistory = try value(.activityHistory)
        codex = try value(.codex)
        cursor = try value(.cursor)
        vercel = try value(.vercel)
        git = try value(.git)
        github = try value(.github)
        local = try value(.local)
        supabase = try value(.supabase)
        plausible = try value(.plausible)
        posthog = try value(.posthog)
        sentry = try value(.sentry)
        datadog = try value(.datadog)
        axiom = try value(.axiom)
        claudeStatus = try value(.claudeStatus)
        attention = try value(.attention)
        focus = try value(.focus)
        servicesOrder = try value(.servicesOrder)
        integrationsOrder = try value(.integrationsOrder)
        burndown = try value(.burndown)
        burndownPrimary = try value(.burndownPrimary)

        byDay = try container.decodeLossyArrayIfPresent(
            DailyBurnDay.self, forKey: .byDay)
        providers = try container.decodeLossyArrayIfPresent(
            QuotaProviderInfo.self, forKey: .providers)
        activity = try container.decodeLossyArrayIfPresent(
            ActivityItem.self, forKey: .activity)
        sources = try container.decodeLossyArrayIfPresent(
            SyncSource.self, forKey: .sources)
        machines = try container.decodeLossyArrayIfPresent(
            MachineSummary.self, forKey: .machines)
    }

    /// Enabled coding-quota providers from the host registry (string ids).
    ///
    /// CodexBar-style: Settings → Sources is the subset. Prefer `providers[]`
    /// intersected with enabled quota `sources[]`. Empty when the host has not
    /// advertised any — never invent Claude/Codex/Cursor.
    ///
    /// Prepaid balances (OpenRouter, AI Gateway) stay in `providers[]` for
    /// their Activity leaf but are not coding quotas — see
    /// `codingQuotaProviders` / `balanceProviders`.
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
                email: row.email,
                kind: row.kind ?? "quota",
                enabled: true
            )
        }
    }

    /// Window / grant meters that belong on Usage rings and the menu-bar tanks.
    /// Excludes prepaid balances — those paint under Activity.
    var codingQuotaProviders: [QuotaProviderInfo] {
        visibleQuotaProviders.filter { !$0.isBalanceOnly }
    }

    /// OpenRouter / AI Gateway — account-use panels on Activity, not Usage.
    var balanceProviders: [QuotaProviderInfo] {
        visibleQuotaProviders.filter(\.isBalanceOnly)
    }

    /// The providers a compact surface shows: menu-bar tanks and the iOS
    /// widget.
    ///
    /// The host picks them (pinned order, enabled only) and ships the ids in
    /// `focus`, so every surface shows the same providers even when one of
    /// them is a poll behind. Falls back to the first `limit` visible
    /// providers when talking to a host that predates the field.
    /// Balance-only ids in `focus` are skipped — they are not tanks.
    func focusProviders(limit: Int = 3) -> [QuotaProviderInfo] {
        let visible = codingQuotaProviders
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

    /// Known-enum view of `codingQuotaProviders` for Mac chrome still typed
    /// on `UsageProvider`. Unknown registry ids are skipped until those
    /// surfaces take string ids. Balance-only providers never appear here.
    var activeQuotaProviders: [UsageProvider] {
        var seen = Set<String>()
        var out: [UsageProvider] = []
        for row in codingQuotaProviders {
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
            // Longer window first — same outside-in order as the rings.
            let lw = lhs.windowS ?? 0
            let rw = rhs.windowS ?? 0
            if lw != rw { return lw > rw }
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
        var balanceLabel: String?
        var balanceLevel: Double?
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
        if let balance = info.primaryBalance {
            balanceLabel = balance.balanceRemainingLabel
            balanceLevel = balance.level
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
            balanceLabel: balanceLabel,
            balanceLevel: balanceLevel,
            spend: info.spend,
            headlinePoolID: info.headline,
            statusNote: info.statusNote,
            needsSignIn: info.needsSignIn,
            statusAlarming: info.statusAlarming,
            displayError: info.displayError
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
    ///
    /// Superseded by `history`, which covers the same ground without needing a
    /// grant to exist. Still decoded because a host older than this build sends
    /// it and nothing else.
    var forgiven: [[Double]]?
    /// Every recent reading, [[epoch, remaining], …], window boundaries and
    /// all — the sawtooth behind `actual`.
    ///
    /// `actual` stops at the live window's start, so the moment a window rolls
    /// it holds one point and draws nothing. This is the same readings
    /// unclipped, which is what keeps a reset from looking like the app
    /// forgetting the week. It climbs at every boundary, so square it off at
    /// `historyRisers` before stroking.
    var history: [[Double]]?
    /// Every instant `history` climbs — scheduled rolls as well as grants.
    ///
    /// Read it through `historyRisers`, which falls back to `resets` for a
    /// host that predates this key.
    var boundaries: [Double]?
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

    /// Where the cross-window curve stands up, for `historyPolyline`.
    ///
    /// `resets` is the fallback rather than the source: it lists only the
    /// resets a provider granted out of band, so a pool whose window merely
    /// ran out — every Claude session, most of the time — offered no cut to
    /// square against, and its climb was drawn as a diagonal across whatever
    /// two samples survived thinning. A host older than `boundaries` still
    /// gets the grants squared, which is what it always did.
    var historyRisers: [Double] {
        if let boundaries, !boundaries.isEmpty { return boundaries }
        return resets?.compactMap(\.t) ?? []
    }

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

    /// The plot domain this pool's chart draws — window, history stub, and the
    /// seven-day clip a monthly pool needs.
    ///
    /// Here rather than in `BurndownChartMath` because it reads the model, and
    /// that file stays model-free so the widget target can compile it. Every
    /// surface drawing a provider burndown goes through this: the Mac canvas,
    /// the Mac header that names the frame, and the phone. Three copies of the
    /// `now`/`historyStart` derivation is how a header ends up describing a
    /// different axis from the one underneath it.
    var chartDomain: BurndownChartAxis.Domain? {
        Self.chartDomain(pools: [self])
    }

    /// The shared domain for pools overlaid on one axis — Cursor's Total and
    /// API. The window comes from `anchor` alone; the others contribute their
    /// samples and history, which is what keeps the overlaid curves registered
    /// against one plot.
    ///
    /// Cursor's pools are one billing cycle but each holds its own reset, so
    /// spanning min-start to max-end would draw the budget diagonal across a
    /// window none of them has. `anchor` defaults to the first pool that names
    /// a window; `MultiBurndownPlot` passes Total explicitly.
    static func chartDomain(
        pools: [Burndown],
        anchor: Burndown? = nil
    ) -> BurndownChartAxis.Domain? {
        let anchored = anchor ?? pools.first {
            $0.windowStart != nil && $0.windowEnd != nil
        }
        guard let anchored,
              let start = anchored.windowStart,
              let end = anchored.windowEnd
        else { return nil }
        let now = pools
            .flatMap { ($0.actual ?? []).compactMap { $0.count >= 2 ? $0[0] : nil } }
            .max() ?? Date().timeIntervalSince1970
        return BurndownChartAxis.domain(
            windowStart: start,
            windowEnd: end,
            now: now,
            // Reaches back toward the spent windows by a stub of the window —
            // see `historyFraction`. `forgiven` is the fallback for a host
            // older than `history`.
            historyStart: pools
                .compactMap { ($0.history ?? $0.forgiven)?.first?.first }
                .min()
        )
    }

    enum CodingKeys: String, CodingKey {
        case provider, pool, status, ideal, actual, projected, samples, headline
        case exhausted, verdict, resets, forgiven, history, boundaries
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
struct BurndownReset: Decodable, Sendable, Identifiable, Equatable {
    /// When the pool came back, epoch seconds.
    var t: Double?
    /// "granted" today. Present so scheduled rolls could join later without
    /// changing the shape.
    var kind: String?
    /// Percentage points the grant handed back. Nil for announcement-only
    /// rows that this Mac never observed in the sample log.
    var forgivenPct: Double?
    /// `observed` (local sample), `announced` (codex-resets.com), or `both`.
    var source: String?
    /// Permalink on the announcement, when the grant matched a public reset.
    var tweetURL: String?
    var tweetID: String?

    var id: Double { t ?? 0 }

    var date: Date? { t.map { Date(timeIntervalSince1970: $0) } }

    var announcementURL: URL? {
        tweetURL.flatMap(URL.init(string:))
    }

    enum CodingKeys: String, CodingKey {
        case t, kind, source
        case forgivenPct = "forgiven_pct"
        case tweetURL = "tweet_url"
        case tweetID = "tweet_id"
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
    /// Prepaid balance remaining, e.g. "$12.34 left".
    var balanceLabel: String?
    /// Fraction of the pot still there, 0…1. Nil when there is no denominator.
    var balanceLevel: Double?
    /// Observed spend leaf for prepaid balance providers (OpenRouter / AI Gateway).
    var spend: BalanceSpend?
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
    /// True when the status note is an alarm (dead login), not a quiet pause.
    var statusAlarming: Bool
    /// Error text worth drawing; nil when `statusNote` already covers it.
    var displayError: String?

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
        balanceLabel: String? = nil,
        balanceLevel: Double? = nil,
        spend: BalanceSpend? = nil,
        headlinePoolID: String? = nil,
        statusNote: String? = nil,
        needsSignIn: Bool = false,
        statusAlarming: Bool = false,
        displayError: String? = nil
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
        self.balanceLabel = balanceLabel
        self.balanceLevel = balanceLevel
        self.spend = spend
        self.headlinePoolID = headlinePoolID
        self.statusNote = statusNote
        self.needsSignIn = needsSignIn
        self.statusAlarming = statusAlarming
        // Nil means "do not draw an error" — including when the raw `error`
        // is set but already summarised by `statusNote` (rate limits). Do not
        // coalesce back to `error` here.
        self.displayError = displayError
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
            headlinePoolID: headlinePoolID,
            displayError: error
        )
    }

    private var allWindows: [MeterWindow] {
        [primary, secondary, tertiary].compactMap { $0 }
    }

    /// A weekly-only Codex account should not show the empty compatibility
    /// slot that the legacy meter uses for Session. Synthetic "—" placeholders
    /// for balance-only providers are dropped the same way.
    var displayableWindows: [MeterWindow] {
        allWindows.filter { window in
            if window.percent == nil && window.id == nil && window.title == "—" {
                return false
            }
            let baseID = id.split(separator: ":", maxSplits: 1)
                .first.map(String.init)
            guard baseID == UsageProvider.codex.rawValue,
                  window.id == "session" else { return true }
            return window.percent != nil
        }
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

/// One published subscription plan from the provider registry.
///
/// Prices are informational list prices, not what Headroom estimates from
/// local token logs and not a user's tax, regional, or employer-adjusted bill.
struct SubscriptionPlanPrice: Decodable, Identifiable, Sendable {
    /// Optional on purpose: a registry row missing its id or title must cost
    /// that row a name, not the provider that carries the catalog. Matching
    /// in `currentPrice(for:)` already skips rows with neither.
    var id: String?
    var title: String?
    var monthlyUSD: Double?
    var annualUSD: Double?
    var unit: String?
    var note: String?

    enum CodingKeys: String, CodingKey {
        case id, title, unit, note
        case monthlyUSD = "monthly_usd"
        case annualUSD = "annual_usd"
    }

    /// Short price text for compact provider rows. A plan without a numeric
    /// amount is intentionally shown as custom rather than guessed.
    var compactPrice: String {
        if let monthlyUSD {
            guard monthlyUSD != 0 else { return "Free" }
            let unitPart = unit.map { " / \($0)" } ?? ""
            let monthly = "$\(monthlyUSD.cleanCurrency)\(unitPart) / mo"
            if let annualUSD {
                return "\(monthly) · $\(annualUSD.cleanCurrency)\(unitPart) / yr"
            }
            return monthly
        }
        return note ?? "Custom"
    }
}

/// Published plan prices for one quota provider.
struct SubscriptionPricing: Decodable, Sendable {
    var currency: String?
    var checked: String?
    var url: String?
    var plans: [SubscriptionPlanPrice]?

    /// Resolve the one published price for the plan the provider reported.
    /// Exact matching is deliberate: "Team" must not silently choose between
    /// Team Standard and Team Premium.
    func currentPrice(for plan: String?) -> SubscriptionPlanPrice? {
        guard let plan else { return nil }
        let normalizedPlan = Self.normalized(plan)
        return plans?.first {
            $0.id.map(Self.normalized) == normalizedPlan
                || $0.title.map(Self.normalized) == normalizedPlan
        }
    }

    private static func normalized(_ value: String) -> String {
        value.lowercased().filter { $0.isLetter || $0.isNumber }
    }

    enum CodingKeys: String, CodingKey {
        case currency, checked, url, plans
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        currency = try container.decodeIfPresent(String.self, forKey: .currency)
        checked = try container.decodeIfPresent(String.self, forKey: .checked)
        url = try container.decodeIfPresent(String.self, forKey: .url)
        plans = try container.decodeLossyArrayIfPresent(
            SubscriptionPlanPrice.self, forKey: .plans)
    }
}

private extension Double {
    var cleanCurrency: String {
        // Whole dollars — subscription catalogs do not quote cents.
        HeadroomFormat.usd(self)
            .replacingOccurrences(of: "$", with: "")
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
    /// Signed-in email when the host can read it from local credentials
    /// (Codex id_token, Cursor cached profile). Nil when unknown — Claude's
    /// OAuth token is opaque, so it often stays blank.
    var email: String?
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
    /// Why the bars froze, when the host could classify it:
    /// `rate_limited` / `provider` / `network`. Nil on healthy rows and on
    /// hosts that only send the free-text `error`.
    var staleCause: String?
    /// Seconds until the host will ask the provider again. Set during an
    /// active rate-limit hold so the card can count down without implying
    /// anything is broken.
    var retryInS: Double?
    var plan: String?
    var error: String?
    var accent: String?
    /// The registry's own color, before any Settings override. Settings marks
    /// this swatch "Default"; everything else just paints `accent`.
    var accentDefault: String?
    /// The registry's own name, before any Settings override.
    var titleDefault: String?
    /// The same-hue shade this account would get from its provider, before
    /// any explicit account override. Absent on the default provider row.
    var accentDerived: String?
    var headline: String?
    /// Where this provider's granted resets get explained, when it explains
    /// them anywhere. A permalink the app only ever opens — nothing fetches
    /// it, so no part of your account leaves the Mac to render a reset.
    var resetNoteURL: String?
    /// Published subscription catalog for this provider. Additive and absent
    /// on hosts that predate the registry's price metadata.
    var subscriptionPricing: SubscriptionPricing?
    var pools: [String: QuotaPoolInfo]?
    /// Observed prepaid spend leaf — OpenRouter / AI Gateway. Absent on
    /// window providers and on hosts that only ship the balance pot.
    var spend: BalanceSpend?

    init(
        id: String,
        title: String? = nil,
        label: String? = nil,
        email: String? = nil,
        kind: String? = nil,
        rank: Int? = nil,
        enabled: Bool? = nil,
        ok: Bool? = nil,
        stale: Bool? = nil,
        staleForS: Double? = nil,
        authRequired: Bool? = nil,
        staleCause: String? = nil,
        retryInS: Double? = nil,
        plan: String? = nil,
        error: String? = nil,
        accent: String? = nil,
        accentDefault: String? = nil,
        titleDefault: String? = nil,
        accentDerived: String? = nil,
        headline: String? = nil,
        resetNoteURL: String? = nil,
        subscriptionPricing: SubscriptionPricing? = nil,
        pools: [String: QuotaPoolInfo]? = nil,
        spend: BalanceSpend? = nil
    ) {
        self.id = id
        self.title = title
        self.label = label
        self.email = email
        self.kind = kind
        self.rank = rank
        self.enabled = enabled
        self.ok = ok
        self.stale = stale
        self.staleForS = staleForS
        self.authRequired = authRequired
        self.staleCause = staleCause
        self.retryInS = retryInS
        self.plan = plan
        self.error = error
        self.accent = accent
        self.accentDefault = accentDefault
        self.titleDefault = titleDefault
        self.accentDerived = accentDerived
        self.headline = headline
        self.resetNoteURL = resetNoteURL
        self.subscriptionPricing = subscriptionPricing
        self.pools = pools
        self.spend = spend
    }

    enum CodingKeys: String, CodingKey {
        case id, title, label, email, kind, rank, enabled, ok, plan, error, accent, stale
        case headline, pools, spend
        case staleForS = "stale_for_s"
        case authRequired = "auth_required"
        case staleCause = "stale_cause"
        case retryInS = "retry_in_s"
        case accentDefault = "accent_default"
        case titleDefault = "title_default"
        case accentDerived = "accent_derived"
        case resetNoteURL = "reset_note_url"
        case subscriptionPricing = "subscription_pricing"
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

    /// The host is holding off because the provider rate-limited it. Older
    /// hosts only put that fact in `error`; prefer the typed cause when it
    /// is there.
    var isRateLimited: Bool {
        if staleCause == "rate_limited" { return true }
        guard isStale, let error else { return false }
        let low = error.lowercased()
        return low.contains("429") || low.contains("too many requests")
    }

    /// The numbers on this card cannot be read as current — frozen, or behind
    /// a login that is no longer working. What drains a ring, since both
    /// failures make the arc a claim about the past.
    var readingSuspect: Bool { needsSignIn || isStale }

    /// Amber / alarm colour is reserved for a dead login. Rate limits and
    /// brief freezes stay secondary — the host is already retrying, and
    /// painting them as warnings taught people to hammer Refresh.
    var statusAlarming: Bool { needsSignIn }

    /// Free-text `error` worth drawing under the status note. Rate-limit
    /// prose is already summarised by `statusNote` ("Paused · retries in…"),
    /// so repeating `HTTP Error 429…` next to it is pure noise.
    var displayError: String? {
        guard let error, !error.isEmpty else { return nil }
        if isRateLimited { return nil }
        return error
    }

    /// "Needs sign-in · 2 hours ago", "Paused · retries in 5m",
    /// "Not updating · 2 hours ago", or nil when the provider is fetching
    /// normally. One place decides what a meter in trouble says, so the Mac
    /// and the phone cannot word it differently.
    var statusNote: String? {
        if needsSignIn {
            guard let age = staleForS else { return HeadroomCopy.needsSignIn }
            return HeadroomCopy.needsSignIn(age: age)
        }
        if isRateLimited {
            if let retry = retryInS, retry > 0 {
                return HeadroomCopy.updatingPaused(retryIn: retry)
            }
            return HeadroomCopy.updatingPaused
        }
        guard isStale else { return nil }
        guard let age = staleForS else { return HeadroomCopy.notUpdating }
        return HeadroomCopy.notUpdating(age: age)
    }

    /// Fallback pool order for hosts that predate `pools[].rank`. It only
    /// names the pools those hosts could serve — Copilot, Gemini, JetBrains
    /// and Zed all arrived with the rank field, so they never land here.
    /// Week before session: longer window outer, matching the host registry.
    static let poolPrecedence = ["week", "total", "api", "auto", "session"]

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

    /// Codex plans that expose only a weekly limit still carry the registry's
    /// session slot with a nil reading. It is not a missing measurement — it
    /// is a meter this account does not have — so compact quota cards should
    /// not spend a row showing "Session —".
    var displayablePools: [(id: String, pool: QuotaPoolInfo)] {
        visiblePools.filter { entry in
            let baseID = id.split(separator: ":", maxSplits: 1)
                .first.map(String.init)
            guard baseID == UsageProvider.codex.rawValue,
                  entry.id == "session" else { return true }
            return entry.pool.pct != nil
        }
    }

    /// Prepaid balance meters on this provider. `ring=False` keeps them out of
    /// `visiblePools`, so rings stay window-only; these are drawn as depletion
    /// bars instead.
    var balancePools: [(id: String, pool: QuotaPoolInfo)] {
        (pools ?? [:])
            .filter { $0.value.isBalance }
            .sorted {
                let lhs = Self.poolRank(id: $0.key, pool: $0.value)
                let rhs = Self.poolRank(id: $1.key, pool: $1.value)
                if lhs != rhs { return lhs < rhs }
                return $0.key < $1.key
            }
            .map { (id: $0.key, pool: $0.value) }
    }

    var primaryBalance: QuotaPoolInfo? { balancePools.first?.pool }

    /// True when this provider has only prepaid balance meters — no window
    /// rings to draw. Usage hides these; Activity paints account use instead.
    var isBalanceOnly: Bool {
        !balancePools.isEmpty && visiblePools.isEmpty
    }

    /// This provider's burndown pools in exactly the selection and order of
    /// `displayablePools`, so a provider's charts line up one-for-one with the
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
        return displayablePools.compactMap { byPool[$0.id] }
    }
}

/// What Claude usage cost over the trailing window, derived from session logs.
///
/// Every dollar figure here is **estimated**: local token counts priced by
/// `host/pricing.py`, never a provider's billing. Anything rendering one says
/// so — see `HeadroomCopy.spendEstimated` and docs/metering.md decision 3.
struct UsageHistory: Decodable, Sendable {
    var activeDays: Int?
    var totalTokens: Double?
    var totalCostUSD: Double?
    var avgCostPerActiveDay: Double?
    /// Share of read context served from cache. Low means sessions keep
    /// rebuilding context from cold instead of building on it.
    var cacheHitPct: Double?
    var topModels: [HistoryModel]?
    /// Models that burned tokens which `pricing.py` has no rates for, so part
    /// of the total above came from the fallback rate. Empty is normal; a name
    /// here is the price table being out of date, and is the thing to add.
    var unpricedModels: [String]?

    enum CodingKeys: String, CodingKey {
        case activeDays = "active_days"
        case totalTokens = "total_tokens"
        case totalCostUSD = "total_cost_usd"
        case avgCostPerActiveDay = "avg_cost_per_active_day"
        case cacheHitPct = "cache_hit_pct"
        case topModels = "top_models"
        case unpricedModels = "unpriced_models"
    }
}

/// Observed prepaid spend for a balance provider (OpenRouter, AI Gateway).
///
/// Dollars here are **billed** by the provider's own credits API — not a local
/// estimate. Additive on `providers[]`; absent when the account only has a
/// pot reading or the report endpoint is unavailable (Hobby AI Gateway).
struct BalanceSpend: Decodable, Sendable, Equatable {
    var todayUSD: Double?
    var periodDays: Int?
    var periodUSD: Double?
    var avgDailyUSD: Double?
    var runwayDays: Double?
    var byDay: [BalanceSpendDay]?
    var byModel: [BalanceSpendModel]?
    var byKey: [BalanceSpendKey]?
    /// Soft failure for the spend series (e.g. report needs Pro). Balance
    /// itself can still be healthy.
    var reportError: String?

    enum CodingKeys: String, CodingKey {
        case todayUSD = "today_usd"
        case periodDays = "period_days"
        case periodUSD = "period_usd"
        case avgDailyUSD = "avg_daily_usd"
        case runwayDays = "runway_days"
        case byDay = "by_day"
        case byModel = "by_model"
        case byKey = "by_key"
        case reportError = "report_error"
    }

    var hasFigures: Bool {
        (todayUSD ?? 0) > 0
            || (periodUSD ?? 0) > 0
            || !(byModel ?? []).isEmpty
            || !(byKey ?? []).isEmpty
            || reportError != nil
    }
}

struct BalanceSpendDay: Decodable, Sendable, Equatable, Identifiable {
    var day: String?
    var usd: Double?
    var id: String { day ?? "\(usd ?? 0)" }
}

struct BalanceSpendModel: Decodable, Sendable, Equatable, Identifiable {
    var id: String?
    var title: String?
    var usd: Double?
    var requests: Int?

    var displayName: String { title ?? id ?? "—" }
}

struct BalanceSpendKey: Decodable, Sendable, Equatable, Identifiable {
    var name: String?
    var usdDaily: Double?
    var usdWeekly: Double?
    var usdMonthly: Double?

    var id: String { name ?? "key" }

    enum CodingKeys: String, CodingKey {
        case name
        case usdDaily = "usd_daily"
        case usdWeekly = "usd_weekly"
        case usdMonthly = "usd_monthly"
    }
}

/// The date-indexed mixed activity series behind the app and ESP32 heatmap.
/// `source` is always "mixed" for the current contract; keeping it explicit
/// prevents a future source-specific view from silently changing cell meaning.
struct ActivityHistory: Decodable, Sendable {
    var source: String?
    var windowDays: Int?
    var start: String?
    var end: String?
    var startWeekday: Int?
    var levels: [Int]?
    var days: [ActivityHistoryDay]?
    var activeDays: Int?
    var currentStreak: Int?
    var bestDay: String?
    var availableSources: [String]?

    enum CodingKeys: String, CodingKey {
        case source
        case windowDays = "window_days"
        case start, end
        case startWeekday = "start_weekday"
        case levels, days
        case activeDays = "active_days"
        case currentStreak = "current_streak"
        case bestDay = "best_day"
        case availableSources = "available_sources"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        source = try container.decodeIfPresent(String.self, forKey: .source)
        windowDays = try container.decodeIfPresent(Int.self, forKey: .windowDays)
        start = try container.decodeIfPresent(String.self, forKey: .start)
        end = try container.decodeIfPresent(String.self, forKey: .end)
        startWeekday = try container.decodeIfPresent(Int.self, forKey: .startWeekday)
        levels = try container.decodeIfPresent([Int].self, forKey: .levels)
        days = try container.decodeLossyArrayIfPresent(
            ActivityHistoryDay.self, forKey: .days)
        activeDays = try container.decodeIfPresent(Int.self, forKey: .activeDays)
        currentStreak = try container.decodeIfPresent(Int.self, forKey: .currentStreak)
        bestDay = try container.decodeIfPresent(String.self, forKey: .bestDay)
        availableSources = try container.decodeIfPresent(
            [String].self, forKey: .availableSources)
    }

    var dayByDate: [String: ActivityHistoryDay] {
        Dictionary(uniqueKeysWithValues: (days ?? []).map { ($0.date, $0) })
    }

    func day(for date: String) -> ActivityHistoryDay? {
        dayByDate[date]
    }

    func level(for date: String) -> Int {
        day(for: date)?.level ?? 0
    }
}

struct ActivityHistoryDay: Decodable, Sendable, Identifiable {
    var date: String
    var level: Int
    var activeMinutes: Int?
    var sessions: Int?
    var tokens: Double?
    var costUSD: Double?
    var sources: [String]?
    var burns: [String: Double]?

    var id: String { date }

    enum CodingKeys: String, CodingKey {
        case date, level
        case activeMinutes = "active_minutes"
        case sessions, tokens
        case costUSD = "cost_usd"
        case sources, burns
    }
}

struct HistoryModel: Decodable, Sendable {
    var model: String?
    var tokens: Double?
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
    /// What shape this meter is — `window`, `balance`, `grant`, … Read it
    /// through `meterKind`, never directly: a host older than the field sends
    /// nil, and nil is not "unknown" here, it is "window". Every meter that
    /// existed before this field was one. See docs/metering.md.
    var kind: String?
    /// Whether these numbers are the provider's reading or one this host
    /// derived from local token counts. Read it through `meterBasis`; nil
    /// means `observed`, for the same reason as above.
    var basis: String?
    /// How much of this meter is spent, 0…1. Nil where there is no
    /// denominator — a count of credits is not a fraction of anything, and a
    /// gauge drawn from a substituted zero would read full for someone
    /// holding none.
    var level: Double?
    /// How much is left, carrying the unit it is counted in, because the unit
    /// is the part that differs between kinds. Nil when the meter has no
    /// reading yet.
    var headroom: MeterHeadroom?
    /// Seconds until the soonest item in a `grant` expires. Deliberately not
    /// `resetsInS`: a reset is relief arriving, an expiry is value leaving.
    var expiresInS: Double?

    enum CodingKeys: String, CodingKey {
        case title, rank, pct, ring, kind, basis, level, headroom
        case expiresInS = "expires_in_s"
        case pacePct = "pace_pct"
        case windowS = "window_s"
        case resetsInS = "resets_in_s"
        case resetsIn = "resets_in"
    }
}

/// How much of a meter is left, in the unit that meter is counted in.
///
/// The unit travels with the number because it is the part that differs:
/// percentage points for a window, credits for a grant, dollars for anything
/// billed. Carries no label — a label is copy, and copy lives in
/// `HeadroomCopy.swift` where `check-glossary-copy.sh` can see it.
struct MeterHeadroom: Decodable, Sendable {
    var value: Double?
    /// `pct` | `count` | `usd`. A unit this build does not recognise is a
    /// number it cannot honestly label, so render nothing rather than guess.
    var unit: String?
}

extension QuotaPoolInfo {
    /// Defaulted at the use site, per docs/contract.md: the host may stop
    /// sending a key, and an older host never sent this one at all.
    var meterKind: String { kind ?? "window" }
    var meterBasis: String { basis ?? "observed" }

    /// True when this meter is the shape the rings were built for. The guard
    /// every ring-drawing surface puts in front of itself once other kinds
    /// exist — a balance has no arc to sweep and must not be handed to one.
    var isWindow: Bool { meterKind == "window" }

    /// Prepaid credit remaining. Distinct from a window: no refill clock.
    var isBalance: Bool { meterKind == "balance" }

    /// True when the numbers were computed here rather than read from the
    /// provider. A surface showing a figure this is true for has to say so.
    var isEstimated: Bool { meterBasis == "estimated" }

    /// `$12.34 left` when this pool is a balance with a USD headroom reading.
    var balanceRemainingLabel: String? {
        guard isBalance,
              headroom?.unit == "usd",
              let value = headroom?.value else { return nil }
        return "\(HeadroomFormat.usd(value, maximumFractionDigits: 2)) \(HeadroomCopy.balanceLeft)"
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
    /// Review requests + assignments on watched repos. Older hosts omit this.
    var inbox: [GitHubInboxItem]?
    var inboxCount: Int?
    var repos: [String]?

    enum CodingKeys: String, CodingKey {
        case ok, configured, error, stale, runs, repos, inbox
        case failCount = "fail_count"
        case runningCount = "running_count"
        case inboxCount = "inbox_count"
    }

    /// Hand-written so one malformed run does not cost the whole document.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        func value<T: Decodable>(_ key: CodingKeys) throws -> T? {
            try container.decodeIfPresent(T.self, forKey: key)
        }

        ok = try value(.ok)
        configured = try value(.configured)
        error = try value(.error)
        stale = try value(.stale)
        failCount = try value(.failCount)
        runningCount = try value(.runningCount)
        inboxCount = try value(.inboxCount)
        repos = try value(.repos)
        runs = try container.decodeLossyArrayIfPresent(
            GitHubRun.self, forKey: .runs)
        inbox = try container.decodeLossyArrayIfPresent(
            GitHubInboxItem.self, forKey: .inbox)
    }
}

struct GitHubInboxItem: Decodable, Identifiable, Sendable {
    var id: String
    var reason: String?
    var repo: String?
    var number: Int?
    var title: String?
    var author: String?
    var url: String?
    var isPr: Bool?
    var ago: String?
    /// See `ActivityItem.hostNeedsAttention` — false once the row is older
    /// than the host's inbox attention window.
    var hostNeedsAttention: Bool?

    enum CodingKeys: String, CodingKey {
        case id, reason, repo, number, title, author, url, ago
        case isPr = "is_pr"
        case hostNeedsAttention = "needs_attention"
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
    /// Opener login for GitHub inbox rows (`review_request` / `assigned` / `mention`).
    var author: String?
    /// Issue or PR number for GitHub inbox rows.
    var number: Int?
    var ago: String?
    var errorMessage: String?
    var url: String?
    var inspectorURL: String?
    /// Host verdict on whether this row belongs on Attention, overriding the
    /// status vocabulary. Sent only where status cannot carry the answer —
    /// an aged GitHub assignment is still "assigned" and still belongs in the
    /// feed. Absent on older hosts, so `needsAttention` falls back to status.
    var hostNeedsAttention: Bool?

    enum CodingKeys: String, CodingKey {
        case id, kind, status, subject, repo, project, branch, sha, target, author, number, ago, url
        case shortSHA = "short_sha"
        case errorMessage = "error_message"
        case inspectorURL = "inspector_url"
        case hostNeedsAttention = "needs_attention"
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

    /// Hand-written so one malformed project does not cost the whole document.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        func value<T: Decodable>(_ key: CodingKeys) throws -> T? {
            try container.decodeIfPresent(T.self, forKey: key)
        }

        ok = try value(.ok)
        configured = try value(.configured)
        error = try value(.error)
        stale = try value(.stale)
        projectCount = try value(.projectCount)
        healthyCount = try value(.healthyCount)
        alertCount = try value(.alertCount)
        lintErrorCount = try value(.lintErrorCount)
        lintWarnCount = try value(.lintWarnCount)
        lintTotal = try value(.lintTotal)
        projects = try container.decodeLossyArrayIfPresent(
            SupabaseProject.self, forKey: .projects)
    }
}

struct SyncSource: Decodable, Identifiable, Sendable {
    var id: String
    var title: String?
    /// User-defined name for an extra login. Nil on the default provider row.
    /// Settings keeps the full `title` ("Claude · Work"); surfaces that draw
    /// a brand mark use this instead — see `QuotaProviderInfo.markTitle`.
    var label: String?
    /// Signed-in email when the host can read it locally. Nil when unknown.
    var email: String?
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
    /// The registry's own name, so Settings can offer "Reset" and tell an
    /// overridden row from a shipped one.
    var titleDefault: String?
    /// Same-hue shade from the provider, before an explicit account override.
    var accentDerived: String?
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
    /// Same typed freeze cause as `QuotaProviderInfo.staleCause`.
    var staleCause: String?
    var configured: Bool?
    var error: String?
    var detail: String?
    var ageS: Int?

    var needsSignIn: Bool { authRequired == true }

    var isRateLimited: Bool {
        if staleCause == "rate_limited" { return true }
        guard stale == true, let error else { return false }
        let low = error.lowercased()
        return low.contains("429") || low.contains("too many requests")
    }

    var isDismissed: Bool { dismissed ?? !(enabled ?? true) }

    enum CodingKeys: String, CodingKey {
        case id, title, label, email, hint, kind, group, accent, enabled, ok, stale
        case configured, error, detail, dismissed
        case authRequired = "auth_required"
        case staleCause = "stale_cause"
        case accentDefault = "accent_default"
        case titleDefault = "title_default"
        case accentDerived = "accent_derived"
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
    /// ISO-8601 from the Management API (`created_at` / `inserted_at`).
    var createdAt: String?
    /// Postgres host / version when the list payload included a `database`
    /// object. Nil on older hosts and on projects that omitted it.
    var database: SupabaseDatabase?
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
        case ref, name, region, status, healthy, services, lints, database
        case organizationID = "organization_id"
        case unhealthyServices = "unhealthy_services"
        case healthError = "health_error"
        case dashboardURL = "dashboard_url"
        case createdAt = "created_at"
        case lintTruncated = "lint_truncated"
        case lintErrorCount = "lint_error_count"
        case lintWarnCount = "lint_warn_count"
        case lintInfoCount = "lint_info_count"
        case lintTotal = "lint_total"
        case advisorError = "advisor_error"
    }

    /// Hand-written for the two lossy lists. `SupabaseService` and
    /// `SupabaseLint` both carry a required name, so one nameless row would
    /// otherwise take the whole document with it.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        func value<T: Decodable>(_ key: CodingKeys) throws -> T? {
            try container.decodeIfPresent(T.self, forKey: key)
        }

        ref = try container.decode(String.self, forKey: .ref)
        name = try value(.name)
        organizationID = try value(.organizationID)
        region = try value(.region)
        status = try value(.status)
        healthy = try value(.healthy)
        unhealthyServices = try value(.unhealthyServices)
        healthError = try value(.healthError)
        dashboardURL = try value(.dashboardURL)
        createdAt = try value(.createdAt)
        database = try value(.database)
        lintTruncated = try value(.lintTruncated)
        lintErrorCount = try value(.lintErrorCount)
        lintWarnCount = try value(.lintWarnCount)
        lintInfoCount = try value(.lintInfoCount)
        lintTotal = try value(.lintTotal)
        advisorError = try value(.advisorError)

        services = try container.decodeLossyArrayIfPresent(
            SupabaseService.self, forKey: .services)
        lints = try container.decodeLossyArrayIfPresent(
            SupabaseLint.self, forKey: .lints)
    }
}

/// Postgres metadata on a Supabase project — subset of the Management API's
/// `database` object that is useful to show without being a secret.
struct SupabaseDatabase: Decodable, Sendable, Equatable {
    var host: String?
    var version: String?
    var postgresEngine: String?
    var releaseChannel: String?

    enum CodingKeys: String, CodingKey {
        case host, version
        case postgresEngine = "postgres_engine"
        case releaseChannel = "release_channel"
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

    /// Hand-written so one malformed site does not cost the whole document.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        func value<T: Decodable>(_ key: CodingKeys) throws -> T? {
            try container.decodeIfPresent(T.self, forKey: key)
        }

        ok = try value(.ok)
        configured = try value(.configured)
        error = try value(.error)
        stale = try value(.stale)
        siteCount = try value(.siteCount)
        visitorsToday = try value(.visitorsToday)
        realtime = try value(.realtime)
        range = try value(.range)
        rangeLabel = try value(.rangeLabel)
        sites = try container.decodeLossyArrayIfPresent(
            PlausibleSite.self, forKey: .sites)
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
    /// Visitor histogram for the configured window — daily for 7d/30d,
    /// hourly for day/24h. Same visual role as OpenRouter `spend.by_day`.
    var byDay: [PlausibleTrafficDay]?
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
        case byDay = "by_day"
        case dashboardURL = "dashboard_url"
        case rangeLabel = "range_label"
    }
}

/// One bar in a Plausible site histogram (`by_day`).
struct PlausibleTrafficDay: Decodable, Sendable, Equatable, Identifiable {
    var day: String?
    var visitors: Int?
    var id: String { day ?? "\(visitors ?? 0)" }
}

struct PostHogUsage: Decodable, Sendable {
    var ok: Bool?
    var configured: Bool?
    var error: String?
    var stale: Bool?
    var projects: [PostHogProject]?
    var projectCount: Int?
    var eventsToday: Int?
    var usersToday: Int?
    var realtime: Int?
    /// Primary window id from the host (`day`, `24h`, `7d`, `30d`).
    var range: String?
    var rangeLabel: String?

    var windowLabel: String {
        rangeLabel ?? range ?? "today"
    }

    enum CodingKeys: String, CodingKey {
        case ok, configured, error, projects, stale, realtime, range
        case projectCount = "project_count"
        case eventsToday = "events_today"
        case usersToday = "users_today"
        case rangeLabel = "range_label"
    }

    /// Hand-written so one malformed project does not cost the whole document.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        func value<T: Decodable>(_ key: CodingKeys) throws -> T? {
            try container.decodeIfPresent(T.self, forKey: key)
        }

        ok = try value(.ok)
        configured = try value(.configured)
        error = try value(.error)
        stale = try value(.stale)
        projectCount = try value(.projectCount)
        eventsToday = try value(.eventsToday)
        usersToday = try value(.usersToday)
        realtime = try value(.realtime)
        range = try value(.range)
        rangeLabel = try value(.rangeLabel)
        projects = try container.decodeLossyArrayIfPresent(
            PostHogProject.self, forKey: .projects)
    }
}

struct SentryUsage: Decodable, Sendable {
    var ok: Bool?
    var configured: Bool?
    var error: String?
    var stale: Bool?
    var org: String?
    var alertCount: Int?
    var issues: [SentryIssue]?

    enum CodingKeys: String, CodingKey {
        case ok, configured, error, stale, org, issues
        case alertCount = "alert_count"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        func value<T: Decodable>(_ key: CodingKeys) throws -> T? {
            try container.decodeIfPresent(T.self, forKey: key)
        }
        ok = try value(.ok)
        configured = try value(.configured)
        error = try value(.error)
        stale = try value(.stale)
        org = try value(.org)
        alertCount = try value(.alertCount)
        issues = try container.decodeLossyArrayIfPresent(
            SentryIssue.self, forKey: .issues)
    }
}

struct SentryIssue: Decodable, Identifiable, Sendable {
    var id: String
    var title: String?
    var project: String?
    var shortId: String?
    var level: String?
    var status: String?
    var ago: String?
    var url: String?

    enum CodingKeys: String, CodingKey {
        case id, title, project, level, status, ago, url
        case shortId = "short_id"
    }
}

struct DatadogUsage: Decodable, Sendable {
    var ok: Bool?
    var configured: Bool?
    var error: String?
    var stale: Bool?
    var site: String?
    var alertCount: Int?
    var warnCount: Int?
    var monitors: [DatadogMonitor]?

    enum CodingKeys: String, CodingKey {
        case ok, configured, error, stale, site, monitors
        case alertCount = "alert_count"
        case warnCount = "warn_count"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        func value<T: Decodable>(_ key: CodingKeys) throws -> T? {
            try container.decodeIfPresent(T.self, forKey: key)
        }
        ok = try value(.ok)
        configured = try value(.configured)
        error = try value(.error)
        stale = try value(.stale)
        site = try value(.site)
        alertCount = try value(.alertCount)
        warnCount = try value(.warnCount)
        monitors = try container.decodeLossyArrayIfPresent(
            DatadogMonitor.self, forKey: .monitors)
    }
}

struct DatadogMonitor: Decodable, Identifiable, Sendable {
    var id: String
    var name: String?
    var overallState: String?
    var status: String?
    var ago: String?
    var url: String?

    enum CodingKeys: String, CodingKey {
        case id, name, status, ago, url
        case overallState = "overall_state"
    }
}

struct AxiomUsage: Decodable, Sendable {
    var ok: Bool?
    var configured: Bool?
    var error: String?
    var stale: Bool?
    var host: String?
    var orgId: String?
    var alertCount: Int?
    var alerts: [AxiomAlert]?

    enum CodingKeys: String, CodingKey {
        case ok, configured, error, stale, host, alerts
        case orgId = "org_id"
        case alertCount = "alert_count"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        func value<T: Decodable>(_ key: CodingKeys) throws -> T? {
            try container.decodeIfPresent(T.self, forKey: key)
        }
        ok = try value(.ok)
        configured = try value(.configured)
        error = try value(.error)
        stale = try value(.stale)
        host = try value(.host)
        orgId = try value(.orgId)
        alertCount = try value(.alertCount)
        alerts = try container.decodeLossyArrayIfPresent(
            AxiomAlert.self, forKey: .alerts)
    }
}

struct AxiomAlert: Decodable, Identifiable, Sendable {
    var id: String
    var name: String?
    var type: String?
    var status: String?
    var ago: String?
    var url: String?
    var description: String?
}

struct PostHogProject: Decodable, Identifiable, Sendable {
    var id: String
    var name: String?
    var eventsToday: Int?
    var usersToday: Int?
    var events7d: Int?
    var users7d: Int?
    var realtime: Int?
    var dashboardURL: String?
    var error: String?
    var range: String?
    var rangeLabel: String?

    var windowLabel: String {
        rangeLabel ?? range ?? "today"
    }

    var displayName: String {
        let trimmed = (name ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? id : trimmed
    }

    enum CodingKeys: String, CodingKey {
        case id, name, realtime, error, range
        case eventsToday = "events_today"
        case usersToday = "users_today"
        case events7d = "events_7d"
        case users7d = "users_7d"
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
    var builds: [LocalBuild]?
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

/// One active `xcodebuild` / `swift build` / Xcode IDE compile on this Mac.
struct LocalBuild: Decodable, Identifiable, Sendable {
    var name: String?
    var kind: String?
    var action: String?
    var scheme: String?
    var target: String?
    var pid: Int?
    var cmd: String?
    var cwd: String?
    var ageS: Int?

    var id: String {
        "\(kind ?? "build"):\(pid ?? 0):\(name ?? "")"
    }

    enum CodingKeys: String, CodingKey {
        case name, kind, action, scheme, target, pid, cmd, cwd
        case ageS = "age_s"
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
        HeadroomFormat.usd(rounded())
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
    /// This question is showing in both places and is answered in the other
    /// one. Saying so beats a row that looks inert for no stated reason.
    var answerOnMac: Bool?

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
        case answerOnMac = "answer_on_mac"
    }
}

struct AgentAttentionEvent: Codable, Sendable, Equatable, Identifiable {
    var id: String
    var provider: String
    var adapter: String
    var machineID: String?
    var machineName: String?
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

    /// Repo-first title for the phone. Older hosts stored prose such as
    /// "Claude finished responding in repo"; the cwd is the durable source
    /// of the repository identity, so newer clients can repair that display
    /// without rewriting the Mac's ledger.
    var displayTitle: String {
        guard let cwd = detail.cwd,
              let last = cwd.split(separator: "/").last,
              !last.isEmpty else { return title }
        return String(last)
    }

    /// Short tool name for a lock-screen glance — Claude / Codex, not the
    /// adapter id (`claude-code`) and not a model tier (Opus / Sonnet).
    var providerDisplayName: String {
        switch provider {
        case "claude-code": return "Claude"
        case "codex": return "Codex"
        default: return provider
        }
    }

    /// Push title: repo, which agent, which Mac. The Attention row keeps
    /// `displayTitle` as the repo alone — mark and machine sit beside it.
    var notificationTitle: String {
        var parts = [displayTitle, providerDisplayName]
        if let machineName, !machineName.isEmpty {
            parts.append(machineName)
        }
        return parts.joined(separator: " • ")
    }

    /// A row nobody can answer — a finished/idle notice — only offers
    /// dismissal, and that is what makes it safe to swipe away.
    var isDismissOnly: Bool {
        !actions.isEmpty && actions.allSatisfy { $0.id == "dismiss" }
    }

    /// Informational lifecycle notices still travel to the phone so they can
    /// notify the person, but they do not belong in the Attention queue. A
    /// real request has at least one answer other than dismissing the row.
    var isActionable: Bool {
        !actions.isEmpty && !isDismissOnly
    }

    enum CodingKeys: String, CodingKey {
        case id, provider, adapter, kind, state, revision, title, summary
        case detail, actions
        case machineID = "machine_id"
        case machineName = "machine_name"
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

/// What a client needs to offer "start a task" without guessing: which
/// providers can take work right now, and which folders the Mac has used.
/// A phone cannot browse the Mac's disk, so it picks from that list.
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
