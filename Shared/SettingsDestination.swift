import Foundation

/// Settings navigation graph shared by macOS and iOS.
///
/// Root order mirrors user intent (General → providers → watches →
/// devices → telemetry → About). Nested leaves sit under Integrations
/// (every watchable thing, see `SettingsIntegration`).
///
/// Integrations is one catalog of what you watch on Activity (and connect).
///
/// Onboarding (`WelcomePane`) maps onto the same ideas where it can:
/// - Providers ↔ Welcome “What to watch” (same symbol)
/// - iPhone ↔ Welcome “On your phone”
/// - General ↔ Welcome “Background helper” (the host this Mac runs)
enum SettingsDestination: Hashable, Sendable {
    case general
    /// The background host: who runs it, the token, USB. Its own root
    /// because it outgrew General.
    case host
    /// The reset calendar feed (docs/calendar.md).
    case calendar
    /// The menu bar glyph: Remaining or Pace, Invert, and the tile.
    case menuBarIcon
    case sources
    case iPhone
    case telemetry
    case integrations
    case about

    /// The other Macs sharing this one's settings. A sidebar root beside
    /// iPhone — the two used to be unlabeled sections inside a "Sync" pane
    /// that neither named, so nothing in Settings said "iPhone" out loud.
    case otherMacs
    /// The ESP32 desk board's panel settings. A root beside the other
    /// devices; shown whether or not a board has reported in, so the pane
    /// explains itself instead of appearing by surprise.
    case deskDisplay
    /// Nested under Integrations.
    case integration(SettingsIntegration)

    /// iOS-only roots / leaves (Mac grants live under `.iPhone`).
    case connection
    case permissions

    /// Mac sidebar roots — short, fixed, progressive disclosure below.
    ///
    /// iPhone and Other Macs are named here rather than folded into one
    /// "Sync" row: a person looking for phone permissions searches for the
    /// word iPhone, and onboarding's phone step points at `.iPhone`.
    static let macRoots: [SettingsDestination] = [
        .general, .menuBarIcon, .host, .sources, .integrations, .iPhone,
        .otherMacs, .deskDisplay, .calendar, .telemetry, .about,
    ]

    /// iPhone Settings tab roots. Connection is the phone’s view of pairing;
    /// Mac’s General covers host endpoint on the Mac itself.
    ///
    /// Integrations is the same watch catalog as Mac — enable, reorder, status.
    /// Keys are entered on the Mac; the phone never sees them.
    static let iOSRoots: [SettingsDestination] = [
        .connection, .sources, .integrations, .iPhone, .about,
    ]

    var title: String {
        switch self {
        case .general: return HeadroomCopy.settingsGeneral
        case .host: return HeadroomCopy.settingsHost
        case .calendar: return HeadroomCopy.calendarTitle
        case .menuBarIcon: return HeadroomCopy.menuBarIcon
        case .sources: return HeadroomCopy.settingsSources
        case .iPhone: return HeadroomCopy.settingsiPhone
        case .telemetry: return HeadroomCopy.settingsTelemetry
        case .integrations: return HeadroomCopy.settingsIntegrations
        case .about: return HeadroomCopy.about
        case .otherMacs: return HeadroomCopy.otherMacs
        case .deskDisplay: return HeadroomCopy.settingsDeskDisplay
        case .integration(let kind): return kind.title
        case .connection: return HeadroomCopy.settingsConnection
        case .permissions: return HeadroomCopy.settingsPermissions
        }
    }

    var symbol: String {
        switch self {
        case .general: return "gearshape"
        case .host: return "server.rack"
        case .calendar: return "calendar"
        case .menuBarIcon: return "menubar.rectangle"
        case .sources: return "checklist"
        case .iPhone: return "iphone"
        case .telemetry: return "chart.xyaxis.line"
        case .integrations: return "link"
        case .about: return "info.circle"
        case .otherMacs: return "laptopcomputer.and.iphone"
        case .deskDisplay: return "display"
        case .integration(let kind): return kind.symbol
        case .connection: return "network"
        case .permissions: return "lock.shield"
        }
    }

    /// True when this destination only makes sense on the Mac host UI.
    ///
    /// `integrations` is on both now — the phone lists the same connections
    /// read-only-ish (on/off and status, no credential fields). The
    /// per-integration leaf stays Mac-only, because configuring one means
    /// typing a key, and keys are never entered on the phone.
    var isMacOnly: Bool {
        switch self {
        case .general, .host, .calendar, .menuBarIcon, .telemetry, .otherMacs,
             .deskDisplay, .integration:
            return true
        case .integrations, .sources, .iPhone, .about, .connection,
             .permissions:
            return false
        }
    }
}

/// One external thing Headroom connects to, and the leaf that configures it.
///
/// Membership is deliberately not "has a Keychain token" — that was the old
/// line, and it scattered connection settings across three roots by the
/// accident of how each service authenticates. If Headroom has to be told
/// something to reach it, it belongs here.
///
/// Raw values match `sources_config` ids where a source exists, so a status
/// lookup against `/usage` needs no second mapping table.
enum SettingsIntegration: String, Hashable, CaseIterable, Sendable {
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
    /// Local servers + Xcode builds (shared `local` source).
    case local

    enum Group: String, CaseIterable, Sendable {
        case code
        case balances
        case services

        var title: String {
            switch self {
            case .code: return HeadroomCopy.integrationsCode
            case .balances: return HeadroomCopy.integrationsBalances
            case .services: return HeadroomCopy.integrationsServices
            }
        }
    }

    var group: Group {
        switch self {
        case .git, .github, .vercel: return .code
        case .openrouter, .aiGateway: return .balances
        case .supabase, .plausible, .posthog, .sentry, .datadog, .axiom, .local:
            return .services
        }
    }

    static func members(of group: Group) -> [SettingsIntegration] {
        allCases.filter { $0.group == group }
    }

    var title: String {
        switch self {
        case .git: return "Git"
        case .github: return HeadroomCopy.githubActions
        case .vercel: return "Vercel"
        case .openrouter: return HeadroomCopy.openRouter
        case .aiGateway: return HeadroomCopy.aiGateway
        case .supabase: return "Supabase"
        case .plausible: return "Plausible"
        case .posthog: return HeadroomCopy.posthog
        case .sentry: return HeadroomCopy.sentry
        case .datadog: return HeadroomCopy.datadog
        case .axiom: return HeadroomCopy.axiom
        case .local: return HeadroomCopy.local
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
        case .local: return "laptopcomputer"
        }
    }

    /// Leaves that share the Mac Activity feed row-count stepper. Same
    /// `@AppStorage` as the Integrations hub — open any of these and the
    /// limit is still there. Balances have no Activity rows.
    var sharesActivityRowLimit: Bool {
        switch self {
        case .git, .github, .vercel, .supabase, .sentry, .datadog, .axiom:
            return true
        case .openrouter, .aiGateway, .plausible,
             .posthog, .local:
            return false
        }
    }
}
