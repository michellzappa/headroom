import SwiftUI

extension UsageSnapshot {
    /// Claude's service health is an attached status, not a quota provider.
    /// Respect the internal source switch so an existing user who disabled
    /// the check does not get a new line on their Claude card.
    var claudeStatusIfEnabled: ClaudeStatus? {
        guard sources?.first(where: { $0.id == "claude-status" })?.enabled
                != false else {
            return nil
        }
        return claudeStatus
    }
}

/// Secondary health line attached to Claude's quota card. Claude Status is a
/// public service check, so it has no provider row, meter, or account of its
/// own.
struct ClaudeStatusLine: View {
    let status: ClaudeStatus
    var showsLink: Bool = true

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: symbol)
            Text(label)
                .lineLimit(1)
            Spacer(minLength: 0)
            if showsLink {
                PermalinkButton(
                    url: Permalink.url(from: status.url),
                    help: "Open Claude service status"
                )
            }
        }
        .font(.caption)
        .foregroundStyle(tint)
    }

    private var label: String {
        guard status.ok == true else { return "Status unavailable" }
        if status.alerting == true {
            return status.incidentName
                ?? status.description
                ?? "Claude outage"
        }
        return status.description ?? "All systems operational"
    }

    private var symbol: String {
        if status.ok != true { return "questionmark.circle" }
        if status.alerting == true { return "exclamationmark.triangle.fill" }
        if isDegraded {
            return "exclamationmark.circle.fill"
        }
        return "checkmark.circle.fill"
    }

    private var tint: Color {
        if status.ok != true { return HeadroomPalette.orange }
        if status.alerting == true { return HeadroomPalette.red }
        if isDegraded {
            return HeadroomPalette.amber
        }
        return HeadroomPalette.green
    }

    private var isDegraded: Bool {
        guard let indicator = status.indicator?.lowercased() else {
            return false
        }
        return indicator == "minor" || indicator == "maintenance"
    }
}

/// How a provider from the host document reads on a ring, wherever it is drawn
/// — the Mac's overview, the phone's quota cards, and the widget cache both
/// apps write.
extension QuotaProviderInfo {
    /// Bands for the pools this provider actually reports, longer window
    /// outermost (week outside, session inside).
    ///
    /// A pool with no percentage gets no band. The host ships every pool its
    /// registry declares for a provider, value or not — Codex on a plan with
    /// only a weekly window still carries an empty `session` — and a band
    /// drawn at nothing is indistinguishable from one at 0% used.
    ///
    /// - Parameter burndown: the provider's burndown pools, when the surface
    ///   has them. Their ideal line is the better pace, because it accounts
    ///   for resets the provider granted mid-window; the pool's own
    ///   window-elapsed pace stands in until the host has sampled.
    func ringLayers(burndown: [Burndown] = []) -> [HeadroomRingLayer] {
        let paceByPool = Dictionary(
            burndown.compactMap { pool -> (String, Double)? in
                guard let id = pool.pool, let pace = pool.pacePercent else {
                    return nil
                }
                return (id, pace)
            },
            uniquingKeysWith: { first, _ in first }
        )
        let layers = visiblePools.compactMap { entry -> HeadroomRingLayer? in
            guard let percent = entry.pool.pct else { return nil }
            return HeadroomRingLayer(
                id: entry.id,
                // The pool, not the provider: these bands are one provider's
                // windows, and every surface that draws them names the
                // provider beside the glyph.
                name: entry.pool.title ?? entry.id.capitalized,
                percent: percent,
                pacePercent: paceByPool[entry.id] ?? entry.pool.pacePct
            )
        }
        return Array(layers.prefix(HeadroomRingStyle.maximumLayerCount))
    }

    var tint: Color {
        HeadroomPalette.providerTint(id: id, accent: accent)
    }
}

extension Array where Element == QuotaProviderInfo {
    /// The accent the user picked for a provider, for surfaces that know the
    /// tool but not the account.
    ///
    /// A coding-agent hook names Claude or Codex and never says which account
    /// answered it, so an exact `claude:work` match is preferred and a base
    /// match is the fallback. Without this the row paints the built-in brand
    /// triple — which is wrong the moment anyone changes an accent in
    /// Settings, and says nothing about *which* provider's colour it is.
    func accentTint(forProvider providerID: String) -> Color {
        func base(_ value: String) -> String {
            String(value.split(separator: ":", maxSplits: 1).first ?? "")
        }
        let match = first { $0.id == providerID }
            ?? first { base($0.id) == base(providerID) }
        return match?.tint ?? HeadroomPalette.providerTint(id: providerID)
    }
}
