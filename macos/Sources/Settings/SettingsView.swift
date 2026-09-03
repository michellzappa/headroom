import AppKit
import SwiftUI

/// Mac Settings: sidebar of intent panes + detail Forms, nested Integrations
/// and Sync. Same taxonomy as iOS (`SettingsDestination`); accessory
/// apps keep the system `Settings` scene so SettingsLink / ⌘, keep working.
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
    @State var mobileTokenMessage: String?
    @State var mobilePermissions = MobilePermissions.allEnabled
    @State var changingMobilePermission: MobilePermission?
    @State var agentGatewayEnabled = false
    @State var agentAlerts = true
    @State var codexBinary = "codex"
    @State var agentProviderStatus: AgentProviderStatus?
    @State var agentGatewayMessage: String?
    @State var agentTaskSurface: AgentTaskSurface?
    @State var pickedTaskFolder: String?
    @State var changingAgentGateway = false
    @State var changingAgentAlerts = false
    @State var claudeHooks: ClaudeHookConfiguration?
    @State var claudeHooksMessage: String?
    @State var changingClaudeHooks = false
    @State var claudeQuestionMode = "notify"
    @State var multiMac = MultiMacConfiguration.unknown
    @State var multiMacMessage: String?
    @State var changingMultiMac = false
    @State var openAtLogin = LaunchAtLogin.isRequested
    @State var openAtLoginNeedsApproval = LaunchAtLogin.needsApproval
    @State var openAtLoginMessage: String?
    @State var hostKeepRunning = HostLifecycle.current == .launchAgent
    @State var hostLifecycleBusy = false
    @State var hostLifecycleMessage: String?
    @State var hostHasLaunchAgent = HostController.hasLaunchAgent
    @State var hostRemoveConfirming = false
    @State var usbFallbackEnabled = HeadroomUSB.isEnabled
    @State var usbTransportBusy = false
    @State var usbTransportMessage: String?
    @State var selection: SettingsDestination? = .general
    /// The pushed leaf under the selected root (one
    /// integration under Integrations/Coding agents). A `NavigationStack`
    /// nested inside `NavigationSplitView`'s detail can't dock its automatic
    /// Back control into the window's real toolbar here, so it fell back to
    /// drawing its own — a chevron floating in the content instead of sitting
    /// in the title bar. Tracking the leaf ourselves and drawing our own Back
    /// button removes that fallback rendering entirely.
    @State var leaf: SettingsDestination?
    @State var columnVisibility = NavigationSplitViewVisibility.all
    @ObservedObject var updates = UpdateChecker.shared
    @AppStorage(UpdateChecker.automaticKey) var automaticUpdateChecks = true
    @AppStorage(MenuBarIconStyle.defaultsKey)
    var menuBarIconStyle = MenuBarIconStyle.remaining.rawValue
    @AppStorage(MenuBarIconStyle.invertDefaultsKey)
    var menuBarIconInvert = false
    @State var updateInstallMessage: String?

    var client: HeadroomClient { HeadroomClient(endpoint: endpoint) }

    /// The host waves loopback callers through, so the token only matters when
    /// pointing this app at another machine.
    var endpointIsRemote: Bool {
        guard let host = URL(string: endpoint)?.host() else { return false }
        return !(host == "127.0.0.1" || host == "localhost" || host == "::1")
    }

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            List(selection: $selection) {
                Section {
                    ForEach(SettingsDestination.macRoots, id: \.self) { dest in
                        Label(dest.title, systemImage: dest.symbol)
                            .tag(dest)
                    }
                }
            }
            .listStyle(.sidebar)
            .navigationTitle(HeadroomCopy.settings)
            .toolbar(removing: .sidebarToggle)
            .navigationSplitViewColumnWidth(min: 220, ideal: 220, max: 220)
        } detail: {
            Group {
                if let leaf {
                    pane(for: leaf)
                        .navigationTitle(leaf.title)
                        .toolbar {
                            ToolbarItem(placement: .navigation) {
                                Button {
                                    self.leaf = nil
                                } label: {
                                    Image(systemName: "chevron.left")
                                }
                                .help("Back to \((selection ?? .general).title)")
                            }
                        }
                } else {
                    let dest = selection ?? .general
                    pane(for: dest)
                        .navigationTitle(dest.title)
                }
            }
            // Identity the detail on the root so a sidebar click always
            // rebuilds the pane — without this, SwiftUI can keep showing the
            // previous root's body when selection updates race a leftover leaf.
            .id(leaf.map { "leaf-\($0.title)" } ?? "root-\((selection ?? .general).title)")
        }
        .frame(width: 820, height: 600)
        .formStyle(.grouped)
        .background(SettingsWindowConfigurator())
        .onChange(of: selection) { _, _ in
            // Sidebar swapped the root; drop any pushed leaf so Back isn't
            // left pointing at a pane that is no longer under it.
            //
            // Do not attach a TapGesture to sidebar rows: on macOS it races
            // List(selection:) and often swallows the click, so the highlight
            // moves (or doesn't) while the detail stays put.
            leaf = nil
        }
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
            refreshOpenAtLogin()
            await reloadSources()
            await reloadMobilePermissions()
            await reloadAgentGateway()
            agentTaskSurface = try? await client.fetchAgentTaskSurface()
            await reloadClaudeHooks()
            await reloadMultiMac()
            await reloadGitHubWatch()
            await reloadGitConfiguration()
            await reloadVercelConfiguration()
            await reloadSupabaseConfiguration()
            await reloadPlausibleConfiguration()
            await reloadPostHogConfiguration()
            await reloadHostHealth()
        }
        .onReceive(NotificationCenter.default.publisher(
            for: NSApplication.didBecomeActiveNotification
        )) { _ in
            // Login Items approval happens in System Settings; re-read on return.
            refreshOpenAtLogin()
        }
        .onChange(of: endpoint) { _, _ in
            Task { await reloadHostHealth() }
        }
    }

    @ViewBuilder
    func pane(for dest: SettingsDestination) -> some View {
        switch dest {
        case .general:
            generalPane
        case .otherMacs:
            otherMacsPane
        case .sources:
            sourcesPane
        case .codingAgents:
            codingAgentsPane
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
