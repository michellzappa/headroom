import AppKit
import SwiftUI

/// The popover shell: header, semantic mode switcher, footer, and the stack of
/// sections. Provider selection stays inside Usage, matching the iPhone's
/// Usage → provider detail relationship.
/// Each section owns its own rendering and preferences — see QuotaSection,
/// ActivitySection, SupabaseSection, ServersSection, BuildsSection.
struct DashboardView: View {
    @ObservedObject var store: UsageStore
    @AppStorage("selectedProvider")
    private var selectedProviderRaw = UsageProvider.codex.rawValue
    @AppStorage("selectedDashboard")
    private var selectedDashboardRaw = DashboardSelection.overview
    @AppStorage("selectedDashboardMode")
    private var selectedModeRaw = DashboardMode.overview.rawValue
    @State private var serverToStop: LocalServer?
    /// Activity / Attention row drill-in. Explicit swap rather than
    /// `NavigationStack` — the latter's chrome is unreliable inside an
    /// `NSPopover`, and a Back that returns to the mode you left is clearer.
    @State private var serviceDetail: ServiceDetailSelection?
    @ObservedObject private var updates = UpdateChecker.shared
    @State private var updateInstallMessage: String?
    @AppStorage("confirmServerStops")
    private var confirmServerStops = true

    private var visibleProviders: [QuotaProviderInfo] {
        store.snapshot.codingQuotaProviders
    }

    private var selectedMode: DashboardMode {
        DashboardMode(rawValue: selectedModeRaw) ?? .overview
    }

    private var isOverview: Bool {
        selectedMode == .overview
    }

    /// The setup card is for "there is no host to talk to", not "the last call
    /// failed". `errorMessage` collects every failure in the app — a refused
    /// server stop, one flaky poll — and keying off it threw the whole
    /// dashboard back to the setup sheet over things the host was fine for.
    ///
    /// First run is not part of this condition any more: it has its own window
    /// (`WelcomeWindowController`), so an empty dashboard behind the welcome is
    /// the dashboard, not a second copy of onboarding.
    private var needsSetup: Bool {
        !store.hostReachable
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            if !needsSetup {
                modeSwitcher
            }
            Divider()
            if let serviceDetail {
                serviceDetailPane(serviceDetail)
            } else {
                ScrollView {
                    VStack(spacing: 16) {
                        if needsSetup {
                            SetupView(store: store) {
                                Task { await store.refresh() }
                            }
                        } else {
                            if let skew = store.hostSkew {
                                HostSkewBanner(skew: skew, store: store)
                            }
                            if selectedMode == .attention {
                                AttentionSection(
                                    store: store,
                                    selection: $serviceDetail
                                )
                            } else if selectedMode == .activity {
                                let blocks = IntegrationWatch.activityBlocks(
                                    from: store.snapshot.integrationsOrder
                                        ?? store.snapshot.servicesOrder
                                )
                                ForEach(blocks) { watch in
                                    activityBlock(watch, blocks: blocks)
                                }
                                MachinesSection(machines: store.snapshot.peerMachines)
                            } else {
                                providerSwitcher
                                if selectedDashboardRaw == DashboardSelection.overview {
                                    QuotaOverviewCard(snapshot: store.snapshot) { providerID in
                                        selectedProviderRaw = providerID
                                        selectedDashboardRaw = providerID
                                    }
                                    OverviewBurndownCard(snapshot: store.snapshot)
                                    DailyBurnCard(
                                        days: store.snapshot.byDay ?? [],
                                        providerIDs: visibleProviders.map(\.id),
                                        tintFor: store.snapshot.tint(forProviderID:)
                                    )
                                    ActivityHistoryCard(
                                        history: store.snapshot.activityHistory
                                    )
                                    SpendCard(
                                        history: store.snapshot.history,
                                        today: store.snapshot.today
                                    )
                                } else {
                                    let providerID = selectedDashboardRaw
                                    let meter = store.snapshot.meter(
                                        forProviderID: providerID)
                                    let provider = store.snapshot
                                        .codingQuotaProviders
                                        .first { $0.id == providerID }
                                    ProviderQuotaCard(
                                        meter: meter,
                                        subscriptionPricing: provider?
                                            .subscriptionPricing,
                                        todayBurn: store.snapshot.byDay?
                                            .last?
                                            .burn(forProviderID: providerID),
                                        tint: store.snapshot.tint(
                                            forProviderID: providerID)
                                    )
                                    // Window burndown only — balance providers
                                    // live on Activity, not Usage tabs.
                                    if provider != nil {
                                        BurndownCard(
                                            providerID: providerID,
                                            rings: store.snapshot.burndownRings(
                                                forProviderID: providerID),
                                            tint: store.snapshot.tint(
                                                forProviderID: providerID),
                                            resetNoteURL: provider?
                                                .resetNoteURL
                                                .flatMap(URL.init(string:))
                                        )
                                    }
                                }
                            }
                        }
                    }
                    .padding(16)
                }
            }
            Divider()
            footer
        }
        .frame(width: 390, height: 620)
        .background(Color(nsColor: .windowBackgroundColor))
        .onChange(of: selectedModeRaw) { _, _ in
            serviceDetail = nil
        }
        .alert(item: $serverToStop) { server in
            Alert(
                title: Text("Stop \(server.name ?? "server")?"),
                message: Text("This terminates the local process."),
                primaryButton: .destructive(Text("Stop")) {
                    Task { await store.stopServer(server) }
                },
                secondaryButton: .cancel()
            )
        }
        .alert(
            "Couldn’t install update",
            isPresented: Binding(
                get: { updateInstallMessage != nil },
                set: { if !$0 { updateInstallMessage = nil } }
            )
        ) {
            Button("OK", role: .cancel) { updateInstallMessage = nil }
        } message: {
            Text(updateInstallMessage ?? "Please try again from Settings.")
        }
    }

    /// Full-height drill-in: Back to the mode you left, then the shared detail
    /// page (same views as iPhone). Resolves by id so numbers stay live.
    @ViewBuilder
    private func serviceDetailPane(_ selection: ServiceDetailSelection) -> some View {
        VStack(spacing: 0) {
            ZStack {
                HStack(spacing: 8) {
                    Button {
                        serviceDetail = nil
                    } label: {
                        Label(selectedMode.title, systemImage: "chevron.left")
                            .labelStyle(.titleAndIcon)
                            .font(.subheadline.weight(.medium))
                    }
                    .buttonStyle(.borderless)
                    .help("Back to \(selectedMode.title)")
                    Spacer(minLength: 0)
                    PermalinkButton(url: serviceDetailPermalink(selection))
                }
                Text(serviceDetailTitle(selection))
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                    .padding(.horizontal, 96)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            Divider()
            resolvedServiceDetail(selection)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
    }

    private func activityItem(id: String) -> ActivityItem? {
        (store.snapshot.activity ?? []).first { $0.id == id }
            ?? AttentionList.failures(in: store.snapshot).first { $0.id == id }
    }

    private func serviceDetailPermalink(
        _ selection: ServiceDetailSelection
    ) -> URL? {
        switch selection {
        case .activity(let id):
            return activityItem(id: id).flatMap(Permalink.activity)
        case .plausible(let domain):
            return store.snapshot.plausible?.sites?
                .first { $0.domain == domain }
                .flatMap { Permalink.url(from: $0.dashboardURL) }
        case .posthog(let id):
            return store.snapshot.posthog?.projects?
                .first { $0.id == id }
                .flatMap { Permalink.url(from: $0.dashboardURL) }
        case .supabase(let ref):
            return store.snapshot.supabase?.projects?
                .first { $0.ref == ref }
                .flatMap { Permalink.url(from: $0.dashboardURL) }
        case .server(let id):
            return store.snapshot.local?.servers?
                .first { $0.id == id }
                .flatMap(Permalink.localServer)
        case .build:
            return nil
        }
    }

    private func serviceDetailTitle(_ selection: ServiceDetailSelection) -> String {
        switch selection {
        case .activity(let id):
            return activityItem(id: id)?.subject ?? "Event"
        case .plausible(let domain):
            return domain
        case .posthog(let id):
            return store.snapshot.posthog?.projects?
                .first { $0.id == id }?.displayName
                ?? "PostHog"
        case .supabase(let ref):
            let project = store.snapshot.supabase?.projects?
                .first { $0.ref == ref }
            return project?.name ?? project?.ref ?? "Supabase"
        case .server(let id):
            return store.snapshot.local?.servers?
                .first { $0.id == id }?.name
                ?? "Server"
        case .build(let id):
            return store.snapshot.local?.builds?
                .first { $0.id == id }?.name
                ?? "Xcode"
        }
    }

    @ViewBuilder
    private func resolvedServiceDetail(
        _ selection: ServiceDetailSelection
    ) -> some View {
        switch selection {
        case .activity(let id):
            if let item = activityItem(id: id) {
                ActivityItemDetail(item: item)
            } else {
                serviceDetailMissing("This event is no longer in the feed.")
            }
        case .plausible(let domain):
            if let site = store.snapshot.plausible?.sites?
                .first(where: { $0.domain == domain }) {
                PlausibleSiteDetail(site: site)
            } else {
                serviceDetailMissing("This site is no longer in the reading.")
            }
        case .posthog(let id):
            if let project = store.snapshot.posthog?.projects?
                .first(where: { $0.id == id }) {
                PostHogProjectDetail(project: project)
            } else {
                serviceDetailMissing("This project is no longer in the reading.")
            }
        case .supabase(let ref):
            if let project = store.snapshot.supabase?.projects?
                .first(where: { $0.ref == ref }) {
                SupabaseProjectDetail(project: project)
            } else {
                serviceDetailMissing("This project is no longer in the reading.")
            }
        case .server(let id):
            if let server = store.snapshot.local?.servers?
                .first(where: { $0.id == id }) {
                LocalServerDetail(
                    server: server,
                    hostName: localComputerName,
                    canStop: server.pid != nil,
                    isStopping: store.stoppingServerID == server.id,
                    onStop: {
                        if confirmServerStops {
                            serverToStop = server
                        } else {
                            Task { await store.stopServer(server) }
                        }
                    }
                )
            } else {
                serviceDetailMissing("This server is no longer listening.")
            }
        case .build(let id):
            if let build = store.snapshot.local?.builds?
                .first(where: { $0.id == id }) {
                LocalBuildDetail(build: build, hostName: localComputerName)
            } else {
                serviceDetailMissing("This build is no longer running.")
            }
        }
    }

    private var localComputerName: String {
        Host.current().localizedName ?? "This Mac"
    }

    private func serviceDetailMissing(_ message: String) -> some View {
        VStack(spacing: 8) {
            Image(systemName: "questionmark.circle")
                .font(.title2)
                .foregroundStyle(.secondary)
            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }

    @ViewBuilder
    private func activityBlock(
        _ watch: IntegrationWatch, blocks: [IntegrationWatch]
    ) -> some View {
        let feed = (store.snapshot.activity ?? []).filter {
            !$0.needsAttention
        }
        switch watch {
        case .git, .github, .vercel, .sentry, .datadog, .axiom:
            // Mixed chronological feed once, at the first feed watch in
            // catalog order. Later feed slots skip.
            if IntegrationWatch.isLeadFeedWatch(watch, in: blocks),
               !feed.isEmpty {
                ActivitySection(items: feed, selection: $serviceDetail)
            }
        case .supabase:
            SupabaseSection(
                data: store.snapshot.supabase,
                selection: $serviceDetail
            )
        case .plausible:
            PlausibleSection(
                data: store.snapshot.plausible,
                selection: $serviceDetail
            )
        case .posthog:
            PostHogSection(
                data: store.snapshot.posthog,
                selection: $serviceDetail
            )
        case .servers:
            ServersSection(
                store: store,
                pendingStop: $serverToStop,
                selection: $serviceDetail
            )
        case .builds:
            BuildsSection(store: store, selection: $serviceDetail)
        case .openrouter, .aiGateway:
            let provider = store.snapshot.balanceProviders
                .first { $0.id == watch.rawValue }
            BalanceSpendSection(
                provider: provider,
                meter: store.snapshot.meter(forProviderID: watch.rawValue)
            )
        }
    }

    private var modeSwitcher: some View {
        HStack(spacing: 2) {
            ForEach(DashboardMode.allCases, id: \.self) { mode in
                Button {
                    selectedModeRaw = mode.rawValue
                    if mode == .overview {
                        selectedDashboardRaw = DashboardSelection.overview
                    }
                } label: {
                    Label(mode.title, systemImage: mode.systemImage)
                        .labelStyle(.titleAndIcon)
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                        .font(.caption.weight(
                            selectedMode == mode ? .semibold : .medium))
                        .foregroundStyle(
                            selectedMode == mode ? .primary : .secondary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                        .padding(.horizontal, 4)
                        .background {
                            if selectedMode == mode {
                                Capsule(style: .continuous)
                                    .fill(Color(nsColor: .controlBackgroundColor))
                                    .shadow(color: .black.opacity(0.06), radius: 1, y: 0.5)
                            }
                        }
                        .contentShape(Capsule(style: .continuous))
                }
                .buttonStyle(.plain)
                .help(mode.title)
                .accessibilityLabel(mode.title)
                .accessibilityAddTraits(
                    selectedMode == mode ? .isSelected : [])
            }
        }
        .padding(3)
        .background(
            Color(nsColor: .separatorColor).opacity(0.35),
            in: Capsule(style: .continuous)
        )
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Dashboard mode")
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 1) {
                Text("Headroom")
                    .font(.headline)
                Text(statusLine)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            if store.isRefreshing {
                ProgressView()
                    .controlSize(.small)
            } else {
                Circle()
                    .fill(headerDotColor)
                    .frame(width: 7, height: 7)
            }
        }
        .padding(16)
    }

    private var headerDotColor: Color {
        // Host-down is a wait, not an alarm — soft amber matches "Not updating"
        // / in-flight. Orange is for actionable Attention and real fetch fails
        // once a host is there to fail against.
        if needsSetup { return HeadroomPalette.amber }
        if store.errorMessage != nil { return HeadroomPalette.orange }
        if store.snapshot.attention?.isWarning == true {
            if store.snapshot.attention?.isCritical == true {
                return HeadroomPalette.red
            }
            return HeadroomPalette.orange
        }
        return HeadroomPalette.green
    }

    /// Always icon + label. Named accounts use the user label beside the
    /// brand mark — "Claude · Work" next to a Claude glyph truncates to the
    /// brand and hides the only word that told the tabs apart. Extra
    /// providers share the fixed popover width and truncate rather than
    /// dropping names (which left friend setups looking like icon-only chrome).
    private var providerSwitcher: some View {
        let tabs = DashboardSelection.tabs(for: visibleProviders)
        return HStack(spacing: 2) {
            ForEach(tabs, id: \.self) { tabID in
                let isSelected = selectedDashboardRaw == tabID
                let fullTitle = DashboardSelection.title(
                    for: tabID, providers: visibleProviders)
                DashboardTabButton(
                    tabID: tabID,
                    title: DashboardSelection.markTitle(
                        for: tabID, providers: visibleProviders),
                    accessibilityTitle: fullTitle,
                    isSelected: isSelected
                ) {
                    selectedDashboardRaw = tabID
                    if tabID != DashboardSelection.overview {
                        selectedProviderRaw = tabID
                    }
                }
            }
        }
        .padding(3)
        .background(
            Color(nsColor: .separatorColor).opacity(0.35),
            in: Capsule(style: .continuous)
        )
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Dashboard")
        .onChange(of: visibleProviders.map(\.id)) { _, ids in
            // Drop onto Summary if the selected provider was disabled.
            if !isOverview, !ids.contains(selectedDashboardRaw) {
                selectedDashboardRaw = DashboardSelection.overview
            }
        }
    }

    private var footer: some View {
        HStack(spacing: 14) {
            Button {
                Task { await store.refresh() }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.plain)
            .disabled(store.isRefreshing)
            .help("Refresh")
            .accessibilityLabel("Refresh")
            if let update = updates.available, UpdateCheck.canSelfUpdate {
                Button {
                    do {
                        try UpdateInstaller.install(update)
                    } catch {
                        updateInstallMessage = error.localizedDescription
                    }
                } label: {
                    Label(
                        HeadroomCopy.newVersionAvailable(
                            from: UpdateCheck.installedVersion,
                            to: update.version
                        ),
                        systemImage: "arrow.down.circle.fill"
                    )
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.tint)
                }
                .buttonStyle(.plain)
                .help("Install Headroom \(update.version)")
                .accessibilityLabel(
                    HeadroomCopy.newVersionAvailableAccessibility(
                        from: UpdateCheck.installedVersion,
                        to: update.version
                    )
                )
            }
            Spacer()
            SettingsLink {
                Image(systemName: "gearshape")
            }
            .buttonStyle(.plain)
            .help(HeadroomCopy.settings)
            .accessibilityLabel(HeadroomCopy.settings)
            Button {
                NSApplication.shared.terminate(nil)
            } label: {
                Image(systemName: "power")
            }
            .buttonStyle(.plain)
            .help("Quit")
            .accessibilityLabel("Quit")
        }
        .font(.body)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 16)
        .padding(.vertical, 11)
    }

    private var statusLine: String {
        if store.isRefreshing {
            // Recovering from an outage kicks /sync/refresh; say so instead of
            // leaving "Updated … ago" frozen under the spinner.
            return store.errorMessage == nil && store.lastRefresh != nil
                ? HeadroomCopy.refreshing
                : HeadroomCopy.reconnecting
        }
        // Setup owns the host-down story. Don't also paint Foundation's
        // "Could not connect to the server" under the title — same fact twice,
        // once as an error.
        if needsSetup {
            return HeadroomCopy.hostNotAnswering
        }
        if let error = store.errorMessage {
            return error
        }
        if let attention = store.snapshot.attention, attention.isWarning {
            return attention.summary ?? HeadroomCopy.needsAttention
        }
        if let stale = worstStaleSource {
            let age = stale.ageS ?? 0
            let minutes = max(1, age / 60)
            let title = stale.title ?? stale.id
            return "\(title) · \(minutes)m stale"
        }
        if let lastRefresh = store.lastRefresh {
            return "Updated \(lastRefresh.formatted(.relative(presentation: .named)))"
        }
        return "Connecting to \(HeadroomClient.displayEndpoint)"
    }

    private var worstStaleSource: SyncSource? {
        (store.snapshot.sources ?? [])
            .filter { ($0.enabled ?? true) && $0.stale == true }
            .sorted { ($0.ageS ?? 0) > ($1.ageS ?? 0) }
            .first
    }
}

/// Segment in the summary/provider switcher. Plain buttons on macOS only
/// hit-test their text unless the padded capsule is an explicit content shape.
private struct DashboardTabButton: View {
    let tabID: String
    let title: String
    /// Spoken / hover name. Keeps the brand when the visible title is only
    /// the account label next to the mark.
    var accessibilityTitle: String = ""
    let isSelected: Bool
    let action: () -> Void

    @State private var hovering = false

    private var spokenTitle: String {
        accessibilityTitle.isEmpty ? title : accessibilityTitle
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                if tabID == DashboardSelection.overview {
                    Image(systemName: "rectangle.grid.2x2")
                        .font(.system(size: 10.5, weight: .medium))
                } else {
                    ProviderMark(providerID: tabID, size: 11)
                }
                Text(title)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
            }
            .font(.caption.weight(isSelected ? .semibold : .medium))
            .foregroundStyle(isSelected ? .primary : .secondary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
            .padding(.horizontal, 4)
            .background {
                if isSelected {
                    Capsule(style: .continuous)
                        .fill(Color(nsColor: .controlBackgroundColor))
                        .shadow(color: .black.opacity(0.06), radius: 1, y: 0.5)
                } else if hovering {
                    Capsule(style: .continuous)
                        .fill(Color.primary.opacity(0.06))
                }
            }
            .contentShape(Capsule(style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .help(spokenTitle)
        .accessibilityLabel(spokenTitle)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}
