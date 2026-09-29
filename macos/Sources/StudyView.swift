import Charts
import SwiftUI

/// The "Your usage" window: what the Claude Code session logs on this Mac say
/// about how you work, and the card you can hand a friend.
///
/// Everything here comes from `GET /study`. The host does the reading and the
/// arithmetic; this view draws. The numbers are yours and real, so they include
/// counts, which is why the card section below shows exactly what a friend gets
/// and how much coarser it is.
struct StudyView: View {
    @ObservedObject var store: StudyStore
    @State private var handleDraft = ""
    @State private var handleMessage: String?
    @FocusState private var handleFocused: Bool

    static let windowSize = NSSize(width: 680, height: 760)

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                content
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(minWidth: 560, minHeight: 480)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    // MARK: State

    @ViewBuilder
    private var content: some View {
        if !store.isLocalHost {
            notice(HeadroomCopy.studyRemoteHost)
        } else if let snapshot = store.snapshot {
            if let insights = snapshot.insights {
                if let message = store.errorMessage {
                    notice(message)
                }
                header(snapshot, insights)
                tiles(insights)
                models(insights)
                monthly(insights)
                rhythm(insights)
                prompts(insights)
                sessions(insights)
                card(snapshot)
            } else if snapshot.isScanning {
                reading
            } else {
                notice(snapshot.error ?? HeadroomCopy.studyEmpty)
            }
        } else if let message = store.errorMessage {
            notice(message)
        } else {
            reading
        }
    }

    private var reading: some View {
        HStack(spacing: 10) {
            ProgressView().controlSize(.small)
            Text(HeadroomCopy.studyReading).foregroundStyle(.secondary)
        }
        .padding(.top, 40)
        .frame(maxWidth: .infinity)
    }

    private func notice(_ text: String) -> some View {
        Label(text, systemImage: "info.circle")
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .cardStyle()
    }

    private func header(_ snapshot: StudySnapshot, _ i: StudyInsights)
        -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(i.firstDay) to \(i.lastDay)")
                .font(.headline)
            Text((i.machines > 1 ? [HeadroomCopy.studyCombined(i.machines)] : [])
                .joined(separator: " · "))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: Tiles

    private func tiles(_ i: StudyInsights) -> some View {
        let input = i.tokens.input + i.tokens.cacheRead + i.tokens.cacheWrite
        return LazyVGrid(
            columns: [GridItem(.adaptive(minimum: 190), spacing: 10)],
            spacing: 10
        ) {
            tile(HeadroomCopy.studyTurns, i.turns.formatted(),
                 "\(i.sessions.formatted()) sessions")
            tile(HeadroomCopy.studyActiveDays, "\(i.activeDays)",
                 "\(i.firstDay) to \(i.lastDay)")
            tile(HeadroomCopy.studyCacheHit,
                 i.cacheHitPct.map { String(format: "%.1f%%", $0) } ?? "None",
                 HeadroomCopy.studyCacheHitCaption)
            tile(HeadroomCopy.studyOutputTokens,
                 HeadroomFormat.compact(i.tokens.output),
                 "\(HeadroomFormat.compact(input)) input")
            tile(HeadroomCopy.studyOutputPerInput,
                 i.outputPer1kInput.map { String(format: "%.1f", $0) } ?? "None",
                 "tokens")
            tile(HeadroomCopy.studyEstimatedCost,
                 HeadroomFormat.usd(i.costUsdEstimate),
                 HeadroomCopy.studyEstimateNote(ratesChecked: i.ratesChecked))
        }
    }

    private func tile(_ label: String, _ value: String, _ caption: String)
        -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title2.weight(.semibold)).monospacedDigit()
            Text(caption).font(.caption2).foregroundStyle(.secondary)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardStyle()
        .accessibilityElement(children: .combine)
    }

    // MARK: Models

    private func models(_ i: StudyInsights) -> some View {
        section(HeadroomCopy.studyModels, caption: HeadroomCopy.studyModelsCaption) {
            VStack(spacing: 8) {
                ForEach(i.models) { model in
                    HStack(spacing: 10) {
                        Text(model.family.capitalized)
                            .frame(width: 64, alignment: .leading)
                        GeometryReader { proxy in
                            ZStack(alignment: .leading) {
                                Capsule().fill(.primary.opacity(0.08))
                                Capsule()
                                    .fill(Self.color(model.family))
                                    .frame(width: max(
                                        3, proxy.size.width * model.outputShare))
                            }
                        }
                        .frame(height: 8)
                        Text(String(format: "%.0f%%", model.outputShare * 100))
                            .monospacedDigit()
                            .frame(width: 40, alignment: .trailing)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
        }
    }

    private func monthly(_ i: StudyInsights) -> some View {
        section(HeadroomCopy.studyOutputByMonth) {
            Chart {
                ForEach(i.monthly) { month in
                    ForEach(month.output.sorted(by: { $0.key < $1.key }),
                            id: \.key) { family, tokens in
                        BarMark(
                            x: .value("Month", month.month),
                            y: .value("Output tokens", tokens)
                        )
                        .foregroundStyle(by: .value("Model", family))
                    }
                }
            }
            .chartForegroundStyleScale(
                domain: Self.families,
                range: Self.families.map(Self.color))
            .chartYAxis { compactYAxis }
            .frame(height: 170)
        }
    }

    // MARK: Rhythm

    private static let weekdayNames = [
        "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
    ]

    private func rhythm(_ i: StudyInsights) -> some View {
        section(HeadroomCopy.studyWhenYouWork) {
            VStack(alignment: .leading, spacing: 14) {
                Chart {
                    ForEach(Array(i.hours.enumerated()), id: \.offset) { hour, n in
                        BarMark(x: .value("Hour", hour),
                                y: .value("Turns", n))
                            .foregroundStyle(Color.accentColor)
                    }
                }
                .chartXAxis {
                    AxisMarks(values: [0, 6, 12, 18, 23]) { value in
                        AxisGridLine()
                        AxisValueLabel {
                            if let hour = value.as(Int.self) {
                                Text(String(format: "%02d", hour))
                            }
                        }
                    }
                }
                .chartYAxis { compactYAxis }
                .frame(height: 110)

                Chart {
                    ForEach(Array(i.weekdays.enumerated()), id: \.offset) { day, n in
                        BarMark(x: .value("Day", Self.weekdayNames[day]),
                                y: .value("Turns", n))
                            .foregroundStyle(Color.accentColor)
                    }
                }
                .chartXScale(domain: Self.weekdayNames)
                .chartYAxis { compactYAxis }
                .frame(height: 90)
            }
        }
    }

    private func prompts(_ i: StudyInsights) -> some View {
        section(HeadroomCopy.studyPromptLength,
                caption: HeadroomCopy.studyPromptLengthCaption) {
            let labels = i.promptLen.map { Self.lowerBound($0.bin) }
            Chart {
                ForEach(Array(i.promptLen.enumerated()), id: \.offset) { index, bin in
                    BarMark(x: .value("Tokens", labels[index]),
                            y: .value("Prompts", bin.count))
                        .foregroundStyle(Color.accentColor)
                }
            }
            .chartXScale(domain: labels)
            .chartYAxis { compactYAxis }
            .frame(height: 120)
        }
    }

    private func sessions(_ i: StudyInsights) -> some View {
        let s = i.session
        return section(HeadroomCopy.studySessions) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Median \(Self.minutes(s.medianMin)), 90th percentile \(Self.minutes(s.p90Min)), longest \(Self.minutes(s.longestMin)). Median \(s.medianTurns) turns.")
                Text(String(format: "%.0f%% of turns run in sub-agents.",
                            i.subagentShare * 100))
                    .foregroundStyle(.secondary)
                if !i.tools.isEmpty {
                    Text(HeadroomCopy.studyTools).font(.caption)
                        .foregroundStyle(.secondary).padding(.top, 4)
                    Text(i.tools.map {
                        "\($0.name) \(HeadroomFormat.compact($0.count))"
                    }.joined(separator: " · "))
                        .font(.callout)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // MARK: Card

    private func card(_ snapshot: StudySnapshot) -> some View {
        section(HeadroomCopy.studyCard, caption: HeadroomCopy.studyCardHint) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    TextField(HeadroomCopy.studyHandle, text: $handleDraft)
                        .textFieldStyle(.roundedBorder)
                        .focused($handleFocused)
                        .onSubmit(saveHandle)
                    Button(HeadroomCopy.studySave, action: saveHandle)
                        .disabled(store.isSavingHandle
                                  || handleDraft == snapshot.handle)
                }
                .onChange(of: snapshot.handle, initial: true) { _, new in
                    if !handleFocused { handleDraft = new }
                }
                if let handleMessage {
                    Text(handleMessage).font(.caption).foregroundStyle(.secondary)
                }

                if let card = snapshot.card {
                    cardShares(card.data)
                }

                if let text = snapshot.cardText {
                    HStack(alignment: .top, spacing: 10) {
                        Text(text)
                            .font(.caption.monospaced())
                            .lineLimit(2)
                            .truncationMode(.middle)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Button(store.copiedCard
                               ? HeadroomCopy.studyCopied
                               : HeadroomCopy.studyCopyCard) {
                            store.copyCard()
                        }
                    }
                }
            }
        }
    }

    private func cardShares(_ d: StudyCardData) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            shareRow("Prompts", d.promptLenShare, unit: "")
            shareRow("Replies", d.outPerTurnShare, unit: "")
            shareRow("Sessions", d.sessionActiveMinShare, unit: " min")
            shareRow("Time of day", d.timeOfDayShare.mapKeys(Self.dayBlock), unit: nil)
            if let cache = d.cacheHitBucketPct {
                row("Cache hit", "\(cache)% or more")
            }
        }
        .font(.callout)
    }

    private func shareRow(_ label: String, _ shares: [String: Double], unit: String?)
        -> some View {
        row(label, Self.describe(shares, unit: unit))
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(.secondary)
                .frame(width: 84, alignment: .leading)
            Text(value)
        }
    }

    private func saveHandle() {
        Task {
            handleMessage = await store.saveHandle(handleDraft)
        }
    }

    // MARK: Pieces

    private func section<Body: View>(
        _ title: String, caption: String? = nil,
        @ViewBuilder _ body: () -> Body
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            if let caption {
                Text(caption).font(.caption).foregroundStyle(.secondary)
            }
            body()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardStyle()
    }

    private var compactYAxis: some AxisContent {
        AxisMarks { value in
            AxisGridLine()
            AxisValueLabel {
                if let n = value.as(Int.self) {
                    Text(HeadroomFormat.compact(n))
                }
            }
        }
    }

    // Model families are not providers, so they take neutral picks from the
    // palette rather than a provider's brand colour.
    static let families = ["opus", "sonnet", "haiku", "fable", "other"]

    static func color(_ family: String) -> Color {
        switch family {
        case "opus": HeadroomPalette.claude
        case "sonnet": HeadroomPalette.amber
        case "haiku": HeadroomPalette.local
        case "fable": HeadroomPalette.git
        default: HeadroomPalette.dim
        }
    }

    /// "131072-262143" → "131k". The lower bound labels the bin; the host bins
    /// on powers of two, so the upper bound is redundant on an axis.
    static func lowerBound(_ bin: String) -> String {
        let head = bin.split(separator: "-").first.map(String.init) ?? bin
        return Int(head).map(HeadroomFormat.compact) ?? head
    }

    static func minutes(_ value: Double) -> String {
        value >= 90 ? String(format: "%.1f h", value / 60)
            : String(format: "%.0f min", value)
    }

    private static func dayBlock(_ key: String) -> String {
        switch key {
        case "night_00_05": "00-05"
        case "morning_06_11": "06-11"
        case "afternoon_12_17": "12-17"
        case "evening_18_23": "18-23"
        default: key
        }
    }

    /// {"1": 0.3, "8": 0.45} → "1+ 30%, 8+ 45%". Numeric bins read as lower
    /// bounds; the time-of-day blocks are already labelled.
    static func describe(_ shares: [String: Double], unit: String?) -> String {
        if shares.isEmpty { return "None" }
        let ordered = shares.sorted { lhs, rhs in
            switch (Int(lhs.key), Int(rhs.key)) {
            case let (a?, b?): a < b
            default: lhs.key < rhs.key
            }
        }
        return ordered.map { key, share in
            let percent = String(format: "%.0f%%", share * 100)
            guard let unit, Int(key) != nil else { return "\(key) \(percent)" }
            return "\(key)+\(unit) \(percent)"
        }.joined(separator: ", ")
    }
}

private extension Dictionary where Key == String {
    func mapKeys(_ transform: (String) -> String) -> [String: Value] {
        Dictionary(uniqueKeysWithValues: map { (transform($0.key), $0.value) })
    }
}
