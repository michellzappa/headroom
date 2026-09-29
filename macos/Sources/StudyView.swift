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
    @State private var tab: Tab = .you
    @State private var friendDraft = ""
    @State private var renamingID: String?
    @State private var aliasDraft = ""
    @State private var aliasMessage: String?
    @State private var removing: StudyFriend?
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
        .confirmationDialog(
            HeadroomCopy.studyRemoveFriendTitle(removing?.name ?? ""),
            isPresented: Binding(
                get: { removing != nil },
                set: { if !$0 { removing = nil } }),
            presenting: removing
        ) { friend in
            Button(HeadroomCopy.studyRemove, role: .destructive) {
                Task { await store.removeFriend(friend.id) }
            }
        }
    }

    // MARK: State

    private enum Tab: String, CaseIterable, Identifiable {
        case you, friends
        var id: String { rawValue }
        var title: String {
            self == .you ? HeadroomCopy.studyTabYou : HeadroomCopy.studyTabFriends
        }
    }

    @ViewBuilder
    private var content: some View {
        if !store.isLocalHost {
            notice(HeadroomCopy.studyRemoteHost)
        } else if let snapshot = store.snapshot {
            Picker("", selection: $tab) {
                ForEach(Tab.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 220)
            if let message = store.errorMessage {
                notice(message)
            }
            switch tab {
            case .you: youTab(snapshot)
            case .friends: friendsTab(snapshot)
            }
        } else if let message = store.errorMessage {
            notice(message)
        } else {
            reading
        }
    }

    @ViewBuilder
    private func youTab(_ snapshot: StudySnapshot) -> some View {
        if let insights = snapshot.insights {
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
        // Always shown, even with no usage of its own: a Mac with no session
        // logs is exactly the one that needs to add another Mac's counts.
        macs(snapshot)
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

    // MARK: Other Macs

    private func macs(_ snapshot: StudySnapshot) -> some View {
        section(HeadroomCopy.studyMacs, caption: HeadroomCopy.studyMacsHint) {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(snapshot.machines) { machine in
                    HStack {
                        Text(machine.name)
                        Text(machine.thisMac
                             ? HeadroomCopy.studyThisMac
                             : "\(HeadroomCopy.studyAnotherMac), counts from \(machine.generated.map { String($0.prefix(10)) } ?? "an earlier day")")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Spacer()
                        if !machine.thisMac {
                            Button(HeadroomCopy.studyRemove) {
                                Task { await store.removeMachine(machine.id) }
                            }
                        }
                    }
                }
                HStack {
                    Button(HeadroomCopy.studyExportCounts) {
                        Task { await store.exportCounts() }
                    }
                    Button(HeadroomCopy.studyImportCounts) {
                        Task { await store.importCounts() }
                    }
                }
                if let message = store.macsMessage {
                    Text(message).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    // MARK: Friends

    @ViewBuilder
    private func friendsTab(_ snapshot: StudySnapshot) -> some View {
        section(HeadroomCopy.studyAddFriend,
                caption: HeadroomCopy.studyAddFriendHint) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    TextField(HeadroomCopy.studyCardField, text: $friendDraft)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit(addDraft)
                    Button(HeadroomCopy.studyAdd, action: addDraft)
                        .disabled(friendDraft.trimmingCharacters(
                            in: .whitespacesAndNewlines).isEmpty)
                    Button(HeadroomCopy.studyPasteAndAdd) {
                        Task { await store.addFriendFromClipboard() }
                    }
                }
                if let message = store.friendMessage {
                    Text(message).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        if snapshot.friends.isEmpty {
            Text(HeadroomCopy.studyNoFriends).foregroundStyle(.secondary)
        } else {
            ForEach(snapshot.friends) { friend in
                friendCard(friend, mine: snapshot.card)
            }
        }
    }

    private func addDraft() {
        Task {
            if await store.addFriend(friendDraft) { friendDraft = "" }
        }
    }

    private func friendCard(_ friend: StudyFriend, mine: StudyCard?)
        -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if renamingID == friend.id {
                    TextField(HeadroomCopy.studyAliasField, text: $aliasDraft)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { saveAlias(friend) }
                    Button(HeadroomCopy.studySave) { saveAlias(friend) }
                } else {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(friend.name).font(.headline)
                        if friend.alias != nil {
                            Text(friend.handle)
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                Spacer()
                Text(friend.week).font(.caption).foregroundStyle(.secondary)
                Menu {
                    Button(HeadroomCopy.studyRename) {
                        aliasDraft = friend.alias ?? ""
                        aliasMessage = nil
                        renamingID = friend.id
                    }
                    Button(HeadroomCopy.studyRemove, role: .destructive) {
                        removing = friend
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
            }
            if renamingID == friend.id, let aliasMessage {
                Text(aliasMessage).font(.caption).foregroundStyle(.secondary)
            }
            compare(mine: mine?.data, theirs: friend.data, name: friend.name)
            if let mine, mine.week != friend.week {
                Text(HeadroomCopy.studyStaleCard)
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardStyle()
    }

    private func saveAlias(_ friend: StudyFriend) {
        Task {
            if let message = await store.setAlias(friend.id, aliasDraft) {
                aliasMessage = message
            } else {
                aliasMessage = nil
                renamingID = nil
            }
        }
    }

    // MARK: Comparison
    //
    // Both columns are cards: yours is the card a friend would get from you,
    // not your detailed numbers. Like for like, so nothing you see about a
    // friend is finer than what they see about you.

    private static let dayBlockOrder = [
        "night_00_05", "morning_06_11", "afternoon_12_17", "evening_18_23",
    ]

    private func compare(mine: StudyCardData?, theirs: StudyCardData, name: String)
        -> some View {
        Grid(alignment: .topLeading, horizontalSpacing: 14, verticalSpacing: 10) {
            GridRow {
                Color.clear.frame(width: 84, height: 1)
                Text(HeadroomCopy.studyYouColumn)
                    .font(.caption).foregroundStyle(.secondary)
                    .gridColumnAlignment(.leading)
                Text(name)
                    .font(.caption).foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            compareRow("Models", modelsCell(mine), modelsCell(theirs))
            compareRow("Time of day", dayCell(mine), dayCell(theirs))
            compareRow("Prompts", textCell(mine?.promptLenShare, unit: ""),
                       textCell(theirs.promptLenShare, unit: ""))
            compareRow("Replies", textCell(mine?.outPerTurnShare, unit: ""),
                       textCell(theirs.outPerTurnShare, unit: ""))
            compareRow("Sessions", textCell(mine?.sessionActiveMinShare, unit: " min"),
                       textCell(theirs.sessionActiveMinShare, unit: " min"))
            compareRow("Cache hit", cacheCell(mine), cacheCell(theirs))
        }
    }

    private func compareRow<A: View, B: View>(
        _ label: String, _ a: A, _ b: B
    ) -> some View {
        GridRow {
            Text(label).font(.callout).foregroundStyle(.secondary)
                .frame(width: 84, alignment: .leading)
            a.frame(maxWidth: .infinity, alignment: .leading)
            b.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func modelsCell(_ d: StudyCardData?) -> some View {
        let week = d?.weeklyModelShare.keys.sorted().last
        let shares = week.flatMap { d?.weeklyModelShare[$0] } ?? [:]
        return shareCell(
            shares, order: Self.families,
            color: { _, key in Self.color(key) },
            text: Self.describe(shares, unit: nil))
    }

    private func dayCell(_ d: StudyCardData?) -> some View {
        let shares = d?.timeOfDayShare ?? [:]
        return shareCell(
            shares, order: Self.dayBlockOrder,
            color: { index, _ in Color.accentColor.opacity(0.3 + 0.2 * Double(index)) },
            text: Self.describe(shares.mapKeys(Self.dayBlock), unit: nil))
    }

    private func shareCell(
        _ shares: [String: Double], order: [String],
        color: @escaping (Int, String) -> Color, text: String
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            if !shares.isEmpty {
                ShareBar(
                    parts: order.compactMap { key in
                        shares[key].map { (key, $0) }
                    },
                    color: color)
            }
            Text(text).font(.caption)
        }
    }

    private func textCell(_ shares: [String: Double]?, unit: String) -> some View {
        Text(Self.describe(shares ?? [:], unit: unit)).font(.caption)
    }

    private func cacheCell(_ d: StudyCardData?) -> some View {
        Text(d?.cacheHitBucketPct.map { "\($0)% or more" } ?? "None")
            .font(.caption)
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

/// A row of segments sized by share. Rounded shares can add up to a little over
/// or under one, so widths are taken as a fraction of their own sum.
private struct ShareBar: View {
    let parts: [(String, Double)]
    let color: (Int, String) -> Color

    var body: some View {
        let total = max(parts.reduce(0) { $0 + $1.1 }, 0.0001)
        GeometryReader { proxy in
            HStack(spacing: 1) {
                ForEach(Array(parts.enumerated()), id: \.offset) { index, part in
                    Rectangle()
                        .fill(color(index, part.0))
                        .frame(width: max(
                            2, (proxy.size.width - CGFloat(parts.count)) * part.1 / total))
                }
            }
            .clipShape(Capsule())
        }
        .frame(height: 8)
    }
}
