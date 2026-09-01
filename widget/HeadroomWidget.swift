import SwiftUI
import WidgetKit

struct HeadroomWidgetEntry: TimelineEntry {
    let date: Date
    let snapshot: HeadroomWidgetSnapshot
}

struct HeadroomWidgetProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> HeadroomWidgetEntry {
        HeadroomWidgetEntry(date: .now, snapshot: .placeholder)
    }

    func snapshot(
        for configuration: HeadroomWidgetConfiguration,
        in context: Context
    ) async -> HeadroomWidgetEntry {
        // The gallery is the one place invented numbers are the right
        // answer — it is showing what the widget looks like, not what your
        // quota is. Everywhere else, no cache means say so.
        let snapshot = context.isPreview ? .placeholder : load()
        return entry(snapshot, configuration)
    }

    func timeline(
        for configuration: HeadroomWidgetConfiguration,
        in context: Context
    ) async -> Timeline<HeadroomWidgetEntry> {
        Timeline(
            entries: [entry(load(), configuration)],
            policy: .after(Date(timeIntervalSinceNow: 15 * 60))
        )
    }

    /// The gallery's own list: the default, then one tile per provider the
    /// cache knows. A picker that only ever offers "Headroom" is how someone
    /// ends up believing the widget can show one provider and no others.
    func recommendations() -> [AppIntentRecommendation<HeadroomWidgetConfiguration>] {
        let providers = HeadroomWidgetSnapshot.cached()?.providers ?? []
        return [
            AppIntentRecommendation(
                intent: HeadroomWidgetConfiguration(provider: .everyProvider),
                description: HeadroomProviderEntity.everyProvider.name
            ),
        ] + providers.map { provider in
            AppIntentRecommendation(
                intent: HeadroomWidgetConfiguration(
                    provider: HeadroomProviderEntity(
                        id: provider.id, name: provider.spokenTitle
                    )
                ),
                description: provider.spokenTitle
            )
        }
    }

    private func entry(
        _ snapshot: HeadroomWidgetSnapshot,
        _ configuration: HeadroomWidgetConfiguration
    ) -> HeadroomWidgetEntry {
        HeadroomWidgetEntry(
            date: .now,
            snapshot: snapshot.showing(configuration.chosenProviderID)
        )
    }

    private func load() -> HeadroomWidgetSnapshot {
        HeadroomWidgetSnapshot.cached() ?? .awaitingFirstSync
    }
}

/// Keeps every tile placed before provider selection existed alive.
///
/// Those tiles were serialized as a `StaticConfiguration` with the kind
/// `HeadroomWidget`. WidgetKit does not migrate an existing tile when the same
/// kind changes configuration systems; it leaves the tile on its last render
/// instead. Keeping that exact static definition lets WidgetKit resume asking
/// for timelines, while configurable tiles use a new identity below.
struct HeadroomLegacyWidgetProvider: TimelineProvider {
    func placeholder(in context: Context) -> HeadroomWidgetEntry {
        HeadroomWidgetEntry(date: .now, snapshot: .placeholder)
    }

    func getSnapshot(
        in context: Context,
        completion: @escaping (HeadroomWidgetEntry) -> Void
    ) {
        let snapshot = context.isPreview ? .placeholder : load()
        completion(HeadroomWidgetEntry(date: .now, snapshot: snapshot))
    }

    func getTimeline(
        in context: Context,
        completion: @escaping (Timeline<HeadroomWidgetEntry>) -> Void
    ) {
        completion(
            Timeline(
                entries: [HeadroomWidgetEntry(date: .now, snapshot: load())],
                policy: .after(Date(timeIntervalSinceNow: 15 * 60))
            )
        )
    }

    private func load() -> HeadroomWidgetSnapshot {
        HeadroomWidgetSnapshot.cached() ?? .awaitingFirstSync
    }
}

/// No product name, no status dot. A widget the user chose to place already
/// says which app it belongs to, and the chart says the rest — both of them
/// were spending the small size's only real currency, which is height.
///
/// **Every family names every source it has, in words, before it draws
/// anything.** The wide family used to be a chart and a legend of names, which
/// meant two states rendered as an empty box: a cache holding `burndown` keys
/// with no curve in them, and any failure inside the canvas. A widget that
/// cannot draw its chart still knows the numbers, so the numbers go first and
/// the chart is what is added to them.
struct HeadroomWidgetView: View {
    let entry: HeadroomWidgetEntry
    @Environment(\.widgetFamily) private var family

    /// The host already picked these — `FOCUS_LIMIT` sources, pinned order.
    /// Capped again here because a widget is not the place to discover that
    /// the number moved.
    private static let maximumProviders = 3

    private var providers: [HeadroomWidgetSnapshot.Provider] {
        Array(entry.snapshot.providers.prefix(Self.maximumProviders))
    }

    private var charted: [HeadroomWidgetSnapshot.Provider] {
        Array(entry.snapshot.charted.prefix(Self.maximumProviders))
    }

    var body: some View {
        Group {
            if family == .systemSmall {
                small
            } else {
                wide
            }
        }
        .containerBackground(.background, for: .widget)
    }

    /// Rings — the small size has no room for a week of chart. One source gets
    /// the whole tile; several share it rather than one of them standing in
    /// for the rest, which is what showing `providers.first` alone did.
    private var small: some View {
        VStack(alignment: .leading, spacing: 0) {
            if providers.count == 1, let solo = providers.first {
                HeadroomRings(layers: solo.ringLayers, tint: solo.tint)
                    .frame(
                        width: entry.snapshot.isStale ? 46 : 62,
                        height: entry.snapshot.isStale ? 46 : 62
                    )
                Spacer(minLength: entry.snapshot.isStale ? 4 : 8)
                Text(solo.title)
                    .font(.caption.weight(.semibold))
                    .lineLimit(1)
                Text(HeadroomCopy.percentUsed(solo.percent))
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
                ForEach(solo.widgetResetLabels, id: \.self) { label in
                    Text(label)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            } else if providers.isEmpty {
                emptyLine
            } else {
                ringRow(
                    diameter: providers.count > 2
                        ? (entry.snapshot.isStale ? 32 : 38)
                        : (entry.snapshot.isStale ? 38 : 50),
                    showsResets: true,
                    showsPace: false
                )
            }
            staleNote
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    /// The combined burndown, every provider on one axis — the same chart the
    /// Mac's Overview leads with, under a line naming each source and what it
    /// has left. Rings stand in until there is history.
    @ViewBuilder
    private var wide: some View {
        if providers.isEmpty {
            VStack(alignment: .leading, spacing: 0) { emptyLine }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        } else if charted.isEmpty {
            VStack(
                alignment: .leading,
                spacing: entry.snapshot.isStale ? 4 : 8
            ) {
                ringRow(
                    diameter: entry.snapshot.isStale ? 40 : 54,
                    showsResets: false,
                    showsPace: true
                )
                Text(HeadroomCopy.noHistoryYet)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                staleNote
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        } else {
            VStack(alignment: .leading, spacing: 6) {
                legend
                CombinedBurndownChart(providers: charted)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        }
    }

    /// One cell per source: its rings, its name, and its readings. The small
    /// family reserves both reset rows; the medium fallback reserves pace,
    /// matching the chart legend even before history arrives.
    private func ringRow(
        diameter: CGFloat,
        showsResets: Bool,
        showsPace: Bool
    ) -> some View {
        HStack(alignment: .top, spacing: providers.count > 2 ? 4 : 8) {
            ForEach(providers) { provider in
                VStack(
                    spacing: entry.snapshot.isStale
                        ? 2
                        : (providers.count > 2 ? 3 : 5)
                ) {
                    HeadroomRings(
                        layers: provider.ringLayers, tint: provider.tint
                    )
                    .frame(width: diameter, height: diameter)
                    Text(provider.title)
                        .font(.caption2)
                        .lineLimit(1)
                        .minimumScaleFactor(0.5)
                    Text(HeadroomCopy.percentUsed(provider.percent))
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                    if showsPace {
                        Text(provider.widgetPaceSlackLabel)
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .minimumScaleFactor(0.6)
                    }
                    if showsResets {
                        ForEach(provider.widgetResetLabels, id: \.self) { label in
                            Text(label)
                                .font(.caption2.monospacedDigit())
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                                .minimumScaleFactor(0.5)
                        }
                    }
                }
                .frame(
                    maxWidth: providers.count > 1 ? .infinity : nil,
                    alignment: .top
                )
            }
        }
    }

    /// Names the chart's lines and reads them at once.
    ///
    /// The swatch is the line's own colour, so this is the chart's key; the
    /// figure is what is **left**, because that is the axis the line is drawn
    /// against. Rings say "used" on the same surfaces — both words stay
    /// attached to their number wherever the two glyphs meet (docs/glossary.md).
    /// A source with no line still gets its row: it is one of your sources, and
    /// a widget that quietly drops it reads as a widget that lost it.
    private var legend: some View {
        HStack(alignment: .top, spacing: 8) {
            ForEach(providers) { provider in
                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 4) {
                        Capsule()
                            .fill(provider.burndownTint)
                            .frame(width: 10, height: 2.5)
                        Text(provider.title)
                            .font(.caption2)
                            .lineLimit(1)
                    }
                    Text(HeadroomCopy.percentLeft(100 - provider.percent))
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    Text(provider.widgetPaceSlackLabel)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .layoutPriority(1)
            }
            Spacer(minLength: 0)
            if entry.snapshot.isStale {
                Text(HeadroomCopy.ago(entry.snapshot.age))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .minimumScaleFactor(0.75)
    }

    private var emptyLine: some View {
        Text(entry.snapshot.attentionSummary ?? HeadroomCopy.openHeadroom)
            .font(.caption)
            .foregroundStyle(.secondary)
    }

    @ViewBuilder
    private var staleNote: some View {
        // With no providers there is nothing to be stale about, and the
        // empty-state line already explains itself — an age beside it would
        // be measuring a reading that was never taken.
        if entry.snapshot.isStale, !providers.isEmpty {
            Text(HeadroomCopy.ago(entry.snapshot.age))
                .font(.caption2)
                .foregroundStyle(.secondary)
                .padding(.top, 4)
        }
    }
}

/// Every provider's remaining quota on one week-wide axis.
///
/// The Mac's `OverviewBurndownCard` in a widget's worth of space: same domain,
/// same weekday bands, same history ghosts behind a solid-then-dashed reading
/// of measured against forecast. What it drops is what needs a caption to be
/// worth the pixels — the percent gutter, the per-provider reset countdowns,
/// the verdicts. Upcoming renewal rules stay on the provider card too.
private struct CombinedBurndownChart: View {
    let providers: [HeadroomWidgetSnapshot.Provider]

    var body: some View {
        Canvas { context, size in
            // Latest sample across the charted pools — same clock the Mac
            // overview uses — so a stale cache does not walk "now" past the
            // strokes it still holds.
            let sampleNow = providers
                .compactMap { $0.burndown?.latestSampleTime }
                .max() ?? Date().timeIntervalSince1970
            let plot = BurndownGeometry(
                rect: burndownPlotRect(in: size, axis: true, gutter: 0),
                domain: OverallBurndownChartMath.domain(
                    now: Date(timeIntervalSince1970: sampleNow)
                )
            )
            guard plot.isDrawable else { return }
            let domain = plot.domain

            drawBurndownScale(&context, plot: plot.rect, labels: false)
            drawBurndownCalendar(
                &context, plot: plot.rect,
                start: domain.startEpoch,
                end: domain.endEpoch,
                now: domain.nowEpoch
            )
            context.stroke(
                plot.rule(at: domain.nowEpoch),
                with: .color(.secondary.opacity(0.4)),
                lineWidth: 1
            )

            for provider in providers {
                guard let series = provider.burndown else { continue }
                let tint = provider.burndownTint

                // Windows already spent, behind everything else. Faint and
                // thin: history that stopped counting, never mistaken for the
                // live curve. Re-clip in case the widget's clock moved on.
                let spent = OverallBurndownChartMath.clipPolyline(
                    series.history ?? [],
                    start: domain.startEpoch,
                    end: domain.endEpoch
                )
                if let ghost = plot.line(spent) {
                    context.stroke(
                        ghost,
                        with: .color(tint.opacity(0.3)),
                        style: StrokeStyle(lineWidth: 1.5, lineJoin: .round)
                    )
                }

                let actual = OverallBurndownChartMath.preparedActual(
                    series.actual, domain: domain
                )
                if let line = plot.line(actual) {
                    context.stroke(
                        line,
                        with: .color(tint),
                        style: StrokeStyle(lineWidth: 2, lineJoin: .round)
                    )
                }
                if let last = actual.last, let head = plot.dot(last, diameter: 6) {
                    context.fill(head, with: .color(tint))
                }

                let projected = OverallBurndownChartMath.preparedProjection(
                    series.projected,
                    windowEnd: series.windowEnd,
                    domain: domain
                )
                if let forecast = plot.line(projected) {
                    context.stroke(
                        forecast,
                        with: .color(tint),
                        style: StrokeStyle(
                            lineWidth: 1.5, lineJoin: .round, dash: [6, 2]
                        )
                    )
                    if let hit = projected.last {
                        let exhausted = hit.count >= 2 && hit[1] <= 0
                        let diameter: CGFloat = exhausted ? 6 : 4
                        if let mark = plot.dot(hit, diameter: diameter) {
                            context.fill(
                                mark, with: .color(tint.opacity(0.85))
                            )
                        }
                    }
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            "\(HeadroomCopy.overallBurndown), \(HeadroomCopy.overallBurndownSubtitle). "
                + providers.map { provider in
                    "\(provider.title) \(Int(provider.percent.rounded())) percent used"
                }.joined(separator: ", ")
        )
    }
}

@main
struct HeadroomWidgetBundle: WidgetBundle {
    var body: some Widget {
        HeadroomLegacyStatusWidget()
        HeadroomStatusWidget()
    }
}

private enum HeadroomWidgetGallery {
    /// Same extension, two homes. On the Mac the numbers never left the machine
    /// the widget is sitting on, so "from your Mac" would read strangely there.
    static var subtitle: String {
        #if os(macOS)
        return "Coding quota and attention status at a glance."
        #else
        return "Coding quota and attention status from your Mac."
        #endif
    }
}

/// The original, all-provider widget. Its definition is retained because its
/// kind and configuration type are part of every tile WidgetKit already saved.
struct HeadroomLegacyStatusWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(
            kind: HeadroomWidgetIdentity.legacyKind,
            provider: HeadroomLegacyWidgetProvider()
        ) { entry in
            HeadroomWidgetView(entry: entry)
        }
        .configurationDisplayName("Headroom — All providers")
        .description(HeadroomWidgetGallery.subtitle)
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

/// New placements can select one provider without changing the serialized
/// definition of the original widget.
struct HeadroomStatusWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(
            kind: HeadroomWidgetIdentity.configurableKind,
            intent: HeadroomWidgetConfiguration.self,
            provider: HeadroomWidgetProvider()
        ) { entry in
            HeadroomWidgetView(entry: entry)
        }
        .configurationDisplayName("Headroom — Provider")
        .description(HeadroomWidgetGallery.subtitle)
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}
