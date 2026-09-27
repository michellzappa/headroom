import SwiftUI

/// iOS Settings sheet — same destination taxonomy as Mac
/// (`SettingsDestination`). Reached from the gear in every tab's toolbar; it
/// owns the only navigation stack inside the sheet.
struct MobileSettingsScreen: View {
    @ObservedObject var store: MobileUsageStore
    @Binding var showsConnection: Bool
    @Environment(\.dismiss) private var dismiss
    @AppStorage("notificationsEnabled") private var notificationsEnabled = false
    @AppStorage("backgroundRefreshEnabled") private var backgroundRefreshEnabled = true
    @State private var pairedComputers = PairedComputerStore.all

    var body: some View {
        NavigationStack {
            List {
                ForEach(SettingsDestination.iOSRoots, id: \.self) { dest in
                    NavigationLink(value: dest) {
                        Label(dest.title, systemImage: dest.symbol)
                    }
                }
            }
            .navigationTitle(HeadroomCopy.settings)
            .navigationDestination(for: SettingsDestination.self) { dest in
                pane(for: dest)
                    .navigationTitle(dest.title)
                    .navigationBarTitleDisplayMode(.inline)
            }
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    @ViewBuilder
    private func pane(for dest: SettingsDestination) -> some View {
        switch dest {
        case .connection:
            connectionPane
        case .sources:
            sourcesPane
        case .integrations:
            integrationsPane
        case .iPhone:
            iPhonePane
        case .about:
            aboutPane
        default:
            EmptyView()
        }
    }

    private var connectionPane: some View {
        Form {
            Section {
                if pairedComputers.isEmpty {
                    Text(HeadroomCopy.noComputersPaired)
                        .foregroundStyle(.secondary) 
                } else {
                    ForEach(pairedComputers) { computer in
                        Button {
                            activate(computer)
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Label(computer.name, systemImage: "desktopcomputer")
                                        .foregroundStyle(.primary)
                                    Text(MobileConnection.hostLabel(for: computer.endpoint))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                if computer.endpoint == MobileConnection.endpoint {
                                    Text(HeadroomCopy.connected)
                                        .font(.caption)
                                        .foregroundStyle(HeadroomPalette.green)
                                } else if MobileTokenStore.read(for: computer.endpoint) != nil {
                                    Image(systemName: "lock.fill")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }
                Button(HeadroomCopy.addComputer, systemImage: "plus") {
                    showsConnection = true
                }
            } header: {
                Text(HeadroomCopy.computers)
            } footer: {
                Text(HeadroomCopy.computerPairingHint)
            }
            Section {
                if let name = store.snapshot.currentMachine?.name, !name.isEmpty {
                    LabeledContent("Mac", value: name)
                }
                LabeledContent("Address", value: connectionName)
                Button("Change connection", systemImage: "network") {
                    showsConnection = true
                }
            } footer: {
                Text(MobileConnection.endpoint)
            }
            // Four read-only rows do not need a screen of their own. They
            // belong next to the connection they qualify.
            Section {
                ForEach(MobilePermission.allCases, id: \.rawValue) { permission in
                    let allowed = store.mobilePermissions[permission]
                    // A trailing `Label` inside `LabeledContent` inflates the
                    // Form row on iOS; keep the status as a fixed-size HStack.
                    LabeledContent(permission.title) {
                        HStack(spacing: 4) {
                            Image(systemName: allowed
                                  ? "checkmark.circle.fill"
                                  : "minus.circle")
                            Text(allowed ? "Allowed" : "Off")
                        }
                        .font(.body)
                        .foregroundStyle(
                            allowed
                                ? AnyShapeStyle(HeadroomPalette.green)
                                : AnyShapeStyle(.secondary)
                        )
                        .fixedSize()
                    }
                }
            } header: {
                Text(HeadroomCopy.settingsPermissions)
            } footer: {
                Text("Grants are set on the Mac under Settings → \(HeadroomCopy.settingsiPhone).")
            }
        }
        .onAppear { reloadPairedComputers() }
        .onChange(of: showsConnection) { _, showing in
            if !showing { reloadPairedComputers() }
        }
    }

    /// AI meters only. Dev tools and local watches live under Integrations.
    private var sourcesPane: some View {
        Form {
            Section {
                ForEach(sources(in: .ai)) { source in
                    sourceRow(source)
                }
            } footer: {
                Text("\(SourceGroup.ai.subtitle) Same list as Mac Settings → \(HeadroomCopy.settingsSources).")
            }
        }
    }

    /// One Integrations catalog: order, enable, status. Credentials stay on
    /// the Mac; the phone only toggles and reorders what Activity watches.
    private var integrationsPane: some View {
        Form {
            Section {
                ForEach(integrationWatches) { watch in
                    catalogRow(watch)
                }
                .onMove(perform: moveIntegration)
                .disabled(!store.mobilePermissions.sources)
            } footer: {
                Text(store.mobilePermissions.sources
                     ? HeadroomCopy.integrationsOrderHint
                     : "Reordering needs the Mac’s Manage sources permission. Add keys on the Mac under Settings → \(HeadroomCopy.settingsIntegrations).")
            }
        }
        .environment(\.editMode, .constant(
            store.mobilePermissions.sources ? .active : .inactive))
    }

    private var integrationWatches: [IntegrationWatch] {
        IntegrationWatch.ordered(
            from: store.snapshot.integrationsOrder ?? store.snapshot.servicesOrder
        )
    }

    private func catalogRow(_ watch: IntegrationWatch) -> some View {
        let sourceID = watch.sourceID
        let source = (store.snapshot.sources ?? []).first { $0.id == sourceID }
        let enabled = source?.enabled ?? true
        return HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(watch.title)
                Text(catalogSubtitle(watch, source: source))
                    .font(.caption)
                    .foregroundStyle(
                        source?.ok == false
                            ? AnyShapeStyle(HeadroomPalette.orange)
                            : AnyShapeStyle(.secondary)
                    )
                    .lineLimit(1)
            }
            Spacer()
            if store.changingSourceID == sourceID {
                ProgressView()
                    .controlSize(.small)
            } else {
                Toggle(
                    "Enabled",
                    isOn: Binding(
                        get: { enabled },
                        set: { on in
                            Task { await store.setSource(sourceID, enabled: on) }
                        }
                    )
                )
                .labelsHidden()
                .disabled(!store.mobilePermissions.sources)
            }
        }
        .opacity(enabled ? 1 : 0.55)
    }

    private func catalogSubtitle(
        _ watch: IntegrationWatch, source: SyncSource?
    ) -> String {
        if let source {
            return sourceStatus(source)
        }
        switch watch {
        case .servers, .builds:
            return HeadroomCopy.local
        default:
            return ""
        }
    }

    private func moveIntegration(from source: IndexSet, to destination: Int) {
        var order = integrationWatches.map(\.rawValue)
        order.move(fromOffsets: source, toOffset: destination)
        Task { await store.setServicesOrder(order) }
    }

    private func sources(in group: SourceGroup) -> [SyncSource] {
        groupedSources.first { $0.group == group }?.sources.filter {
            $0.id != "claude-status"
        } ?? []
    }

    private var iPhonePane: some View {
        Form {
            Section {
                Toggle("Attention notifications", isOn: $notificationsEnabled)
                    .onChange(of: notificationsEnabled) { _, enabled in
                        if enabled {
                            Task { await MobileNotifications.requestAuthorization() }
                        }
                    }
                Toggle("Background refresh", isOn: $backgroundRefreshEnabled)
            } footer: {
                // A fact, not a control. It sat between two toggles looking
                // like a third one.
                Text("Stopping a server from here asks for Face ID first.")
            }
        }
    }

    private var aboutPane: some View {
        Form {
            Section {
                AboutHeadroomView()
            } footer: {
                Text(HeadroomCopy.aboutOpenSourceFooter)
            }
        }
    }

    private var connectionName: String {
        MobileConnection.hostLabel
    }

    private func reloadPairedComputers() {
        // Existing installs have one token but no paired-computers metadata.
        // Seed the list from the current snapshot so adding this screen does
        // not make a working connection look lost.
        if PairedComputerStore.all.isEmpty, MobileConnection.isConfigured {
            PairedComputerStore.upsert(
                endpoint: MobileConnection.endpoint,
                machineID: store.snapshot.currentMachine?.id,
                machineName: store.snapshot.currentMachine?.name
            )
        }
        pairedComputers = PairedComputerStore.all
    }

    private func activate(_ computer: PairedComputer) {
        guard computer.endpoint != MobileConnection.endpoint,
              MobileTokenStore.read(for: computer.endpoint) != nil else { return }
        store.stopLiveUpdates()
        store.forgetArchive()
        UserDefaults.standard.set(computer.endpoint, forKey: MobileConnection.endpointKey)
        UserDefaults.standard.set(true, forKey: MobileConnection.configuredKey)
        Task { await store.configured() }
    }

    private var groupedSources: [(group: SourceGroup, sources: [SyncSource])] {
        (store.snapshot.sources ?? []).groupedBySourceGroup()
    }

    private func sourceRow(_ source: SyncSource) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(source.title ?? source.id.capitalized)
                Text(sourceStatus(source))
                    .font(.caption)
                    .foregroundStyle(
                        source.ok == false
                            ? AnyShapeStyle(HeadroomPalette.orange)
                            : AnyShapeStyle(.secondary)
                    )
            }
            Spacer()
            if store.changingSourceID == source.id {
                ProgressView()
                    .controlSize(.small)
            } else {
                Toggle(
                    "Enabled",
                    isOn: Binding(
                        get: { source.enabled ?? true },
                        set: { enabled in
                            Task { await store.setSource(source.id, enabled: enabled) }
                        }
                    )
                )
                .labelsHidden()
                .disabled(!store.mobilePermissions.sources)
            }
        }
    }

    private func sourceStatus(_ source: SyncSource) -> String {
        // Off / Library rows keep a blank poll payload whose detail is
        // "not connected". That string means no Keychain key on the Mac
        // Integrations hub — here it would lie about a source you paused.
        if source.enabled == false {
            if source.configured == true, let detail = source.detail {
                return detail
            }
            return "Off"
        }
        if source.ok == false {
            return source.error ?? source.detail ?? "Error"
        }
        if let detail = source.detail ?? source.hint {
            return detail
        }
        return "OK"
    }
}
