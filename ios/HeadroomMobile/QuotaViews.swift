import Charts
import SwiftUI

struct QuotaOverviewCard: View {
    let snapshot: UsageSnapshot

    private var providers: [QuotaProviderInfo] {
        snapshot.codingQuotaProviders
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(HeadroomCopy.codingQuotas)
                .font(.headline)
            if providers.isEmpty {
                ContentUnavailableView(
                    "No quota data",
                    systemImage: "chart.pie.fill",
                    description: Text(HeadroomCopy.waitingForMacSync)
                )
            } else {
                ForEach(providers) { provider in
                    NavigationLink {
                        ProviderQuotaDetail(
                            provider: provider,
                            meter: snapshot.meter(for: provider),
                            burndown: provider.orderedBurndown(
                                from: snapshot.burndown?[provider.id]
                            ),
                            subscriptionPricing: provider.subscriptionPricing,
                            todayBurn: snapshot.byDay?
                                .last?
                                .burn(forProviderID: provider.id),
                            claudeStatus: provider.id == "claude"
                                ? snapshot.claudeStatusIfEnabled
                                : nil,
                        )
                    } label: {
                        HStack(spacing: 10) {
                            ProviderSummaryRow(
                                provider: provider,
                                meter: snapshot.meter(for: provider),
                                burndown: provider.orderedBurndown(
                                    from: snapshot.burndown?[provider.id]
                                ),
                                claudeStatus: provider.id == "claude"
                                    ? snapshot.claudeStatusIfEnabled
                                    : nil
                            )
                            Image(systemName: "chevron.right")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.tertiary)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    if provider.id != providers.last?.id {
                        Divider()
                    }
                }
            }
        }
        .headroomCard()
    }
}

private struct ProviderSummaryRow: View {
    let provider: QuotaProviderInfo
    let meter: ProviderMeter
    /// The provider's burndown pools, for the pace dots. Empty is fine.
    var burndown: [Burndown] = []
    var claudeStatus: ClaudeStatus? = nil

    var body: some View {
        HStack(spacing: 16) {
            if provider.isBalanceOnly {
                spendSummaryMark
            } else {
                HeadroomRings(
                    layers: provider.ringLayers(burndown: burndown),
                    tint: provider.tint
                )
                .frame(width: 82, height: 82)
                .opacity(provider.readingSuspect ? 0.4 : 1)
            }

            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    ProviderMark(providerID: provider.id, size: 16)
                    Text(provider.markTitle)
                        .font(.headline)
                    if let plan = meter.plan {
                        Text(plan)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                if provider.id == "claude", let claudeStatus {
                    ClaudeStatusLine(status: claudeStatus, showsLink: false)
                }
                ForEach(
                    Array(meter.displayableWindows.prefix(3).enumerated()),
                    id: \.offset
                ) { _, window in
                    HStack {
                        Text(window.title)
                        Spacer()
                        Text(
                            window.percent.map {
                                "\(Int($0.rounded()))%"
                            } ?? "—"
                        )
                            .monospacedDigit()
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                if let period = meter.spend?.periodUSD,
                   let days = meter.spend?.periodDays {
                    Text("\(period.dollarLabel) / \(days)d")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                } else if let balance = meter.balanceLabel {
                    Text(balance)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                }
                if let today = meter.spend?.todayUSD {
                    Text("\(today.dollarLabel) today")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .monospacedDigit()
                }
                // Ahead of the headline, because a headline written from
                // frozen percentages is confidently wrong.
                if let status = meter.statusNote {
                    Text(status)
                        .font(.caption2)
                        .foregroundStyle(
                            meter.statusAlarming
                                ? HeadroomPalette.orange : Color.secondary)
                        .lineLimit(1)
                }
                // Same reasoning as the Mac card: `ok` stays true while the
                // host replays frozen bars, so an error is worth showing
                // whenever there is one. Rate-limit text is already in the
                // status note ("Paused · retries in…").
                if let error = meter.displayError {
                    Text(error)
                        .font(.caption2)
                        .foregroundStyle(
                            meter.statusAlarming
                                ? HeadroomPalette.orange : Color.secondary)
                        .lineLimit(2)
                } else if let headline = provider.headline {
                    Text(headline)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .lineLimit(2)
                }
            }
        }
    }

    private var spendSummaryMark: some View {
        let days = meter.spend?.byDay ?? []
        return Group {
            if days.contains(where: { ($0.usd ?? 0) > 0 }) {
                BalanceSpendSparkline(
                    days: days, tint: provider.tint, diameter: 82
                )
                .opacity(provider.readingSuspect ? 0.4 : 1)
            } else {
                Text("—")
                    .font(.title2)
                    .foregroundStyle(.tertiary)
                    .frame(width: 82, height: 82)
            }
        }
    }
}

private struct ProviderQuotaDetail: View {
    let provider: QuotaProviderInfo
    let meter: ProviderMeter
    /// Already in `visiblePools` order — one chart per progress bar, same
    /// sequence. Do not re-sort here.
    let burndown: [Burndown]
    let subscriptionPricing: SubscriptionPricing?
    /// Headline-meter points burned today (`by_day`), same as the board.
    var todayBurn: Double? = nil
    var claudeStatus: ClaudeStatus? = nil

    /// "Connected" is a claim about right now, and a provider whose numbers
    /// stopped arriving is in no position to make it. `ok` alone would let it.
    private var statusLabel: String {
        if let status = meter.statusNote { return status }
        return meter.ok == false ? HeadroomCopy.needsAttention : "Connected"
    }

    private var statusTint: Color {
        if meter.statusAlarming || meter.ok == false {
            return HeadroomPalette.orange
        }
        if provider.readingSuspect {
            return .secondary
        }
        return HeadroomPalette.green
    }

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 16) {
                    HStack {
                        if provider.isBalanceOnly {
                            if let days = meter.spend?.byDay,
                               days.contains(where: { ($0.usd ?? 0) > 0 }) {
                                BalanceSpendSparkline(
                                    days: days,
                                    tint: provider.tint,
                                    diameter: 112
                                )
                                .opacity(provider.readingSuspect ? 0.4 : 1)
                            } else {
                                Text(HeadroomCopy.accountUse)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .frame(width: 112, height: 112)
                            }
                        } else {
                            HeadroomRings(
                                layers: provider.ringLayers(burndown: burndown),
                                tint: provider.tint
                            )
                            .frame(width: 112, height: 112)
                            .opacity(provider.readingSuspect ? 0.4 : 1)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 4) {
                            if let period = meter.spend?.periodUSD,
                               let days = meter.spend?.periodDays {
                                Text("\(period.dollarLabel) / \(days)d")
                                    .font(.title3.weight(.semibold))
                                    .monospacedDigit()
                            } else {
                                Text(meter.plan ?? HeadroomCopy.planUnknown)
                                    .foregroundStyle(.secondary)
                            }
                            if let todayBurn, !provider.isBalanceOnly {
                                Text(HeadroomFormat.todayBurn(todayBurn))
                                    .foregroundStyle(.secondary)
                                    .monospacedDigit()
                            }
                            if let today = meter.spend?.todayUSD {
                                Text(today.dollarLabel + " today")
                                    .foregroundStyle(.secondary)
                                    .monospacedDigit()
                            }
                            Text(statusLabel)
                                .foregroundStyle(statusTint)
                                .multilineTextAlignment(.trailing)
                        }
                        .font(.subheadline)
                    }

                    if provider.id == "claude", let claudeStatus {
                        ClaudeStatusLine(status: claudeStatus)
                    }

                    ForEach(
                        Array(meter.displayableWindows.enumerated()),
                        id: \.offset
                    ) { _, window in
                        MobileQuotaRow(window: window, tint: provider.tint)
                    }

                    if let spend = meter.spend, spend.hasFigures || spend.reportError != nil {
                        BalanceSpendCard(
                            spend: spend,
                            remainingLabel: meter.balanceLabel,
                            tint: provider.tint
                        )
                    } else if let balance = meter.balanceLabel {
                        MobileBalanceRow(
                            label: balance,
                            level: meter.balanceLevel,
                            tint: provider.tint
                        )
                    }

                    if let pace = meter.paceLabel {
                        HStack {
                            Text(pace)
                            Spacer()
                            if let runsOut = meter.runsOutIn {
                                Text("Runs out in \(runsOut)")
                            }
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }

                    if let credits = meter.resetCreditsLabel {
                        HStack {
                            Text(credits)
                            Spacer()
                            if let expiry = meter.resetCreditsExpiryLabel {
                                Text(expiry)
                                    .monospacedDigit()
                                    .lineLimit(1)
                                    .minimumScaleFactor(0.85)
                            }
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }

                    if let cost = meter.costLabel {
                        Text(cost)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if let error = meter.displayError {
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(
                                meter.statusAlarming
                                    ? HeadroomPalette.orange : Color.secondary)
                            .lineLimit(2)
                    }
                }
                .headroomCard()

                if let subscriptionPricing {
                    SubscriptionPricingView(
                        pricing: subscriptionPricing,
                        currentPlan: meter.plan)
                    .headroomCard()
                }

                ForEach(burndown) { pool in
                    BurndownChart(
                        pool: pool,
                        tint: provider.tint,
                        resetNoteURL: provider.resetNoteURL
                            .flatMap(URL.init(string:))
                    )
                }
            }
            .padding()
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle(provider.displayTitle)
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct MobileQuotaRow: View {
    let window: MeterWindow
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(window.title)
                    .fontWeight(.medium)
                Spacer()
                Text(window.percent.map { "\(Int($0.rounded()))%" } ?? "—")
                    .monospacedDigit()
            }
            ProgressView(
                value: min(max(window.percent ?? 0, 0), 100),
                total: 100
            )
            .tint(tint)
            HStack {
                if let pace = window.pacePercent {
                    Text("Pace \(Int(pace.rounded()))%")
                }
                Spacer()
                if let reset = window.reset {
                    Text("Resets \(reset)")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }
}

private struct MobileBalanceRow: View {
    let label: String
    let level: Double?
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Balance")
                    .fontWeight(.medium)
                Spacer()
                Text(label)
                    .monospacedDigit()
            }
            ProgressView(
                value: max(0, min(level ?? 0, 1)),
                total: 1
            )
            .tint(tint)
        }
    }
}

// SubscriptionPricingView lives in Shared/ — one block, two type scales.

private struct BurndownPoint: Identifiable {
    let id: String
    let date: Date
    let remaining: Double
    /// Style key — indexes `chartForegroundStyleScale`.
    let series: String
    /// Which polyline this point belongs to. Usually the same as `series`, but
    /// spent windows share one style across several separate lines: Charts
    /// joins every point of a series into one stroke, and a stroke that spans a
    /// reset would climb across the jump instead of stopping at it.
    let lineID: String
}

private struct BurndownChart: View {
    let pool: Burndown
    let tint: Color
    /// Codex mid-window grant note. Nil → plain caption, no link.
    var resetNoteURL: URL? = nil

    private var axisDomain: BurndownChartAxis.Domain? { pool.chartDomain }

    private var points: [BurndownPoint] {
        guard let domain = axisDomain else {
            return rawPoints(
                ideal: pool.ideal, actual: pool.actual,
                projected: pool.croppedProjected,
                spent: [OverallBurndownChartMath.historyPolyline(
                    pool.history ?? pool.forgiven,
                    risersAt: pool.historyRisers
                )]
            )
        }
        // Clip series to the (possibly 7-day-capped) plot domain so monthly
        // pools don't smear past the weekday columns.
        let ideal = OverallBurndownChartMath.clipPolyline(
            pool.ideal ?? [],
            start: domain.startEpoch, end: domain.endEpoch
        )
        let actual = OverallBurndownChartMath.clipPolyline(
            pool.actual ?? [],
            start: domain.startEpoch, end: domain.endEpoch
        )
        let projected = OverallBurndownChartMath.clipPolyline(
            pool.croppedProjected,
            start: domain.startEpoch, end: domain.endEpoch
        )
        // Windows already spent, in the stub before this one. Never fitted and
        // never measured against the budget — those budgets are gone. The climb
        // at each reset is drawn: that is the recharge.
        let spent = [
            OverallBurndownChartMath.clipPolyline(
                OverallBurndownChartMath.historyPolyline(
                    pool.history ?? pool.forgiven,
                    risersAt: pool.historyRisers
                ),
                start: domain.startEpoch, end: domain.endEpoch
            )
        ]
        return rawPoints(ideal: ideal, actual: actual, projected: projected,
                         spent: spent)
    }

    private func rawPoints(
        ideal: [[Double]]?,
        actual: [[Double]]?,
        projected: [[Double]]?,
        spent: [[[Double]]] = []
    ) -> [BurndownPoint] {
        func rows(
            _ values: [[Double]]?,
            series: String,
            lineID: String? = nil
        ) -> [BurndownPoint] {
            let line = lineID ?? series
            return (values ?? []).enumerated().compactMap { index, pair in
                guard pair.count >= 2 else { return nil }
                return BurndownPoint(
                    id: "\(line)-\(index)",
                    date: Date(timeIntervalSince1970: pair[0]),
                    remaining: min(max(pair[1], 0), 100),
                    series: series,
                    lineID: line
                )
            }
        }
        return rows(ideal, series: "Budget")
            + rows(actual, series: "Actual")
            + rows(projected, series: "Projected")
            + spent.enumerated().flatMap { index, segment in
                rows(segment, series: "Forgiven", lineID: "Forgiven-\(index)")
            }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(HeadroomCopy.poolBurndown(pool.poolTitle))
                        .font(.headline)
                    Text(pool.headline ?? pool.resetsIn.map { "Resets \($0)" } ?? "")
                        .font(.caption)
                        .foregroundStyle(
                            pool.inDeficit == true
                                ? AnyShapeStyle(HeadroomPalette.amber)
                                : AnyShapeStyle(.secondary)
                        )
                    // Under the headline, not above it: the headline is the
                    // reading, this is only what the axis spans.
                    if let axisDomain {
                        Text(axisDomain.frameLabel)
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
                Spacer()
                if let remaining = pool.remainingPct {
                    Text("\(Int(remaining.rounded()))% left")
                        .font(.subheadline.monospacedDigit())
                }
            }

            if points.isEmpty {
                Text(HeadroomCopy.noHistoryYet)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 24)
            } else if let domain = axisDomain {
                let nowDate = points
                    .filter { $0.series == "Actual" }
                    .map(\.date)
                    .max() ?? domain.end
                // Names sit at band centres, rules at the midnights between
                // them — a part-day column still gets its weekday.
                let dayColumns = BurndownChartAxis.dayColumns(
                    start: domain.start, end: domain.end
                )
                let dayLabels = dayColumns.map(\.mid)
                let dayRules = BurndownChartAxis.dayGridLines(
                    start: domain.start, end: domain.end
                )
                let hourDates = BurndownChartAxis.hourMarks(
                    start: domain.start, end: domain.end
                )

                Chart {
                    ForEach(points) { point in
                        LineMark(
                            x: .value("Time", point.date),
                            y: .value("Remaining", point.remaining),
                            series: .value("Line", point.lineID)
                        )
                        .foregroundStyle(by: .value("Series", point.series))
                        .lineStyle(
                            StrokeStyle(
                                lineWidth: point.series == "Actual" ? 3 : 1.5,
                                dash: point.series == "Projected" ? [6, 2] : []
                            )
                        )
                    }
                    // Solid rule where a mid-window grant restarted the curve.
                    ForEach(
                        OverallBurndownChartMath.preparedResets(
                            pool.resets?.compactMap(\.t),
                            domain: OverallBurndownChartMath.Domain(
                                start: domain.start,
                                end: domain.end,
                                now: nowDate
                            )
                        ),
                        id: \.self
                    ) { granted in
                        RuleMark(x: .value(
                            "Reset granted",
                            Date(timeIntervalSince1970: granted)
                        ))
                        .foregroundStyle(tint.opacity(0.55))
                        .lineStyle(StrokeStyle(lineWidth: 1))
                    }
                }
                .chartForegroundStyleScale([
                    "Budget": Color.secondary.opacity(0.45),
                    "Actual": tint,
                    "Projected": tint.opacity(0.55),
                    "Forgiven": tint.opacity(0.3),
                ])
                .chartXScale(domain: domain.start...domain.end)
                .chartYScale(domain: 0...100)
                .chartPlotStyle { $0.clipped() }
                .chartXAxis {
                    if domain.showsDayAxis {
                        AxisMarks(values: dayRules) { _ in
                            AxisGridLine()
                        }
                        AxisMarks(values: dayLabels) { value in
                            AxisValueLabel(anchor: .center) {
                                if let date = value.as(Date.self) {
                                    Text(
                                        date,
                                        format: .dateTime.weekday(.abbreviated)
                                    )
                                    .foregroundStyle(
                                        date <= nowDate
                                            ? Color.secondary
                                            : Color.secondary.opacity(0.55)
                                    )
                                }
                            }
                        }
                    } else {
                        AxisMarks(values: hourDates) { value in
                            AxisGridLine()
                            AxisValueLabel(anchor: .center) {
                                if let date = value.as(Date.self) {
                                    Text(date, format: .dateTime.hour().minute())
                                        .foregroundStyle(Color.secondary)
                                }
                            }
                        }
                    }
                }
                .chartYAxis {
                    AxisMarks(values: [0, 50, 100]) {
                        AxisGridLine()
                        AxisValueLabel(anchor: .trailing)
                    }
                }
                .frame(height: 190)

                if let resets = pool.resets, !resets.isEmpty {
                    ResetHistoryList(
                        resets: resets, tint: tint, noteURL: resetNoteURL
                    )
                }
            }
        }
        .headroomCard()
    }
}

private struct OverallBurndownPoint: Identifiable {
    let id: String
    let date: Date
    let remaining: Double
}

private struct OverallBurndownSeries: Identifiable {
    let id: String
    let provider: QuotaProviderInfo
    let pool: Burndown
    let actual: [OverallBurndownPoint]
    let projected: [OverallBurndownPoint]
    /// Recent cross-window readings, including the sawtooth at resets.
    /// `forgiven` is the compatibility fallback for older hosts.
    let history: [OverallBurndownPoint]
    let resetsIn: String?
}

struct OverallBurndownChart: View {
    let providers: [QuotaProviderInfo]
    let snapshot: UsageSnapshot

    /// Raw series before domain clip — carries windowEnd for domain math.
    private var series: [OverallBurndownSeries] {
        providers.compactMap { provider in
            // Same pool the Mac card and the widget draw, from the same pool
            // set the provider's own bars use.
            let pool = snapshot.overviewBurndown(forProviderID: provider.id)
            guard let pool else { return nil }

            func points(
                _ values: [[Double]]?,
                kind: String
            ) -> [OverallBurndownPoint] {
                (values ?? []).enumerated().compactMap { index, pair in
                    guard pair.count >= 2 else { return nil }
                    return OverallBurndownPoint(
                        id: "\(provider.id)-\(kind)-\(index)",
                        date: Date(timeIntervalSince1970: pair[0]),
                        remaining: min(max(pair[1], 0), 100)
                    )
                }
            }

            let actual = points(pool.actual, kind: "actual")
            guard !actual.isEmpty else { return nil }
            return OverallBurndownSeries(
                id: provider.id,
                provider: provider,
                pool: pool,
                actual: actual,
                projected: points(pool.projected, kind: "projected"),
                history: points(
                    pool.history ?? pool.forgiven, kind: "history"
                ),
                resetsIn: pool.resetsIn
            )
        }
    }

    private var now: Date {
        series.flatMap(\.actual).map(\.date).max() ?? .now
    }

    private var domain: OverallBurndownChartMath.Domain {
        OverallBurndownChartMath.domain(now: now)
    }

    private func chartPoints(
        _ pairs: [[Double]],
        idPrefix: String
    ) -> [OverallBurndownPoint] {
        pairs.enumerated().compactMap { index, pair in
            guard pair.count >= 2 else { return nil }
            return OverallBurndownPoint(
                id: "\(idPrefix)-\(index)",
                date: Date(timeIntervalSince1970: pair[0]),
                remaining: min(max(pair[1], 0), 100)
            )
        }
    }

    /// Crop at reset/empty, then clip to the fixed 7-day domain.
    private func drawnSeries(
        in domain: OverallBurndownChartMath.Domain
    ) -> [OverallBurndownSeries] {
        series.map { entry in
            OverallBurndownSeries(
                id: entry.id,
                provider: entry.provider,
                pool: entry.pool,
                actual: chartPoints(
                    OverallBurndownChartMath.preparedActual(
                        entry.pool.actual, domain: domain
                    ),
                    idPrefix: "\(entry.id)-a"
                ),
                projected: chartPoints(
                    OverallBurndownChartMath.preparedProjection(
                        entry.pool.projected,
                        windowEnd: entry.pool.windowEnd,
                        domain: domain
                    ),
                    idPrefix: "\(entry.id)-p"
                ),
                history: chartPoints(
                    OverallBurndownChartMath.clipPolyline(
                        OverallBurndownChartMath.historyPolyline(
                            entry.pool.history ?? entry.pool.forgiven,
                            risersAt: entry.pool.historyRisers
                        ),
                        start: domain.startEpoch,
                        end: domain.endEpoch
                    ),
                    idPrefix: "\(entry.id)-h"
                ),
                resetsIn: entry.resetsIn
            )
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Frame under the title, tertiary — same slot and weight as the
            // provider charts, so the two rules read as a labelled pair.
            VStack(alignment: .leading, spacing: 2) {
                Text(HeadroomCopy.overallBurndown)
                    .font(.headline)
                Text(domain.frameLabel)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }

            if series.isEmpty {
                Text(HeadroomCopy.noHistoryYet)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 24)
            } else {
                let domain = self.domain
                let nowDate = domain.now
                let range = domain.start...domain.end
                let drawn = drawnSeries(in: domain)
                Chart {
                    RuleMark(x: .value("Now", nowDate))
                        .foregroundStyle(Color.secondary.opacity(0.4))
                        .lineStyle(StrokeStyle(lineWidth: 1))

                    ForEach(drawn) { entry in
                        // Windows already spent, behind everything else.
                        // Faint and thin: history that stopped counting must
                        // never be mistaken for the live curve. The reset
                        // risers are made explicit so iOS matches the Mac
                        // chart even when the reset is not a grant.
                        ForEach(entry.history) { point in
                            LineMark(
                                x: .value("Time", point.date),
                                y: .value("Remaining", point.remaining),
                                series: .value(
                                    "Series", "\(entry.id)-history"
                                )
                            )
                            .foregroundStyle(entry.provider.tint.opacity(0.3))
                            .lineStyle(StrokeStyle(
                                lineWidth: 2, lineJoin: .round
                            ))
                        }
                        ForEach(
                            OverallBurndownChartMath.preparedResets(
                                entry.pool.resets?.compactMap(\.t),
                                domain: OverallBurndownChartMath.Domain(
                                    start: domain.start,
                                    end: domain.end,
                                    now: nowDate
                                )
                            ),
                            id: \.self
                        ) { reset in
                            RuleMark(
                                x: .value(
                                    "Reset granted",
                                    Date(timeIntervalSince1970: reset)
                                )
                            )
                            .foregroundStyle(
                                entry.provider.tint.opacity(0.55)
                            )
                            .lineStyle(StrokeStyle(lineWidth: 1))
                        }
                        ForEach(entry.actual) { point in
                            LineMark(
                                x: .value("Time", point.date),
                                y: .value("Remaining", point.remaining),
                                series: .value("Series", entry.id)
                            )
                            .foregroundStyle(entry.provider.tint)
                            .lineStyle(StrokeStyle(
                                lineWidth: 3, lineJoin: .round
                            ))
                        }
                        if let last = entry.actual.last {
                            PointMark(
                                x: .value("Time", last.date),
                                y: .value("Remaining", last.remaining)
                            )
                            .foregroundStyle(entry.provider.tint)
                            .symbolSize(36)
                        }
                        ForEach(entry.projected) { point in
                            LineMark(
                                x: .value("Time", point.date),
                                y: .value("Remaining", point.remaining),
                                series: .value(
                                    "Series", "\(entry.id)-projected"
                                )
                            )
                            .foregroundStyle(entry.provider.tint.opacity(0.55))
                            .lineStyle(StrokeStyle(
                                lineWidth: 2,
                                lineJoin: .round,
                                dash: [6, 2]
                            ))
                        }
                        if let last = entry.projected.last {
                            PointMark(
                                x: .value("Time", last.date),
                                y: .value("Remaining", last.remaining)
                            )
                            .foregroundStyle(entry.provider.tint.opacity(0.7))
                            .symbolSize(last.remaining <= 0 ? 36 : 22)
                        }
                    }
                }
                .chartXScale(domain: range)
                .chartYScale(domain: 0...100)
                .chartPlotStyle { $0.clipped() }
                .chartXAxis {
                    AxisMarks(values: .stride(by: .day)) { value in
                        AxisGridLine()
                        AxisValueLabel(anchor: .center) {
                            if let date = value.as(Date.self) {
                                let calendar = Calendar.current
                                let isToday = calendar.isDate(
                                    date, inSameDayAs: nowDate
                                )
                                Text(
                                    date,
                                    format: .dateTime.weekday(.abbreviated)
                                )
                                .fontWeight(isToday ? .semibold : .regular)
                                .foregroundStyle(
                                    date <= nowDate
                                        ? Color.secondary
                                        : Color.secondary.opacity(0.55)
                                )
                            }
                        }
                    }
                }
                .chartYAxis {
                    AxisMarks(values: [0, 50, 100]) {
                        AxisGridLine()
                        AxisValueLabel(anchor: .trailing)
                    }
                }
                .frame(height: 190)

                HStack(alignment: .top, spacing: 12) {
                    ForEach(drawn) { entry in
                        VStack(alignment: .leading, spacing: 2) {
                            Label {
                                Text(entry.provider.markTitle)
                                    .font(.caption2.weight(.medium))
                            } icon: {
                                Circle()
                                    .fill(entry.provider.tint)
                                    .frame(width: 7, height: 7)
                            }
                            if let resets = entry.resetsIn {
                                Text(HeadroomCopy.resets(resets))
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .monospacedDigit()
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
        .headroomCard()
    }
}

struct DailyBurnChart: View {
    let days: [DailyBurnDay]
    let providers: [QuotaProviderInfo]

    private var visibleDays: [DailyBurnDay] { Array(days.suffix(7)) }

    private var todayTotal: Double {
        let ids = providers.map(\.id)
        return visibleDays.last.map { $0.total(forProviderIDs: ids) } ?? 0
    }

    private var subtitle: String {
        if todayTotal > 0 {
            return HeadroomFormat.todayBurn(todayTotal)
        }
        return HeadroomCopy.dailyBurnUnit
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(HeadroomCopy.dailyBurn)
                    .font(.headline)
                Spacer()
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if visibleDays.isEmpty || providers.isEmpty {
                Text(HeadroomCopy.noBurnHistoryYet)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 20)
            } else {
                Chart {
                    ForEach(visibleDays) { day in
                        ForEach(providers) { provider in
                            BarMark(
                                x: .value("Day", day.date),
                                y: .value(
                                    "Burn",
                                    day.burn(forProviderID: provider.id)
                                )
                            )
                            .foregroundStyle(
                                by: .value("Provider", provider.markTitle)
                            )
                        }
                    }
                }
                .chartForegroundStyleScale(
                    domain: providers.map(\.markTitle),
                    range: providers.map(\.tint)
                )
                .chartLegend(position: .bottom, alignment: .leading, spacing: 12)
                .chartXAxis {
                    AxisMarks { value in
                        AxisValueLabel(anchor: .center) {
                            if let raw = value.as(String.self) {
                                Text(HeadroomFormat.shortWeekday(isoDate: raw))
                            }
                        }
                    }
                }
                .frame(height: 170)
            }
        }
        .headroomCard()
    }

}
