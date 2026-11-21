import AppKit
import HouseKit
import SwiftUI

/// Mac Settings in the house shell (HouseKit `HouseSettingsView`): a sidebar
/// of intent panes, detail Forms, and integration leaves pushed on top. Same
/// taxonomy as iOS (`SettingsDestination`); accessory apps keep the system
/// `Settings` scene so SettingsLink / ⌘, keep working.
struct SettingsView: View {
    @AppStorage("usageEndpoint")
    var endpoint = "http://127.0.0.1:8737/usage"
    @AppStorage("activityRowLimit")
    var activityRowLimit = 8
    @AppStorage("serverRowLimit")
    var serverRowLimit = 5

    /// Mac dashboard display caps — not host fetch limits. Kept in one place
    /// so the hub stepper, leaf steppers, and Activity/Servers sections clamp
    /// the same way.
    static let activityRowLimitRange = 3...24
    static let serverRowLimitRange = 1...8
    @AppStorage("confirmServerStops")
    var confirmServerStops = true
    @AppStorage(ResetNotifications.defaultsKey)
    var notifyOnQuotaReset = false
    @AppStorage(HeadroomTelemetry.enabledKey)
    var telemetryEnabled = true
    @State var telemetryPreview: HeadroomTelemetryBatch?
    @State var telemetryPreviewLoading = false
    @State var telemetryCopyMessage: String?
    @State var communityStats: HeadroomCommunityStats?
    @State var communityStatsLoading = false
    @State var communityStatsMessage: String?

    @State var sources: [SyncSource] = []
    @State var sourcesMessage: String?
    @State var isSyncing = false
    @State var togglingSourceID: String?
    /// Service the pointer is dragging over, for the insertion line.
    @State var dropTargetID: String?
    /// Live usage by account id — feeds the Active card's bars.
    @State var usageProviders: [String: QuotaProviderInfo] = [:]
    /// Public Claude service health, shown on Claude's provider row.
    @State var claudeStatus: ClaudeStatus?
    /// Last document read here, for General's menu-bar preview strip. Settings
    /// is its own scene and holds no `UsageStore`, so the preview draws off
    /// the snapshot `reloadSources()` already fetched.
    @State var menuBarPreviewSnapshot: UsageSnapshot?
    /// Activity panel pin order from the host (legacy).
    @State var servicesOrder: [String] = IntegrationWatch.activityBlocks(from: nil)
        .map(\.rawValue)
    /// Full Integrations catalog pin order.
    @State var integrationsOrder: [String] = IntegrationWatch.allCases.map(\.rawValue)
    /// Multi-account capability + current logins, from `/accounts`. Empty on
    /// hosts predating the endpoint, which simply hides "Add account…".
    @State var accountProviders: [AccountProvider] = []
    /// Credential detection from `/setup`, for the Library's dimmed chips.
    @State var detectedSources: [String: Bool] = [:]
    /// Provider whose add-account sheet is open.
    @State var addingAccountProvider: AccountProvider?

    @State var supabaseToken = ""
    @State var tokenStored = false
    @State var supabaseMessage: String?
    @State var supabaseConfig = SupabaseConfiguration()
    @State var supabaseProjectsDraft = ""
    @State var savingSupabaseProjects = false
    @State var supabaseProjectsEditable = true

    @State var plausibleToken = ""
    @State var plausibleTokenStored = false
    @State var plausibleMessage: String?
    @State var plausibleRange = "24h"
    @State var plausibleConfig = PlausibleConfiguration()
    @State var plausibleHostDraft = "https://plausible.io"
    @State var plausibleSitesDraft = ""
    @State var savingPlausibleSites = false
    @State var plausibleSitesEditable = true

    @State var posthogToken = ""
    @State var posthogTokenStored = false
    @State var posthogMessage: String?
    @State var posthogRange = "24h"
    @State var posthogHostDraft = "https://us.posthog.com"
    @State var posthogConfig = PostHogConfiguration()
    @State var posthogProjectsDraft = ""
    @State var savingPostHogProjects = false
    @State var posthogProjectsEditable = true

    @State var sentryToken = ""
    @State var sentryTokenStored = false
    @State var sentryMessage: String?
    @State var sentryOrgDraft = ""
    @State var datadogAPIKey = ""
    @State var datadogAppKey = ""
    @State var datadogKeysStored = false
    @State var datadogMessage: String?
    @State var datadogSiteDraft = "datadoghq.com"
    @State var axiomToken = ""
    @State var axiomTokenStored = false
    @State var axiomMessage: String?
    @State var axiomHostDraft = "https://api.axiom.co"
    @State var axiomOrgDraft = ""

    @State var openrouterToken = ""
    @State var openrouterTokenStored = false
    @State var openrouterMessage: String?
    @State var aiGatewayToken = ""
    @State var aiGatewayTokenStored = false
    @State var aiGatewayMessage: String?

    @State var githubToken = ""
    @State var githubTokenStored = false
    @State var githubMessage: String?
    /// Comma-separated drafts, so one field edits a list without a row editor.
    @State var githubOwners = ""
    @State var githubAlwaysRepos = ""
    @State var githubMaxDiscovered = 6
    @State var githubAvailable: [String] = []
    @State var githubWatching: [String] = []
    @State var githubDevRoot = "~/Dev"
    @State var savingGitHubWatch = false
    /// False when the host predates /github/watch, so the fields don't sit
    /// there taking edits that can never be saved.
    @State var githubWatchEditable = true

    /// Git and Vercel had no UI at all before Integrations became the one
    /// place connections live — both were edit-`~/.headroom/config.json`-and-
    /// restart. Drafts are held as typed and only parsed on save, same as the
    /// GitHub owner fields above.
    @State var gitConfig = GitConfiguration()
    @State var gitDevRootDraft = ""
    @State var gitAuthorsDraft = ""
    @State var gitMessage: String?
    @State var savingGit = false
    /// False when the host predates /config/git, so the fields do not take
    /// edits that can never be saved.
    @State var gitEditable = true

    @State var vercelConfig = VercelConfiguration()
    @State var vercelTeamsDraft = ""
    @State var vercelMessage: String?
    @State var savingVercel = false
    @State var vercelEditable = true

    @State var hostToken = ""
    @State var hostTokenStored = false
    @State var hostHealth: HealthReport?
    @State var hostHealthMessage: String?
    @State var hostHealthLoading = false
    /// The zone the host draws day boundaries in. Blank until /config/timezone
    /// answers, which is also how a host predating the route stays read-only.
    @State var timezoneDraft = ""
    @State var timezoneMessage: String?
    /// The reset calendar feed. Nil until /config/calendar answers, which is
    /// also how a host predating the feed keeps the section read-only.
    @State var calendarConfig: CalendarFeedConfiguration?
    @State var calendarMessage: String?
    @State var mobileTokenMessage: String?
    @State var mobilePermissions = MobilePermissions.allEnabled
    @State var changingMobilePermission: MobilePermission?
    @State var multiMac = MultiMacConfiguration.unknown
    @State var multiMacMessage: String?
    @State var changingMultiMac = false
    @State var hostKeepRunning = HostLifecycle.current == .launchAgent
    @State var hostLifecycleBusy = false
    @State var hostLifecycleMessage: String?
    @State var hostHasLaunchAgent = HostController.hasLaunchAgent
    @State var hostRemoveConfirming = false
    @State var usbFallbackEnabled = HeadroomUSB.isEnabled
    /// Settings → Desk display. Loaded by the pane's own `.task`, like the
    /// time zone, so opening Settings for something else does not pay for it.
    @State var deskDisplay = DeskDisplayConfiguration()
    @State var deskDisplayMessage: String?
    /// False when the host predates /config/display, so the controls do not
    /// take edits that can never be saved.
    @State var deskDisplayEditable = true
    @State var savingDeskDisplay = false
    @State var usbTransportBusy = false
    @State var usbTransportMessage: String?
    /// Selected pane and pushed leaf. Held here so a caller can select a
    /// pane with `navigation.show(pane:)`.
    @State var navigation = SettingsNavigation(selection: SettingsPane.generalID)
    let updates = UpdateChecker.shared
    @AppStorage(MenuBarIconStyle.defaultsKey)
    var menuBarIconStyle = MenuBarIconStyle.remaining.rawValue
    @AppStorage(MenuBarIconStyle.invertDefaultsKey)
    var menuBarIconInvert = false
    @AppStorage(MenuBarIconStyle.tileDefaultsKey)
    var menuBarIconTile = false
    @AppStorage(MenuBarIconStyle.hideAttentionDotDefaultsKey)
    var menuBarIconHideAttentionDot = false

    var client: HeadroomClient { HeadroomClient(endpoint: endpoint) }

    /// The host waves loopback callers through, so the token only matters when
    /// pointing this app at another machine.
    var endpointIsRemote: Bool {
        guard let host = URL(string: endpoint)?.host() else { return false }
        return !(host == "127.0.0.1" || host == "localhost" || host == "::1")
    }

    var body: some View {
        HouseSettingsView(
            appName: HeadroomCopy.product,
            panes: SettingsDestination.macRoots.map { settingsPane($0) },
            navigation: navigation
        )
        .task {
            tokenStored = TokenStore.supabase.exists()
            plausibleTokenStored = TokenStore.plausible.exists()
            posthogTokenStored = TokenStore.posthog.exists()
            sentryTokenStored = TokenStore.sentry.exists()
            datadogKeysStored = TokenStore.datadogAPI.exists()
                && TokenStore.datadogApp.exists()
            axiomTokenStored = TokenStore.axiom.exists()
            githubTokenStored = TokenStore.github.exists()
            openrouterTokenStored = TokenStore.openrouter.exists()
            aiGatewayTokenStored = TokenStore.aiGateway.exists()
            hostTokenStored = TokenStore.host.exists()
            await reloadSources()
            await reloadMobilePermissions()
            await reloadMultiMac()
            await reloadGitHubWatch()
            await reloadGitConfiguration()
            await reloadVercelConfiguration()
            await reloadSupabaseConfiguration()
            await reloadPlausibleConfiguration()
            await reloadPostHogConfiguration()
            await reloadHostHealth()
        }
        .onChange(of: endpoint) { _, _ in
            Task { await reloadHostHealth() }
        }
    }

    /// One sidebar row of the house shell for a Mac root.
    func settingsPane(_ dest: SettingsDestination) -> SettingsPane {
        SettingsPane(id: dest.settingsID, title: dest.title, symbol: dest.symbol) {
            pane(for: dest)
        }
    }

    /// The page an integration row pushes, with a Back button to its pane.
    func integrationLeaf(_ kind: SettingsIntegration) -> SettingsLeaf {
        let dest = SettingsDestination.integration(kind)
        return SettingsLeaf(id: dest.settingsID, title: dest.title) {
            integrationPane(kind)
        }
    }

    @ViewBuilder
    func pane(for dest: SettingsDestination) -> some View {
        switch dest {
        case .general:
            generalPane
        case .host:
            Form { hostSection }
        case .calendar:
            Form {
                calendarSection
                CalendarWriteSection(sync: ResetCalendarSync.shared)
            }
        case .menuBarIcon:
            menuBarIconPane
        case .otherMacs:
            otherMacsPane
        case .deskDisplay:
            deskDisplayPane
        case .sources:
            sourcesPane
        case .iPhone:
            iPhonePane
        case .telemetry:
            telemetryPane
        case .integrations:
            integrationsHub
        case .integration(let kind):
            integrationPane(kind)
        case .about:
            aboutPane
        case .connection, .permissions:
            // iOS-only destinations — Mac never selects them.
            EmptyView()
        }
    }
}

extension SettingsDestination {
    /// Stable id for the house shell's pane and leaf selection.
    var settingsID: String {
        switch self {
        case .general: return SettingsPane.generalID
        case .about: return SettingsPane.aboutID
        case .host: return "host"
        case .calendar: return "calendar"
        case .menuBarIcon: return "menu-bar-icon"
        case .sources: return "sources"
        case .iPhone: return "iphone"
        case .telemetry: return "telemetry"
        case .integrations: return "integrations"
        case .otherMacs: return "other-macs"
        case .deskDisplay: return "desk-display"
        case .integration(let kind): return "integration-\(kind.rawValue)"
        case .connection: return "connection"
        case .permissions: return "permissions"
        }
    }
}
