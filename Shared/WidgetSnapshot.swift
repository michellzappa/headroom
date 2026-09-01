import Foundation
#if os(macOS)
import Security
#endif

/// Where the app leaves the widget's cache, on both platforms.
///
/// The group id is not the same string everywhere. iOS provisions the bare
/// `group.…` name, while macOS requires the team id in front of it — including
/// for an app like this one that runs outside the sandbox. Hardcoding the team
/// would stop forks from signing as themselves (see `$HEADROOM_TEAM_ID`), so
/// the Mac reads its own signature for it instead.
enum HeadroomAppGroup {
    static let name = "group.com.centaur-labs.headroom"

    /// Nil on macOS only when the running copy has no team — an ad-hoc or
    /// unsigned build. The sandbox would deny that copy the container anyway,
    /// so the widget falls through to its placeholder rather than reading a
    /// suite that silently isn't shared.
    static let identifier: String? = {
        #if os(macOS)
        guard let team = signingTeamIdentifier() else { return nil }
        return "\(team).\(name)"
        #else
        return name
        #endif
    }()

    static func defaults() -> UserDefaults? {
        guard let identifier else { return nil }
        return UserDefaults(suiteName: identifier)
    }

    static func containerURL() -> URL? {
        guard let identifier else { return nil }
        return FileManager.default.containerURL(
            forSecurityApplicationGroupIdentifier: identifier
        )
    }

    /// Key for the encoded `HeadroomWidgetSnapshot` inside the group defaults.
    static let snapshotKey = "widgetSnapshot"

    /// Key for the same payload inside a WatchConnectivity dictionary. The
    /// phone writes it, the watch reads it, and neither can see the other's
    /// container — so this is the one string that has to agree across devices.
    static let watchPayloadKey = "snapshot"

    #if os(macOS)
    private static func signingTeamIdentifier() -> String? {
        var code: SecCode?
        guard SecCodeCopySelf([], &code) == errSecSuccess, let code else {
            return nil
        }
        var staticCode: SecStaticCode?
        guard SecCodeCopyStaticCode(code, [], &staticCode) == errSecSuccess,
              let staticCode
        else { return nil }
        var info: CFDictionary?
        guard SecCodeCopySigningInformation(
            staticCode,
            SecCSFlags(rawValue: kSecCSSigningInformation),
            &info
        ) == errSecSuccess,
            let values = info as? [String: Any]
        else { return nil }
        return values[kSecCodeInfoTeamIdentifier as String] as? String
    }
    #endif
}

struct HeadroomWidgetSnapshot: Codable, Sendable {
    struct Provider: Codable, Identifiable, Sendable {
        struct Layer: Codable, Sendable {
            var id: String
            /// The pool's title. Optional because a cache written before this
            /// field existed carried the title in `id`, which is what the
            /// fallback reads — see `WidgetSnapshotPresentation`.
            var name: String?
            var percent: Double?
            var pacePercent: Double?
            /// Compact countdown source for the small widget. Optional for
            /// caches written before reset rows existed.
            var resetsIn: String?
        }

        /// One provider's line on the combined burndown: what is left against
        /// time, plus the dashed forecast. Compact `[[epoch, remainingPct], …]`
        /// pairs, cropped and clipped to the chart's week before they are
        /// written, so the widget only has to scale them into a rect.
        struct Series: Codable, Sendable {
            var actual: [[Double]]
            var projected: [[Double]]
            /// Windows already spent, squared at each riser and clipped to the
            /// week — the faint sawtooth behind `actual`. Optional so a cache
            /// written before this field still decodes.
            var history: [[Double]]?
            /// Held for forecast cropping (`preparedProjection`); the overview
            /// chart does not paint a renewal rule from it.
            var windowEnd: Double?
            var exhausted: Bool?

            init(
                actual: [[Double]] = [],
                projected: [[Double]] = [],
                history: [[Double]]? = nil,
                windowEnd: Double? = nil,
                exhausted: Bool? = nil
            ) {
                self.actual = actual
                self.projected = projected
                self.history = history
                self.windowEnd = windowEnd
                self.exhausted = exhausted
            }

            /// An absent curve is an empty one, never a decode failure — the
            /// chart simply has nothing to draw for it.
            init(from decoder: Decoder) throws {
                let row = try decoder.container(keyedBy: CodingKeys.self)
                actual = try row.decodeIfPresent(
                    [[Double]].self, forKey: .actual) ?? []
                projected = try row.decodeIfPresent(
                    [[Double]].self, forKey: .projected) ?? []
                history = try row.decodeIfPresent(
                    [[Double]].self, forKey: .history)
                windowEnd = try row.decodeIfPresent(
                    Double.self, forKey: .windowEnd)
                exhausted = try row.decodeIfPresent(
                    Bool.self, forKey: .exhausted)
            }
        }

        var id: String
        /// Drawn beside the mark, so it is the host's short `label` —
        /// `Work` for `claude:work`.
        var title: String
        /// The full title for surfaces with no mark to lean on, which in
        /// practice means anything spoken: `Claude · Work`. Optional for the
        /// same reason `Layer.name` is — an older app's cache has no such key,
        /// and `title` is the closest thing it holds.
        var name: String?
        var percent: Double
        /// Percentage points between this provider's binding quota and its
        /// ideal pace. Optional so an older cache still decodes; the widget
        /// presents absence as an explicit unknown reading.
        var paceDeltaPct: Double?
        /// Reset countdowns live at provider scope rather than only on ring
        /// layers: a provider can know when a pool resets before it has a
        /// percentage worth drawing. Optional for older caches.
        var sessionResetsIn: String?
        var weekResetsIn: String?
        var accent: String?
        /// Optional so widgets can still decode a cache written by an older app.
        var layers: [Layer]?
        /// Also optional: a provider with no history yet has no line to draw,
        /// and the wide widget falls back to rings when none of them do.
        var burndown: Series?

        init(
            id: String,
            title: String,
            name: String? = nil,
            percent: Double = 0,
            paceDeltaPct: Double? = nil,
            sessionResetsIn: String? = nil,
            weekResetsIn: String? = nil,
            accent: String? = nil,
            layers: [Layer]? = nil,
            burndown: Series? = nil
        ) {
            self.id = id
            self.title = title
            self.name = name
            self.percent = percent
            self.paceDeltaPct = paceDeltaPct
            self.sessionResetsIn = sessionResetsIn
            self.weekResetsIn = weekResetsIn
            self.accent = accent
            self.layers = layers
            self.burndown = burndown
        }

        /// Only `id` is genuinely required: it is identity, and a row without
        /// one cannot be drawn or told apart. Everything else falls back, so a
        /// cache written by a different build costs at most a label.
        init(from decoder: Decoder) throws {
            let row = try decoder.container(keyedBy: CodingKeys.self)
            id = try row.decode(String.self, forKey: .id)
            title = try row.decodeIfPresent(String.self, forKey: .title) ?? id
            name = try row.decodeIfPresent(String.self, forKey: .name)
            percent = try row.decodeIfPresent(
                Double.self, forKey: .percent) ?? 0
            paceDeltaPct = try row.decodeIfPresent(
                Double.self, forKey: .paceDeltaPct)
            sessionResetsIn = try row.decodeIfPresent(
                String.self, forKey: .sessionResetsIn)
            weekResetsIn = try row.decodeIfPresent(
                String.self, forKey: .weekResetsIn)
            accent = try row.decodeIfPresent(String.self, forKey: .accent)
            layers = try row.decodeLossyArrayIfPresent(
                Layer.self, forKey: .layers)
            burndown = try row.decodeIfPresent(Series.self, forKey: .burndown)
        }
    }

    var updatedAt: Date
    var attentionLevel: String?
    var attentionSummary: String?
    var providers: [Provider]

    init(
        updatedAt: Date,
        attentionLevel: String? = nil,
        attentionSummary: String? = nil,
        providers: [Provider] = []
    ) {
        self.updatedAt = updatedAt
        self.attentionLevel = attentionLevel
        self.attentionSummary = attentionSummary
        self.providers = providers
    }

    /// Tolerant on purpose, and more so than `/usage` needs to be.
    ///
    /// This cache outlives the build that wrote it, and on the watch it is
    /// the only thing a complication ever sees — `WatchSnapshotCache` decodes
    /// with `try?`, so a single unreadable key would not show an error, it
    /// would show the placeholder forever, with nothing to say why. A missing
    /// `updatedAt` therefore reads as `distantPast`, which `isStale` already
    /// knows how to say out loud, and one malformed provider costs that
    /// provider rather than the whole face.
    init(from decoder: Decoder) throws {
        let root = try decoder.container(keyedBy: CodingKeys.self)
        updatedAt = try root.decodeIfPresent(
            Date.self, forKey: .updatedAt) ?? .distantPast
        attentionLevel = try root.decodeIfPresent(
            String.self, forKey: .attentionLevel)
        attentionSummary = try root.decodeIfPresent(
            String.self, forKey: .attentionSummary)
        providers = try root.decodeLossyArrayIfPresent(
            Provider.self, forKey: .providers) ?? []
    }

    /// Unlike the app, a widget never learns that a fetch failed — it only ever
    /// sees the cache. So it judges by age, with a couple of refresh intervals
    /// of slack before it calls the numbers history.
    static let freshWindow: TimeInterval = 45 * 60

    var isStale: Bool {
        Date().timeIntervalSince(updatedAt) > Self.freshWindow
    }

    var age: TimeInterval {
        Date().timeIntervalSince(updatedAt)
    }

    /// The last cache the app wrote, or nil when there is none to read — no
    /// group container, nothing written yet, or a payload this build can't
    /// decode. Every one of those reads the same to a widget: placeholder.
    static func cached() -> HeadroomWidgetSnapshot? {
        guard let data = HeadroomAppGroup.defaults()?
                .data(forKey: HeadroomAppGroup.snapshotKey)
        else { return nil }
        return try? JSONDecoder().decode(Self.self, from: data)
    }

    /// Gallery preview only: a week-shaped series, so the widget picker shows
    /// the chart it will draw rather than an empty frame.
    private static func demoBurndown(
        remaining: Double,
        perDay: Double
    ) -> Provider.Series {
        let day: TimeInterval = 24 * 60 * 60
        let now = Date().timeIntervalSince1970
        let spent = 100 - remaining
        let priorRemaining = min(100, remaining + perDay * 3)
        return Provider.Series(
            actual: stride(from: 0.0, through: 3.0, by: 0.25).map { offset in
                [now - (3 - offset) * day, 100 - spent * offset / 3]
            },
            projected: stride(from: 0.0, through: 4.0, by: 0.5).map { offset in
                [now + offset * day, max(0, remaining - perDay * offset)]
            },
            // A prior window that drained then recharged — same shape the
            // overview ghosts behind the live curve.
            history: [
                [now - 6.5 * day, priorRemaining],
                [now - 4.5 * day, max(8, priorRemaining - perDay * 2)],
                [now - 3.5 * day, max(8, priorRemaining - perDay * 2)],
                [now - 3.5 * day, 100],
                [now - 3.0 * day, 100 - spent * 0.05],
            ],
            windowEnd: now + 4 * day,
            exhausted: false
        )
    }

    /// Nothing has been cached yet — no app group, no fetch, or a payload
    /// this build could not read.
    ///
    /// Distinct from `placeholder` on purpose, and the distinction is the
    /// whole point: `placeholder` carries invented numbers for the widget
    /// gallery, where showing a real-looking chart is the job. Rendering
    /// those same numbers on someone's actual home screen states that Claude
    /// is at 42% when nothing has ever been read. An empty provider list is
    /// what makes the view say so instead.
    static let awaitingFirstSync = HeadroomWidgetSnapshot(
        updatedAt: .distantPast,
        attentionSummary: HeadroomCopy.openToSync,
        providers: []
    )

    static let placeholder = HeadroomWidgetSnapshot(
        updatedAt: .now,
        attentionLevel: nil,
        attentionSummary: HeadroomCopy.openToSync,
        providers: [
            Provider(
                id: "claude",
                title: "Claude",
                name: "Claude",
                percent: 42,
                paceDeltaPct: 11,
                sessionResetsIn: "1h 34m",
                weekResetsIn: "5d 2h",
                accent: "#D97757",
                layers: [
                    Provider.Layer(
                        id: "session", name: "Session",
                        percent: 42, pacePercent: 53,
                        resetsIn: "1h 34m"
                    ),
                    Provider.Layer(
                        id: "week", name: "Weekly",
                        percent: 28, pacePercent: 31,
                        resetsIn: "5d 2h"
                    ),
                ],
                burndown: demoBurndown(remaining: 72, perDay: 16)
            ),
            Provider(
                id: "codex",
                title: "Codex",
                name: "Codex",
                percent: 28,
                paceDeltaPct: 7,
                sessionResetsIn: "2h 18m",
                weekResetsIn: "5d 13h",
                accent: "#10A37F",
                layers: [
                    Provider.Layer(
                        id: "session", name: "Session",
                        percent: 28, pacePercent: 35,
                        resetsIn: "2h 18m"
                    ),
                    Provider.Layer(
                        id: "week", name: "Weekly",
                        percent: 18, pacePercent: 31,
                        resetsIn: "5d 13h"
                    ),
                ],
                burndown: demoBurndown(remaining: 82, perDay: 9)
            ),
        ]
    )
}
