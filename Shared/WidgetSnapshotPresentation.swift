import SwiftUI

// How the cached widget snapshot becomes something to draw. Compiled by the
// widget extensions and the watch complication — the three surfaces that read
// the cache rather than the model layer — so a provider's tint, its ring
// layers, and which of them is the one worth naming are decided once.

extension HeadroomWidgetSnapshot.Provider.Series {
    /// Rows that really are a `[time, remaining]` pair.
    ///
    /// The three surfaces here read a cache they did not write, and may not
    /// have been the build that wrote it. Nothing validates row width on the
    /// way in, so it is validated on the way out.
    var samples: [[Double]] { actual.filter { $0.count >= 2 } }

    /// Whether this series has a stroke in it. Two samples is the floor —
    /// one is a dot, not a line, and a `burndown` key holding neither is a
    /// provider the chart cannot say anything about.
    var isDrawable: Bool { samples.count >= 2 }

    var latestSampleTime: Double? {
        OverallBurndownChartMath.latestSampleTime(actual)
    }
}

extension HeadroomWidgetSnapshot.Provider {
    /// What VoiceOver calls this provider: the full title where the cache
    /// carries one, and the mark title otherwise — which is what an app older
    /// than the `name` key wrote, and the same string it already draws.
    var spokenTitle: String { name ?? title }

    /// Always present in the medium widget's legend. An older cache provides
    /// no value, but still gets the required unknown-state copy.
    var widgetPaceSlackLabel: String {
        HeadroomCopy.widgetPaceSlack(paceDeltaPct)
    }

    /// Both rows are structural in the small widget. Missing layers and
    /// older caches keep their labels and show an explicit unknown countdown.
    var widgetResetLabels: [String] {
        // The layer fallback reads the first cache shape used while this field
        // was introduced; current writers keep resets independently of rings.
        let sessionReset = sessionResetsIn
            ?? layers?.first { $0.id == "session" }?.resetsIn
        let weeklyReset = weekResetsIn
            ?? layers?.first { $0.id == "week" }?.resetsIn
        return [
            HeadroomCopy.widgetReset("5h", duration: sessionReset),
            HeadroomCopy.widgetReset("Weekly", duration: weeklyReset),
        ]
    }

    var ringLayers: [HeadroomRingLayer] {
        if let layers, !layers.isEmpty {
            return layers.map {
                HeadroomRingLayer(
                    id: $0.id,
                    // Older caches put the pool's title in `id`, so the
                    // fallback speaks the same words it always did.
                    name: $0.name ?? $0.id,
                    percent: $0.percent,
                    pacePercent: $0.pacePercent
                )
            }
        }
        return [
            HeadroomRingLayer(
                id: id,
                name: spokenTitle,
                percent: percent,
                pacePercent: nil
            ),
        ]
    }

    var tint: Color {
        HeadroomPalette.providerTint(id: id, accent: accent)
    }

    /// A spent pool recedes rather than warns — the same reading the Mac gives
    /// it, minus the AppKit colour surgery `Color.drained()` needs.
    var burndownTint: Color {
        burndown?.exhausted == true ? tint.opacity(0.45) : tint
    }

    /// Where this provider's forecast reaches empty, if it does so inside the
    /// charted week. The series is already cropped at empty before it is
    /// written, so a last point at zero *is* the crossing.
    var emptiesAt: Double? {
        guard let last = burndown?.projected.last, last.count >= 2,
              last[1] <= 0
        else { return nil }
        return last[0]
    }
}

extension HeadroomWidgetSnapshot {
    /// The provider the wrist should name when it can only name one.
    ///
    /// Whichever forecast runs dry first, because that is the one that changes
    /// what you do next. With nothing running dry it falls back to the most
    /// spent, which is the same provider the meters lead with everywhere else.
    var bindingProvider: Provider? {
        let emptying = providers
            .compactMap { provider in provider.emptiesAt.map { ($0, provider) } }
            .min { $0.0 < $1.0 }
        return emptying?.1 ?? providers.max { $0.percent < $1.percent }
    }

    /// The same reading, narrowed to the provider a tile was configured for.
    /// Nil draws everything, which is the default and what every widget placed
    /// before the configuration existed does.
    ///
    /// An id that matches nothing draws everything too. A provider leaves the
    /// top 3 whenever someone reorders the pane or turns it off, and a tile
    /// that answered that by going empty would read as a broken widget rather
    /// than as a provider that is no longer there. Every figure it draws is
    /// labelled with whose it is, so showing more than was asked for is
    /// legible; showing nothing is not.
    func showing(_ providerID: String?) -> HeadroomWidgetSnapshot {
        guard let providerID,
              providers.contains(where: { $0.id == providerID })
        else { return self }
        var narrowed = self
        narrowed.providers = providers.filter { $0.id == providerID }
        return narrowed
    }

    /// Providers with enough history to draw a line.
    ///
    /// The test is the stroke, not the key. A `burndown` object whose curve is
    /// empty — what an older build wrote, and what a lossy decode leaves behind
    /// — used to count as charted, so the wide widget committed its whole
    /// surface to a chart with nothing in it instead of falling back to rings.
    var charted: [Provider] {
        providers.filter { $0.burndown?.isDrawable == true }
    }
}
