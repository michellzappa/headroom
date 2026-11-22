import Foundation

/// One row in the Integrations catalog — watched stuff on this Mac.
///
/// Host `integrations_order` pins these. Activity lays out only ids where
/// `paintsActivity` is true. Prepaid balances (OpenRouter, AI Gateway) paint
/// an account-use block there — not Usage rings. Claude Code and Codex are
/// quota sources, not this list.
enum IntegrationWatch: String, CaseIterable, Identifiable, Sendable {
    case git
    case github
    case vercel
    case openrouter
    case aiGateway = "ai-gateway"
    case supabase
    case plausible
    case posthog
    case sentry
    case datadog
    case axiom
    case servers
    case builds

    var id: String { rawValue }

    /// `sources.enabled` key — servers/builds share `local`.
    var sourceID: String {
        switch self {
        case .servers, .builds: return "local"
        default: return rawValue
        }
    }

    /// Whether Activity draws a block for this watch.
    var paintsActivity: Bool { true }

    /// Activity feed `kind` for git / Actions / Vercel / alert rows; nil for panels.
    /// The Activity tab paints those kinds as one chronological list, once,
    /// at the first feed watch in catalog order — later feed watches skip.
    var activityFeedKind: String? {
        switch self {
        case .git: return "commit"
        case .github: return "github"
        case .vercel: return "deployment"
        case .sentry: return "sentry"
        case .datadog: return "datadog"
        case .axiom: return "axiom"
        default: return nil
        }
    }

    /// True for watches whose events live in the mixed chronological feed.
    var paintsActivityFeed: Bool { activityFeedKind != nil }

    /// First feed watch in `blocks` owns the mixed Recent section; later ones
    /// must not paint it again.
    static func isLeadFeedWatch(
        _ watch: IntegrationWatch, in blocks: [IntegrationWatch]
    ) -> Bool {
        blocks.first(where: \.paintsActivityFeed) == watch
    }

    var title: String {
        switch self {
        case .git: return "Git"
        case .github: return HeadroomCopy.githubActions
        case .vercel: return "Vercel"
        case .openrouter: return HeadroomCopy.openRouter
        case .aiGateway: return HeadroomCopy.aiGateway
        case .supabase: return HeadroomCopy.supabase
        case .plausible: return HeadroomCopy.plausible
        case .posthog: return HeadroomCopy.posthog
        case .sentry: return HeadroomCopy.sentry
        case .datadog: return HeadroomCopy.datadog
        case .axiom: return HeadroomCopy.axiom
        case .servers: return HeadroomCopy.localServers
        case .builds: return HeadroomCopy.xcodeBuilds
        }
    }

    var symbol: String {
        switch self {
        case .git: return "arrow.triangle.branch"
        case .github: return "chevron.left.forwardslash.chevron.right"
        case .vercel: return "triangle"
        case .openrouter: return "arrow.triangle.swap"
        case .aiGateway: return "bolt.horizontal"
        case .supabase: return "cylinder.split.1x2"
        case .plausible: return "chart.xyaxis.line"
        case .posthog: return "chart.bar.doc.horizontal"
        case .sentry: return "ladybug"
        case .datadog: return "chart.xyaxis.line"
        case .axiom: return "scroll"
        case .servers: return "server.rack"
        case .builds: return "hammer"
        }
    }

    /// Settings leaf when one exists (servers/builds share Local).
    var settingsIntegration: SettingsIntegration? {
        switch self {
        case .git: return .git
        case .github: return .github
        case .vercel: return .vercel
        case .openrouter: return .openrouter
        case .aiGateway: return .aiGateway
        case .supabase: return .supabase
        case .plausible: return .plausible
        case .posthog: return .posthog
        case .sentry: return .sentry
        case .datadog: return .datadog
        case .axiom: return .axiom
        case .servers, .builds: return .local
        }
    }

    static func ordered(from raw: [String]?) -> [IntegrationWatch] {
        var seen = Set<IntegrationWatch>()
        var out: [IntegrationWatch] = []
        for id in raw ?? [] {
            guard let watch = IntegrationWatch(rawValue: id),
                  seen.insert(watch).inserted
            else { continue }
            out.append(watch)
        }
        for watch in IntegrationWatch.allCases where seen.insert(watch).inserted {
            out.append(watch)
        }
        return out
    }

    /// Activity blocks only, in catalog pin order.
    static func activityBlocks(from raw: [String]?) -> [IntegrationWatch] {
        ordered(from: raw).filter(\.paintsActivity)
    }
}
