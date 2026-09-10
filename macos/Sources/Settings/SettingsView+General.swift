import SwiftUI

extension SettingsView {
    var generalPane: some View {
        Form {
            hostSection

            timezoneSection

            Section {
                menuBarIconPreview
                Picker(HeadroomCopy.menuBarIcon, selection: $menuBarIconStyle) {
                    Text(HeadroomCopy.menuBarIconRemaining)
                        .tag(MenuBarIconStyle.remaining.rawValue)
                    Text(HeadroomCopy.menuBarIconPace)
                        .tag(MenuBarIconStyle.pace.rawValue)
                }
                .pickerStyle(.segmented)
                Toggle(HeadroomCopy.menuBarIconInvert, isOn: $menuBarIconInvert)
            } footer: {
                Text(HeadroomCopy.menuBarIconHint)
            }

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

            updatesSection

            Section {
                Button(HeadroomCopy.showWelcome) {
                    NotificationCenter.default.post(
                        name: .headroomShowWelcome, object: nil)
                }
            }
        }
        .formStyle(.grouped)
    }

    /// The glyph as the menu bar draws it, on a strip that reads as one.
    /// Drawn by the real renderer at the real 18pt, so the picker cannot
    /// describe a mark the status item does not paint.
    var menuBarIconPreview: some View {
        let preview = menuBarPreviewWindows
        return LabeledContent(HeadroomCopy.menuBarIconPreview) {
            VStack(alignment: .trailing, spacing: 6) {
                HStack(spacing: 12) {
                    Image(nsImage: MeterIconRenderer.render(
                        windows: preview.windows,
                        healthy: true,
                        style: MenuBarIconStyle(rawValue: menuBarIconStyle)
                            ?? .remaining,
                        invert: menuBarIconInvert,
                        accessibilityDescription: HeadroomCopy.menuBarIcon
                    ))
                    // Neighbours and a clock, dimmed: they place the glyph in
                    // a menu bar without competing with it.
                    Image(systemName: "wifi")
                        .foregroundStyle(.tertiary)
                    Image(systemName: "battery.100")
                        .foregroundStyle(.tertiary)
                    Text(Self.menuBarPreviewClock)
                        .font(.system(size: 12))
                        .foregroundStyle(.tertiary)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(
                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                        .fill(.quaternary)
                )
                if preview.isSample {
                    Text(HeadroomCopy.menuBarIconPreviewSample)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    /// Live top-3 tanks when the host reports them, sample numbers otherwise.
    /// An empty strip would answer nothing about what the two styles draw,
    /// and the picker is often the first thing a new install touches.
    var menuBarPreviewWindows: (windows: [MeterWindow], isSample: Bool) {
        if let snapshot = menuBarPreviewSnapshot {
            let live = snapshot.focusProviders().map {
                snapshot.meter(for: $0).menuBarWindow
            }
            if live.contains(where: { $0.percent != nil }) {
                return (live, false)
            }
        }
        return (Self.menuBarPreviewSampleWindows, true)
    }

    /// One slot under pace, one on it, one over — so switching styles or
    /// flipping Invert visibly changes the mark.
    static let menuBarPreviewSampleWindows: [MeterWindow] = [
        MeterWindow(title: "Weekly", percent: 22, pacePercent: 40),
        MeterWindow(title: "Weekly", percent: 54, pacePercent: 52),
        MeterWindow(title: "Weekly", percent: 86, pacePercent: 61),
    ]

    static var menuBarPreviewClock: String {
        Date().formatted(date: .omitted, time: .shortened)
    }

    /// Drop an Integrations catalog row into another's slot.
    func moveServicePanel(_ dragged: String, before target: String) async {
        guard dragged != target else { return }
        var order = integrationsOrder
        guard let from = order.firstIndex(of: dragged) else { return }
        order.remove(at: from)
        guard let to = order.firstIndex(of: target) else { return }
        order.insert(dragged, at: to)
        await commitServicesOrder(order)
    }

    func nudgeServicePanel(_ id: String, by offset: Int) async {
        var order = integrationsOrder
        guard let from = order.firstIndex(of: id) else { return }
        let to = from + offset
        guard order.indices.contains(to) else { return }
        order.swapAt(from, to)
        await commitServicesOrder(order)
    }

    func commitServicesOrder(_ order: [String]) async {
        do {
            let stored = try await client.setIntegrationsOrder(order)
            integrationsOrder = IntegrationWatch.ordered(from: stored).map(\.rawValue)
            servicesOrder = IntegrationWatch.activityBlocks(from: stored).map(\.rawValue)
            await reloadSources()
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    /// The zone every day boundary is drawn in.
    ///
    /// Loaded here rather than in the window's `.task` so opening Settings
    /// to flip one switch does not also pay for a pane nobody looked at.
    var timezoneSection: some View {
        Section {
            TextField(
                "Time zone",
                text: $timezoneDraft,
                prompt: Text(TimeZone.current.identifier)
            )
            .onSubmit { Task { await saveTimezone() } }
            HStack {
                Button(HeadroomCopy.settingsSave) {
                    Task { await saveTimezone() }
                }
                .disabled(timezoneDraft
                    .trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Button("Use this Mac’s") {
                    timezoneDraft = TimeZone.current.identifier
                }
                .disabled(timezoneDraft == TimeZone.current.identifier)
                Spacer()
            }
            if let timezoneMessage {
                Text(timezoneMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text("Day boundaries")
        } footer: {
            // The default is UTC, which is right for nobody in particular —
            // and until this field existed it could only be changed by hand
            // editing ~/.headroom/config.json.
            Text("Where daily burn, resets and history start a new day. Follows you to your other Macs, so one person's charts agree about when today began.")
        }
        .task { await reloadTimezone() }
    }

    func reloadTimezone() async {
        guard let config = try? await client.fetchTimezoneConfiguration(),
              let zone = config.timezone, !zone.isEmpty
        else { return }
        timezoneDraft = zone
    }

    func saveTimezone() async {
        let zone = timezoneDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !zone.isEmpty else { return }
        do {
            let config = try await client.setTimezoneConfiguration(zone)
            timezoneDraft = config.timezone ?? zone
            timezoneMessage = "Saved."
        } catch {
            timezoneMessage = error.localizedDescription
        }
    }

    var updatesSection: some View {
        Section {
            LabeledContent(HeadroomCopy.appUpdatesCurrent) {
                Text(UpdateCheck.installedVersion)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            LabeledContent(HeadroomCopy.appUpdatesLatest) {
                Text(updatesLatestLabel)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            Toggle(HeadroomCopy.automaticUpdateChecks, isOn: $automaticUpdateChecks)
            Button(
                updates.isChecking
                    ? HeadroomCopy.checkingForUpdates
                    : HeadroomCopy.checkForUpdates
            ) {
                Task { await updates.check() }
            }
            .disabled(updates.isChecking)

            if let found = updates.available, UpdateCheck.canSelfUpdate {
                Button(HeadroomCopy.installUpdate) {
                    do {
                        try UpdateInstaller.install(found)
                    } catch {
                        updateInstallMessage = error.localizedDescription
                    }
                }
                .buttonStyle(.borderedProminent)
            }
        } header: {
            Text(HeadroomCopy.appUpdates)
        } footer: {
            // A manual check still runs from a copy that cannot install what
            // it finds, so say why the result will not turn into a button
            // rather than leaving a dead end.
            if let updateInstallMessage {
                Text(updateInstallMessage)
            } else if !UpdateCheck.canSelfUpdate {
                Text(HeadroomCopy.updatesNotFromHere)
            } else if updates.lastError != nil {
                Text(HeadroomCopy.updateCheckFailed)
            } else if updates.available == nil, updates.lastChecked != nil {
                Text(HeadroomCopy.upToDate)
            } else {
                Text("Headroom looks weekly for a newer notarized build, and asks before installing one.")
            }
        }
    }

    /// Feed version when known; otherwise the same "Not available" the host
    /// rows use for a field that has not been filled yet.
    var updatesLatestLabel: String {
        updates.latestVersion ?? HeadroomCopy.notAvailable
    }

    var hostSection: some View {
        Section {
            TextField(text: $endpoint) {
                Text("Endpoint")
            }
            // No poll-interval control here on purpose. docs/product.md names
            // poll intervals as a tradeoff with a right answer, and the app
            // already overrode the chosen number three ways — retry backoff,
            // a 15s floor, and the idle escalation — so it only applied while
            // the popover was active and nothing was failing.
            if endpointIsRemote {
                SecureField(
                    "Host token",
                    text: $hostToken,
                    prompt: keyFieldPrompt(stored: hostTokenStored)
                )
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
                    Text(hostTokenStored
                         ? HeadroomCopy.inKeychain
                         : "Not set")
                        .font(.caption)
                        .foregroundStyle(
                            hostTokenStored
                                ? AnyShapeStyle(.secondary)
                                : AnyShapeStyle(HeadroomPalette.orange))
                }
            }

            Divider()

            LabeledContent(HeadroomCopy.hostRunning) {
                Text(hostLocationLabel)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            LabeledContent(HeadroomCopy.hostProcess) {
                Text(hostProcessLabel)
                    .foregroundStyle(.secondary)
            }
            if !endpointIsRemote, HostController.isBundled {
                Toggle(HeadroomCopy.hostKeepRunning, isOn: Binding(
                    get: { hostKeepRunning },
                    set: { setHostKeepRunning($0) }
                ))
                .disabled(hostLifecycleBusy)
                Text(hostKeepRunning
                     ? HeadroomCopy.hostKeepRunningOn
                     : HeadroomCopy.hostKeepRunningOff)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let hostLifecycleMessage {
                    Text(hostLifecycleMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Toggle(HeadroomCopy.usbFallback, isOn: Binding(
                    get: { usbFallbackEnabled },
                    set: { setUSBFallback($0) }
                ))
                .disabled(usbTransportBusy || hostLifecycleBusy)
                Text(usbFallbackEnabled
                     ? HeadroomCopy.usbFallbackOn
                     : HeadroomCopy.usbFallbackOff)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                LabeledContent(HeadroomCopy.usbDevice) {
                    Text(HeadroomUSB.detectedPortLabel)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                if let usb = hostHealth?.usb {
                    LabeledContent(HeadroomCopy.usbBridge) {
                        Text(usbStatusLabel(usb))
                            .foregroundStyle(
                                usb.enabled
                                    ? AnyShapeStyle(HeadroomPalette.green)
                                    : AnyShapeStyle(.secondary)
                            )
                    }
                }
                if let usbTransportMessage {
                    Text(usbTransportMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                // Only when there is one to remove. The plist outlives the app
                // if someone deletes Headroom without leaving first, and then
                // launchd respawns a host from a bundle that is gone.
                if hostHasLaunchAgent {
                    Button(HeadroomCopy.hostRemoveService, role: .destructive) {
                        hostRemoveConfirming = true
                    }
                    .disabled(hostLifecycleBusy)
                }
            }
            LabeledContent(HeadroomCopy.hostStatus) {
                if hostHealthLoading {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Label(
                        hostHealth != nil && hostHealth?.ok != false
                            ? HeadroomCopy.hostReachable
                            : HeadroomCopy.hostUnavailable,
                        systemImage: hostHealth != nil && hostHealth?.ok != false
                            ? "checkmark.circle.fill"
                            : "exclamationmark.triangle"
                    )
                    .foregroundStyle(
                        hostHealth != nil && hostHealth?.ok != false
                            ? HeadroomPalette.green
                            : HeadroomPalette.orange
                    )
                }
            }
            if let hostHealth {
                LabeledContent(HeadroomCopy.hostVersion) {
                    Text(hostHealth.version ?? HeadroomCopy.hostNotAvailable)
                        .foregroundStyle(.secondary)
                }
                LabeledContent(HeadroomCopy.hostBuild) {
                    Text(hostHealth.build ?? HeadroomCopy.hostNotAvailable)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                LabeledContent(HeadroomCopy.hostUptime) {
                    Text(hostUptimeLabel(hostHealth.uptimeS))
                        .foregroundStyle(.secondary)
                }
                LabeledContent(HeadroomCopy.hostSourcesReporting) {
                    Text("\(hostHealth.sources.count)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            if let hostHealthMessage {
                Text(hostHealthMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Button(HeadroomCopy.hostRefreshDetails) {
                Task { await reloadHostHealth() }
            }
            .disabled(hostHealthLoading)
        } header: {
            Text("Host")
        } footer: {
            Text(endpointIsRemote
                 ? "Remote hosts need the host token (~/.headroom/token) — not the mobile token used by iPhone."
                 : "Mac, iPhone, and ESP32 all read this host. If it’s down, tap Start host or run ./scripts/install-host.sh from a clone. Source toggles also hide ESP32 pages.")
        }
        .alert(HeadroomCopy.hostRemoveServiceTitle, isPresented: $hostRemoveConfirming) {
            Button(HeadroomCopy.hostRemoveServiceConfirm, role: .destructive) {
                removeBackgroundService()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(HeadroomCopy.hostRemoveServiceBody)
        }
    }

    /// Leave nothing behind, for someone about to delete the app.
    ///
    /// Quitting is part of the action rather than a suggestion afterwards. The
    /// app reinstalls the LaunchAgent whenever it finds no host running, so a
    /// removal that left the app open would be undone by its own poll loop.
    func removeBackgroundService() {
        hostLifecycleBusy = true
        HostProcess.shared.stop()
        HostController.uninstall()
        hostHasLaunchAgent = HostController.hasLaunchAgent
        NSApp.terminate(nil)
    }

    func saveHostToken() {
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

    var hostLocationLabel: String {
        guard let url = URL(string: endpoint), let host = url.host() else {
            return endpoint
        }
        let address = url.port.map { "\(host):\($0)" } ?? host
        return endpointIsRemote ? "Remote · \(address)" : "This Mac · \(address)"
    }

    var hostProcessLabel: String {
        if endpointIsRemote { return HeadroomCopy.hostRemoteEndpoint }
        guard HostController.isBundled else { return HeadroomCopy.hostLocalProcess }
        return HostLifecycle.current == .appOwned
            ? HeadroomCopy.hostOwnedByApp
            : HeadroomCopy.hostLocalLaunchAgent
    }

    /// On means launchd owns the host. Off means this app does, and quitting
    /// Headroom stops it. The toggle reads as "keep running" rather than naming
    /// a LaunchAgent, because the choice is about what a quit does.
    func setHostKeepRunning(_ keepRunning: Bool) {
        hostKeepRunning = keepRunning
        hostLifecycleBusy = true
        hostLifecycleMessage = nil
        let mode: HostLifecycle = keepRunning ? .launchAgent : .appOwned
        HostLifecycle.store(mode)
        Task {
            let outcome = await HostLifecycleCoordinator.shared.apply(mode)
            switch outcome.readiness {
            case .ready:
                hostLifecycleMessage = nil
            case let .foreign(build):
                hostLifecycleMessage = """
                    Another host already owns :8737\(build.map { " (\($0))" } ?? "").
                    """
            case .silent:
                hostLifecycleMessage =
                    outcome.errorMessage ?? HeadroomCopy.hostUnavailable
            }
            // Switching to app-owned deletes the plist; switching back writes
            // one. The remove button appears and disappears with it.
            hostHasLaunchAgent = HostController.hasLaunchAgent
            await reloadHostHealth()
            hostLifecycleBusy = false
        }
    }

    /// Reinstall or respawn the current host so the transport environment is
    /// applied immediately. The preference is shared by launchd and the
    /// app-owned child; only the supervisor changes when this switch changes.
    func setUSBFallback(_ enabled: Bool) {
        usbFallbackEnabled = enabled
        UserDefaults.standard.set(enabled, forKey: HeadroomUSB.defaultsKey)
        usbTransportBusy = true
        usbTransportMessage = nil
        Task {
            let outcome = await HostLifecycleCoordinator.shared.apply(
                HostLifecycle.current)
            switch outcome.readiness {
            case .ready:
                usbTransportMessage = nil
            case let .foreign(build):
                usbTransportMessage = "Another host already owns :8737\(build.map { " (\($0))" } ?? "")."
            case .silent:
                usbTransportMessage = outcome.errorMessage ?? HeadroomCopy.hostUnavailable
            }
            await reloadHostHealth()
            usbTransportBusy = false
        }
    }

    func usbStatusLabel(_ usb: USBHealth) -> String {
        guard usb.enabled else { return HeadroomCopy.usbDisabled }
        if let activePort = usb.activePort {
            return "Active · \(activePort)"
        }
        if let port = usb.ports.first {
            return "Enabled · waiting on \(port)"
        }
        return HeadroomCopy.usbWaiting
    }

    func hostUptimeLabel(_ seconds: Int?) -> String {
        guard let seconds else { return HeadroomCopy.hostNotAvailable }
        let days = seconds / 86_400
        let hours = (seconds % 86_400) / 3_600
        let minutes = (seconds % 3_600) / 60
        if days > 0 { return "\(days)d \(hours)h \(minutes)m" }
        if hours > 0 { return "\(hours)h \(minutes)m" }
        return "\(minutes)m"
    }

    func reloadHostHealth() async {
        hostHealthLoading = true
        hostHealthMessage = nil
        defer { hostHealthLoading = false }
        do {
            hostHealth = try await client.health()
        } catch {
            hostHealth = nil
            hostHealthMessage = error.localizedDescription
        }
    }

    func refreshOpenAtLogin() {
        openAtLogin = LaunchAtLogin.isRequested
        openAtLoginNeedsApproval = LaunchAtLogin.needsApproval
    }

    func setOpenAtLogin(_ enabled: Bool) {
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
