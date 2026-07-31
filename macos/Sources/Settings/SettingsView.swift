import AppKit
import SwiftUI

/// Mac Settings: sidebar of intent panes + detail Forms, nested Integrations
/// and Other Macs. Same taxonomy as iOS (`SettingsDestination`); accessory
/// apps keep the system `Settings` scene so SettingsLink / ⌘, keep working.
struct SettingsView: View {
    @AppStorage("usageEndpoint")
    private var endpoint = "http://127.0.0.1:8737/usage"
    @AppStorage("refreshInterval")
    private var refreshInterval = 60
    @AppStorage("activityRowLimit")
    private var activityRowLimit = 8
    @AppStorage("serverRowLimit")
    private var serverRowLimit = 5
    @AppStorage("confirmServerStops")
    private var confirmServerStops = true
    @AppStorage("supabaseRowLimit")
    private var supabaseRowLimit = 6
    @AppStorage("plausibleRowLimit")
    private var plausibleRowLimit = 6

    @State private var sources: [SyncSource] = []
    @State private var sourcesMessage: String?
    @State private var isSyncing = false
    @State private var togglingSourceID: String?
    /// Service the pointer is dragging over, for the insertion line.
    @State private var dropTargetID: String?
    /// Live usage by account id — feeds the Active card's bars.
    @State private var usageProviders: [String: QuotaProviderInfo] = [:]
    /// Multi-account capability + current logins, from `/accounts`. Empty on
    /// hosts predating the endpoint, which simply hides "Add account…".
    @State private var accountProviders: [AccountProvider] = []
    /// Credential detection from `/setup`, for the Library's dimmed chips.
    @State private var detectedSources: [String: Bool] = [:]
    /// Provider whose add-account sheet is open.
    @State private var addingAccountProvider: AccountProvider?

    @State private var supabaseToken = ""
    @State private var tokenStored = false
    @State private var supabaseMessage: String?

    @State private var plausibleToken = ""
    @State private var plausibleTokenStored = false
    @State private var plausibleMessage: String?
    @State private var plausibleRange = "24h"

    @State private var githubToken = ""
    @State private var githubTokenStored = false
    @State private var githubMessage: String?
    /// Comma-separated drafts, so one field edits a list without a row editor.
    @State private var githubOwners = ""
    @State private var githubAlwaysRepos = ""
    @State private var githubMaxDiscovered = 6
    @State private var githubWatching: [String] = []
    @State private var githubDevRoot = "~/Dev"
    @State private var savingGitHubWatch = false
    /// False when the host predates /github/watch, so the fields don't sit
    /// there taking edits that can never be saved.
    @State private var githubWatchEditable = true

    @State private var hostToken = ""
    @State private var hostTokenStored = false
    @State private var mobileTokenMessage: String?
    @State private var mobilePermissions = MobilePermissions.allEnabled
    @State private var changingMobilePermission: MobilePermission?
    @State private var agentGatewayEnabled = false
    @State private var codexBinary = "codex"
    @State private var agentProviderStatus: AgentProviderStatus?
    @State private var agentGatewayMessage: String?
    @State private var changingAgentGateway = false
    @State private var claudeHooks: ClaudeHookConfiguration?
    @State private var claudeHooksMessage: String?
    @State private var changingClaudeHooks = false
    @State private var multiMac = MultiMacConfiguration.unknown
    @State private var multiMacMessage: String?
    @State private var changingMultiMac = false
    @State private var openAtLogin = LaunchAtLogin.isRequested
    @State private var openAtLoginNeedsApproval = LaunchAtLogin.needsApproval
    @State private var openAtLoginMessage: String?
    @State private var selection: SettingsDestination? = .general
    @State private var columnVisibility = NavigationSplitViewVisibility.all

    private var tokenDraft: String {
        supabaseToken.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var plausibleTokenDraft: String {
        plausibleToken.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var githubTokenDraft: String {
        githubToken.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var client: HeadroomClient { HeadroomClient(endpoint: endpoint) }

    /// The host waves loopback callers through, so the token only matters when
    /// pointing this app at another machine.
    private var endpointIsRemote: Bool {
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
            NavigationStack {
                let dest = selection ?? .general
                pane(for: dest)
                    .navigationTitle(dest.title)
                    .navigationDestination(for: SettingsDestination.self) { sub in
                        pane(for: sub)
                            .navigationTitle(sub.title)
                    }
            }
        }
        .frame(width: 820, height: 600)
        .formStyle(.grouped)
        .background(SettingsWindowConfigurator())
        .task {
            tokenStored = TokenStore.supabase.exists()
            plausibleTokenStored = TokenStore.plausible.exists()
            githubTokenStored = TokenStore.github.exists()
            hostTokenStored = TokenStore.host.exists()
            refreshOpenAtLogin()
            await reloadSources()
            await reloadMobilePermissions()
            await reloadAgentGateway()
            await reloadClaudeHooks()
            await reloadMultiMac()
            await reloadGitHubWatch()
        }
        .onReceive(NotificationCenter.default.publisher(
            for: NSApplication.didBecomeActiveNotification
        )) { _ in
            // Login Items approval happens in System Settings; re-read on return.
            refreshOpenAtLogin()
        }
    }

    @ViewBuilder
    private func pane(for dest: SettingsDestination) -> some View {
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

    private var generalPane: some View {
        Form {
            hostSection

            Section {
                Toggle(HeadroomCopy.openAtLogin, isOn: Binding(
                    get: { openAtLogin },
                    set: { setOpenAtLogin($0) }
                ))
                if openAtLoginNeedsApproval {
                    Button(HeadroomCopy.openLoginItemsSettings) {
                        LaunchAtLogin.openLoginItemsSettings()
                    }
                }
            } footer: {
                if let openAtLoginMessage {
                    Text(openAtLoginMessage)
                } else if openAtLoginNeedsApproval {
                    Text("macOS is waiting for you to allow Headroom in Login Items.")
                } else {
                    Text("Start the menu bar when you log in. The background host is separate and keeps its own LaunchAgent.")
                }
            }
            .onAppear(perform: refreshOpenAtLogin)

            Section {
                NavigationLink(value: SettingsDestination.otherMacs) {
                    LabeledContent(HeadroomCopy.otherMacs) {
                        Text(multiMac.enabled ? "On" : "Off")
                            .foregroundStyle(.secondary)
                    }
                }
            } footer: {
                Text("Share sources and settings between Macs through iCloud Drive.")
            }

            Section {
                Button(HeadroomCopy.showWelcome) {
                    NotificationCenter.default.post(
                        name: .headroomShowWelcome, object: nil)
                }
            }
        }
        .formStyle(.grouped)
    }

    private var hostSection: some View {
        Section {
            TextField(text: $endpoint) {
                Text("Endpoint")
            }
            Picker(selection: $refreshInterval) {
                Text("15 seconds").tag(15)
                Text("30 seconds").tag(30)
                Text("1 minute").tag(60)
                Text("2 minutes").tag(120)
            } label: {
                Text("Refresh")
            }
            if endpointIsRemote {
                SecureField("Host token", text: $hostToken)
                HStack {
                    Button(hostTokenStored ? "Replace token" : "Save token") {
                        saveHostToken()
                    }
                    .disabled(hostToken.trimmingCharacters(
                        in: .whitespacesAndNewlines).isEmpty)
                    if hostTokenStored {
                        Button("Forget", role: .destructive) {
                            TokenStore.host.delete()
                            hostTokenStored = false
                            hostToken = ""
                        }
                    }
                    Spacer()
                    Text(hostTokenStored ? "Keychain" : "Not set")
                        .font(.caption)
                        .foregroundStyle(
                            hostTokenStored
                                ? AnyShapeStyle(.secondary)
                                : AnyShapeStyle(HeadroomPalette.amber))
                }
            }
        } header: {
            Text("Host")
        } footer: {
            Text(endpointIsRemote
                 ? "Remote hosts need the host token (~/.headroom/token) — not the mobile token used by iPhone."
                 : "Mac, iPhone, and ESP32 all read this host. If it’s down, tap Start host or run ./scripts/install-host.sh from a clone. Source toggles also hide ESP32 pages.")
        }
    }

    private var otherMacsPane: some View {
        Form {
            Section {
                Toggle(
                    "Share settings between my Macs",
                    isOn: Binding(
                        get: { multiMac.enabled },
                        set: { enabled in
                            multiMac.enabled = enabled
                            Task { await saveMultiMac(enabled) }
                        }
                    )
                )
                .disabled(endpointIsRemote || changingMultiMac)

                LabeledContent("This Mac") {
                    HStack(spacing: 6) {
                        Text(multiMac.machine.name)
                            .foregroundStyle(.secondary)
                        if changingMultiMac {
                            ProgressView().controlSize(.small)
                        }
                    }
                }

                if multiMac.enabled {
                    // Ordered before the peer count on purpose: when macOS is
                    // blocking the read, "no other Macs yet" is not merely
                    // unhelpful, it is wrong. Publishing still works, so every
                    // Mac reports the same reassuring nothing.
                    if multiMac.mode == "cloudkit",
                       !MachineCloudSync.isAvailable {
                        // Only this side can know: the host has no idea how the
                        // app was signed. A development build silently doing
                        // nothing here is the most confusing outcome available.
                        //
                        // Two different reasons, and they need different
                        // answers. A notarized release with no iCloud profile
                        // is not something its owner can fix by downloading
                        // another copy of what they already have, which is
                        // exactly what the old wording sent them off to do.
                        Label(
                            MachineCloudSync.isDeveloperIDSigned
                            ? "This release was built without the iCloud "
                                + "profile, so multi-Mac sync is off."
                            : "Local builds cannot use iCloud. A notarized "
                                + "release carries the profile that turns "
                                + "multi-Mac sync on.",
                            systemImage: "exclamationmark.triangle"
                        )
                        .font(.caption)
                        .foregroundStyle(HeadroomPalette.amber)
                    } else if let failure = MachineCloudSync.lastFailure {
                        // Ahead of the host's trouble_detail because that field
                        // only ever describes the folder transport. A CloudKit
                        // round that threw used to fall all the way through to
                        // "No other Macs yet", which reads as a working sync
                        // that nobody else has joined.
                        Label(failure, systemImage: "exclamationmark.triangle")
                            .font(.caption)
                            .foregroundStyle(HeadroomPalette.amber)
                    } else if let detail = multiMac.troubleDetail {
                        Label(detail, systemImage: "exclamationmark.triangle")
                            .font(.caption)
                            .foregroundStyle(HeadroomPalette.amber)
                    } else if multiMac.peers.isEmpty {
                        Text("No other Macs yet. Turn this on over there too.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(multiMac.peers) { peer in
                            LabeledContent(peer.title) {
                                Text(peer.lastSeenLabel)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    // Only in folder mode is there a path worth showing.
                    // CloudKit has nowhere for anyone to look.
                    if !multiMac.directory.isEmpty {
                        Text(multiMac.directory)
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                }
                if let multiMacMessage {
                    Text(multiMacMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } footer: {
                Text(endpointIsRemote
                     ? "Multi-Mac settings must be changed on the Mac running the Headroom host."
                     : "Enabled sources, provider order, and accent colours follow you between Macs over iCloud. Credentials, file paths, and this Mac's local servers and commits are never shared. Quota percentages already match everywhere, because your provider counts the account rather than the machine.")
            }
        }
        .formStyle(.grouped)
    }

    private var codingAgentsPane: some View {
        Form {
            Section {
                LabeledContent(HeadroomCopy.claudeCodeHooks) {
                    if changingClaudeHooks {
                        ProgressView().controlSize(.small)
                    } else {
                        Label(
                            claudeHookStatus,
                            systemImage: claudeHookStatusSymbol
                        )
                        .font(.caption)
                        .foregroundStyle(claudeHookStatusColor)
                    }
                }
                HStack {
                    Button(claudeHooks?.installed == true
                           ? HeadroomCopy.reinstallHooks
                           : HeadroomCopy.installHooks) {
                        Task { await changeClaudeHooks("install") }
                    }
                    .disabled(endpointIsRemote || changingClaudeHooks)
                    if claudeHooks?.installed == true {
                        Button(HeadroomCopy.removeHooks) {
                            Task { await changeClaudeHooks("uninstall") }
                        }
                        .disabled(endpointIsRemote || changingClaudeHooks)
                    }
                    Button(HeadroomCopy.sendTestAttention) {
                        Task { await changeClaudeHooks("test") }
                    }
                    .disabled(endpointIsRemote || changingClaudeHooks)
                    Spacer()
                }
                if let path = claudeHooks?.settingsPath {
                    Text(path)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                if let claudeHooksMessage {
                    Text(claudeHooksMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Divider()

                Toggle(
                    "Enable Codex attention gateway",
                    isOn: Binding(
                        get: { agentGatewayEnabled },
                        set: { enabled in
                            agentGatewayEnabled = enabled
                            Task { await saveAgentGateway() }
                        }
                    )
                )
                .disabled(endpointIsRemote || changingAgentGateway)

                TextField("Codex executable", text: $codexBinary)
                    .textFieldStyle(.roundedBorder)
                    .disabled(endpointIsRemote || changingAgentGateway)
                if let path = agentProviderStatus?.resolvedBinary {
                    Text(HeadroomCopy.usingCodex(at: path))
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }

                HStack {
                    Button("Choose…") {
                        chooseCodexBinary()
                    }
                    .disabled(endpointIsRemote || changingAgentGateway)
                    Button("Apply & test") {
                        Task { await saveAgentGateway() }
                    }
                    .disabled(
                        endpointIsRemote
                        || changingAgentGateway
                        || codexBinary.trimmingCharacters(
                            in: .whitespacesAndNewlines).isEmpty
                    )
                    Spacer()
                    if changingAgentGateway {
                        ProgressView().controlSize(.small)
                    } else if let status = agentProviderStatus {
                        Label(
                            agentStatusLabel(status),
                            systemImage: agentStatusSymbol(status)
                        )
                        .font(.caption)
                        .foregroundStyle(agentStatusColor(status))
                    }
                }
                if let agentGatewayMessage {
                    Text(agentGatewayMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } footer: {
                Text(endpointIsRemote
                     ? "Coding-agent server settings must be changed on the Mac running the Headroom host."
                     : "Claude hooks observe existing Claude Code sessions and can return permission answers. Codex runs as a Headroom-owned App Server. The iPhone permission separately controls remote answers.")
            }
        }
        .formStyle(.grouped)
    }

    private var iPhonePane: some View {
        Form {
            Section {
                LabeledContent("Discovery") {
                    Text("Automatic on local Wi‑Fi")
                        .foregroundStyle(.secondary)
                }
                HStack {
                    Button("Copy mobile token") {
                        copyMobileToken()
                    }
                    Spacer()
                    if let mobileTokenMessage {
                        Text(mobileTokenMessage)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                ForEach(MobilePermission.allCases, id: \.rawValue) { permission in
                    HStack {
                        Text(permission.title)
                        Spacer()
                        if changingMobilePermission == permission {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Toggle(
                                permission.title,
                                isOn: Binding(
                                    get: { mobilePermissions[permission] },
                                    set: { enabled in
                                        Task {
                                            await setMobilePermission(
                                                permission,
                                                enabled: enabled
                                            )
                                        }
                                    }
                                )
                            )
                            .labelsHidden()
                        }
                    }
                }
            } footer: {
                Text("Copy mobile token (~/.headroom/mobile-token), open Headroom on iPhone, tap this Mac, paste once. Do not use the host token (that’s for the ESP32). Tailscale names remain available as a fallback.")
            }
        }
        .formStyle(.grouped)
    }

    private var sourcesPane: some View {
        SettingsSourcesPane(
            sources: sources,
            usage: usageProviders,
            accountProviders: accountProviders,
            detected: detectedSources,
            busyID: togglingSourceID,
            isSyncing: isSyncing,
            message: sourcesMessage,
            dropTargetID: dropTargetID,
            onToggleRows: { ids, enabled in
                Task { await setSourceRows(ids, enabled: enabled) }
            },
            onDismissRows: { ids in
                Task { await dismissSourceRows(ids) }
            },
            onRemoveAccount: { id in
                Task { await removeAccount(id) }
            },
            onAddAccount: { provider in
                addingAccountProvider = provider
            },
            onRefresh: { ids in
                Task { await refreshSources(ids) }
            },
            onMoveService: { dragged, target in
                dropTargetID = nil
                Task { await moveService(dragged, before: target) }
            },
            onNudgeService: { id, offset in
                Task { await nudgeService(id, by: offset) }
            },
            onDropTarget: { id, targeted in
                dropTargetID = targeted ? id : nil
            },
            onAccent: { ids, hex in
                Task { await setAccents(ids, hex: hex) }
            }
        )
        .sheet(item: $addingAccountProvider) { provider in
            AddAccountSheet(provider: provider, endpoint: endpoint) {
                await reloadSources()
            }
        }
    }

    private var integrationsHub: some View {
        Form {
            Section {
                ForEach(SettingsIntegration.allCases, id: \.self) { kind in
                    NavigationLink(value: SettingsDestination.integration(kind)) {
                        Label(kind.title, systemImage: kind.symbol)
                        Spacer()
                        Text(integrationStatus(kind))
                            .foregroundStyle(
                                integrationConnected(kind)
                                    ? AnyShapeStyle(.secondary)
                                    : AnyShapeStyle(HeadroomPalette.amber)
                            )
                    }
                }
            } footer: {
                Text("Keys stay in the Keychain on this Mac. The iPhone never sees them.")
            }

            Section("Dashboard") {
                Stepper(
                    "\(HeadroomCopy.activity) rows: \(activityRowLimit)",
                    value: $activityRowLimit,
                    in: 3...14
                )
                Stepper(
                    "\(HeadroomCopy.localServers): \(serverRowLimit)",
                    value: $serverRowLimit,
                    in: 1...8
                )
                Stepper(
                    "Supabase projects: \(supabaseRowLimit)",
                    value: $supabaseRowLimit,
                    in: 1...20
                )
                Stepper(
                    "Plausible sites: \(plausibleRowLimit)",
                    value: $plausibleRowLimit,
                    in: 1...20
                )
                Toggle("Confirm before stopping servers", isOn: $confirmServerStops)
            }
        }
        .formStyle(.grouped)
    }

    private func integrationStatus(_ kind: SettingsIntegration) -> String {
        integrationConnected(kind) ? "Connected" : "Not connected"
    }

    private func integrationConnected(_ kind: SettingsIntegration) -> Bool {
        switch kind {
        case .supabase: return tokenStored
        case .plausible: return plausibleTokenStored
        case .github: return githubTokenStored
        }
    }

    @ViewBuilder
    private func integrationPane(_ kind: SettingsIntegration) -> some View {
        Form {
            switch kind {
            case .supabase: supabaseSections
            case .plausible: plausibleSections
            case .github: githubSections
            }
        }
        .formStyle(.grouped)
    }

    @ViewBuilder
    private var supabaseSections: some View {
        Section {
            LabeledContent("Status") {
                Text(tokenStored ? "Keychain" : "Not connected")
                    .foregroundStyle(tokenStored ? AnyShapeStyle(.secondary) : AnyShapeStyle(HeadroomPalette.amber))
            }
            SecureField("sbp_… or access token", text: $supabaseToken)
                .onSubmit {
                    if !tokenDraft.isEmpty { saveSupabaseToken() }
                }
            HStack {
                if tokenDraft.isEmpty {
                    Button("Refresh") {
                        Task { await refreshSources(["supabase"]) }
                    }
                    .disabled(!tokenStored || isSyncing)
                } else {
                    Button(tokenStored ? "Replace" : "Connect") {
                        saveSupabaseToken()
                    }
                    .disabled(isSyncing)
                    .keyboardShortcut(.defaultAction)
                }
                if tokenStored {
                    Button("Disconnect", role: .destructive) {
                        disconnectSupabase()
                    }
                    .disabled(isSyncing)
                }
                Spacer()
                Button("Create token…") {
                    openURL("https://supabase.com/dashboard/account/tokens")
                }
                .buttonStyle(.link)
            }
            if let supabaseMessage {
                Text(supabaseMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        } footer: {
            Text("PAT stays in Keychain.")
        }
    }

    @ViewBuilder
    private var plausibleSections: some View {
        Section {
            LabeledContent("Status") {
                Text(plausibleTokenStored ? "Keychain" : "Not connected")
                    .foregroundStyle(plausibleTokenStored
                                     ? AnyShapeStyle(.secondary)
                                     : AnyShapeStyle(HeadroomPalette.amber))
            }
            SecureField("Stats API key", text: $plausibleToken)
                .onSubmit {
                    if !plausibleTokenDraft.isEmpty { savePlausibleToken() }
                }
            Picker("Window", selection: Binding(
                get: { plausibleRange },
                set: { newValue in
                    guard newValue != plausibleRange else { return }
                    plausibleRange = newValue
                    Task { await applyPlausibleRange(newValue) }
                }
            )) {
                Text("Today").tag("day")
                Text("Last 24 hours").tag("24h")
                Text("Last 7 days").tag("7d")
                Text("Last 30 days").tag("30d")
            }
            .disabled(isSyncing)
            HStack {
                if plausibleTokenDraft.isEmpty {
                    Button("Refresh") {
                        Task { await refreshSources(["plausible"]) }
                    }
                    .disabled(!plausibleTokenStored || isSyncing)
                } else {
                    Button(plausibleTokenStored ? "Replace" : "Connect") {
                        savePlausibleToken()
                    }
                    .disabled(isSyncing)
                }
                if plausibleTokenStored {
                    Button("Disconnect", role: .destructive) {
                        disconnectPlausible()
                    }
                    .disabled(isSyncing)
                }
                Spacer()
                Button("Create key…") {
                    openURL("https://plausible.io/settings#api-keys")
                }
                .buttonStyle(.link)
            }
            if let plausibleMessage {
                Text(plausibleMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        } footer: {
            Text("API key stays in Keychain.")
        }
    }

    @ViewBuilder
    private var githubSections: some View {
        Section {
            LabeledContent("Status") {
                Text(githubTokenStored ? "Keychain" : "Not connected")
                    .foregroundStyle(githubTokenStored ? AnyShapeStyle(.secondary) : AnyShapeStyle(HeadroomPalette.amber))
            }
            SecureField("ghp_… (repo + actions:read)", text: $githubToken)
                .onSubmit {
                    if !githubTokenDraft.isEmpty { saveGitHubToken() }
                }
            HStack {
                if githubTokenDraft.isEmpty {
                    Button("Refresh") {
                        Task { await refreshSources(["github"]) }
                    }
                    .disabled(!githubTokenStored || isSyncing)
                } else {
                    Button(githubTokenStored ? "Replace" : "Connect") {
                        saveGitHubToken()
                    }
                    .disabled(isSyncing)
                }
                if githubTokenStored {
                    Button("Disconnect", role: .destructive) {
                        disconnectGitHub()
                    }
                    .disabled(isSyncing)
                }
                Spacer()
                Button("Create token…") {
                    openURL("https://github.com/settings/tokens")
                }
                .buttonStyle(.link)
            }
            if let githubMessage {
                Text(githubMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Divider()
            if !githubWatchEditable {
                Text("Repo settings need a running, up to date host.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            TextField(
                "Owners",
                text: $githubOwners,
                prompt: Text("acme/, ada/ (blank watches every repo found)")
            )
            TextField(
                "Always watch",
                text: $githubAlwaysRepos,
                prompt: Text("acme/api, ada/site")
            )
            Stepper(
                "Discover up to \(githubMaxDiscovered) repos",
                value: $githubMaxDiscovered,
                in: 0...50
            )
            HStack {
                Button("Save repos") {
                    Task { await saveGitHubWatch() }
                }
                .disabled(savingGitHubWatch || !githubWatchEditable)
                if savingGitHubWatch {
                    ProgressView().controlSize(.small)
                }
                Spacer()
            }
            if !githubWatching.isEmpty {
                LabeledContent("Watching") {
                    Text(githubWatching.joined(separator: ", "))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }
        } footer: {
            Text("Owners filter the repos found under \(githubDevRoot). Always-watch takes owner/name and ignores that filter. Failures show under \(HeadroomCopy.activity).")
        }
    }

    private var aboutPane: some View {
        Form {
            Section {
                AboutHeadroomView()
            }
        }
        .formStyle(.grouped)
    }

    /// Mirrors `sources_config.FOCUS_LIMIT`.
    private var focusLimit: Int { 3 }

    /// AI services in pinned order, each carrying its account ids as a block.
    /// The wire order stays account-level; the pane reorders services.
    private var aiServiceBlocks: [(id: String, rowIDs: [String])] {
        SourceService.services(from: sources)
            .filter { $0.group == .ai }
            .map { ($0.id, $0.rows.map(\.id)) }
    }

    /// Drop `dragged` into `target`'s slot, moving the service's accounts as
    /// one block. The list sent is the whole AI group including disabled
    /// rows — a service you turned off keeps its place rather than sinking.
    private func moveService(_ dragged: String, before target: String) async {
        guard dragged != target else { return }
        var blocks = aiServiceBlocks
        guard let from = blocks.firstIndex(where: { $0.id == dragged }) else {
            return
        }
        let moved = blocks.remove(at: from)
        guard let to = blocks.firstIndex(where: { $0.id == target }) else {
            return
        }
        blocks.insert(moved, at: to)
        await commitOrder(blocks.flatMap(\.rowIDs), movedID: dragged)
    }

    /// Keyboard / VoiceOver path to the same reorder, so pinning isn't
    /// drag-only.
    private func nudgeService(_ id: String, by offset: Int) async {
        var blocks = aiServiceBlocks
        guard let from = blocks.firstIndex(where: { $0.id == id }) else {
            return
        }
        let to = from + offset
        guard blocks.indices.contains(to) else { return }
        blocks.swapAt(from, to)
        await commitOrder(blocks.flatMap(\.rowIDs), movedID: id)
    }

    /// Repaint a service everywhere — every account of it, one POST each,
    /// then one reload. `nil` restores the shipped color.
    private func setAccents(_ ids: [String], hex: String?) async {
        togglingSourceID = ids.first
        defer { togglingSourceID = nil }
        do {
            for id in ids {
                _ = try await client.setSourceAccent(id, hex: hex)
            }
            // Colors are presentation only — the host republished the cached
            // document, so re-reading it is the whole update.
            await reloadSources()
            sourcesMessage = hex == nil
                ? "Restored the default color."
                : "Color updated — menu bar, rings and iPhone follow."
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    /// Remove an extra login. The host re-execs to rebuild its registry, so
    /// this waits for the restart the same way the add sheet does.
    private func removeAccount(_ id: String) async {
        togglingSourceID = id
        defer { togglingSourceID = nil }
        let before = try? await client.health().uptimeS
        do {
            _ = try await client.removeAccount(id)
            sourcesMessage = "Removed \(id). Restarting host…"
            await AddAccountSheet.waitForRestart(
                client: client, previousUptime: before)
            await reloadSources()
            sourcesMessage = "Removed \(id)."
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    private func commitOrder(_ order: [String], movedID: String) async {
        togglingSourceID = movedID
        defer { togglingSourceID = nil }
        // Reordering is local bookkeeping — nothing to refetch.
        do {
            _ = try await client.setSourceOrder(order)
            await reloadSources()
            sourcesMessage = "Reordered — top \(focusLimit) drive the menu bar."
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    private func saveHostToken() {
        let token = hostToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else { return }
        do {
            try TokenStore.host.save(token)
            hostToken = ""
            hostTokenStored = true
            Task { await reloadSources() }
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    private func reloadMobilePermissions() async {
        if let permissions = try? await client.fetchMobilePermissions() {
            mobilePermissions = permissions
        }
    }

    private func reloadAgentGateway() async {
        do {
            let configuration = try await client.fetchAgentGatewayConfiguration()
            agentGatewayEnabled = configuration.enabled
            codexBinary = configuration.codexBinary
            agentProviderStatus = configuration.provider
        } catch {
            agentGatewayMessage = error.localizedDescription
        }
    }

    private func reloadClaudeHooks() async {
        do {
            claudeHooks = try await client.fetchClaudeHookConfiguration()
        } catch {
            claudeHooksMessage = error.localizedDescription
        }
    }

    private func reloadMultiMac() async {
        do {
            multiMac = try await client.fetchMultiMacConfiguration()
        } catch {
            multiMacMessage = error.localizedDescription
        }
    }

    private func saveMultiMac(_ enabled: Bool) async {
        guard !changingMultiMac else { return }
        changingMultiMac = true
        multiMacMessage = nil
        defer { changingMultiMac = false }
        do {
            multiMac = try await client.setMultiMacConfiguration(enabled: enabled)
            if multiMac.enabled {
                multiMacMessage = multiMac.peers.isEmpty
                    ? nil
                    : "Found \(multiMac.peers.count) other Mac"
                        + (multiMac.peers.count == 1 ? "." : "s.")
            } else {
                // The folder is left where it is. Turning sync off should stop
                // this Mac publishing, not reach into iCloud and delete a
                // record the other Macs are still reading.
                multiMacMessage = "This Mac has stopped sharing."
            }
        } catch {
            multiMacMessage = error.localizedDescription
            await reloadMultiMac()
        }
    }

    private func changeClaudeHooks(_ action: String) async {
        guard !changingClaudeHooks else { return }
        changingClaudeHooks = true
        claudeHooksMessage = nil
        defer { changingClaudeHooks = false }
        do {
            claudeHooks = try await client.changeClaudeHooks(action)
            switch action {
            case "install":
                claudeHooksMessage =
                    "Claude Code will now send attention events to Headroom."
            case "uninstall":
                claudeHooksMessage =
                    "Headroom-owned hooks were removed. Other Claude hooks were preserved."
            case "test":
                claudeHooksMessage =
                    "Test attention added. Check Activity on this Mac or iPhone."
            default:
                break
            }
        } catch {
            claudeHooksMessage = error.localizedDescription
            await reloadClaudeHooks()
        }
    }

    private var claudeHookStatus: String {
        switch claudeHooks?.state {
        case "installed": "Installed"
        case "outdated": "Update available"
        case "modified_externally": "Modified externally"
        case "error": "Configuration error"
        default: "Not installed"
        }
    }

    private var claudeHookStatusSymbol: String {
        switch claudeHooks?.state {
        case "installed": "checkmark.circle.fill"
        case "not_installed", nil: "circle"
        default: "exclamationmark.triangle.fill"
        }
    }

    private var claudeHookStatusColor: AnyShapeStyle {
        switch claudeHooks?.state {
        case "installed": AnyShapeStyle(.green)
        case "not_installed", nil: AnyShapeStyle(.secondary)
        default: AnyShapeStyle(HeadroomPalette.amber)
        }
    }

    private func saveAgentGateway() async {
        guard !changingAgentGateway else { return }
        let binary = codexBinary.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !binary.isEmpty else { return }
        changingAgentGateway = true
        agentGatewayMessage = nil
        defer { changingAgentGateway = false }
        do {
            var configuration = try await client.setAgentGatewayConfiguration(
                enabled: agentGatewayEnabled,
                codexBinary: binary
            )
            for _ in 0..<5 {
                guard configuration.enabled,
                      configuration.provider.connection == "starting"
                else { break }
                try? await Task.sleep(for: .milliseconds(400))
                configuration = try await client.fetchAgentGatewayConfiguration()
            }
            agentGatewayEnabled = configuration.enabled
            codexBinary = configuration.codexBinary
            agentProviderStatus = configuration.provider
            agentGatewayMessage = configuration.enabled
                ? (configuration.provider.connection == "ready"
                    ? "Codex App Server is ready."
                    : configuration.provider.error)
                : "Gateway is off."
        } catch {
            agentGatewayMessage = error.localizedDescription
            await reloadAgentGateway()
        }
    }

    private func chooseCodexBinary() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.message = "Choose the Codex CLI executable"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        codexBinary = url.path
    }

    private func agentStatusLabel(_ status: AgentProviderStatus) -> String {
        if !status.available { return "Executable not found" }
        switch status.connection {
        case "ready": return "Ready"
        case "starting": return "Starting…"
        case "disconnected": return "Disconnected"
        case "stopped", "disabled": return "Off"
        default: return status.connection.capitalized
        }
    }

    private func agentStatusSymbol(_ status: AgentProviderStatus) -> String {
        if !status.available || status.connection == "disconnected" {
            return "exclamationmark.triangle.fill"
        }
        return status.connection == "ready"
            ? "checkmark.circle.fill"
            : "circle.dotted"
    }

    private func agentStatusColor(
        _ status: AgentProviderStatus
    ) -> AnyShapeStyle {
        if !status.available || status.connection == "disconnected" {
            return AnyShapeStyle(HeadroomPalette.amber)
        }
        if status.connection == "ready" {
            return AnyShapeStyle(HeadroomPalette.green)
        }
        return AnyShapeStyle(.secondary)
    }

    private func setMobilePermission(
        _ permission: MobilePermission,
        enabled: Bool
    ) async {
        guard changingMobilePermission == nil else { return }
        changingMobilePermission = permission
        defer { changingMobilePermission = nil }
        var updated = mobilePermissions
        updated[permission] = enabled
        do {
            mobilePermissions = try await client.setMobilePermissions(updated)
        } catch {
            mobileTokenMessage = error.localizedDescription
        }
    }

    private func saveSupabaseToken() {
        let token = tokenDraft
        guard !token.isEmpty else { return }
        do {
            try TokenStore.supabase.save(token)
            supabaseToken = ""
            tokenStored = true
            supabaseMessage = "Saved — refreshing…"
            Task { await refreshSources(["supabase"]) }
        } catch {
            supabaseMessage = error.localizedDescription
        }
    }

    private func disconnectSupabase() {
        TokenStore.supabase.delete()
        tokenStored = false
        supabaseToken = ""
        supabaseMessage = "Disconnected"
        Task { await refreshSources(["supabase"]) }
    }

    private func savePlausibleToken() {
        let token = plausibleTokenDraft
        guard !token.isEmpty else { return }
        do {
            try TokenStore.plausible.save(token)
            plausibleToken = ""
            plausibleTokenStored = true
            plausibleMessage = "Saved — refreshing…"
            Task { await refreshSources(["plausible"]) }
        } catch {
            plausibleMessage = error.localizedDescription
        }
    }

    private func disconnectPlausible() {
        TokenStore.plausible.delete()
        plausibleTokenStored = false
        plausibleToken = ""
        plausibleMessage = "Disconnected"
        Task { await refreshSources(["plausible"]) }
    }

    private func applyPlausibleRange(_ range: String) async {
        isSyncing = true
        defer { isSyncing = false }
        do {
            let saved = try await client.setPlausibleRange(range)
            plausibleRange = saved
            await client.waitForRefresh(sources: ["plausible"])
            await reloadSources()
            plausibleMessage = sources
                .first(where: { $0.id == "plausible" })?
                .detail ?? "Window updated"
        } catch {
            plausibleMessage = error.localizedDescription
        }
    }

    private func saveGitHubToken() {
        let token = githubTokenDraft
        guard !token.isEmpty else { return }
        do {
            try TokenStore.github.save(token)
            githubToken = ""
            githubTokenStored = true
            githubMessage = "Saved — refreshing Actions…"
            Task { await refreshSources(["github"]) }
        } catch {
            githubMessage = error.localizedDescription
        }
    }

    private func reloadGitHubWatch() async {
        do {
            applyGitHubWatch(try await client.fetchGitHubWatch())
            githubWatchEditable = true
        } catch {
            githubWatchEditable = false
        }
    }

    private func applyGitHubWatch(_ watch: GitHubWatch) {
        githubOwners = watch.owners.joined(separator: ", ")
        githubAlwaysRepos = watch.alwaysRepos.joined(separator: ", ")
        githubMaxDiscovered = watch.maxDiscovered
        githubWatching = watch.watching
        if let root = watch.devRoot, !root.isEmpty { githubDevRoot = root }
    }

    /// Both fields take a comma- or newline-separated list; the host does the
    /// real validation and says which entry it refused.
    private func splitList(_ text: String) -> [String] {
        text.split(whereSeparator: { $0 == "," || $0.isNewline })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    private func saveGitHubWatch() async {
        savingGitHubWatch = true
        defer { savingGitHubWatch = false }
        do {
            let watch = try await client.setGitHubWatch(
                owners: splitList(githubOwners),
                alwaysRepos: splitList(githubAlwaysRepos),
                maxDiscovered: githubMaxDiscovered
            )
            applyGitHubWatch(watch)
            githubMessage = watch.watching.isEmpty
                ? "Saved. Nothing matched under \(githubDevRoot) yet."
                : "Watching \(watch.watching.count) "
                    + (watch.watching.count == 1 ? "repo." : "repos.")
            await refreshSources(["github"])
        } catch {
            githubMessage = error.localizedDescription
        }
    }

    private func disconnectGitHub() {
        TokenStore.github.delete()
        githubTokenStored = false
        githubToken = ""
        githubMessage = "Disconnected"
        Task { await refreshSources(["github"]) }
    }

    /// Toggle a whole service or a single account — same path, different id
    /// lists. One POST either way, so three Claude accounts flip together
    /// instead of racing three writes.
    private func setSourceRows(_ ids: [String], enabled: Bool) async {
        guard let first = ids.first else { return }
        togglingSourceID = first
        defer { togglingSourceID = nil }
        do {
            var map = Dictionary(
                uniqueKeysWithValues: sources.map { ($0.id, $0.enabled ?? true) })
            for id in ids { map[id] = enabled }
            // Turning on also un-dismisses: a Library chip tap and an Active
            // toggle are the same write, and both must land the row in
            // Active. Turning off sends no dismissed key — that's a pause,
            // and the row stays put.
            _ = try await client.setSources(
                map,
                dismissed: enabled
                    ? Dictionary(uniqueKeysWithValues: ids.map { ($0, false) })
                    : nil)
            // Toggling on kicks a refresh host-side; wait for it to land rather
            // than guessing how long it takes.
            if enabled {
                await client.waitForRefresh(sources: ids)
            }
            await reloadSources()
            let names = ids.joined(separator: ", ")
            sourcesMessage = enabled
                ? "Enabled \(names) — ESP32 will show it on next poll."
                : "Paused \(names) — stays listed, stops fetching; ESP32 will hide that page."
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    /// The row's ✕: back to the Library. The host flips `dismissed` and
    /// disables the rows in the same write.
    private func dismissSourceRows(_ ids: [String]) async {
        guard let first = ids.first else { return }
        togglingSourceID = first
        defer { togglingSourceID = nil }
        do {
            _ = try await client.setSources(
                [:],
                dismissed: Dictionary(
                    uniqueKeysWithValues: ids.map { ($0, true) }))
            await reloadSources()
            sourcesMessage =
                "Moved \(ids.joined(separator: ", ")) to the Library."
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    private func refreshSources(_ ids: [String]?) async {
        isSyncing = true
        defer { isSyncing = false }
        do {
            try await client.refresh(sources: ids)
            // /sync/refresh answers 202 and works in the background.
            await client.waitForRefresh(sources: ids)
            await reloadSources()
            if ids == ["supabase"] {
                supabaseMessage = sources
                    .first(where: { $0.id == "supabase" })?
                    .detail ?? "Supabase refreshed"
            }
            if ids == ["plausible"] {
                plausibleMessage = sources
                    .first(where: { $0.id == "plausible" })?
                    .detail ?? "Plausible refreshed"
            }
            sourcesMessage = "Synced."
        } catch {
            sourcesMessage = error.localizedDescription
        }
        tokenStored = TokenStore.supabase.exists()
        plausibleTokenStored = TokenStore.plausible.exists()
        githubTokenStored = TokenStore.github.exists()
    }

    private func reloadSources() async {
        do {
            let snapshot = try await client.fetchUsage()
            sources = snapshot.sources ?? []
            usageProviders = Dictionary(
                (snapshot.providers ?? []).map { ($0.id, $0) },
                uniquingKeysWith: { first, _ in first })
            if let range = snapshot.plausible?.range {
                plausibleRange = range
            }
            if sources.isEmpty {
                sourcesMessage = "Host has no sources payload — restart com.centaur-labs.headroom."
            }
        } catch {
            sourcesMessage = error.localizedDescription
        }
        // Capability and detection are additive context: a host predating
        // either endpoint just means no "Add account…" links and no dimmed
        // chips, never an error in the pane.
        if let accounts = try? await client.fetchAccounts() {
            accountProviders = accounts.providers
        }
        if let setup = try? await client.fetchSetup() {
            detectedSources = Dictionary(
                setup.sources.map { ($0.id, $0.detected) },
                uniquingKeysWith: { first, _ in first })
        }
    }

    private func openURL(_ string: String) {
        guard let url = URL(string: string) else { return }
        NSWorkspace.shared.open(url)
    }

    private func copyMobileToken() {
        guard let value = HostController.mobileToken else {
            mobileTokenMessage = "Start the host first"
            return
        }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
        mobileTokenMessage = "Copied"
    }

    private func refreshOpenAtLogin() {
        openAtLogin = LaunchAtLogin.isRequested
        openAtLoginNeedsApproval = LaunchAtLogin.needsApproval
    }

    private func setOpenAtLogin(_ enabled: Bool) {
        openAtLoginMessage = nil
        do {
            try LaunchAtLogin.setEnabled(enabled)
            refreshOpenAtLogin()
        } catch {
            refreshOpenAtLogin()
            openAtLoginMessage = error.localizedDescription
        }
    }
}

/// SwiftUI's Settings scene is panel-like in a menu-bar app. Promote it to a
/// regular, floating window and activate the app when the window is shown.
private struct SettingsWindowConfigurator: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        SettingsWindowObserverView()
    }

    func updateNSView(_ nsView: NSView, context: Context) {}
}

private final class SettingsWindowObserverView: NSView {
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard let window else { return }
        window.title = "Headroom Settings"
        window.level = .floating
        window.hidesOnDeactivate = false
        window.collectionBehavior.insert(.moveToActiveSpace)
        window.styleMask.insert([.titled, .closable, .miniaturizable])
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }
}

